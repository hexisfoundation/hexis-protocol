"""
HEXIS Trust API v0.6.1
======================
Production-grade architecture for "say no when necessary".

Designed to handle 10M registered actors on single $6/mo VPS through:
  - Aggressive caching (90%+ cache hit on reads)
  - Smart rate limiting (per IP, per actor, per API key)
  - Graceful degradation (503 when overloaded)
  - Connection pooling (SQLite WAL + concurrent reads)
  - Async write queue (batched writes)
  - Honest backpressure (429 with Retry-After)

Hexis Foundation — no legal entity, by design
contact@hexisfoundation.org
"""

import asyncio
import hashlib
import hmac
import json
import logging
import os
import secrets
import sqlite3
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional, Dict, List, Tuple, Any

from fastapi import FastAPI, HTTPException, Request, Header, Depends
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

# Local module, added 2026-08-14. It must be deployed in the same `mv` as this
# file: `py_compile` checks syntax, not imports, so a cutover that leaves it
# behind passes every pre-flight check and then fails at startup forever, five
# seconds apart, under Restart=always. See DEPLOY.md.
import hexis_reconcile

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("hexis")


# ============================================================
# CONFIGURATION - tune these for capacity
# ============================================================

DB_PATH = "/opt/hexis_newflow/hexis.db"
SERVER_VERSION = "0.8.0"

# Rate limits (sliding window)
RATE_LIMIT_PER_IP_PER_SEC = 10       # max 10 req/sec per IP
RATE_LIMIT_PER_IP_PER_MIN = 300      # max 300 req/min per IP
RATE_LIMIT_PER_ACTOR_PER_MIN = 60    # max 60 events/min per actor
RATE_LIMIT_FREE_TIER_PER_DAY = 1000  # 1000 calls/day for free API key

# Capacity thresholds (graceful degradation)
MAX_CONCURRENT_REQUESTS = 5000       # reject 503 above this
MAX_QUEUE_SIZE = 10000               # write queue cap

# Cache settings
CACHE_TRUST_TTL_SEC = 30             # /trust/{id} cached 30s
CACHE_LEADERBOARD_TTL_SEC = 60       # /leaderboard cached 60s
CACHE_MAX_ENTRIES = 100000           # LRU cap

# Reconciliation (2026-08-14)
RECONCILE_INTERVAL_SEC = 3600        # recompute the stored totals hourly
RECONCILE_MAX_REPORTED = 20          # listed per report; the count is never capped

# Connection pool
DB_READ_POOL_SIZE = 20               # concurrent reads
DB_WRITE_BATCH_INTERVAL_MS = 100     # batch writes every 100ms
DB_WRITE_BATCH_MAX_SIZE = 500        # max 500 events per batch

# Sensitivity tier multipliers
SENSITIVITY_TIERS = {
    1: ("Public", 1.0),
    2: ("Internal", 5.0),
    3: ("Confidential", 20.0),
    4: ("Regulated", 100.0),
}

# Geographic context multiplier (GDP-adjusted)
COUNTRY_C_MULTIPLIER = {
    "US": 0.85, "GB": 0.90, "DE": 0.92, "JP": 0.95,
    "KR": 1.00, "CN": 1.20, "BR": 1.35, "MX": 1.40,
    "IN": 1.55, "VN": 1.69, "ID": 1.62, "PH": 1.58,
    "NG": 1.85, "PK": 1.78, "BD": 1.80, "ET": 1.95,
}


# ============================================================
# RATE LIMITER (sliding window)
# ============================================================

class RateLimiter:
    """Sliding window rate limiter with per-key buckets."""

    def __init__(self):
        self.buckets: Dict[str, deque] = defaultdict(deque)
        self.lock = asyncio.Lock()

    async def check(self, key: str, limit: int, window_sec: float) -> Tuple[bool, int]:
        """Returns (allowed, retry_after_seconds)."""
        now = time.time()
        async with self.lock:
            bucket = self.buckets[key]
            # remove expired
            while bucket and bucket[0] < now - window_sec:
                bucket.popleft()
            if len(bucket) >= limit:
                retry_after = int(bucket[0] + window_sec - now) + 1
                return False, retry_after
            bucket.append(now)
            return True, 0

    async def cleanup(self):
        """Periodic cleanup of empty buckets."""
        async with self.lock:
            empty_keys = [k for k, v in self.buckets.items() if not v]
            for k in empty_keys:
                del self.buckets[k]


rate_limiter = RateLimiter()


# ============================================================
# CACHE (LRU + TTL)
# ============================================================

class TTLCache:
    """Simple LRU cache with TTL. Single-node only."""

    def __init__(self, max_size: int = CACHE_MAX_ENTRIES):
        self.max_size = max_size
        self.data: Dict[str, Tuple[float, Any]] = {}
        self.access_order: deque = deque()
        self.hits = 0
        self.misses = 0
        self.lock = asyncio.Lock()

    async def get(self, key: str, ttl: float) -> Optional[Any]:
        async with self.lock:
            entry = self.data.get(key)
            if entry is None:
                self.misses += 1
                return None
            timestamp, value = entry
            if time.time() - timestamp > ttl:
                del self.data[key]
                self.misses += 1
                return None
            self.hits += 1
            return value

    async def set(self, key: str, value: Any):
        async with self.lock:
            if len(self.data) >= self.max_size:
                # evict oldest
                while self.access_order and len(self.data) >= self.max_size:
                    oldest = self.access_order.popleft()
                    self.data.pop(oldest, None)
            self.data[key] = (time.time(), value)
            self.access_order.append(key)

    async def invalidate(self, key: str):
        async with self.lock:
            self.data.pop(key, None)

    async def stats(self) -> Dict[str, int]:
        async with self.lock:
            total = self.hits + self.misses
            hit_rate = (self.hits / total * 100) if total > 0 else 0
            return {
                "size": len(self.data),
                "hits": self.hits,
                "misses": self.misses,
                "hit_rate_pct": round(hit_rate, 2),
            }


cache = TTLCache()


# ============================================================
# CAPACITY GUARD (concurrent request limit)
# ============================================================

class CapacityGuard:
    """Counts in-flight requests, rejects when over limit."""

    def __init__(self, max_concurrent: int):
        self.max_concurrent = max_concurrent
        self.in_flight = 0
        self.rejected_total = 0
        self.lock = asyncio.Lock()

    async def acquire(self) -> bool:
        async with self.lock:
            if self.in_flight >= self.max_concurrent:
                self.rejected_total += 1
                return False
            self.in_flight += 1
            return True

    async def release(self):
        async with self.lock:
            self.in_flight = max(0, self.in_flight - 1)


capacity_guard = CapacityGuard(MAX_CONCURRENT_REQUESTS)


# ============================================================
# DATABASE LAYER (connection pool)
# ============================================================

class DBPool:
    """SQLite connection pool with separate read/write paths."""

    def __init__(self, db_path: str, read_pool_size: int = DB_READ_POOL_SIZE):
        self.db_path = db_path
        self.read_pool: asyncio.Queue = asyncio.Queue(maxsize=read_pool_size)
        self.write_lock = asyncio.Lock()
        self._initialized = False

    async def init(self):
        if self._initialized:
            return
        # ensure WAL mode + optimizations
        conn = sqlite3.connect(self.db_path, isolation_level=None)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA cache_size=-64000")  # 64MB
        conn.execute("PRAGMA temp_store=MEMORY")
        conn.execute("PRAGMA mmap_size=268435456")  # 256MB
        self._create_schema(conn)
        conn.close()

        # populate read pool
        for _ in range(self.read_pool.maxsize):
            c = sqlite3.connect(self.db_path, isolation_level=None, check_same_thread=False)
            c.execute("PRAGMA query_only=ON")
            c.row_factory = sqlite3.Row
            await self.read_pool.put(c)
        self._initialized = True

    def _create_schema(self, conn):
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS actors (
                actor_id TEXT PRIMARY KEY,
                country TEXT,
                hexis_score REAL DEFAULT 0,
                event_count INTEGER DEFAULT 0,
                first_seen INTEGER,
                last_seen INTEGER
            );
            CREATE INDEX IF NOT EXISTS idx_actors_score ON actors(hexis_score DESC);
            CREATE INDEX IF NOT EXISTS idx_actors_last_seen ON actors(last_seen DESC);

            CREATE TABLE IF NOT EXISTS events (
                event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                actor_id TEXT NOT NULL,
                country TEXT,
                fee REAL,
                event_type TEXT,
                tier INTEGER DEFAULT 1,
                hexis_minted REAL,
                timestamp INTEGER,
                FOREIGN KEY(actor_id) REFERENCES actors(actor_id)
            );
            CREATE INDEX IF NOT EXISTS idx_events_actor ON events(actor_id);
            CREATE INDEX IF NOT EXISTS idx_events_time ON events(timestamp DESC);

            CREATE TABLE IF NOT EXISTS api_keys (
                key_prefix TEXT PRIMARY KEY,
                key_hash TEXT NOT NULL,
                created INTEGER,
                last_used INTEGER,
                calls_today INTEGER DEFAULT 0,
                day_start INTEGER,
                tier TEXT DEFAULT 'free',
                label TEXT,
                -- Which actor_ids this key may credit: a comma-separated list,
                -- or '*'. NULL means no authorisation was ever recorded, and
                -- key_may_credit() refuses it. Added 2026-08-23 (OPEN.md #2).
                actor_scope TEXT
            );

            -- Added after api_keys existed, so CREATE TABLE above will not
            -- add it to a database that predates it. Refused keys, not crashes.
            CREATE TABLE IF NOT EXISTS network_stats (
                key TEXT PRIMARY KEY,
                value REAL
            );
            INSERT OR IGNORE INTO network_stats(key, value) VALUES('total_actors', 0);
            INSERT OR IGNORE INTO network_stats(key, value) VALUES('total_events', 0);
            INSERT OR IGNORE INTO network_stats(key, value) VALUES('total_hexis_mined', 0);
        """)

        # `label` was added to api_keys on 2026-08-14. CREATE TABLE IF NOT
        # EXISTS does nothing to a table that already exists, so a database
        # created before that date needs the column added explicitly or every
        # key issuance fails on a table the schema block claims is correct.
        cols = {r[1] for r in conn.execute("PRAGMA table_info(api_keys)")}
        if "label" not in cols:
            conn.execute("ALTER TABLE api_keys ADD COLUMN label TEXT")
        # `actor_scope` added 2026-08-23, same reasoning. It arrives NULL on any
        # pre-existing key, and key_may_credit() refuses a NULL scope rather
        # than reading it as permission. That is deliberate: widening old keys
        # to "*" during a migration would grant exactly the authority this
        # column exists to withhold. There are zero keys in the live database,
        # so nothing is being broken here — but the default would be the same
        # if there were.
        if "actor_scope" not in cols:
            conn.execute("ALTER TABLE api_keys ADD COLUMN actor_scope TEXT")

    @asynccontextmanager
    async def read(self):
        conn = await self.read_pool.get()
        try:
            yield conn
        finally:
            await self.read_pool.put(conn)

    @asynccontextmanager
    async def write(self):
        async with self.write_lock:
            conn = sqlite3.connect(self.db_path, isolation_level=None)
            conn.row_factory = sqlite3.Row
            try:
                yield conn
            finally:
                conn.close()


db = DBPool(DB_PATH)


# ============================================================
# WRITE QUEUE (batched async writes)
# ============================================================

class WriteQueue:
    """Batches integrity event writes to reduce SQLite contention."""

    def __init__(self, max_size: int = MAX_QUEUE_SIZE):
        self.queue: asyncio.Queue = asyncio.Queue(maxsize=max_size)
        self.dropped = 0
        self.processed = 0
        self._task: Optional[asyncio.Task] = None

    async def submit(self, event: dict) -> bool:
        try:
            self.queue.put_nowait(event)
            return True
        except asyncio.QueueFull:
            self.dropped += 1
            return False

    async def start(self):
        self._task = asyncio.create_task(self._worker())

    async def _worker(self):
        while True:
            try:
                batch = []
                # collect first item (blocking)
                first = await self.queue.get()
                batch.append(first)
                # collect more without blocking
                deadline = time.time() + DB_WRITE_BATCH_INTERVAL_MS / 1000
                while len(batch) < DB_WRITE_BATCH_MAX_SIZE and time.time() < deadline:
                    try:
                        timeout = max(0.001, deadline - time.time())
                        item = await asyncio.wait_for(self.queue.get(), timeout=timeout)
                        batch.append(item)
                    except asyncio.TimeoutError:
                        break

                await self._flush(batch)
                self.processed += len(batch)
            except Exception as e:
                log.error("write worker error: %s", e)
                await asyncio.sleep(0.1)

    async def _flush(self, batch: List[dict]):
        async with db.write() as conn:
            conn.execute("BEGIN")
            try:
                for ev in batch:
                    self._insert_event(conn, ev)
                    self._update_actor(conn, ev)
                # invalidate caches for affected actors
                for ev in batch:
                    asyncio.create_task(cache.invalidate(f"trust:{ev['actor_id']}"))
                asyncio.create_task(cache.invalidate("leaderboard"))
                asyncio.create_task(cache.invalidate("status"))
                conn.execute("COMMIT")
            except Exception as e:
                conn.execute("ROLLBACK")
                log.error("flush failed: %s", e)
                raise

    def _insert_event(self, conn, ev):
        conn.execute("""
            INSERT INTO events(actor_id, country, fee, event_type, tier, hexis_minted, timestamp)
            VALUES(?, ?, ?, ?, ?, ?, ?)
        """, (ev["actor_id"], ev["country"], ev["fee"], ev["event_type"],
              ev["tier"], ev["hexis_minted"], ev["timestamp"]))

    def _update_actor(self, conn, ev):
        now = ev["timestamp"]
        conn.execute("""
            INSERT INTO actors(actor_id, country, hexis_score, event_count, first_seen, last_seen)
            VALUES(?, ?, ?, 1, ?, ?)
            ON CONFLICT(actor_id) DO UPDATE SET
                hexis_score = hexis_score + excluded.hexis_score,
                event_count = event_count + 1,
                last_seen = excluded.last_seen,
                country = COALESCE(country, excluded.country)
        """, (ev["actor_id"], ev["country"], ev["hexis_minted"], now, now))
        # update aggregates
        conn.execute("UPDATE network_stats SET value = value + ? WHERE key = 'total_hexis_mined'",
                     (ev["hexis_minted"],))
        conn.execute("UPDATE network_stats SET value = value + 1 WHERE key = 'total_events'")


write_queue = WriteQueue()


# ============================================================
# HEXIS FORMULA
# ============================================================

def compute_hexis(fee: float, country: str, event_type: str, tier: int = 1) -> float:
    """
    HEXIS = S * BO * W * TDR * T * C
    Simplified for v0.6.1; full witness model in v0.7+.
    """
    S = 0.7   # sacrifice score (default)
    BO = min(1.0, fee / 100.0)  # betrayal opportunity
    W = 0.6   # witness score (default - automated)
    TDR = 1.0  # time decay (fresh event)
    T = 1.0   # timing (submitted before outcome)
    C = COUNTRY_C_MULTIPLIER.get(country.upper(), 1.0)

    # tier multiplier on BO
    tier_name, tier_mult = SENSITIVITY_TIERS.get(tier, SENSITIVITY_TIERS[1])
    BO_scaled = min(1.0, BO * (tier_mult ** 0.5))

    hexis = S * BO_scaled * W * TDR * T * C
    return round(hexis, 6)


def compute_grade(score: float, known: bool = True) -> Tuple[str, float]:
    """
    Returns (grade, x402_collateral_multiplier).

    `known` is whether this store holds any record of the actor at all, and it
    is the whole point of this function's second parameter existing.

    Until 2026-08-23 there was no such parameter. A score of zero because the
    actor was never seen and a score of zero because the actor was seen and
    earned nothing returned the same verdict: Reject, at 10.0x collateral. This
    store is fed only by POST /integrity/submit and holds no actors at all,
    while the bridge holds four that have earned HEXIS through compute jobs.
    So every real actor on the network was told, by the service named "HEXIS
    Trust API", that it was rejected — three of them graded Moderate or Low a
    hostname away.

    An absent record is not a bad record. It is the absence of one, and the
    only defensible verdict over it is that nothing is known. `Unverified`
    carries 0.0 rather than a multiplier because a collateral number would be
    a claim, and there is nothing here to base one on. That is exactly how the
    bridge has always treated a zero score (`get_trust_grade`, grade
    "Unverified", collateral_mult 0.0); the two services now agree on the
    meaning of not knowing.
    """
    if not known:
        return "Unverified", 0.0
    if score >= 0.05:
        return "High", 1.0
    elif score >= 0.005:
        return "Moderate", 1.5
    elif score >= 0.001:
        return "Low", 3.0
    elif score >= 0.0001:
        return "Minimal", 5.0
    else:
        return "Reject", 10.0


# ============================================================
# REQUEST/RESPONSE MODELS
# ============================================================

class IntegrityEventSubmit(BaseModel):
    actor_id: str = Field(..., min_length=1, max_length=128)
    country: str = Field(..., min_length=2, max_length=3)
    fee: float = Field(..., ge=0)
    event_type: str = Field(..., max_length=64)
    tier: int = Field(default=1, ge=1, le=4)


# APIKeyCreateRequest was deleted 2026-08-14 with POST /keys/create. Keys are
# issued on the host now, so nothing parses a key request off the network.


# ============================================================
# AUTHENTICATION (2026-08-14)
#
# What was here before was named `check_api_key` and authenticated nothing. It
# returned None for a missing, malformed or unknown key instead of raising, and
# the one endpoint that depended on it used the result like this:
#
#     if api_key:
#         if api_key.get("tier") == "free":
#             pass
#
# So `POST /integrity/submit` was open to anyone, and the caller chose both the
# actor_id being credited and, through `fee`/`country`/`tier`, the amount. One
# anonymous request put any actor_id at the top trust grade: the High threshold
# is 0.05 and a single event with fee >= 100 mints ~0.357.
#
# Read the failure honestly: the dependency existed, the endpoint declared it,
# and every review that looked for "is this endpoint authenticated" would have
# found a Depends() and stopped. It typechecked. It just did not refuse
# anybody. `Optional[dict]` returning None on failure is what let it through
# — the type says "there may be no key" and the code above says "and that is
# fine".
# ============================================================

async def require_api_key(authorization: Optional[str] = Header(default=None)) -> dict:
    """
    A valid API key, or 401. Never returns None — that was the bug.

    Keys are issued off the network (`--create-key`, below). This proves *who*
    is calling, and only that. Which actor_id they may credit is a separate
    question, asked by key_may_credit() at the write site since 2026-08-23;
    before that it was asked nowhere and any keyholder could mint trust for
    anybody.
    """
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, {
            "error": "api_key_required",
            "reason": "send Authorization: Bearer <key>; keys are issued off "
                      "the network and POST /keys/create is gone",
        })
    key = authorization[len("Bearer "):].strip()
    if len(key) < 16:
        raise HTTPException(401, {"error": "api_key_malformed"})

    prefix = key[:8]
    key_hash = hashlib.sha256(key.encode()).hexdigest()
    async with db.read() as conn:
        row = conn.execute(
            "SELECT * FROM api_keys WHERE key_prefix = ?", (prefix,)
        ).fetchone()
    # Compare the hash rather than let SQL do it, so a wrong key costs the same
    # time as a right one.
    if row is None or not hmac.compare_digest(row["key_hash"], key_hash):
        raise HTTPException(401, {"error": "api_key_unknown"})
    return dict(row)


# Read by the startup audit to tell a guarded write from an unguarded one.
require_api_key._hexis_api_key_guard = True


ACTOR_SCOPE_ANY = "*"


def key_may_credit(row: dict, actor_id: str) -> Tuple[bool, str]:
    """
    Whether this key is allowed to credit this actor. Authorisation, which is
    not the same question as authentication and until 2026-08-23 was not asked.

    A key proved *who* called. It established nothing about which actor_id the
    caller might name, so any keyholder could mint trust for anybody — the
    defect recorded as OPEN.md #2, whose closing condition was "before any key
    is ever issued to a third party". No key has ever been issued at all, so
    this lands with nothing in front of it to break.

    Fails closed on purpose. A key with no scope recorded is refused rather
    than waved through, because the alternative reproduces exactly the bug
    that cost this project two junk chain rows: a check that cannot answer,
    answering yes. There is no migration granting old keys "*" — there are no
    old keys, and if there were, silently widening them would be the wrong
    default anyway.
    """
    scope = (row.get("actor_scope") or "").strip()
    if not scope:
        return False, (
            "this key has no actor_scope recorded and is refused. keys issued "
            "before 2026-08-23 predate authorisation; reissue with "
            "--create-key <label> <actor_id[,actor_id...]|*>")
    if scope == ACTOR_SCOPE_ANY:
        return True, ""
    allowed = {s.strip() for s in scope.split(",") if s.strip()}
    if actor_id in allowed:
        return True, ""
    return False, (
        f"this key is not authorised to credit '{actor_id}'. authentication "
        f"proves who called; it does not decide whose trust they may mint")


def mint_api_key(db_path: str, label: Optional[str] = None,
                 actor_scope: Optional[str] = None) -> str:
    """
    Issue a key. Not reachable over the network, by design.

    `POST /keys/create` used to do this for anyone who asked, unauthenticated
    and unlimited. Gating it leaves the question of who may mint a key, and the
    answer here is deliberately the boring one: whoever has a shell on the
    host. That is the trust boundary this system already has, so it invents
    nothing — no operator key, no admin endpoint, no second credential to
    protect.

    `actor_scope` is which actor_ids the key may credit: a comma-separated list,
    or "*" for any. Required — a key with no scope authenticates and authorises
    nothing, which is the point.

        python3 hexis_api_v0.6.1.py --create-key "label" "actor_a,actor_b"
    """
    if not actor_scope or not actor_scope.strip():
        raise ValueError(
            "actor_scope is required: a comma-separated list of actor_ids this "
            "key may credit, or '*' for any. A key without one can call "
            "nothing, so issuing it would only look like access.")
    raw_key = secrets.token_urlsafe(32)
    conn = sqlite3.connect(db_path, isolation_level=None)
    try:
        now = int(time.time())
        conn.execute(
            "INSERT INTO api_keys(key_prefix, key_hash, created, day_start, "
            "tier, label, actor_scope) VALUES(?, ?, ?, ?, 'free', ?, ?)",
            (raw_key[:8], hashlib.sha256(raw_key.encode()).hexdigest(), now,
             now, label, actor_scope.strip()),
        )
    finally:
        conn.close()
    return raw_key


# Write routes that legitimately carry no key guard. Every entry needs a
# reason: this list is the only way a write escapes authentication, and the
# bridge's version of it is what found seven unguarded routes nobody had
# inventoried.
UNGUARDED_WRITE_ROUTES = {
    # Returns 410 Gone unconditionally and touches no state.
    "/keys/create",
}


def audit_write_route_protection(app_) -> list:
    """
    Refuse to start if any write route is unauthenticated.

    The same check the bridge runs, for the same reason. This service went five
    months with its only write endpoint open because the guard was present and
    inert; a check that asks "does a POST route carry a working guard" would
    have said no on the first boot.
    """
    from fastapi.routing import APIRoute

    write_methods = {"POST", "PUT", "PATCH", "DELETE"}
    unguarded = []
    for route in app_.routes:
        if not isinstance(route, APIRoute) or not (route.methods & write_methods):
            continue
        if route.path in UNGUARDED_WRITE_ROUTES:
            continue
        guarded = any(
            getattr(d.dependency, "_hexis_api_key_guard", False)
            for d in route.dependencies
        ) or any(
            getattr(d.call, "_hexis_api_key_guard", False)
            for d in route.dependant.dependencies
        )
        if not guarded:
            unguarded.append(f"{sorted(route.methods)} {route.path}")
    return unguarded


# ============================================================
# RECONCILIATION (2026-08-14)
#
# `actors.hexis_score`, `network_stats.total_hexis_mined` and
# `network_stats.total_events` are running totals mutated in place. The `events`
# table is the ledger that produced them, and nothing compared the two — the
# Harmony shape recorded in CORRECTIONS.md. hexis_reconcile.py is the
# comparison; this section is when it runs and what happens when it disagrees.
# ============================================================

class ReconcileState:
    """Last result and counters, so a mismatch is visible without reading logs."""

    def __init__(self):
        self.runs = 0
        self.mismatches = 0
        self.last: Optional[hexis_reconcile.ReconcileResult] = None
        self.last_ok_at: Optional[str] = None
        self.last_record_written = False

    def observe(self, result, recorded: bool):
        self.runs += 1
        self.last = result
        self.last_record_written = recorded
        if result.ok:
            self.last_ok_at = result.checked_at
        else:
            self.mismatches += 1

    def summary(self) -> dict:
        if self.last is None:
            return {"ok": None, "runs": 0, "reason": "has not run yet"}
        return {
            "ok": self.last.ok,
            "last_run": self.last.checked_at,
            "source": self.last.source,
            "runs": self.runs,
            "mismatches": self.mismatches,
            "discrepancy_count": self.last.discrepancy_count,
            "last_ok": self.last_ok_at,
            "actors": self.last.actors_checked,
            "events": self.last.events_total,
            "recorded": self.last_record_written,
        }


reconcile_state = ReconcileState()


async def run_reconcile(source: str, *, fatal: bool):
    """
    Recompute, record, then decide.

    The order matters: the record is written *before* anything raises, so the
    boot that refuses to complete still leaves the reason on disk. A validator
    whose failure is only visible in a log the incident may also destroy is
    most of the way back to having no validator.

    `fatal` is True at boot and False afterwards, and the asymmetry is
    deliberate. At startup a mismatch is an outage; once the service is up,
    taking it down over a number that is already wrong helps nobody, so the
    running check logs, records, and shows the failure through /health,
    /status and /metrics instead.
    """
    async with db.read() as conn:
        # `to_thread` because the reconcile is synchronous SQLite work: at this
        # database's current size it is microseconds, at the size this service
        # was designed for it is not, and blocking the event loop on an hourly
        # timer is the kind of thing that only shows up under load.
        result = await asyncio.to_thread(
            hexis_reconcile.reconcile_hexis_db,
            conn,
            source=source,
            db_path=DB_PATH,
            max_reported=RECONCILE_MAX_REPORTED,
        )

    record_path = hexis_reconcile.default_record_path(DB_PATH)
    recorded = True
    try:
        await asyncio.to_thread(hexis_reconcile.append_record, record_path, result)
    except OSError as e:
        recorded = False
        log.error("reconcile record could not be written to %s: %s", record_path, e)

    reconcile_state.observe(result, recorded)
    report = hexis_reconcile.format_report(result)
    (log.error if not result.ok else log.info)("%s", report)

    if fatal and not recorded:
        # A check that cannot record itself is most of the way to not having
        # run. At boot that is a disk or permission fault worth stopping for;
        # it is also trivially fixable, unlike the mismatch case below.
        raise RuntimeError(
            f"reconcile could not write its record to {record_path} — "
            "fix the path or permissions; the check is not trusted without it"
        )
    if fatal and not result.ok:
        raise RuntimeError(
            "hexis.db does not reconcile:\n" + report +
            f"\nFull report appended to {record_path}. Nothing was repaired: a "
            "mismatch does not say which side is wrong, and rewriting the "
            "totals to match the rows would destroy the evidence in the case "
            "where it is rows that went missing. Diagnose with "
            "`python3 hexis_api_v0.6.1.py --reconcile`."
        )
    return result


async def _reconcile_loop():
    """
    Sleeps first, deliberately.

    With `Restart=always` and `RestartSec=5`, anything a startup path does
    immediately runs every five seconds during a crash loop. t=0 is already
    covered by the boot check, so the timer's job starts one interval later.
    """
    while True:
        await asyncio.sleep(RECONCILE_INTERVAL_SEC)
        try:
            await run_reconcile("periodic", fatal=False)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # never let the timer die quietly
            log.error("reconcile loop error: %s", e)


# ============================================================
# MIDDLEWARE
# ============================================================

async def rate_limit_middleware(request: Request, call_next):
    """Layer 1: capacity guard. Layer 2: per-IP rate limit."""
    # capacity check first (fail fast)
    if not await capacity_guard.acquire():
        return JSONResponse(
            {"error": "service_overloaded", "retry_after": 5},
            status_code=503,
            headers={"Retry-After": "5"}
        )
    try:
        # rate limit by IP
        ip = request.client.host if request.client else "unknown"
        # skip rate limit for /health and /metrics
        if request.url.path in ("/health", "/metrics", "/favicon.ico"):
            return await call_next(request)

        ok_sec, retry_sec = await rate_limiter.check(
            f"ip_sec:{ip}", RATE_LIMIT_PER_IP_PER_SEC, 1.0
        )
        if not ok_sec:
            return JSONResponse(
                {"error": "rate_limited_per_second", "retry_after": retry_sec},
                status_code=429,
                headers={"Retry-After": str(retry_sec)}
            )
        ok_min, retry_min = await rate_limiter.check(
            f"ip_min:{ip}", RATE_LIMIT_PER_IP_PER_MIN, 60.0
        )
        if not ok_min:
            return JSONResponse(
                {"error": "rate_limited_per_minute", "retry_after": retry_min},
                status_code=429,
                headers={"Retry-After": str(retry_min)}
            )

        return await call_next(request)
    finally:
        await capacity_guard.release()


# ============================================================
# APP LIFECYCLE
# ============================================================

@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("HEXIS API v%s starting", SERVER_VERSION)
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    await db.init()

    # Refuse to serve if any write endpoint is unauthenticated. A hole found at
    # startup is an outage; the same hole found later is an incident.
    unguarded = audit_write_route_protection(app)
    if unguarded:
        raise RuntimeError(
            "unauthenticated write routes: " + ", ".join(unguarded) +
            " — attach Depends(require_api_key) or add the route to "
            "UNGUARDED_WRITE_ROUTES with a reason"
        )
    log.info("Write-route key audit passed.")

    # Refuse to serve totals that disagree with the rows beneath them. Run
    # before the write queue starts, so nothing is in flight while it reads.
    await run_reconcile("boot", fatal=True)

    await write_queue.start()
    reconcile_task = asyncio.create_task(_reconcile_loop())
    log.info("Ready. Capacity: %d concurrent. Cache: %d entries. "
             "Reconcile every %ds.",
             MAX_CONCURRENT_REQUESTS, CACHE_MAX_ENTRIES, RECONCILE_INTERVAL_SEC)
    yield
    reconcile_task.cancel()
    log.info("Shutting down")


app = FastAPI(
    title="HEXIS Trust API",
    description="Proof of Integrity v0.8.0 - hexisfoundation.org",
    version=SERVER_VERSION,
    lifespan=lifespan,
)
app.middleware("http")(rate_limit_middleware)
# Tightened 2026-08-14. Was allow_origins=["*"] with POST allowed, on a service
# whose only write endpoint required nothing — so any page a browser loaded
# could mint trust from the visitor's machine.
#
# Be clear about what this does and does not do: CORS is a policy the *browser*
# enforces on reading responses. A form-encoded POST is a "simple request", it
# is sent without a preflight whatever is configured here, and the server still
# processes it. Restricting methods and origins removes the convenient path and
# stops arbitrary sites reading our responses; it is not what closed the hole.
# `require_api_key` on /integrity/submit is.
#
# Reads stay open to our own pages. Nothing cross-origin may write, and no
# credentials are accepted cross-origin at all.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://hexisfoundation.org",
        "https://www.hexisfoundation.org",
        "https://api.hexisfoundation.org",
        "https://bridge.hexisfoundation.org",
    ],
    allow_credentials=False,
    allow_methods=["GET"],
    allow_headers=["*"],
)


# ============================================================
# ENDPOINTS
# ============================================================

@app.get("/")
async def root():
    return {
        "service": "HEXIS Trust API",
        "version": SERVER_VERSION,
        "docs": "/docs",
        "status": "/status",
        "foundation": "hexisfoundation.org",
    }


@app.get("/health")
async def health():
    """
    Load balancer health check.

    Reports a failed reconcile in the body and keeps returning 200. That is
    DEPLOY.md's own rule — verify by content, never by status code — applied to
    this service: an external uptime probe reading only the status line is not
    a reason to flip a service out of rotation, but "ok" while the stored
    totals disagree with the rows is a lie, so the word changes.
    """
    rec = reconcile_state.summary()
    return {
        "status": "degraded" if rec.get("ok") is False else "ok",
        "version": SERVER_VERSION,
        "reconcile": rec,
    }


@app.get("/status")
async def status():
    """Public network stats."""
    cached = await cache.get("status", 5.0)
    if cached is not None:
        return cached
    async with db.read() as conn:
        rows = conn.execute("SELECT key, value FROM network_stats").fetchall()
        actors_count = conn.execute("SELECT COUNT(*) FROM actors").fetchone()[0]
    stats = {row["key"]: row["value"] for row in rows}
    result = {
        "version": SERVER_VERSION,
        "actors": actors_count,
        "events": int(stats.get("total_events", 0)),
        "hexis_mined": round(stats.get("total_hexis_mined", 0), 6),
        "in_flight": capacity_guard.in_flight,
        "max_concurrent": MAX_CONCURRENT_REQUESTS,
        "queue_pending": write_queue.queue.qsize(),
        "queue_processed": write_queue.processed,
        "queue_dropped": write_queue.dropped,
        # CORRECTIONS.md notes that this one response mixes stored figures with
        # computed ones and marks neither, which is how a reader ends up
        # trusting a stored number because the number beside it happens to be
        # derived. Marked now. `events` and `hexis_mined` are still stored
        # totals — what changed is that they are reconciled against `events`
        # hourly and at boot, and `reconcile` below says when that last held.
        "derivation": {
            "actors": "computed",
            "events": "stored",
            "hexis_mined": "stored",
        },
        "reconcile": reconcile_state.summary(),
    }
    await cache.set("status", result)
    return result


@app.get("/metrics")
async def metrics():
    """Internal metrics for ops."""
    cache_stats = await cache.stats()
    return {
        "version": SERVER_VERSION,
        "cache": cache_stats,
        "capacity": {
            "in_flight": capacity_guard.in_flight,
            "max": MAX_CONCURRENT_REQUESTS,
            "rejected_total": capacity_guard.rejected_total,
        },
        "write_queue": {
            "pending": write_queue.queue.qsize(),
            "processed": write_queue.processed,
            "dropped": write_queue.dropped,
        },
        "reconcile": reconcile_state.summary(),
    }


@app.get("/trust/{actor_id}")
async def get_trust(actor_id: str):
    """Look up trust score for any actor. Heavily cached."""
    if not actor_id or len(actor_id) > 128:
        raise HTTPException(400, "invalid actor_id")

    cache_key = f"trust:{actor_id}"
    cached = await cache.get(cache_key, CACHE_TRUST_TTL_SEC)
    if cached is not None:
        return cached

    async with db.read() as conn:
        row = conn.execute(
            "SELECT * FROM actors WHERE actor_id = ?", (actor_id,)
        ).fetchone()

    if row is None:
        score = 0.0
        events = 0
        country = None
    else:
        score = row["hexis_score"] or 0.0
        events = row["event_count"] or 0
        country = row["country"]

    known = row is not None
    grade, collateral_mult = compute_grade(score, known=known)
    accept = known and grade != "Reject"
    result = {
        "actor_id": actor_id,
        "hexis_score": round(score, 6),
        "grade": grade,
        "event_count": events,
        "country": country,
        "known_here": known,
        # Where this answer comes from, said in the answer itself rather than
        # left for a reader to infer from a zero. This store is not the
        # network's record; it is one of two, it is fed by a different path,
        # and for now it is empty. A caller deciding whether to transact needs
        # to know which of those produced the number above.
        "store": {
            "scope": "events submitted to this service via POST /integrity/submit",
            "separate_from": "https://bridge.hexisfoundation.org",
            "note": (
                "'Unverified' means this store holds no record of the actor. "
                "It is not a judgement about the actor and must not be read as "
                "one. Actors that earned HEXIS through NEWFLOW compute jobs are "
                "recorded on the bridge: GET "
                "https://bridge.hexisfoundation.org/trust/{actor_id}. The two "
                "stores are not synchronised and can disagree."),
        },
        "x402_headers": {
            "X-Hexis-Score": f"{score:.6f}",
            "X-Hexis-Grade": grade,
            "X-Hexis-Accept": "true" if accept else "false",
            "X-Hexis-Collateral-Mult": f"{collateral_mult:.1f}",
        },
    }
    await cache.set(cache_key, result)
    return result


@app.post("/integrity/submit")
async def submit_integrity_event(
    event: IntegrityEventSubmit,
    request: Request,
    api_key: dict = Depends(require_api_key),
):
    """
    Submit honest behavior event. Goes to async write queue.
    Requires a valid API key since 2026-08-14, and since 2026-08-23 a key that
    is authorised for the actor_id being credited. Per-actor rate limit applies.
    """
    # AUTHORISATION (2026-08-23, OPEN.md #2). Asked before anything else this
    # endpoint does, because every step below it writes or reserves something
    # on behalf of an actor the caller has now been shown to be entitled to
    # name. A key proved who called; this decides whose trust they may mint.
    allowed, why = key_may_credit(api_key, event.actor_id)
    if not allowed:
        raise HTTPException(403, {
            "error": "actor_not_in_key_scope",
            "actor_id": event.actor_id,
            "reason": why,
        })

    # per-actor rate limit
    ok_actor, retry_actor = await rate_limiter.check(
        f"actor:{event.actor_id}",
        RATE_LIMIT_PER_ACTOR_PER_MIN,
        60.0,
    )
    if not ok_actor:
        raise HTTPException(
            429,
            f"actor rate limit exceeded, retry in {retry_actor}s",
            headers={"Retry-After": str(retry_actor)},
        )

    # NOTE: the free-tier daily quota is NOT enforced. `calls_today` and
    # `last_used` exist in the schema and are written by no code path, so
    # GET /usage/{prefix} reports 0 forever and the "1000 calls/day" in /docs
    # is not a limit. Left as it is rather than half-implemented: the block
    # that used to sit here was `if tier == "free": pass`, which is how an
    # unenforced quota reads as an enforced one. Recorded in CORRECTIONS.md.
    # Per-actor and per-IP rate limits above are real and do apply.

    # compute HEXIS (the formula is deterministic and fast)
    hexis_minted = compute_hexis(
        fee=event.fee,
        country=event.country,
        event_type=event.event_type,
        tier=event.tier,
    )

    # queue for async write
    queued = await write_queue.submit({
        "actor_id": event.actor_id,
        "country": event.country.upper(),
        "fee": event.fee,
        "event_type": event.event_type,
        "tier": event.tier,
        "hexis_minted": hexis_minted,
        "timestamp": int(time.time()),
    })

    if not queued:
        raise HTTPException(
            503,
            "write queue full - try again later",
            headers={"Retry-After": "5"},
        )

    return {
        "status": "accepted",
        "actor_id": event.actor_id,
        "hexis_minted": hexis_minted,
        "queued": True,
        "queue_size": write_queue.queue.qsize(),
    }


@app.get("/integrity/{actor_id}")
async def get_integrity_history(actor_id: str, limit: int = 100):
    """Recent events for an actor."""
    if limit > 1000:
        limit = 1000
    cache_key = f"history:{actor_id}:{limit}"
    cached = await cache.get(cache_key, 10.0)
    if cached is not None:
        return cached
    async with db.read() as conn:
        rows = conn.execute("""
            SELECT event_id, event_type, fee, tier, hexis_minted, timestamp
            FROM events WHERE actor_id = ?
            ORDER BY timestamp DESC LIMIT ?
        """, (actor_id, limit)).fetchall()
    result = {
        "actor_id": actor_id,
        "events": [dict(r) for r in rows],
        "count": len(rows),
    }
    await cache.set(cache_key, result)
    return result


@app.get("/leaderboard")
async def leaderboard(limit: int = 100):
    """Top actors by HEXIS score. Cached aggressively."""
    if limit > 500:
        limit = 500
    cache_key = f"leaderboard:{limit}"
    cached = await cache.get(cache_key, CACHE_LEADERBOARD_TTL_SEC)
    if cached is not None:
        return cached
    async with db.read() as conn:
        rows = conn.execute("""
            SELECT actor_id, country, hexis_score, event_count
            FROM actors
            WHERE hexis_score > 0
            ORDER BY hexis_score DESC
            LIMIT ?
        """, (limit,)).fetchall()
    result = {
        "leaderboard": [
            {
                "rank": i + 1,
                "actor_id": r["actor_id"],
                "country": r["country"],
                "hexis_score": round(r["hexis_score"], 6),
                "events": r["event_count"],
                "grade": compute_grade(r["hexis_score"])[0],
            }
            for i, r in enumerate(rows)
        ]
    }
    await cache.set(cache_key, result)
    return result


@app.post(
    "/keys/create",
    # 2026-08-23. The prose docs above said "Gone (410) since 2026-08-14" from
    # the day it closed; the machine-readable contract did not. /openapi.json
    # carried `deprecated: false` and a summary FastAPI derived from the
    # function name — "Create Api Key Gone" — so any client generated from the
    # spec got a working-looking method that 410s, and the word "Gone" reached
    # the public contract only by accident. A doc a human reads and a doc a
    # generator reads are both published surfaces.
    deprecated=True,
    summary="Gone (410) — keys are issued off the network",
    description=(
        "**410 Gone since 2026-08-14.** This issued, to any anonymous caller "
        "and without limit, the key that authenticates every write on this "
        "service. Keys are issued on the host now; ask an operator."),
)
async def create_api_key_gone():
    # GATED 2026-08-14. This minted a credential to anyone who asked,
    # unauthenticated and unlimited, and that credential is now the only thing
    # standing in front of /integrity/submit. An endpoint that issues the key
    # required to reach it is not a gate; it is a queue.
    #
    # Keys are issued on the host instead — `--create-key`, see mint_api_key.
    # No new credential was invented to protect this: shell access is the trust
    # boundary the system already has.
    raise HTTPException(
        status_code=410,
        detail="gone: POST /keys/create is internal-only since 2026-08-14. It "
               "issued, to any anonymous caller and without limit, the key "
               "that authenticates writes. Keys are issued off the network; "
               "ask an operator.",
    )


@app.get("/usage/{key_prefix}")
async def get_usage(key_prefix: str):
    """Check usage for an API key by its prefix."""
    async with db.read() as conn:
        row = conn.execute(
            "SELECT key_prefix, calls_today, day_start, tier FROM api_keys WHERE key_prefix = ?",
            (key_prefix,)
        ).fetchone()
    if not row:
        raise HTTPException(404, "key not found")
    return dict(row)


# ============================================================
# DOCS
# ============================================================

@app.get("/docs", include_in_schema=False)
async def custom_docs():
    """Plain markdown docs. No JS dependencies."""
    md = f"""# HEXIS Trust API v{SERVER_VERSION}

Proof of Integrity Protocol - hexisfoundation.org

## Endpoints

### GET /trust/{{actor_id}}
Get trust score and x402-compatible headers for any actor.
Heavily cached (30s TTL).

### POST /integrity/submit
Submit honest behavior event. Async write queue.
Body: actor_id, country, fee, event_type, tier (1-4)
Requires `Authorization: Bearer <key>`. Returns 401 without one.

### GET /integrity/{{actor_id}}
Recent events for actor. Cached 10s.

### GET /leaderboard
Top actors by HEXIS score. Cached 60s.

### GET /status
Public network stats. `derivation` says which figures are stored totals and
which are computed; `reconcile` says when the stored ones were last checked
against the events that produced them.

### GET /health
Health check (no rate limit).

### GET /metrics
Internal ops metrics.

### POST /keys/create
**Gone (410) since 2026-08-14.** It issued, to any anonymous caller and
without limit, the key that authenticates writes. Keys are issued off the
network now; ask an operator.

## Rate Limits

- 10 req/sec per IP
- 300 req/min per IP
- 60 events/min per actor

The "1000 calls/day for a free API key" that stood here was never enforced.
`calls_today` is written by no code path, so the quota did not exist and this
page was the only place it did. The three limits above are real.

## Capacity

- {MAX_CONCURRENT_REQUESTS} concurrent requests max
- Returns 503 when overloaded
- Returns 429 when rate limited
- Both with Retry-After header

## Sensitivity Tiers

- Tier 1 (Public): 1.0x BO multiplier
- Tier 2 (Internal): 5.0x
- Tier 3 (Confidential): 20.0x
- Tier 4 (Regulated): 100.0x

## Geographic Justice

Country-based C multiplier. Worker in Vietnam earns
~3.4x more HEXIS per honest job than worker in US.

Geography is not a tax on integrity.
"""
    return JSONResponse(
        content=md,
        media_type="text/markdown",
    )


# ============================================================
# MAIN
# ============================================================

if __name__ == "__main__":
    import sys

    # Key issuance lives here rather than on the network. Run it on the host:
    #     python3 hexis_api_v0.6.1.py --create-key "partner name"
    if "--create-key" in sys.argv:
        i = sys.argv.index("--create-key")
        label = sys.argv[i + 1] if len(sys.argv) > i + 1 else None
        scope = sys.argv[i + 2] if len(sys.argv) > i + 2 else None
        if not label or not scope:
            print('usage: --create-key "<label>" "<actor_id[,actor_id...]|*>"')
            print()
            print("The scope is which actor_ids the key may credit. It is not")
            print("optional: a key with no scope can call nothing, so issuing")
            print("one would only look like access. Use '*' deliberately, and")
            print("only for a caller entitled to credit anybody.")
            sys.exit(2)
        try:
            _key = mint_api_key(DB_PATH, label, scope)
        except ValueError as _e:
            print(f"refused: {_e}")
            sys.exit(1)
        print(f"api_key : {_key}")
        print(f"prefix  : {_key[:8]}")
        print(f"label   : {label}")
        print(f"scope   : {scope}")
        print("This is the only time the key is shown. It is stored hashed.")
        sys.exit(0)

    # Read-only reconcile without starting the service. This is what to run
    # when the boot check has refused to start: it prints the same report the
    # service would have logged, and appends the same record, without needing
    # the port or the service to be up.
    #
    # There is no --repair, on purpose. A mismatch does not say which side is
    # wrong. Deciding that is a person's job, and the repair is a SQL statement
    # written after that decision — see DEPLOY.md.
    if "--reconcile" in sys.argv:
        _result = hexis_reconcile.run_and_record(DB_PATH, source="cli")
        print(hexis_reconcile.format_report(_result))
        print(f"record  : {hexis_reconcile.default_record_path(DB_PATH)}")
        sys.exit(0 if _result.ok else 1)

    import uvicorn
    uvicorn.run(
        app,
        # LOOPBACK ONLY since 2026-08-14. Was "0.0.0.0", for the same reason
        # and with the same consequence as the bridge on 8400: nginx already
        # proxies here from api.hexisfoundation.org
        # (sites-available/api.hexisfoundation.org, proxy_pass
        # http://127.0.0.1:8401), so nothing public changes, and what goes away
        # is a second path straight to the app that skipped nginx.
        #
        # ufw restricted 8401 to Cloudflare's origin ranges, which is not a
        # restriction — those are the egress addresses of every Cloudflare
        # Worker. Combined with access_log=False below, a request arriving that
        # way was unfiltered AND unrecorded. That combination is what made the
        # August /stake/credit review unresolvable on the bridge; this service
        # had it too, and was checked only because the bridge was.
        host="127.0.0.1",
        port=8401,
        workers=1,
        loop="asyncio",
        log_level="info",
        # As on the bridge: this process keeps no record of any request it
        # serves. nginx's access.log is the only one, and it is only complete
        # because host is loopback above.
        access_log=False,
    )
