"""
HEXIS x NEWFLOW Bridge v0.8.0
==============================

Merged version: v0.6.1 production backbone + v0.1 NEWFLOW economy layer.

WHAT THIS UPGRADES vs v0.6.1
----------------------------
Restored from v0.1 (regression fix):
  - BridgeState with newflow_core integration (Wallet, ChainState, Ledger, Transfer)
  - ECU balance tracking via STATE.chain.get_balance()
  - Faucet auto-funds new workers and consumers
  - Adversarial frame in HEXIS event:
      asset_could_have_taken, asset_actually_returned, gain_if_betrayed
  - Genesis block init log: "NEWFLOW + HEXIS bridge online"
  - SCS (SunkCostMintEngine) ECU minting from compute energy
  - POST /job/{job_id}/complete  (HEXIS auto-mining on honest delivery)
  - GET  /status                 (NEWFLOW chain + HEXIS index + SCS combined)
  - GET  /events                 (recent log entries for UI)
  - UI:  network status panel + live events log

WHAT THIS KEEPS from v0.6.1
---------------------------
  - FastAPI async, uvicorn server
  - SQLite WAL mode (bridge.db for persistent jobs/workers)
  - Async write queue (batched flushing)
  - LRU + TTL cache for /trust (100K entries, 30s TTL)
  - Rate limiting: 10/sec/IP, 300/min/IP, 60/min/actor
  - Capacity guard: max 5000 concurrent, 503 + Retry-After
  - Sensitivity Tiers 1-4 (BO multiplier 1x / 5x / 20x / 100x)
  - Geographic C multiplier per country
  - GET /health, GET /metrics, GET /docs
  - "Say No" honest backpressure (429 / 503 with Retry-After)

PENDING (deferred to v0.7)
--------------------------
  - Bilateral stake mechanism (D3 Counterparty Integrity)
  - Consumer-side staking + symmetric slashing
  - Pair-frequency cap (anti-collusion)

PORT: 8400, bound to 127.0.0.1 — reachable only through nginx. See BIND_HOST.
"""

import asyncio
import dataclasses
import json
import time
import math
import hashlib
import os
import re
import sys
import sqlite3
import uuid
import logging
from collections import deque, defaultdict, OrderedDict
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple, Any
from contextlib import asynccontextmanager

# Ensure local module path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fastapi import FastAPI, Request, HTTPException, Depends
from fastapi.responses import JSONResponse, HTMLResponse, PlainTextResponse, Response
from pydantic import BaseModel, Field
import uvicorn

# -- v0.1 NEWFLOW + HEXIS core modules (must exist in /opt/hexis_newflow/) --
import hexis_cid
from hexis_mining import HexisMiner, BehaviorEvent
import hexis_mining as mining      # for C_DECAY_HALFLIFE_MINTS at the mint site
from hexis_ledger import (
    HexisLedger, LocalIndex, LedgerRecord, IPFSClient, PinQueue, PinService,
    pinning_enabled,
)
from newflow_core import (
    Wallet, ChainState, Ledger, Transfer, JobCommitment,
    ProofWithPayment, generate_mock_proof, build_block,
    OptimisticPool, ValidatorVerifier, StakedProof,
    ProofStatus, ValidationError, sha256, FAUCET_AMOUNT,
)
from scs_engine import SunkCostMintEngine, ENERGY_UNIT_GENESIS, JOULES_PER_KWH
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from hexis_audit import AuditLogger, get_audit_router, VALID_ACTIONS
from hexis_stake import StakeManager, get_stake_router, get_dispute_router
import hexis_stake            # the module itself: the boot audit reads its source
from whitepaper_figures import audit_whitepaper_figures, figure_summary
import hexis_ledger_entries as ledger_entries
from hexis_severity import SeverityEngine, get_severity_router
from hexis_geo import GeoEconomics, BenchmarkGate, get_geo_router
from hexis_sampling import SamplingEngine, get_sampling_router, workload_result


# ===========================================================
# CONSTANTS
# ===========================================================

SERVER_VERSION = "0.8.0"

# The genesis allocation, named rather than written as literals inside
# `_init_genesis`. A figure buried in a method body cannot be checked against
# the whitepaper that prints it — `whitepaper_figures.py` reads these, and the
# 39,000,000 here is §4's supply table. It is not the mint cap: the engine
# enforces `scs_engine.MAX_SUPPLY = 950_000`, the two have never been
# reconciled, and both are printed in §4 with that disagreement stated.
GENESIS_TOTAL_ECU     = 39_000_000
GENESIS_VALIDATOR_ECU =    100_000
GENESIS_FAUCET_ECU    =     50_000

# The VPS path stays the default, so deployed behaviour is unchanged. The env
# var exists so the schema and the signature tests can run on a throwaway
# database on a laptop — /opt/hexis_newflow does not exist there, and a test
# that cannot run locally is a test nobody runs.
DB_PATH = os.environ.get("HEXIS_DB_PATH", "/opt/hexis_newflow/bridge.db")
TRUST_API_URL = "http://localhost:8401"

# LOOPBACK ONLY since 2026-08-13. Was "0.0.0.0".
#
# nginx proxies to http://127.0.0.1:8400 (sites-available/bridge.hexisfoundation.org),
# so no public path changes. What changes is the path that was never meant to
# exist: with the socket bound to every interface, a request could reach the app
# directly on :8400 and skip nginx entirely. ufw restricts that port to
# Cloudflare's published origin ranges, which sounds like a control and is not —
# those ranges are the egress addresses of every Cloudflare Worker, so "from
# Cloudflare" is a set anyone can join for free.
#
# The reason it mattered is that nginx is the only request log this service has:
# uvicorn runs with access_log=False (see uvicorn.run at the bottom of this
# file). A request arriving on :8400 directly was therefore not merely
# unfiltered, it was unrecorded — during the August forensic review of
# /stake/credit there was no way to rule that path out, and no way to ever go
# back and check. Binding to loopback is what makes the nginx log complete for
# anything arriving over the network.
#
# Still bypasses nginx by design, and this is the honest limit of that claim:
# any process on the host, and anyone with an SSH tunnel (`seal_remote.py` uses
# one — see DEPLOY.md), talks to 127.0.0.1:8400 directly. Loopback closes the
# remote hole, not the local one.
BIND_HOST = "127.0.0.1"
BIND_PORT = 8400

# Rate limits
RATE_LIMIT_PER_IP_PER_SEC = 10
RATE_LIMIT_PER_IP_PER_MIN = 300
RATE_LIMIT_PER_ACTOR_PER_MIN = 60

# Cache
CACHE_MAX_ENTRIES = 100_000
CACHE_TTL_SEC = 30.0

# Capacity guard
MAX_CONCURRENT_REQUESTS = 5000

# Write queue
MAX_QUEUE_SIZE = 50_000
BATCH_SIZE = 500
BATCH_INTERVAL_MS = 100

# Hardware tiers (label, watts, joules-per-day-est)
HARDWARE_TIERS = {
    1: ("LOW (Laptop/Desktop CPU)", 65, 234000),  # phone bi loai (2026-07-24)
    2: ("MEDIUM (RTX 3080)",  320,  1152000),
    3: ("HIGH (A100)",        400,  1440000),
    4: ("EXTREME (H100)",     700,  2520000),
}

# Geographic C multiplier (country -> integrity-cost factor)
GEO_MULTIPLIER = {
    "US": 0.85, "GB": 0.90, "DE": 0.92, "JP": 0.95,
    "KR": 1.00, "CN": 1.20, "BR": 1.35, "MX": 1.40,
    "IN": 1.55, "VN": 1.69, "ID": 1.62, "PH": 1.58,
    "NG": 1.85, "PK": 1.78, "BD": 1.80, "ET": 1.95,
}
DEFAULT_GEO_MULT = 1.00

# Sensitivity Tiers (BO multiplier for HEXIS mining)
SENSITIVITY_TIERS = {
    1: ("Low (commodity compute)",  1.0),
    2: ("Standard (medium-value)",  5.0),
    3: ("High (financial/health)", 20.0),
    4: ("Extreme (life-critical)", 100.0),
}

# Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger("bridge")


# ===========================================================
# RATE LIMITER
# ===========================================================

class RateLimiter:
    """Sliding window rate limiter keyed by string."""

    def __init__(self):
        self.buckets: Dict[str, deque] = defaultdict(deque)
        self.lock = asyncio.Lock()

    async def check(self, key: str, limit: int, window_sec: float) -> Tuple[bool, int]:
        now = time.time()
        async with self.lock:
            bucket = self.buckets[key]
            while bucket and bucket[0] < now - window_sec:
                bucket.popleft()
            if len(bucket) >= limit:
                retry = int(bucket[0] + window_sec - now) + 1
                return False, max(1, retry)
            bucket.append(now)
            return True, 0


# ===========================================================
# LRU + TTL CACHE
# ===========================================================

class LRUCache:
    def __init__(self, max_size: int = CACHE_MAX_ENTRIES, ttl: float = CACHE_TTL_SEC):
        self.cache: "OrderedDict[str, Tuple[Any, float]]" = OrderedDict()
        self.ttl = ttl
        self.max_size = max_size
        self.lock = asyncio.Lock()
        self.hits = 0
        self.misses = 0

    async def get(self, key: str):
        async with self.lock:
            if key not in self.cache:
                self.misses += 1
                return None
            val, expires = self.cache[key]
            if time.time() > expires:
                del self.cache[key]
                self.misses += 1
                return None
            self.cache.move_to_end(key)
            self.hits += 1
            return val

    async def set(self, key: str, value):
        async with self.lock:
            self.cache[key] = (value, time.time() + self.ttl)
            self.cache.move_to_end(key)
            while len(self.cache) > self.max_size:
                self.cache.popitem(last=False)

    async def invalidate(self, key: str):
        async with self.lock:
            self.cache.pop(key, None)


# ===========================================================
# CAPACITY GUARD ("Say No" architecture)
# ===========================================================

class CapacityGuard:
    def __init__(self, max_concurrent: int = MAX_CONCURRENT_REQUESTS):
        self.max = max_concurrent
        self.current = 0
        self.lock = asyncio.Lock()

    async def acquire(self) -> bool:
        async with self.lock:
            if self.current >= self.max:
                return False
            self.current += 1
            return True

    async def release(self):
        async with self.lock:
            self.current = max(0, self.current - 1)


# ===========================================================
# ASYNC WRITE QUEUE (batched SQLite writes)
# ===========================================================

class WriteQueue:
    def __init__(self, db_path: str, max_size: int = MAX_QUEUE_SIZE):
        self.db_path = db_path
        self.queue: deque = deque()
        self.lock = asyncio.Lock()
        self.max_size = max_size
        self.flushed_total = 0

    async def submit(self, sql: str, params: tuple) -> bool:
        async with self.lock:
            if len(self.queue) >= self.max_size:
                return False
            self.queue.append((sql, params))
            return True

    async def flush(self):
        batch: List[Tuple[str, tuple]] = []
        async with self.lock:
            if not self.queue:
                return
            n = min(BATCH_SIZE, len(self.queue))
            for _ in range(n):
                batch.append(self.queue.popleft())
        if not batch:
            return

        def _write():
            conn = sqlite3.connect(self.db_path, timeout=30)
            try:
                conn.execute("PRAGMA journal_mode=WAL;")
                conn.execute("PRAGMA synchronous=NORMAL;")
                for sql, params in batch:
                    conn.execute(sql, params)
                conn.commit()
            finally:
                conn.close()

        await asyncio.get_event_loop().run_in_executor(None, _write)
        self.flushed_total += len(batch)

    async def run_forever(self):
        while True:
            await asyncio.sleep(BATCH_INTERVAL_MS / 1000.0)
            try:
                await self.flush()
            except Exception as e:
                log.warning("WriteQueue flush error: %s", e)


# ===========================================================
# DB INIT
# ===========================================================

def init_db():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    try:
        conn.executescript("""
            PRAGMA journal_mode=WAL;
            PRAGMA synchronous=NORMAL;

            CREATE TABLE IF NOT EXISTS workers (
                address TEXT PRIMARY KEY,
                country TEXT,
                hardware_tier INTEGER,
                registered_at TEXT,
                jobs_completed INTEGER DEFAULT 0,
                pubkey TEXT,
                benchmark_passed INTEGER DEFAULT 0
            );

            -- CONSUMERS (added 2026-08-15, phase 3e)
            --
            -- The other half of the identity layer. Workers got keys on
            -- 2026-08-11; consumers did not, which is why /job/request and
            -- /stake/lock were signed by the worker named in a request the
            -- consumer initiated. `pubkey` is NOT NULL here from the start:
            -- the workers table allows nulls because it predates keys and
            -- seven legacy actors live in it, and there is no equivalent
            -- history to carry over.
            CREATE TABLE IF NOT EXISTS consumers (
                address TEXT PRIMARY KEY,
                country TEXT,
                registered_at TEXT,
                pubkey TEXT NOT NULL,
                jobs_requested INTEGER DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS jobs (
                job_id TEXT PRIMARY KEY,
                worker_address TEXT,
                consumer_address TEXT,
                task_type TEXT,
                compute_units INTEGER,
                fee_ecu REAL,
                hexis_at_start REAL,
                trust_grade TEXT,
                status TEXT,
                created_at TEXT,
                completed_at TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_jobs_worker ON jobs(worker_address);

            -- ============================================================
            -- IDENTITY LAYER (added 2026-08-11)
            --
            -- Both tables are new, so an existing bridge.db picks them up on
            -- the next start. The IF NOT EXISTS trap documented in
            -- MIGRATIONS.md applies to missing COLUMNS in an existing table,
            -- not to whole tables that are absent. No manual migration.
            -- ============================================================

            -- Display names. Nothing more.
            --
            -- DO NOT add UNIQUE to handle. DO NOT index handle. DO NOT join
            -- on handle. DO NOT reference this table from a foreign key.
            -- Reads go one direction only: actor_id -> handle, resolved at
            -- the presentation layer just before a response goes out.
            --
            -- The test: drop this table and the bridge must still run,
            -- losing nothing but display names. See README rule 6.
            CREATE TABLE IF NOT EXISTS actor_handles (
                actor_id   TEXT PRIMARY KEY,   -- base58 address; the reference
                handle     TEXT,               -- display only, not unique
                updated_at TEXT
            );

            -- Every signed write, kept as evidence.
            --
            -- Three jobs, one table:
            --   1. Replay rejection. nonce is the PRIMARY KEY, so a repeat
            --      nonce fails the INSERT. Check and record are one atomic
            --      statement — a separate "have I seen this?" lookup would
            --      race two concurrent replays through.
            --   2. Proof, after the fact, that a write really was signed.
            --      A security property nobody can check later is a security
            --      property nobody has.
            --   3. Attribution for the audit chain: anyone can confirm that
            --      audit record X came from a request signed by key Y.
            --
            -- Written by the signature dependency in 3c; created here so the
            -- schema lands in one step.
            CREATE TABLE IF NOT EXISTS write_signatures (
                nonce       TEXT PRIMARY KEY,  -- from the signature params
                keyid       TEXT NOT NULL,     -- base58 address that signed
                method      TEXT NOT NULL,
                path        TEXT NOT NULL,
                created_at  INTEGER NOT NULL,  -- unix s, the signature's own
                received_at INTEGER NOT NULL,  -- unix s, this server's clock
                signature   TEXT NOT NULL      -- the Signature header value
            );

            -- Pruning walks received_at; attribution queries walk keyid.
            CREATE INDEX IF NOT EXISTS idx_write_sig_received
                ON write_signatures(received_at);
            CREATE INDEX IF NOT EXISTS idx_write_sig_keyid
                ON write_signatures(keyid);
        """)
        conn.commit()
    finally:
        conn.close()


# ===========================================================
# SIGNATURE POLICY
#
# The constants and the verification itself live in hexis_identity.py. They
# are re-exported here only so that existing references keep resolving; the
# single definition is over there, next to the code that enforces it.
# ===========================================================

from hexis_identity import (            # noqa: E402
    claimed_keyid,
    SIG_MAX_AGE_S,
    SIG_CLOCK_SKEW_S,
    SIG_NONCE_TTL_S,
    SIG_RETENTION_S,
    SIG_TAG,
    SIG_REQUIRED_COMPONENTS,
    address_for_pubkey,
    signing_url,
    verify_signed_write,
    prune_write_signatures as _prune_write_signatures,
)


def prune_write_signatures(retain_seconds: int = SIG_RETENTION_S) -> int:
    """Prune signature evidence past retention, on this bridge's database."""
    return _prune_write_signatures(DB_PATH, retain_seconds)


# Pruning runs from the write path rather than a timer: no background task to
# forget to start, and nothing to prune when nothing is being written.
PRUNE_EVERY_N_WRITES = 1000
_writes_since_prune = 0


def _maybe_prune():
    global _writes_since_prune
    _writes_since_prune += 1
    if _writes_since_prune % PRUNE_EVERY_N_WRITES:
        return
    try:
        removed = prune_write_signatures()
        if removed:
            STATE.log(f"Pruned {removed} signature rows past retention")
    except Exception as e:
        # Never fail a legitimate write because housekeeping failed.
        STATE.log(f"Signature prune failed: {e}", level="warn")


# ===========================================================
# WRITE AUTHENTICATION (3c, 2026-08-12)
#
# Every write endpoint carries a signature guard. Which actor a signature must
# belong to differs per endpoint — a path parameter here, a body field there —
# so each route says so explicitly rather than the guard guessing.
# ===========================================================

def _resolve_actor_key(actor_id: str) -> str:
    """
    The public key an actor must sign with, or a refusal.

    The 403 is where the seven pre-identity testnet actors land. They hold no
    key, so they cannot sign, so they cannot write. Their history stays fully
    readable and nothing is migrated: they are being wiped before Block 0, and
    building them a way to acquire a key now would be building the migration
    path we decided not to build — a path that, once it exists, is exactly
    what an attacker would look for.
    """
    # Both registries, since 3e. Consumers are looked up the same way workers
    # are, and `audit_identity_registries()` refuses to boot if one id or one
    # key appears in both — so this lookup order can never be what decides
    # which key an actor signs with.
    actor = STATE.workers.get(actor_id) or STATE.consumers.get(actor_id)
    if actor is None:
        raise HTTPException(
            status_code=404,
            detail={"error": "actor_not_registered", "actor_id": actor_id},
        )
    pubkey = actor.get("pubkey")
    if not pubkey:
        raise HTTPException(
            status_code=403,
            detail={
                "error": "legacy_actor_read_only",
                "actor_id": actor_id,
                "reason": "actor registered before keys were mandatory and "
                          "holds none; reads still work, writes do not",
            },
        )
    return pubkey


# Deliberately no "any registered actor" binding. /stake/expire was the one
# route that wanted one, and the fact that it wanted one was the finding: a
# write no actor owns is a write no signature can authorise. It is gated
# internal-only now. If another such route appears, gate it too rather than
# reintroduce a binding whose meaning is "somebody was logged in".


def _worker_for_terms(job_id: str):
    """
    The worker named in the proposed terms for this job.

    Read from stake_terms, not from the request body. `require_signature`
    checks the signature against whatever actor this returns, so letting the
    caller supply it would let anyone nominate a key they hold and accept on
    another worker's behalf — the same shape as the /job/complete guard, which
    takes the worker from the job for the same reason.
    """
    if not job_id:
        return None
    try:
        with stake._conn() as c:
            row = c.execute("SELECT worker_id FROM stake_terms WHERE job_id=?",
                            (job_id,)).fetchone()
        return row[0] if row else None
    except Exception:
        return None


def _worker_for_job_stake(job_id: str):
    """The worker whose stake a job-scoped write acts on."""
    job = STATE.jobs.get(job_id) or {}
    if job.get("worker_address"):
        return job["worker_address"]
    # A lock can exist for a job this process never saw in memory.
    try:
        rows = (stake.status(job_id) or {}).get("worker_stake") or []
        return rows[0].get("worker_id") if rows else None
    except Exception:
        return None


def require_signature(actor_from):
    """
    Build a dependency that authenticates one write endpoint.

    actor_from(request, payload) -> the actor this request acts for. The
    signature must be made by THAT actor's key: a valid signature by actor A
    on a request that modifies actor B is a perfectly valid signature and an
    unauthorised write, and separating the two is the entire point of the
    check.
    """

    async def _guard(request: Request):
        body = await request.body()
        try:
            payload = json.loads(body) if body else {}
        except Exception:
            payload = {}
        if not isinstance(payload, dict):
            payload = {}

        actor_id = actor_from(request, payload)
        if not actor_id:
            raise HTTPException(
                status_code=400,
                detail={"error": "actor_not_identified",
                        "reason": "cannot tell which actor this write acts for"},
            )

        result = verify_signed_write(
            method       = request.method,
            url          = signing_url(request),
            headers      = request.headers,
            body         = body,
            pubkey_hex   = _resolve_actor_key(actor_id),
            expect_keyid = actor_id,
            db_path      = DB_PATH,
            path         = request.scope.get("path", ""),
        )
        _maybe_prune()
        return result

    # Read by the startup audit below to tell a guarded route from an
    # unguarded one.
    _guard._hexis_signature_guard = True
    return _guard


# Write routes that legitimately carry no guard. Every entry needs a reason,
# because this list is the only way a write escapes authentication.
UNGUARDED_WRITE_ROUTES = {
    # Verifies its own signature inline: the key is in the request body,
    # since the actor does not exist yet and there is nothing to look up.
    "/worker/register",
    # Same, for the consumer side (3e, 2026-08-15). Same inline verification,
    # same reason, same proof-of-possession.
    "/consumer/register",
    # Returns 410 Gone unconditionally and touches no state.
    "/worker/{address}/bind_pubkey",
    # Same: 410 Gone unconditionally, gated internal-only since stake v3.
    "/stake/release",
    # Same: 410 Gone unconditionally, gated internal-only 2026-08-12. It
    # created escrow balance with nothing debited anywhere.
    "/stake/credit",
    # Same: 410 Gone unconditionally, gated internal-only 2026-08-12. An
    # unscoped sweep over every stale lock that no caller can authorise.
    "/stake/expire",
    # Already cryptographically protected, by the Foundation key held off
    # this machine. Out of scope by instruction, and correct as it stands.
    "/audit/seal",
}


def audit_identity_registries() -> list:
    """
    Refuse to start if one actor could resolve to two different keys.

    Added with 3e. `_resolve_actor_key` now consults `workers` and then
    `consumers`. If an address existed in both, that lookup ORDER would decide
    which key a signature is checked against — a control whose behaviour
    depends on the order two dictionaries happen to be written in is not a
    control. The same applies to one public key registered under two
    addresses: the holder of that key could sign for either.

    Neither is reachable through the register endpoints, which reject a
    duplicate address with 409. This exists because "not reachable through the
    endpoints" is a statement about today's endpoints, and the rows are what
    matter.
    """
    problems = []

    both = sorted(set(STATE.workers) & set(STATE.consumers))
    for addr in both:
        w = (STATE.workers[addr] or {}).get("pubkey")
        c = (STATE.consumers[addr] or {}).get("pubkey")
        problems.append(
            f"{addr} is registered as both a worker and a consumer "
            f"(worker key {str(w)[:16]}…, consumer key {str(c)[:16]}…) — "
            f"which key it signs with would depend on lookup order"
        )

    by_key: Dict[str, List[str]] = {}
    for registry, label in ((STATE.workers, "worker"), (STATE.consumers, "consumer")):
        for addr, rec in registry.items():
            pubkey = (rec or {}).get("pubkey")
            if pubkey:
                by_key.setdefault(pubkey, []).append(f"{label}:{addr}")
    for pubkey, holders in sorted(by_key.items()):
        if len(holders) > 1:
            problems.append(
                f"public key {pubkey[:16]}… is registered to {len(holders)} "
                f"actors ({', '.join(holders)}) — one key holder could sign "
                f"for any of them"
            )

    return problems


def audit_write_route_protection(app_) -> list:
    """
    Refuse to start if any write route is unauthenticated.

    Decorating each route is visible but forgettable; a new POST added in six
    months inherits nothing. This turns that omission into a failure to boot
    instead of a silent hole, which is the difference between a control that
    exists and a control that holds.
    """
    from fastapi.routing import APIRoute

    write_methods = {"POST", "PUT", "PATCH", "DELETE"}
    unguarded = []
    for route in app_.routes:
        if not isinstance(route, APIRoute):
            continue
        if not (route.methods & write_methods):
            continue
        if route.path in UNGUARDED_WRITE_ROUTES:
            continue
        guarded = any(
            getattr(d.dependency, "_hexis_signature_guard", False)
            for d in route.dependencies
        )
        if not guarded:
            unguarded.append(f"{sorted(route.methods)} {route.path}")
    return unguarded


# ===========================================================
# AUDIT ACTION TYPES (2026-08-13)
#
# `AuditLogger._insert` refuses any action_type outside VALID_ACTIONS, and
# every emitter — hexis_stake, hexis_severity, hexis_sampling — wraps its audit
# call in `except Exception: pass`, deliberately, so that a failed audit write
# cannot break an economic transaction. The two together mean an unlisted
# action type is discarded in total silence: no error, no log line, and no gap
# in the chain to find later, because a rejected event is never assigned a
# sequence.
#
# That is not a hypothetical. It happened twice. Six types on 2026-08-12, found
# by reading the code; six more on 2026-08-13 — the whole sampling module —
# found only because this scan was written. Fixing the twelve fixes nothing:
# the thirteenth arrives the next time someone adds an `_audit(...)` call and
# does not think to edit a set in another file, which is the ordinary case, not
# the careless one.
#
# So the allowlist is checked against the source rather than against memory.
# Same closed-by-default move as the route audit above, and the same argument:
# a control that depends on remembering is not a control.
# ===========================================================

# Where an action type can be named, and which positional argument names it.
# None means the parameter is keyword-only.
#
# `_insert` is on this list even though it is private, because it is not only
# reached through the public wrappers: `genesis` and `daily_seal` are written
# by calling it directly, and a scan that trusted the public API would have
# declared both of them unemitted while the chain visibly contains them.
_AUDIT_EMIT_CALLS = {
    "log_action":        1,      # log_action(actor_id, action_type, payload, ...)
    "log_action_within": 2,      # log_action_within(conn, actor_id, action_type, ...)
    "_audit":            0,      # _audit(action, actor_id, counterparty, data)
    "_insert":           None,   # _insert(conn, *, action_type=..., ...)
}
_AUDIT_TYPE_KEYWORDS = ("action_type", "action")

# Call sites that pass an action type through instead of naming one, so the
# scan cannot read a literal there. Each is a forwarder whose value originates
# at a site the scan *does* read, listed here with which one. Anything
# unresolvable that is not declared here fails the boot — an undeclared
# forwarder is indistinguishable from a type nobody checked.
#
# Keyed by enclosing function, not file and line: line numbers rot on every
# edit above them, and an exemption that rots silently is the thing being
# fixed.
AUDIT_ACTION_FORWARDERS = {
    # The audit_fn handed to StakeManager, SeverityEngine and SamplingEngine.
    # Its `action` argument arrives from the `_audit("literal", ...)` calls in
    # those modules, all of which are scanned directly.
    "_stake_audit_fn",
    # The audit-wire wrapper: `audit.log_action(**spec)`, where spec is built
    # by _audit_derive_action from dict literals, also scanned directly.
    "_wrapped_log",
    # AuditLogger's own two public wrappers. Both end in
    # `self._insert(..., action_type=action_type, ...)`, passing on whatever
    # their caller named — and their callers are what the rest of this scan is
    # reading.
    "log_action",
    "log_action_within",
}


def _is_main_guard(node) -> bool:
    """True for `if __name__ == "__main__":` — self-test code, never emitted."""
    import ast
    if not isinstance(node, ast.If) or not isinstance(node.test, ast.Compare):
        return False
    left = node.test.left
    return (isinstance(left, ast.Name) and left.id == "__name__"
            and any(isinstance(c, ast.Constant) and c.value == "__main__"
                    for c in node.test.comparators))


def _walk_with_function(node, fn_name):
    """Yield (node, enclosing function name) for the whole tree below `node`."""
    import ast
    for child in ast.iter_child_nodes(node):
        if _is_main_guard(child):
            continue
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
            yield child, fn_name
            yield from _walk_with_function(child, child.name)
            continue
        yield child, fn_name
        yield from _walk_with_function(child, fn_name)


def audit_action_types() -> tuple:
    """
    Refuse to start if the code can emit an action type the allowlist rejects.

    Reads the source of every module loaded from this directory — not a
    hardcoded file list, which would go stale the same way the allowlist did —
    and collects action types from the two shapes they appear in: an argument
    to an audit call, and an `"action_type": "..."` entry in a dict literal
    (which is how _audit_derive_action builds its specs).

    Returns (problems, summary). A non-empty `problems` means do not serve.
    """
    import ast

    here = os.path.dirname(os.path.abspath(__file__))
    sources = sorted({
        os.path.abspath(m.__file__)
        for m in list(sys.modules.values())
        if getattr(m, "__file__", None)
        and os.path.dirname(os.path.abspath(m.__file__)) == here
    })

    emitted = {}        # action type -> ["file:line", ...]
    unresolved = []     # ("file:line", function name)

    for path in sources:
        try:
            tree = ast.parse(open(path, encoding="utf-8").read(), path)
        except (OSError, SyntaxError):
            # A module whose source cannot be read cannot be checked, and a
            # check that skips what it cannot read is not a check.
            return ([f"cannot read source of {os.path.basename(path)} — "
                     f"the action-type scan cannot cover it"], "")
        name = os.path.basename(path)

        for node, fn in _walk_with_function(tree, "<module>"):
            if isinstance(node, ast.Dict):
                for k, v in zip(node.keys, node.values):
                    if (isinstance(k, ast.Constant) and k.value == "action_type"
                            and isinstance(v, ast.Constant)
                            and isinstance(v.value, str)):
                        emitted.setdefault(v.value, []).append(f"{name}:{k.lineno}")
                continue

            if not isinstance(node, ast.Call):
                continue
            func = node.func
            called = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
            if called not in _AUDIT_EMIT_CALLS:
                continue

            arg = None
            for kw in node.keywords:
                if kw.arg in _AUDIT_TYPE_KEYWORDS:
                    arg = kw.value
            if arg is None:
                pos = _AUDIT_EMIT_CALLS[called]
                if pos is not None and len(node.args) > pos:
                    arg = node.args[pos]

            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                emitted.setdefault(arg.value, []).append(f"{name}:{node.lineno}")
            else:
                unresolved.append((f"{name}:{node.lineno}", fn))

    problems = []
    for action in sorted(set(emitted) - VALID_ACTIONS):
        problems.append(
            f"'{action}' is emitted at {', '.join(emitted[action])} but is not "
            f"in VALID_ACTIONS — every one of those calls writes nothing"
        )
    for where, fn in unresolved:
        if fn in AUDIT_ACTION_FORWARDERS:
            continue
        problems.append(
            f"{where} in {fn}() names its action type dynamically, so it cannot "
            f"be checked — declare it in AUDIT_ACTION_FORWARDERS with the "
            f"literal site it forwards from, or pass a literal"
        )

    # Allowed but never emitted. Not a failure: an action type may be written
    # by hand, by a migration, or by code deleted since. Reported because an
    # allowlist nobody prunes is how the first six went unnoticed.
    unused = sorted(VALID_ACTIONS - set(emitted))
    summary = (f"{len(emitted)} action types emitted, all allowed"
               + (f"; {len(unused)} allowed but not emitted anywhere: "
                  f"{', '.join(unused)}" if unused else ""))
    return problems, summary


# Every stake operation, and what it is allowed to do to the total. A burn is
# the only legitimate way for ECU to leave the system, and it has to be
# declared here before the check will accept it.
_CONSERVATION_TOLERANCE = 1e-9


def audit_ecu_conservation() -> list:
    """
    Refuse to start if a money-moving operation does not conserve ECU.

    The write-route audit asks whether a caller was allowed to act. This asks
    the other question, which no amount of authentication answers: given that
    the call was allowed, did the books balance afterwards. Harmony lost ~4bn
    tokens on 2026-08-12 to an unauthorised mint, and the part worth copying
    is not the exploit but the aftermath — their own totalSupply endpoint kept
    reporting the old figure, and an outside analyst found the inflation
    before their monitoring did. A supply number that cannot disagree with the
    rows underneath it is a number that cannot report a breach.

    So this runs the real operations against a throwaway database and compares
    `stake.ecu_total()` before and against after. Conserving operations must
    leave it untouched; `dispute_slash` must reduce it by exactly the amount
    it wrote to `slash_log`, and by not one unit more.

    What this does NOT do, and cannot yet: verify the live database. A
    snapshot of escrow balances has nothing to be reconciled against, because
    escrow has no entry ledger — only the audit chain, which records intent
    rather than double-entry movement. Building that ledger is the fix for the
    finding recorded in CORRECTIONS.md; until it exists, the invariant is
    enforced on the code paths rather than on the data.
    """
    import os
    import shutil
    import tempfile

    from hexis_stake import StakeManager, SIDE_CONSUMER, SIDE_WORKER

    C, W = "conservation_consumer", "conservation_worker"
    SEED, C_STAKE, W_STAKE, FEE = 1000.0, 100.0, 50.0, 25.0

    violations = []
    tmpdir = tempfile.mkdtemp(prefix="hexis_ecu_conservation_")

    def scratch(name):
        # credit() is the money printer this check exists because of. Used
        # here on purpose: a throwaway database needs a starting supply from
        # somewhere, and nothing outside this function can reach it.
        s = StakeManager(db_path=os.path.join(tmpdir, f"{name}.db"))
        s.credit(C, SEED)
        s.credit(W, SEED)
        return s

    def age_locks(s, seconds):
        """Make the locks old enough for abort/expire without sleeping."""
        with s._conn() as c:
            for tbl in ("consumer_stake", "worker_stake"):
                c.execute(f"UPDATE {tbl} SET locked_at = locked_at - ?", (seconds,))

    def run(name, body, expected_burn=0.0):
        s = scratch(name)
        # Measured before the lock, so the lock's own debits are inside the
        # window: an operation that loses ECU on the way in is as much a
        # violation as one that creates it on the way out.
        before = s.ecu_total()
        try:
            # P1 bilateral stake (2026-08-23). lock() is frozen until both
            # parties have agreed the terms, so the self-test walks the real
            # flow instead of the old shortcut. It is measured inside the
            # `before` window deliberately: propose and accept must move no
            # ECU, and if either ever started to, this check would catch it
            # rather than quietly absorb it.
            _terms = s.propose_terms("conservation_job", C, W,
                                     C_STAKE, W_STAKE, FEE, 1)
            s.accept_terms("conservation_job", _terms["terms_hash"])
            s.lock("conservation_job", C, W, C_STAKE, W_STAKE, FEE, 1)
            body(s)
        except Exception as e:
            violations.append(f"{name}: operation raised {type(e).__name__}: {e}")
            return
        after = s.ecu_total()
        drift = (before - after) - expected_burn
        if abs(drift) > _CONSERVATION_TOLERANCE:
            direction = "created" if drift < 0 else "destroyed"
            violations.append(
                f"{name}: {abs(drift):.8f} ECU {direction} — total was {before:.8f}, "
                f"is {after:.8f}, burn declared {expected_burn:.8f}"
            )

    try:
        run("settle_complete", lambda s: s.settle_complete("conservation_job"))

        def _abort(s):
            age_locks(s, 120)
            s.abort_unstarted("conservation_job")
        run("abort_unstarted", _abort)

        def _expire(s):
            age_locks(s, 7200)
            result = s.expire_stale(3600)
            if result["expired_count"] != 1:
                violations.append(
                    f"expire_stale: swept {result['expired_count']} locks, expected 1 "
                    "— conservation was measured over the wrong operation"
                )
        run("expire_stale", _expire)

        # A slash is the one operation that may reduce the total, and only by
        # the stake it says it took. Checked in both directions because the
        # fee follows the honest party and the two branches are separate code.
        run("dispute_slash[consumer at fault]",
            lambda s: s.dispute_slash("conservation_job", SIDE_CONSUMER, W, "conservation check"),
            expected_burn=C_STAKE)
        run("dispute_slash[worker at fault]",
            lambda s: s.dispute_slash("conservation_job", SIDE_WORKER, C, "conservation check"),
            expected_burn=W_STAKE)

        # transfer() moves escrow between actors and must be flat. Run outside
        # run() because it needs no lock.
        s = scratch("transfer")
        before = s.ecu_total()
        s.transfer(C, W, 37.5)
        after = s.ecu_total()
        if abs(before - after) > _CONSERVATION_TOLERANCE:
            violations.append(
                f"transfer: total moved {before - after:.8f} ECU — a transfer must be flat"
            )
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

    return violations


# ===========================================================
# BRIDGE STATE (from v0.1, kept intact)
# ===========================================================

# Set once, when this process starts. Every ephemeral figure below counts from
# this instant and from nothing earlier, so it is served alongside them rather
# than left for a reader to work out.
PROCESS_STARTED_AT = datetime.now(timezone.utc).isoformat()


# Which figures each public read serves from memory that is wiped at restart,
# which come from something durable, and which are computed from those at
# request time. Added 2026-08-14; **presentation only, no number changed.**
#
# Held as data rather than written into prose so the tests can hold it against
# the actual response: every key classified exactly once, nothing served that
# is unclassified, nothing classified that is not served. A marker that has
# drifted away from what is served is worse than no marker, and this file
# exists partly because of a comment that had drifted.
DURABILITY = {
    "/status": {
        "ephemeral": [
            "newflow.chain_height",      # ChainState, re-genesised each start
            "newflow.total_jobs",        # STATE.jobs, never reloaded
            "newflow.jobs_completed",    # counted from STATE.jobs
            "scs.ecu_minted_phase0",     # SunkCostMintEngine counter
            "scs.halving_phase",         # derived from that counter
            "workers[].balance_ecu",     # ChainState.balances
            "recent_events",             # STATE.events, an in-process buffer
            "hexis.pins.pinned_this_boot",  # counted by this PinService only
            "hexis.pins.last_error",        # the last failure this process saw
        ],
        "durable": [
            "newflow.total_workers",     # reloaded from the workers table
            "workers[].address",
            "workers[].country",
            "workers[].tier",
            "workers[].jobs",            # workers.jobs_completed, from SQLite
            "hexis.total_records",       # bridge_hexis_index.json
            "hexis.unique_actors",
            "hexis.pins.pinned",         # pin_status in that same file
            "hexis.pins.pending",
            "hexis.pins.unpinned_legacy",
            "hexis.pins.queued",         # bridge_hexis_pins.json, on disk
            "scs.energy_unit_genesis",   # a constant in the source
            "dormant.disclosure",        # likewise a constant in the source
            "ledger.head_seq",           # ledger_entries, append-only in bridge.db
            "ledger.head_hash",
            "ledger.entries",
            "ledger.totals",             # summed from those entries
        ],
        "derived": [
            "workers[].hexis",           # summed from the hexis index per call
            "workers[].grade",
            "durability",
            # Read from the environment at import, not from any store. It can
            # differ between two processes of the same service.
            "hexis.pins.enabled",
            # The reconcile *result* describes this process's last run. The
            # entries it read are durable; when the check last ran is not.
            "ledger.ok",
            "ledger.checked_at",
            "ledger.source",
            "ledger.discrepancy_count",
            "ledger.discrepancies",
            "ledger.chain_checked",
            "ledger.anchored_in_audit_chain",
            "ledger.runs",
            "ledger.mismatches",
            "ledger.last_ok_at",
            # Recomputed from the audit chain on every call. The evidence
            # underneath is durable and append-only — a firing recorded in the
            # chain stays recorded — but "how many days dormant" is a
            # subtraction done now, and it moves while nothing happens.
            "dormant.mechanisms",
            "dormant.never_fired",
        ],
    },
    "/trust/{actor_id}": {
        "ephemeral": [
            "newflow_balance_ecu",       # ChainState.balances
        ],
        "durable": [
            "record_count",
            "jobs_completed",
            "hardware_tier",
            "country",
        ],
        "derived": [
            "actor_id",                  # echoed from the request
            "hexis_total",               # summed from the index per call
            "grade",
            "accept",
            "collateral_multiplier",
            "geo_multiplier",
            "verified_at",
            "x402_headers",              # all of the above, restated
            "durability",
        ],
    },
}


# === Dormant mechanisms (2026-08-17) =======================================
# STEP4_PROPOSAL.md §9 measured two anti-concentration mechanisms against the
# real history and found that neither has ever bound anyone: zero
# `geo_damping_scale` values and zero `mint_capped` events in the entire audit
# chain. That was found by going looking. Nothing in the system said it.
#
# This is the same disclosure move as C's: the protocol's own thesis applied to
# itself. A mechanism that has never fired is not thereby working — it may be
# calibrated past anything that will ever happen, which is what §9 concluded
# about both of these (absolute thresholds on a network whose size nobody has
# measured). Reporting the dormancy makes the question visible without
# pretending to answer it, and it costs one query per mechanism behind the
# existing /status cache.
#
# Each probe names the trace the mechanism leaves in the audit chain **when it
# actually changes an outcome** — not when it runs. `geo.damping()` is called on
# every mint and returns 1.0; only a scale below 1.0 writes
# `geo_damping_scale`, and only that is the mechanism biting.
#
#   (name, wired, kind, needle)
#     kind "payload" -> a key that appears in an event payload
#     kind "action"  -> an action_type that exists only when it fires
DORMANCY_PROBES = (
    ("geo_damping",       "2026-07-24", "payload", "geo_damping_scale"),
    ("mint_share_cap",    "2026-07-24", "payload", "mint_capped"),
    ("context_decay",     "2026-08-17", "payload", "c_decay_scale"),
    ("posp_independence", "2026-08-17", "action",  "posp_claim_refused"),
    ("stake_slash",       "2026-07-22", "action",  "slash"),
)

DORMANCY_DISCLOSURE = (
    "Every mechanism in `never_fired` has never once changed an outcome on this "
    "network. That is a measurement, not a reassurance: silence from a safety "
    "mechanism does not show it is protecting anything, only that nothing has "
    "yet reached the threshold someone guessed. See STEP4_PROPOSAL.md section 9."
)


def dormancy_block() -> dict:
    """
    When each declared mechanism last actually bit, read from the audit chain.

    `dormant_days` counts from the last firing, or from the date the mechanism
    was wired if it has never fired — so the number answers "how long has this
    been silent" in both cases, which is the question worth asking.

    A failure here degrades to a stated failure rather than an omission: an
    unreadable chain must not make a dormant mechanism look measured.
    """
    out = {}
    try:
        conn = sqlite3.connect(DB_PATH, timeout=10)
        try:
            for name, wired, kind, needle in DORMANCY_PROBES:
                if kind == "action":
                    row = conn.execute(
                        "SELECT MAX(timestamp_unix) FROM audit_log "
                        "WHERE action_type = ?", (needle,)).fetchone()
                else:
                    # The payload is stored as compact JSON, so the key appears
                    # quoted. Matching the quotes keeps `mint_capped` from being
                    # found inside some other string that merely contains it.
                    row = conn.execute(
                        "SELECT MAX(timestamp_unix) FROM audit_log "
                        "WHERE payload LIKE ?", (f'%"{needle}"%',)).fetchone()
                last = row[0] if row else None
                since = last if last else datetime.fromisoformat(
                    wired + "T00:00:00+00:00").timestamp()
                out[name] = {
                    "fired_ever":   last is not None,
                    "last_fired":   (datetime.fromtimestamp(last, timezone.utc)
                                     .isoformat() if last else None),
                    "dormant_days": round((time.time() - since) / 86400.0, 1),
                    "wired":        wired,
                    "trace":        f"{kind}:{needle}",
                }
        finally:
            conn.close()
    except Exception as e:
        return {
            "disclosure":  DORMANCY_DISCLOSURE,
            "never_fired": None,
            "mechanisms":  {"error": f"the audit chain could not be read: {e}"},
        }
    return {
        "disclosure":  DORMANCY_DISCLOSURE,
        "never_fired": sorted(n for n, m in out.items() if not m["fired_ever"]),
        "mechanisms":  out,
    }


def durability_block(endpoint: str) -> dict:
    """
    The marker served with a response. Says which of its own numbers reset.

    Deliberately blunt about the consequence rather than only the mechanism: a
    reader who sees `chain_height: 0` next to 36 HEXIS records needs to be told
    that those two numbers are not measuring the same span of time.
    """
    d = DURABILITY[endpoint]
    return {
        "ephemeral": d["ephemeral"],
        "durable": d["durable"],
        "derived": d["derived"],
        "ephemeral_since": PROCESS_STARTED_AT,
        "note": "Ephemeral figures live only in this process. A restart resets "
                "them to genesis and there is no record of what they held "
                "before, so they are a view of this process's lifetime and not "
                "a history of the network. Durable figures survive restarts. "
                "Making chain state durable is scheduled work, not done.",
    }


class BridgeState:
    """
    NEWFLOW chain state in memory; HEXIS records on disk.

    Rewritten 2026-08-14. It used to end with "In production: NEWFLOW state
    persists to disk, HEXIS records persist to IPFS" — present tense, describing
    two measures that do not exist. Neither has been built. What follows is what
    the code actually does, and CORRECTIONS.md keeps the old sentence.

    **Everything constructed here is wiped when the process restarts**, and
    `_init_genesis()` then runs again: a new validator wallet, a new faucet
    wallet, a fresh allocation. `chain`, `ledger`, `scs`, `jobs` and `events`
    have no persistence of any kind and nothing reads them back from anywhere.

    Two things survive a restart, by two different mechanisms:

      - `hexis_index` is a JSON file on disk and loads itself.
      - `workers` is reloaded from the `workers` table in the lifespan below
        (gap #4, 2026-07-22) — the one registry someone noticed and fixed.

    `jobs` was not given the same treatment, so `bridge.db` holds job rows that
    `STATE.jobs` knows nothing about after a restart, and `/status` reports
    `total_jobs: 0` beside per-worker job counts read from the database. That
    is visible on the live endpoint right now; the measured figures are in
    CORRECTIONS.md.

    This is presentation-marked, not fixed. Making the chain durable is the
    bridge half of the reconcile item, and it is scheduled after 3e because 3e
    changes who the parties to a movement are — writing them into a permanent
    ledger first would make today's placeholder binding permanent.
    """

    def __init__(self):
        # Identifies this chain state in the ledger. Per BridgeState, not per
        # process: `chain` entries are only reconcilable against the balances
        # of the ChainState that produced them, and building a second
        # BridgeState — which tests do, and which any in-process restart would
        # — gives a fresh, empty ChainState while the old entries remain. A
        # process-wide id would have made the previous instance's genesis look
        # like money this one had lost.
        self.boot_id = uuid.uuid4().hex

        # NEWFLOW — all four of these are memory only. See the class docstring:
        # a restart re-runs genesis and the history before it is simply gone.
        self.chain = ChainState()
        self.ledger = Ledger()
        self.scs = SunkCostMintEngine()
        self.validator, self._validator_restored = self._load_or_create_wallet("validator")
        self.pool = OptimisticPool()

        # HEXIS
        self.hexis_index = LocalIndex("bridge_hexis_index.json")
        self.hexis_miner = HexisMiner()

        # IPFS pinning (2026-08-16). The queue is durable and is written on
        # every mint whether or not a JWT is configured — the content of a
        # record is what the 36 older ones lack, and that is fixed here even
        # if nothing is ever pinned. The service thread is started in the
        # lifespan, not here, so importing this module stays side-effect free.
        self.pin_queue = PinQueue("bridge_hexis_pins.json")
        self.pin_service = PinService(
            queue     = self.pin_queue,
            ipfs      = IPFSClient(),
            on_pinned = self.hexis_index.set_pinned,
            log       = lambda m: self.log(f"IPFS: {m}", level="hexis"),
        )

        # In-memory registries. `workers` and `consumers` are refilled from
        # their tables in the lifespan; `jobs` and `events` are not refilled
        # from anywhere, although `bridge.db` holds the job rows that would do
        # it.
        self.workers: Dict[str, dict] = {}
        self.consumers: Dict[str, dict] = {}
        self.jobs: Dict[str, dict] = {}
        self.events: List[dict] = []

        # Bootstrap genesis. This runs on EVERY start, not only the first —
        # new validator wallet, new faucet wallet, balances back to allocation.
        self._init_genesis()

    # ------------------------------------------------------------------
    # DURABLE PROTOCOL WALLETS (2026-08-23, OPEN.md #6)
    # ------------------------------------------------------------------
    WALLET_TABLE_DDL = """
        CREATE TABLE IF NOT EXISTS protocol_wallets (
            role            TEXT PRIMARY KEY,
            address         TEXT NOT NULL,
            private_key_hex TEXT NOT NULL,
            created_at      REAL NOT NULL
        )
    """

    def _load_or_create_wallet(self, role: str):
        """
        The validator and faucet wallets, restored if they exist.

        Returns (wallet, restored). Until 2026-08-23 both were `Wallet()` — a
        fresh keypair on every boot — so the issuing authority of this ledger
        changed identity each time the process restarted. That is the part
        OPEN.md #6 says persistence has to start with: balances can reset and
        be rebuilt, but a chain whose issuer is a different key every morning
        cannot be replayed by anyone, including us.

        `role` is the primary key, so there is exactly one validator and one
        faucet for the life of the database. There is no rotation path here on
        purpose — rotating an issuing key is a decision with consequences for
        every row already signed under the old one, and it should not be a
        side effect of a restart.

        The private key is stored beside the balances it issues. That adds no
        exposure the system did not already have: this process generates these
        keys itself and holds them in memory, and the trust boundary is shell
        access on the host, which is the same boundary that protects the
        database. It is emphatically NOT the Foundation seal key, which has
        never been on this machine and is not stored by anything here.
        """
        try:
            conn = sqlite3.connect(DB_PATH, timeout=30.0)
            try:
                conn.execute(self.WALLET_TABLE_DDL)
                row = conn.execute(
                    "SELECT address, private_key_hex FROM protocol_wallets "
                    "WHERE role = ?", (role,)).fetchone()
                if row:
                    w = Wallet(private_key_hex=row[1])
                    if w.address != row[0]:
                        # The stored key does not derive the stored address.
                        # Refuse rather than pick one: a mismatch here means
                        # the row was edited or corrupted, and guessing which
                        # half is right would be inventing an issuing identity.
                        raise RuntimeError(
                            f"protocol_wallets['{role}'] is inconsistent: the "
                            f"stored key derives {w.address}, the row says "
                            f"{row[0]}. Refusing to boot with an ambiguous "
                            f"issuing key.")
                    return w, True
                w = Wallet()
                conn.execute(
                    "INSERT INTO protocol_wallets(role, address, "
                    "private_key_hex, created_at) VALUES(?,?,?,?)",
                    (role, w.address, w.private_key_hex, time.time()))
                conn.commit()
                return w, False
            finally:
                conn.close()
        except RuntimeError:
            raise
        except Exception as e:
            # Fail closed. A wallet that could not be persisted would be a
            # fresh identity next boot, which is the defect, and a boot that
            # silently reintroduced it would be indistinguishable from one
            # that fixed it.
            raise RuntimeError(
                f"could not load or create the {role} wallet: {e}. Refusing to "
                f"boot rather than fall back to a per-boot keypair.")

    def _init_genesis(self):
        faucet, faucet_restored = self._load_or_create_wallet("faucet")
        self.faucet_wallet = faucet
        alloc = {
            self.validator.address: GENESIS_VALIDATOR_ECU,
            faucet.address:         GENESIS_FAUCET_ECU,
            "NETWORK_RESERVE":      GENESIS_TOTAL_ECU - GENESIS_VALIDATOR_ECU
                                    - GENESIS_FAUCET_ECU,
        }
        self.chain.apply_genesis(alloc, faucet_address=faucet.address)
        # Durable record of an allocation that is not durable. Every start
        # mints these balances again, to fresh addresses, and the ledger
        # accumulates one genesis per boot — which is not noise, it is the
        # clearest available statement of what "the chain resets" costs.
        #
        # The legs are built from `chain.balances` after the call, not from
        # `alloc` before it, and the difference is not academic: the ledger
        # caught it on its first run. `apply_genesis` skips NETWORK_RESERVE, so
        # 38,850,000 of the 39,000,000 ECU named here is never credited to
        # anything. That is consistent with whitepaper §4 — the reserve is
        # "released via verified compute — 95 years", not held as a balance at
        # genesis — but no release path is implemented, so nothing has come out
        # of it and nothing can yet. A ledger written from the allocation
        # instead of from the balances would have recorded supply that does not
        # exist.
        #
        # The two figures do not line up and this is the honest place to say
        # so: §4 splits 39,000,000 into Genesis Burn 1,950,000, Genesis
        # Contributors 1,872,000 and Network Reserve 35,178,000. This code has
        # neither of the first two, so it puts their 3,822,000 into
        # NETWORK_RESERVE as well. The unissued total is right; the label on
        # part of it is not.
        _unissued = sum(v for k, v in alloc.items() if k not in self.chain.balances)
        self._record_chain_op(
            "chain_genesis",
            [(ledger_entries.CHAIN, addr, float(bal))
             for addr, bal in self.chain.balances.items()],
            reason=f"genesis, re-minted every start; {_unissued} ECU declared "
                   f"in the allocation is not issued to any balance; "
                   f"validator wallet "
                   f"{'restored' if self._validator_restored else 'CREATED (first ever)'}, "
                   f"faucet wallet "
                   f"{'restored' if faucet_restored else 'CREATED (first ever)'}")
        # Which half of OPEN.md #6 is fixed and which is not, said at the point
        # a reader would otherwise assume both. The wallets are durable as of
        # 2026-08-23; the balances are still re-minted on every start.
        self.log(
            f"Genesis block initialized. NEWFLOW + HEXIS bridge online. "
            f"Validator {self.validator.address[:12]}... "
            f"({'restored' if self._validator_restored else 'created'}), "
            f"faucet {faucet.address[:12]}... "
            f"({'restored' if faucet_restored else 'created'}). "
            f"Balances are re-minted each start; the wallets are not.")

    # -- the chain's ledger legs ------------------------------------------
    #
    # `ChainState` is memory only, so these entries outlive the balances they
    # describe. They are reconciled against `ChainState.balances` only for the
    # current process (`boot_id`), which is a real check of the running
    # process and not a claim that the balances survive a restart. They do not.

    def _record_chain_op(self, op: str, legs: list, *, reason: str = "") -> None:
        total = sum(d for _, _, d in legs)
        legs = list(legs) + [(ledger_entries.CHAIN_ISSUANCE, "", -total)]
        conn = sqlite3.connect(DB_PATH, timeout=10)
        try:
            ledger_entries.record_op(conn, op, legs, reason=reason,
                                     boot_id=self.boot_id)
            conn.commit()
        finally:
            conn.close()

    def chain_transfer(self, tx, validator_address: str, *, reason: str = "") -> None:
        """
        The only way this file is allowed to move chain ECU.

        Wrapped rather than called directly so that the movement and its ledger
        legs cannot come apart — and so `audit_chain_write_sites()` can refuse
        to boot if a future caller reaches past it. Same argument as the escrow
        choke point in `hexis_stake.py`, for the same reason: the next person
        needing a transfer will copy the four lines above it.
        """
        self.chain.apply_transfer(tx, validator_address)
        self._record_chain_op("chain_transfer", [
            (ledger_entries.CHAIN, tx.sender,   -(tx.amount + tx.fee)),
            (ledger_entries.CHAIN, tx.receiver, +tx.amount),
            (ledger_entries.CHAIN, validator_address, +tx.fee),
        ], reason=reason)

    def actor_pubkey(self, actor_id: str) -> Optional[str]:
        """
        The Ed25519 public key (hex) an actor signs with, or None.

        None means one of two things, and both are fine here: a legacy testnet
        actor that never bound a key, or an id that is not a registered actor
        at all. Records simply omit the field in that case.

        Consults both registries since 3e. Before that it read only `workers`,
        so a HEXIS record mined for a consumer carried no key — see
        `mine_hexis_for_consumer`, which passes this straight through.
        """
        return ((self.workers.get(actor_id) or self.consumers.get(actor_id) or {})
                .get("pubkey"))

    def log(self, msg: str, level: str = "info", data: Optional[dict] = None) -> dict:
        entry = {
            "ts":    datetime.now(timezone.utc).isoformat(),
            "level": level,
            "msg":   msg,
            "data":  data or {},
        }
        self.events.append(entry)
        if len(self.events) > 1000:
            self.events = self.events[-1000:]
        print(f"[{entry['ts'][11:19]}] [{level.upper()}] {msg}")
        return entry

    def get_hexis_score(self, actor_id: str) -> float:
        records = self.hexis_index.get_by_actor(actor_id)
        return sum(r.get("hexis_raw", 0) for r in records)

    def get_trust_grade(self, hexis_score: float) -> dict:
        if hexis_score >= 0.05:
            return {"grade": "High",         "collateral_mult": 1.0, "accept": True}
        if hexis_score >= 0.005:
            return {"grade": "Moderate",     "collateral_mult": 1.5, "accept": True}
        if hexis_score >= 0.001:
            return {"grade": "Low",          "collateral_mult": 3.0, "accept": True}
        if hexis_score >= 0.0001:
            return {"grade": "Minimal",      "collateral_mult": 5.0, "accept": True}
        if hexis_score > 0.0:
            return {"grade": "Insufficient", "collateral_mult": 0.0, "accept": False}
        return     {"grade": "Unverified",   "collateral_mult": 0.0, "accept": False}

    def _index_and_pin(self, event, result: dict, actor_id: str,
                       hexis_raw: float) -> None:
        """
        Index one mined record, retain its canonical content, queue the pin.

        Both mint sites go through here so the record is built one way. The
        order matters and is deliberate:

          1. Build the canonical `LedgerRecord` — the thing a CID would
             address, and the thing the 36 records minted before today do not
             have anywhere.
          2. Write the index entry, with `record_hash` and `pin_status`. This
             is the durable record and it must not depend on the next step.
          3. Hand it to the pin service, which returns immediately.

        Nothing here may raise past this method: a failure to queue a pin is
        not a reason to fail a job that was already delivered honestly. A
        failure to *index* is different and is left to propagate — that one
        loses the mint.
        """
        record = None
        try:
            record = LedgerRecord.build(
                result, dataclasses.asdict(event), self.actor_pubkey(actor_id))
        except Exception as e:
            self.log(f"HEXIS record build failed for {actor_id[:16]}: {e}",
                     level="warn")

        self.hexis_index.add(
            event_id     = result["event_id"],
            cid          = f"local:{result['proof_hash'][:16]}",
            hexis_raw    = hexis_raw,
            actor_id     = actor_id,
            actor_pubkey = self.actor_pubkey(actor_id),
            record_hash  = (record or {}).get("record_hash"),
            # Content lives in the index now, not only in the pin queue —
            # success used to delete our only local copy (2026-08-23).
            record       = record,
            # "pending" is the honest word for a record with content retained
            # and no CID yet, whether that is because Pinata is down or
            # because no JWT is configured at all. `/status` distinguishes the
            # two by reporting whether pinning is enabled at all.
            pin_status   = "pending" if record else None,
        )
        if record:
            try:
                self.pin_service.submit(
                    result["event_id"], record, f"hexis-{result['event_id']}")
            except Exception as e:
                self.log(f"HEXIS pin queue failed for {actor_id[:16]}: {e}",
                         level="warn")

    def mine_hexis_for_job(
        self,
        worker_id: str,
        worker_country: str,
        job_value_ecu: float,
        job_id: str,
        sensitivity_tier: int = 1,
    ) -> dict:
        """
        Auto-mine HEXIS event when NEWFLOW job completes honestly.
        Includes adversarial frame: could_have_taken vs actually_returned.
        Sensitivity tier scales BO multiplier (1x / 5x / 20x / 100x).
        """
        bo_mult = SENSITIVITY_TIERS.get(sensitivity_tier, ("", 1.0))[1]
        scaled_value = job_value_ecu * bo_mult

        event = BehaviorEvent(
            event_id                = sha256(f"newflow_job_{job_id}_{worker_id}"),
            actor_id                = worker_id,
            timestamp               = time.time(),
            # 2026-08-23. This sentence used to end "TEE proof verified by
            # validator." There is no TEE and there never was, so every record
            # minted before today carries an assertion of a verification that
            # did not happen — 32 of them, permanently, because the chain is
            # append-only. What is written now is what actually occurred.
            description             = (
                f"NEWFLOW compute job delivered honestly. "
                f"Job ID: {job_id[:16]}. "
                f"Value: {job_value_ecu:.2f} ECU. "
                f"Sensitivity tier: {sensitivity_tier} (BO x{bo_mult}). "
                f"No TEE and no proof-of-compute: delivery is asserted by this "
                f"host, which is also the party that mints."
            ),
            actor_country           = worker_country,
            asset_could_have_taken  = scaled_value,
            asset_actually_returned = scaled_value,
            prob_betrayal_detected  = 0.95,
            gain_if_betrayed        = scaled_value,
            # 2026-08-23, OPEN.md #4. The three names here used to be
            # "NEWFLOW Validator", "On-chain TEE Proof" and "Consumer
            # Confirmation". The second referred to a TEE that does not exist;
            # the third to a confirmation never asked for — the consumer signs
            # /job/request and is not consulted again. Each now names something
            # that exists and that a reader can go and check.
            #
            # The TYPES AND WEIGHTS ARE DELIBERATELY UNCHANGED, so W is
            # unchanged at 0.14085 and the economic model is exactly the one
            # this testnet has been running. That is the decision on record:
            # stop asserting what is false, without silently repricing every
            # future mint. It follows that **this does not close OPEN.md #4** —
            # the closing condition there requires W to fall, or real
            # corroboration to be built. Neither has happened. What has gone
            # away is the fabrication, which was the part that could mislead.
            #
            # None of the three is an independent party. `independent_witnesses`
            # below says so in the payload rather than leaving it to be inferred
            # from three plausible-looking names.
            witness_sources         = [
                {"type": "adversarial",
                 "name": "NEWFLOW validator (this host; not adversarial and "
                         "not independent — weight retained, see OPEN.md #4)"},
                {"type": "neutral",
                 "name": f"Audit chain job_complete row for {job_id[:16]}"},
                {"type": "allied",
                 "name": "Consumer signature on /job/request (consent to the "
                         "job, not confirmation of delivery)"},
            ],
            mention_counts          = {"30d": 1, "1y": 0, "5y": 0},
        )

        result = self.hexis_miner.mine(event)

        if result.get("eligible"):
            # GEO-ECONOMICS (wired 2026-07-24): (A) C dong — scale hexis_raw
            # theo c_eff/C_base (formula §5 khong doi, chi scale tai mint);
            # (B) mint share cap — vuot cap thi job van tra fee, khong mint.
            hexis_adj = result["hexis_raw"]

            # C DECAY (2026-08-17). The country behind C is self-declared and
            # unverified, so the boost is a leg-up rather than a standing
            # subsidy: it decays with the actor's own mint count, half-life
            # C_DECAY_HALFLIFE_MINTS. Applied here, as a scale on hexis_raw,
            # for the same reason the geo damping below is applied here —
            # the §5 formula is not being edited.
            #
            # Only the part of C above 1.0 decays. A country whose C is below
            # 1.0 keeps it: a penalty that fades with seniority would be a
            # different mechanism with a different argument behind it, and
            # nobody has made that argument.
            try:
                _c = float(result.get("C", 1.0))
                if _c > 1.0:
                    _prior = len(self.hexis_index.get_by_actor(worker_id))
                    _decay = 0.5 ** (_prior / mining.C_DECAY_HALFLIFE_MINTS)
                    _c_eff = 1.0 + (_c - 1.0) * _decay
                    if _c_eff < _c:
                        result["c_decay_prior_mints"] = _prior
                        result["c_decay_scale"] = round(_c_eff / _c, 6)
                        hexis_adj = round(hexis_adj * (_c_eff / _c), 12)
                        result["hexis_raw"] = hexis_adj
            except Exception as _ce:
                # A decay that cannot be computed must not stop a mint that
                # has already been earned. Full C stands, and it is logged.
                self.log(f"C decay skipped for {worker_id[:16]}: {_ce}", level="warn")

            try:
                _scale = geo.c_scale(worker_country, float(result.get("C", 1.0)))
                if _scale < 1.0:
                    result["hexis_raw_base"]    = result["hexis_raw"]
                    result["geo_damping_scale"] = round(_scale, 6)
                    hexis_adj = round(hexis_adj * _scale, 12)
                    result["hexis_raw"] = hexis_adj
                _mint_ok, _mint_why = geo.mint_allowed(worker_id, worker_country)
            except Exception as _ge:
                _mint_ok, _mint_why = True, f"geo engine unavailable: {_ge}"
            if not _mint_ok:
                result["mint_capped"] = _mint_why
                result["hexis_raw"] = 0.0
                self.log(
                    f"HEXIS mint CAPPED for {worker_id[:16]}: {_mint_why} "
                    f"(job van tra fee — compute thi ban duoc, trust thi khong)",
                    level="warn",
                )
                return result
            self._index_and_pin(event, result, worker_id, hexis_adj)
            try:
                geo.record_mint(worker_id, worker_country, hexis_adj)
            except Exception:
                pass
            self.log(
                f"HEXIS mined for {worker_id[:16]}: {result['hexis_raw']:.6f}",
                level="hexis",
                data=result,
            )

        return result

    def mine_hexis_for_consumer(
        self,
        consumer_id: str,
        worker_id: str,
        job_value_ecu: float,
        job_id: str,
        sensitivity_tier: int = 1,
    ) -> dict:
        """
        Consumer-side HEXIS mint (P1 Bilateral Stake, v0.7, wired 2026-07-22).
        Cung duong formula voi worker (BehaviorEvent -> HexisMiner.mine()).
        Witness set mong hon worker: khong co adversarial witness tu nhien
        phia consumer ngoai viec worker chon khong dispute.
        """
        bo_mult = SENSITIVITY_TIERS.get(sensitivity_tier, ("", 1.0))[1]
        scaled_value = job_value_ecu * bo_mult

        event = BehaviorEvent(
            event_id                = sha256(f"newflow_consumer_{job_id}_{consumer_id}"),
            actor_id                = consumer_id,
            timestamp                = time.time(),
            description              = (
                f"NEWFLOW consumer settled job honestly (paid, no dispute raised). "
                f"Job ID: {job_id[:16]}. Value: {job_value_ecu:.2f} ECU. "
                f"Sensitivity tier: {sensitivity_tier} (BO x{bo_mult})."
            ),
            actor_country            = "??",
            asset_could_have_taken   = scaled_value,
            asset_actually_returned  = scaled_value,
            prob_betrayal_detected   = 0.90,
            gain_if_betrayed         = scaled_value,
            witness_sources          = [
                {"type": "adversarial", "name": "Worker Non-Dispute Confirmation"},
                {"type": "neutral",     "name": "On-chain ECU Settlement Record"},
            ],
            mention_counts           = {"30d": 1, "1y": 0, "5y": 0},
        )

        result = self.hexis_miner.mine(event)

        if result.get("eligible"):
            # actor_pubkey resolves since 3e (2026-08-15). Before that
            # consumers held no key, so it was always None and the field was
            # omitted — an honest record of one signed party and one anonymous
            # one. Records mined before that date still carry no consumer key
            # and are not being backfilled: the key did not exist then, and
            # writing one in now would claim it did.
            self._index_and_pin(event, result, consumer_id, result["hexis_raw"])
            self.log(
                f"HEXIS mined for consumer {consumer_id[:16]}: {result['hexis_raw']:.6f}",
                level="hexis",
                data=result,
            )

        return result

    def wipe_hexis(self, actor_id: str, reason: str) -> dict:
        """
        He qua slash (Appendix D: "forfeits ... plus all HEXIS standing").
        Append-only: KHONG xoa lich su, chi append 1 record bu tru dua tong
        ve 0. Ban than viec wipe cung tro thanh 1 phan cua chain.
        """
        current = self.get_hexis_score(actor_id)
        if current <= 0:
            return {"actor_id": actor_id, "wiped": 0.0, "reason": reason}
        offset = -current
        event_id = sha256(f"hexis_wipe_{actor_id}_{time.time()}")
        self.hexis_index.add(
            event_id     = event_id,
            cid          = f"local:wipe:{event_id[:16]}",
            hexis_raw    = offset,
            actor_id     = actor_id,
            actor_pubkey = self.actor_pubkey(actor_id),
        )
        self.log(
            f"HEXIS wiped for {actor_id[:16]}: {current:.6f} -> 0 (reason: {reason})",
            level="hexis",
            data={"actor_id": actor_id, "prior_total": current, "reason": reason},
        )
        return {"actor_id": actor_id, "wiped": current, "reason": reason}


# ===========================================================
# GLOBAL SINGLETONS
# ===========================================================

rate_limiter = RateLimiter()
cache = LRUCache()
capacity = CapacityGuard()
write_queue = WriteQueue(DB_PATH)
STATE: Optional[BridgeState] = None  # initialised in lifespan

# /status response cache (patch #11): tra cache trong STATUS_CACHE_TTL giay
STATUS_CACHE_TTL = 3.0
_status_cache = None
_status_cache_ts = 0.0

# ===========================================================
# LEDGER RECONCILE STATE
# ===========================================================

#: An hour, matching the Trust API's. Long enough that it is not a load
#: source, short enough that a mismatch is found the same day it appears.
LEDGER_RECONCILE_INTERVAL_S = 3600
LEDGER_MAX_REPORTED = 20


class LedgerState:
    """
    What the last reconcile found, so `/status` and `/metrics` can say it.

    Kept out of `BridgeState` on purpose: this survives nothing and describes
    the process, and `BridgeState` is where the durability confusion lived.
    """

    def __init__(self):
        self.last: Optional[dict] = None
        self.last_ok_at: Optional[str] = None
        self.mismatches = 0
        self.runs = 0
        self.anchored = 0        # runs whose result reached the audit chain

    def observe(self, result, anchored: bool) -> None:
        self.runs += 1
        self.last = result.as_record({"anchored": anchored})
        if anchored:
            self.anchored += 1
        if result.ok:
            self.last_ok_at = result.checked_at
        else:
            self.mismatches += 1

    def as_dict(self) -> dict:
        """
        The same keys whether or not a run has happened yet.

        A response whose shape depends on internal state cannot be classified
        by the durability marker, and a client written against the populated
        shape would break on the empty one. Nulls before the first run, and
        `runs: 0` says which case it is.
        """
        last = self.last or {}
        return {
            "ok": last.get("ok"),
            "checked_at": last.get("checked_at"),
            "source": last.get("source"),
            "entries": last.get("entries"),
            "head_seq": last.get("head_seq"),
            "head_hash": last.get("head_hash"),
            "discrepancy_count": last.get("discrepancy_count"),
            "discrepancies": last.get("discrepancies", []),
            "totals": last.get("totals", {}),
            "chain_checked": last.get("chain_checked"),
            "anchored_in_audit_chain": last.get("anchored", False),
            "runs": self.runs,
            "mismatches": self.mismatches,
            "last_ok_at": self.last_ok_at,
        }


ledger_state = LedgerState()


def _ledger_reconcile_sync(source: str) -> tuple:
    """
    The blocking half: reconcile, then anchor the result in the audit chain.

    Runs on a worker thread. Returns (result, anchored). The anchor is a
    `ledger_reconcile` event carrying the ledger head — hash-linked into a
    chain that is signed daily with a key that is not on this host, which is
    what makes this record tamper-evident and `reconcile_hexis_db.jsonl` not.
    """
    conn = sqlite3.connect(DB_PATH, timeout=10)
    try:
        result = ledger_entries.reconcile(
            conn, source=source, db_path=DB_PATH,
            chain_balances=dict(STATE.chain.balances) if STATE else None,
            boot_id=STATE.boot_id if STATE else ledger_entries.BOOT_ID,
            max_reported=LEDGER_MAX_REPORTED)
    finally:
        conn.close()
    anchored = False
    try:
        audit.log_action(
            actor_id="system", counterparty_id=None,
            action_type="ledger_reconcile",
            payload={
                "source": source,
                "ok": result.ok,
                "entries": result.entries,
                "head_seq": result.head_seq,
                "head_hash": result.head_hash,
                "discrepancy_count": result.discrepancy_count,
                "totals": result.totals,
            })
        anchored = True
    except Exception as e:                                  # noqa: BLE001
        # Say so rather than silently downgrading. A reconcile whose result
        # never reached the chain is not tamper-evident, and a caller reading
        # "ok" would have no way to tell.
        STATE.log(f"ledger reconcile could not be anchored: {e}", level="warn")
    return result, anchored


async def run_ledger_reconcile(source: str, *, fatal: bool):
    result, anchored = await asyncio.to_thread(_ledger_reconcile_sync, source)
    ledger_state.observe(result, anchored)
    report = ledger_entries.format_report(result)
    STATE.log(report, level="warn" if not result.ok else "info")
    if fatal and not result.ok:
        raise RuntimeError(
            "the bridge ledger does not reconcile:\n" + report +
            "\n\nThe balances and the entries behind them disagree. This is a "
            "data failure, not a code one — rolling the release back does not "
            "change it. See DEPLOY.md."
        )
    if fatal and not anchored:
        raise RuntimeError(
            "the boot reconcile could not be written to the audit chain — "
            "without the anchor the reconcile is an unsigned assertion about "
            "a database, which is the property this ledger exists to avoid"
        )
    return result


async def _ledger_reconcile_loop():
    # Sleeps first. The unit restarts every five seconds on failure, and a
    # loop that reconciles before its first sleep would turn a crash loop into
    # a reconcile loop against a database that is already unhappy.
    while True:
        await asyncio.sleep(LEDGER_RECONCILE_INTERVAL_S)
        try:
            await run_ledger_reconcile("periodic", fatal=False)
        except asyncio.CancelledError:
            raise
        except Exception as e:                              # noqa: BLE001
            STATE.log(f"ledger reconcile loop error: {e}", level="warn")


# ===========================================================
# PYDANTIC MODELS
# ===========================================================

class WorkerRegisterReq(BaseModel):
    country: str = Field(..., min_length=2, max_length=3)
    hardware_tier: int = Field(..., ge=1, le=4)
    # MANDATORY as of 2026-08-11 (was optional, added 2026-07-23). An actor
    # with no key cannot sign, and an actor that cannot sign cannot write.
    # Registering one would only create an account that is dead on arrival.
    client_pubkey: str = Field(..., min_length=64, max_length=64)  # ed25519 hex


class ConsumerRegisterReq(BaseModel):
    country: str = Field(..., min_length=2, max_length=3)
    # Mandatory, with no optional phase to grow out of. Workers had one and it
    # cost an amendment on 2026-08-11; there is no reason to repeat it when the
    # answer is already known.
    client_pubkey: str = Field(..., min_length=64, max_length=64)  # ed25519 hex


class BindPubkeyReq(BaseModel):
    pubkey: str = Field(..., min_length=64, max_length=64)


class JobCompleteReq(BaseModel):
    signature: Optional[str] = Field(None, max_length=256)  # ed25519(job_id) hex
    result: Optional[str] = Field(None, max_length=128)      # PoSP workload hash (added 2026-07-24)


class JobRequestReq(BaseModel):
    job_id: str = Field(..., min_length=1, max_length=128)  # client-generated (added 2026-07-22)
    worker_address: str = Field(..., min_length=1, max_length=128)
    # MANDATORY as of 2026-08-15 (3e). It was Optional, and when it was absent
    # the server generated a Wallet, kept the private key, and called the
    # result a consumer. That is the arrangement /worker/register was rewritten
    # to end on 2026-08-11: an identity whose key the server holds is the
    # server's identity, not the actor's. It is also what this endpoint now
    # binds its signature to, and a signer that the request may omit is not a
    # signer.
    consumer_address: str = Field(..., min_length=1, max_length=128)
    task_type: str = Field("llm_inference_mid_1B_tokens", max_length=64)
    compute_units: int = Field(100, gt=0)
    fee_ecu: float = Field(..., gt=0)
    sensitivity_tier: int = Field(1, ge=1, le=4)


# ===========================================================
# LIFESPAN
# ===========================================================

@asynccontextmanager
async def lifespan(app: FastAPI):
    global STATE
    init_db()
    STATE = BridgeState()

    # Refuse to serve if any write endpoint is unauthenticated. A hole found
    # at startup is an outage; the same hole found later is an incident.
    _unguarded = audit_write_route_protection(app)
    if _unguarded:
        raise RuntimeError(
            "unauthenticated write routes: " + ", ".join(_unguarded) +
            " — attach Depends(require_signature(...)) or add the route to "
            "UNGUARDED_WRITE_ROUTES with a reason"
        )
    STATE.log("Write-route signature audit passed.")

    # And refuse to serve if the books do not balance. The check above asks
    # who may call; this one asks whether the call conserves ECU.
    _leaks = audit_ecu_conservation()
    if _leaks:
        raise RuntimeError(
            "ECU conservation violated: " + "; ".join(_leaks) +
            " — an operation is creating or destroying ECU that no burn "
            "accounts for; do not serve until it balances"
        )
    STATE.log("ECU supply-conservation audit passed.")

    # And refuse to serve if the tamper-evident record is incomplete before it
    # has recorded anything. A write that never reaches the chain is worse than
    # one that fails loudly: the chain still verifies, so nothing ever says the
    # history is short.
    _dropped, _summary = audit_action_types()
    if _dropped:
        raise RuntimeError(
            "audit action types would be discarded: " + "; ".join(_dropped) +
            " — every caller swallows the rejection, so these events would be "
            "lost in silence and the chain would still verify as intact"
        )
    STATE.log(f"Audit action-type audit passed. {_summary}")

    # And refuse to serve if a number in the published whitepaper disagrees
    # with the code that produces it.
    #
    # This is the drift half of the 2026-08-23 audit and only that half. Seven
    # of its nineteen false claims were true when written and were overtaken
    # by a change that reached the code and not the paper; those are now
    # mechanically impossible to leave in place. The other fourteen were
    # assertions that were never true, and nothing here can detect a mechanism
    # that was never built.
    #
    # So: **a passing line below does not mean the whitepaper is verified.**
    # It means no checked NUMBER contradicts the code today. The wallet hard
    # cap is in the checked table precisely because it passes — 10,000 in the
    # paper, 10,000 in `hexis_mining` — while the sentence beside it about
    # enforcement was false for months. Numbers are checkable; claims are not.
    #
    # The live values are read here rather than from module defaults on
    # purpose. Sigma defaults to 0.0 as a safety gate and severity is tuned by
    # UPDATE on its config table, so a check against the defaults would pass
    # while the paper's claim about the running network was wrong.
    _figure_live = {
        "genesis_total_ecu": GENESIS_TOTAL_ECU,
        "bridge_version":    SERVER_VERSION,
        "sampling_sigma":    sampling.get_config().get("sigma"),
    }
    _sev_cfg = severity.get_config()
    for _t in (1, 2, 3, 4):
        _figure_live[f"severity_mult_t{_t}"] = _sev_cfg.get(f"damage_mult_t{_t}")
    _figure_problems, _figure_notices = audit_whitepaper_figures(_figure_live)
    if _figure_problems:
        raise RuntimeError(
            "whitepaper and code disagree about a number: " +
            "; ".join(_figure_problems) +
            " — decide which side drifted and correct that side publicly and "
            "dated, the way CORRECTIONS.md sets out. Do not relax the check"
        )
    # A feature this node has not switched on. Not a refusal — a new node must
    # be able to start, or the second independent node can never exist — but
    # never silent either: an unannounced off switch is how a published claim
    # outlives the thing it described.
    for _n in _figure_notices:
        STATE.log(f"Whitepaper figure NOT ENABLED HERE: {_n}", level="warn")
    STATE.log(f"Whitepaper figure audit passed. {figure_summary()}"
              + (f" {len(_figure_notices)} not enabled on this node."
                 if _figure_notices else ""))

    # Reload workers tu SQLite (gap #4, wired 2026-07-22): STATE.workers la
    # in-memory, mat sach moi restart — nap lai metadata tu bang workers.
    # wallet=None: private key server-side da mat, /job/request co fallback.
    try:
        _c = sqlite3.connect(DB_PATH, timeout=10)
        _c.row_factory = sqlite3.Row
        _rows = _c.execute(
            "SELECT address, country, hardware_tier, registered_at, jobs_completed, pubkey, benchmark_passed FROM workers"
        ).fetchall()
        _c.close()
        for _r in _rows:
            STATE.workers[_r["address"]] = {
                "address":        _r["address"],
                "country":        _r["country"],
                "hardware_tier":  _r["hardware_tier"],
                "registered_at":  _r["registered_at"],
                "jobs_completed": _r["jobs_completed"] or 0,
                "pubkey":         _r["pubkey"],
                "benchmark_passed": bool(_r["benchmark_passed"]),
                "hexis_score":    STATE.get_hexis_score(_r["address"]),
                "wallet":         None,
            }
        STATE.log(f"Reloaded {len(_rows)} workers from SQLite.")
    except Exception as _e:
        STATE.log(f"Worker reload from SQLite failed: {_e}", level="warn")

    # Consumers, same treatment (3e). Written alongside the worker reload
    # rather than after the fact, because the worker reload exists precisely
    # because somebody had to come back and add it — see the durability entry
    # in CORRECTIONS.md. A consumer that cannot be found after a restart
    # cannot sign, so its jobs and its locked stake become unreachable.
    try:
        _c = sqlite3.connect(DB_PATH, timeout=10)
        _c.row_factory = sqlite3.Row
        _rows = _c.execute(
            "SELECT address, country, registered_at, pubkey, jobs_requested "
            "FROM consumers"
        ).fetchall()
        _c.close()
        for _r in _rows:
            STATE.consumers[_r["address"]] = {
                "address":        _r["address"],
                "country":        _r["country"],
                "registered_at":  _r["registered_at"],
                "pubkey":         _r["pubkey"],
                "jobs_requested": _r["jobs_requested"] or 0,
            }
        STATE.log(f"Reloaded {len(_rows)} consumers from SQLite.")
    except Exception as _e:
        STATE.log(f"Consumer reload from SQLite failed: {_e}", level="warn")

    # Only now, with both registries filled, is the collision check meaningful:
    # run it against what was actually loaded, not against an empty pair of
    # dictionaries.
    _collisions = audit_identity_registries()
    if _collisions:
        raise RuntimeError(
            "identity registries collide: " + "; ".join(_collisions) +
            " — one actor must resolve to exactly one key, or the signature "
            "check is decided by lookup order rather than by the signature"
        )
    STATE.log("Identity registry audit passed. "
              f"{len(STATE.workers)} worker(s), {len(STATE.consumers)} consumer(s), "
              "no shared address or key.")
    # The books. Two audits over the source first — they say whether a
    # movement *can* bypass the ledger — then the reconcile, which says
    # whether one already has.
    _bad_escrow = ledger_entries.audit_escrow_write_sites(hexis_stake)
    if _bad_escrow:
        raise RuntimeError(
            "escrow is written outside the ledger choke point: "
            + "; ".join(_bad_escrow) +
            " — every balance change must go through _debit/_credit_in_tx, "
            "which write the entry in the same transaction"
        )
    _bad_chain = ledger_entries.audit_chain_write_sites(sys.modules[__name__])
    if _bad_chain:
        raise RuntimeError(
            "chain balances are moved outside BridgeState.chain_transfer(): "
            + "; ".join(_bad_chain) +
            " — a movement with no ledger legs disappears at the next restart"
        )
    _bad_fund = audit_fund_escrow_call_sites()
    if _bad_fund:
        raise RuntimeError(
            "fund_escrow() is reachable from something other than the CLI: "
            + "; ".join(_bad_fund) +
            " — it mints ECU and its only authorisation is shell access on "
            "this host, so a call site inside the request path would publish "
            "a money printer. See the section above fund_escrow()."
        )
    _bad_doc = audit_document_seal_call_sites()
    if _bad_doc:
        raise RuntimeError(
            "record_document_seal() is reachable from something other than the "
            "CLI: " + "; ".join(_bad_doc) +
            " — it writes chain events, and an event written on request can "
            "push the head somewhere nobody meant to sign. See the section "
            "above record_document_seal()."
        )
    _bad_ots = audit_ots_anchor_call_sites()
    if _bad_ots:
        raise RuntimeError(
            "record_ots_anchor() is reachable from something other than the "
            "CLI: " + "; ".join(_bad_ots) +
            " — it writes chain events, and an event written on request can "
            "push the head somewhere nobody meant to sign. See the section "
            "above record_ots_anchor()."
        )
    _bad_succ = audit_successor_designation_call_sites()
    if _bad_succ:
        raise RuntimeError(
            "record_successor_designation() is reachable from something other "
            "than the CLI: " + "; ".join(_bad_succ) +
            " — it names the key that a future reader would treat as this "
            "project's successor, and a route that writes it on request is a "
            "route that lets somebody else name one. See the section above "
            "record_successor_designation()."
        )
    STATE.log("Ledger write-site audit passed. Escrow grants, document seals, "
              "Bitcoin anchors and successor designations are CLI-only.")

    # RECORD CONTENT RESTORE (2026-08-23). Runs at boot, in the process that
    # owns the index, because any other process writing the index file loses
    # the write on the service's next in-memory _save(). Files are dropped
    # into record_restore/ named <event_id>.json; each is accepted only if it
    # hashes to the record_hash the index has carried since the mint. Consumed
    # files are renamed .done, refusals .refused — a file that vanished
    # silently would be indistinguishable from one that was restored.
    _restore_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "record_restore")
    if os.path.isdir(_restore_dir):
        for _fn in sorted(os.listdir(_restore_dir)):
            if not _fn.endswith(".json"):
                continue
            _fp = os.path.join(_restore_dir, _fn)
            try:
                with open(_fp, "rb") as _f:
                    _content = json.loads(_f.read())
                _eid = _fn[:-5]
                _verdict = STATE.hexis_index.restore_content(
                    _eid, _content, requeue=STATE.pin_queue)
            except Exception as _re:
                _verdict = f"refused: {_re}"
            _ok = _verdict.startswith(("restored", "already"))
            os.replace(_fp, _fp + (".done" if _ok else ".refused"))
            STATE.log(f"Record restore {_fn}: {_verdict}",
                      level="info" if _ok else "warn")

    # DORMANCY, SAID OUT LOUD (2026-08-23, second half of OPEN.md #9).
    #
    # /status has carried a `dormant` block for a month. Reporting a number to
    # an endpoint nobody polls is not the same as saying it: a safety mechanism
    # that has never once changed an outcome reads as protection right up until
    # somebody checks. This puts it in the journal at every boot, at WARN, with
    # the day count attached, so the silence has to be read rather than found.
    #
    # It is deliberately not an error and does not block startup. Dormant is
    # not broken — it is unanswered, and the two need different words.
    DORMANT_LOUD_AFTER_DAYS = 14
    try:
        _d = dormancy_block()
        _quiet = [
            (name, info.get("dormant_days"))
            for name, info in (_d.get("mechanisms") or {}).items()
            if isinstance(info.get("dormant_days"), (int, float))
            and info["dormant_days"] >= DORMANT_LOUD_AFTER_DAYS
            and not info.get("fired_ever")
        ]
        if _quiet:
            _quiet.sort(key=lambda x: -x[1])
            STATE.log(
                "DORMANT: " + ", ".join(f"{n} ({d:.0f}d)" for n, d in _quiet)
                + f" — each has never changed an outcome in "
                  f"{DORMANT_LOUD_AFTER_DAYS}+ days. That is a measurement, "
                  f"not a reassurance: it shows nothing has reached the "
                  f"threshold someone guessed, not that anyone is protected.",
                level="warn")
        else:
            STATE.log(f"Dormancy check: no declared mechanism has been silent "
                      f"for {DORMANT_LOUD_AFTER_DAYS}+ days without firing.")
    except Exception as _de:
        # Same rule as everywhere else here: a check that cannot run says so.
        STATE.log(f"Dormancy check could not run: {_de}. Treat the mechanisms "
                  f"below as unreported, not as healthy.", level="warn")

    # Balances that predate the ledger get the one entry they are missing,
    # once, on the first boot that finds them. See ensure_opening_balances.
    def _open_ledger():
        conn = sqlite3.connect(DB_PATH, timeout=10)
        try:
            opened = ledger_entries.ensure_opening_balances(conn)
            conn.commit()
            return opened
        finally:
            conn.close()

    _opened = await asyncio.to_thread(_open_ledger)
    if _opened:
        audit.log_action(actor_id="system", counterparty_id=None,
                         action_type="ledger_opening", payload=_opened)
        STATE.log(
            f"Ledger opening balances written: {_opened['total_ecu']} ECU over "
            f"{_opened['escrow_actors']} escrow actor(s) and "
            f"{_opened['locked_actors']} with locked stake. Asserted, not derived."
        )

    await run_ledger_reconcile("boot", fatal=True)

    queue_task = asyncio.create_task(write_queue.run_forever())
    sweep_task = asyncio.create_task(stake_expiry_sweep())
    ledger_task = asyncio.create_task(_ledger_reconcile_loop())
    STATE.log(f"Stale-lock sweep scheduled every {STAKE_SWEEP_INTERVAL_S}s.")
    STATE.log(f"Ledger reconcile scheduled every {LEDGER_RECONCILE_INTERVAL_S}s.")

    # IPFS pinning. Starting it is unconditional and refusing to start is not
    # a boot failure — an unpinned record is a weaker record, not a wrong one,
    # and the bridge has to keep running to make more of them. What the state
    # is gets reported at /status rather than decided here.
    STATE.pin_service.start()
    _pins = STATE.hexis_index.pin_summary()
    if STATE.pin_service.enabled():
        STATE.log(
            f"IPFS pinning ON. {len(STATE.pin_queue)} record(s) queued, "
            f"{_pins['pinned']} already pinned, "
            f"{_pins['unpinned_legacy']} pre-2026-08-16 record(s) that have no "
            f"retained content and are not queued."
        )
    else:
        STATE.log(
            f"IPFS pinning OFF — no PINATA_JWT in the environment. Records are "
            f"still built and retained ({len(STATE.pin_queue)} queued); nothing "
            f"is being sent anywhere. See DEPLOY.md, 'Turning pinning on'.",
            level="warn",
        )

    try:
        yield
    finally:
        STATE.pin_service.stop()
        for t in (queue_task, sweep_task, ledger_task):
            t.cancel()
        for t in (queue_task, sweep_task, ledger_task):
            try:
                await t
            except asyncio.CancelledError:
                pass


# The description renders at the top of /docs, above every endpoint, which is
# the point: the disclosure is not a footnote to be found. A protocol whose
# whole thesis is that a claim is worth what its verification is worth has to
# say out loud where it accepts a claim it does not verify.
DOCS_DESCRIPTION = """
**Disclosure — the country behind the Context multiplier (C) is self-declared
and is not verified.** An actor types a 2–3 character country code at
registration and nothing checks it: not against the request, not against an
IP, not against anything. C is derived from that string and multiplies every
HEXIS mint.

Since 2026-08-17 the range is `[0.8, 1.25]` (was `[0.5, 2.0]`), so the most a
false declaration can buy is 56% faster accrual rather than 300%, and the
boost decays with an actor's own mint count at a half-life of 100 mints — a
leg-up for a newcomer, not a standing subsidy. Neither change makes the
declaration true. They bound what it is worth.

The same string no longer decides who may audit whom. That gate now runs on
measurable independence — separate stake, no shared transaction history, and a
benchmark fingerprint — because it guards something more dangerous than a
mint rate.

See `STEP4_PROPOSAL.md` and `CORRECTIONS.md` in the repository.
"""

app = FastAPI(
    lifespan=lifespan,
    title="HEXIS x NEWFLOW Bridge",
    version=SERVER_VERSION,
    description=DOCS_DESCRIPTION,
    docs_url="/docs",
)

# === P1.5 Audit & Compliance Layer (wired 2026-05-24) ===
audit = AuditLogger(DB_PATH)
app.include_router(get_audit_router(audit))

# === P1 Bilateral Stake (wired 2026-07-22) ===
# GATE: hexis_settle_fn / hexis_wipe_fn PHAI la None cho toi khi Block 0 mint
# (luat da chot tu State v4 §0: "Chi bind hexis_fn vao HexisMiner SAU KHI
# Block 0 da mint. HEXIS side cua P1 'armed' sau Block 0, khong truoc.")
# ECU escrow (lock/release/slash) test duoc ngay - khong bi gate.
# TODO Block 0: doi 2 dong None ben duoi thanh:
#   hexis_settle_fn=lambda c,w,v,t,j: STATE.mine_hexis_for_consumer(c,w,v,j,t),
#   hexis_wipe_fn=lambda a,r: STATE.wipe_hexis(a,r),
def _stake_audit_fn(action, actor_id, counterparty, data):
    return audit.log_action(actor_id=actor_id, counterparty_id=counterparty,
                             action_type=action, payload=data)

# === Severity Tiers v0.8 bootstrap (wired 2026-07-22) ===
# Loop: audit detect -> stake enforce -> severity classify.
# Rule-based, moi tham so trong bang severity_config (calibrate = UPDATE).
severity = SeverityEngine(
    db_path=DB_PATH,
    audit_fn=_stake_audit_fn,
)

stake = StakeManager(
    db_path=DB_PATH,
    audit_fn=_stake_audit_fn,
    hexis_settle_fn=None,   # GATED - xem comment tren
    hexis_wipe_fn=None,     # GATED - xem comment tren
    eligibility_fn=severity.check_eligible,   # 403 neu quarantine/blacklist/debt
    incident_fn=severity.record_incident,     # classify moi slash
)
app.include_router(get_stake_router(stake, write_deps={
    # 3e, 2026-08-15. Bound to the consumer, the party that initiates a lock —
    # it was bound to `worker_id` as an admitted placeholder, which meant the
    # worker signed for a call debiting the consumer's escrow.
    #
    # This is better and it is not sufficient, so it is worth being exact.
    # `lock()` debits BOTH sides: `consumer_amount + job_value_ecu` from the
    # consumer and `worker_amount` from the worker. One signature therefore
    # still moves the other party's money, whichever party signs. Binding to
    # the consumer is the right half — the consumer initiates, and its debit
    # is the larger of the two — but a two-party debit wants two signatures,
    # and there is nowhere in the flow that the worker has agreed yet: lock
    # happens BEFORE /job/request, so no job row exists to carry consent.
    # Recorded as OPEN in CORRECTIONS.md rather than closed by picking a side
    # and not saying so.
    "/stake/lock": [Depends(require_signature(
        lambda request, payload: payload.get("consumer_id")))],
    # P1 BILATERAL STAKE (2026-08-23, OPEN.md #1). The proposal is signed by
    # the consumer, who is making it.
    "/stake/terms": [Depends(require_signature(
        lambda request, payload: payload.get("consumer_id")))],
    # And the acceptance by the WORKER named in those terms — read from the
    # stored row, never from the request, so a caller cannot nominate whose
    # key gets checked. This is the signature the whole defect was about: it
    # is produced by the consenting party, at the moment of consent, over a
    # URL that contains this job_id and a nonce that is recorded, so it
    # cannot be forwarded by the counterparty or replayed onto another job.
    "/stake/terms/accept": [Depends(require_signature(
        lambda request, payload: _worker_for_terms(
            request.path_params.get("job_id"))))],
    # Unwinding a lock is scoped to the job, so the worker on that job signs.
    "/stake/abort": [Depends(require_signature(
        lambda request, payload: _worker_for_job_stake(payload.get("job_id"))))],
    # /stake/expire and /stake/credit took no guard here and are not missing
    # one: both answer 410 Gone unconditionally as of 2026-08-12. A signature
    # would have made each attributable without making either authorised, and
    # an attributable money printer is still a money printer.
}))


# === Escrow funding, host-side only (2026-08-17) ============================
# The gap this closes and the gap it deliberately leaves open.
#
# Escrow had no funding path at all. `/stake/credit` answered 410 Gone from
# 2026-08-12 — correctly, because it was an unauthenticated money printer — and
# nothing replaced it, so the only ECU issuance left was `_posp_reward`, which
# pays a validator for an audit that only exists after a job has completed, and
# a job cannot start without locked stake. A closed loop with no entry point:
# the whole mint -> pin path was unreachable on the live service, which is how
# `pins.pinned` sat at 0 on a day the pinning code was working fine.
#
# So the entry point is a shell command, in the `--create-key` tradition of
# hexis_api_v0.6.1.py:
#
#     python3 hexis_bridge_v0.6.2.py --fund-escrow <actor_id> <amount>
#
# **No HTTP endpoint, and no operator key.** The authorisation is shell access
# on the host, which is the trust boundary this system already has — the same
# argument that put key issuance on the host rather than behind a token. An
# endpoint would need a credential, the credential would live on the machine
# that also holds the audit chain, and protecting it would be a new problem
# invented to solve an old one. There is also no way for an actor to fund
# itself: nothing in the request path can reach this function, and the boot
# audit below refuses to start the service if that ever stops being true.
#
# What this is NOT: a chain -> escrow conversion. Chain balances are rebuilt
# from genesis on every restart (see `durability_block`), so converting one
# into escrow would turn something that vanishes at the next restart into
# something that does not — a printer with extra steps, and one whose output
# would look like ordinary settled value. Ruled out deliberately, 2026-08-17.
#
# What the record says, and what it cannot: the ledger gets balanced legs,
# `issuance -> escrow:<actor>`, in one transaction, so the money and its
# explanation cannot come apart. The audit event carries the reason verbatim.
# It does **not** say who ran the command, because there is no operator
# identity here to name and inventing one would be a lie about how the
# authorisation actually works. The chain records that a grant happened, and
# the honest answer to "granted by whom" is "whoever had a shell".
FUND_ESCROW_REASON = "testnet operator grant"

# Per grant, not a budget: run it twice and you have granted twice. A cap this
# shape only stops a slipped digit, and it is calibrated to do exactly that.
# The largest escrow balance this network has ever held is 3320 ECU, and one
# pass of the verification loop needs about 60 — so 1000 is far above any test
# need and well below anything already here, which means a mistyped 10000 is
# refused and a mistyped 100 just funds a test.
FUND_ESCROW_MAX_PER_GRANT = 1000.0


def _registered_role(actor_id: str) -> Optional[str]:
    """'worker', 'consumer', or None. Read from SQLite, not from STATE.

    STATE is built in the lifespan, so it does not exist when this module is
    run as a CLI. The tables do.
    """
    conn = sqlite3.connect(DB_PATH, timeout=10)
    try:
        for table, role in (("workers", "worker"), ("consumers", "consumer")):
            try:
                row = conn.execute(
                    f"SELECT 1 FROM {table} WHERE address = ?", (actor_id,)
                ).fetchone()
            except sqlite3.OperationalError:
                continue          # table absent on a fresh database
            if row:
                return role
    finally:
        conn.close()
    return None


def fund_escrow(actor_id: str, amount) -> dict:
    """
    Grant ECU into one registered actor's escrow, through the ledger.

    Every refusal below is a refusal *before* any money moves, which is the
    only ordering worth having: `stake.credit()` opens `BEGIN IMMEDIATE` and
    writes the balance and both ledger legs in one transaction, so once it is
    entered the outcome is all-or-nothing and there is nothing left to check.

    The one gap worth naming: the audit event is written after that transaction
    commits, by `credit()` itself, exactly as it is for every other caller. If
    that second write fails, the ECU exists and the ledger records where it came
    from, but the chain carries no `escrow_credit` row for it. The reconcile
    would still balance, because the reconcile reads the ledger. This function
    reports the audit event's sequence so a caller can see it landed, rather
    than assuming it did.
    """
    actor_id = (actor_id or "").strip()
    if not actor_id:
        raise ValueError("actor_id is required")
    if actor_id in ledger_entries.SYSTEM_ACCOUNTS:
        raise ValueError(
            f"{actor_id!r} is a ledger system account, not an actor. Crediting "
            "it would put ECU on the wrong side of the books."
        )
    try:
        amount = float(amount)
    except (TypeError, ValueError):
        raise ValueError(f"amount must be a number, got {amount!r}")
    if not amount > 0:
        raise ValueError(f"amount must be > 0, got {amount}")
    if amount > FUND_ESCROW_MAX_PER_GRANT:
        raise ValueError(
            f"{amount} ECU exceeds the per-grant cap of "
            f"{FUND_ESCROW_MAX_PER_GRANT} ECU (FUND_ESCROW_MAX_PER_GRANT)"
        )
    role = _registered_role(actor_id)
    if role is None:
        raise ValueError(
            f"{actor_id} is not a registered worker or consumer. Register "
            "first: a grant to an address nobody holds is ECU that can never "
            "move again, and it would sit in the ledger as a permanent "
            "unexplained issuance."
        )

    before = stake.balance(actor_id)
    after = stake.credit(actor_id, amount, reason=FUND_ESCROW_REASON)

    conn = sqlite3.connect(DB_PATH, timeout=10)
    try:
        head_seq, head_hash = ledger_entries.head(conn)
        row = conn.execute(
            "SELECT sequence, event_id FROM audit_log WHERE action_type = ? "
            "ORDER BY sequence DESC LIMIT 1", ("escrow_credit",)
        ).fetchone()
    finally:
        conn.close()

    return {
        "actor_id":       actor_id,
        "role":           role,
        "amount":         amount,
        "reason":         FUND_ESCROW_REASON,
        "balance_before": before,
        "balance_after":  after,
        "ledger_head":    f"{head_seq} {head_hash[:16]}",
        "audit_event":    f"{row[0]} {row[1][:16]}" if row else "NOT WRITTEN",
    }


def audit_fund_escrow_call_sites() -> List[str]:
    """
    Refuse to start if `fund_escrow` is reachable from anything but the CLI.

    The claim "no path for an actor to fund itself" is worth exactly as much as
    the thing that enforces it. A comment does not enforce it; the next person
    to want a convenient admin route will not read this one. So the claim is
    checked against the source on every boot, the same closed-by-default move
    as the write-route and action-type audits above.

    `_walk_with_function` skips the `if __name__ == "__main__":` block, so the
    CLI's own call is invisible to this scan and every other call site is not.
    A decorator on the definition is caught separately: `@app.post(...)` over
    `fund_escrow` would add no call site at all.
    """
    import ast

    path = os.path.abspath(__file__)
    try:
        tree = ast.parse(open(path, encoding="utf-8").read(), path)
    except (OSError, SyntaxError) as e:
        return [f"cannot read {os.path.basename(path)}: {e} — a scan that "
                f"skips what it cannot read is not a scan"]

    name = os.path.basename(path)
    problems = []
    for node, fn in _walk_with_function(tree, "<module>"):
        if isinstance(node, ast.Call):
            called = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
            if called == "fund_escrow":
                problems.append(f"{name}:{node.lineno} in {fn}()")
        elif (isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                and node.name == "fund_escrow" and node.decorator_list):
            problems.append(
                f"{name}:{node.lineno} fund_escrow carries "
                f"{len(node.decorator_list)} decorator(s) — a route decorator "
                f"here would publish the grant path"
            )
    return problems


# === Document seal, host-side only (2026-08-19) ============================
# What this ties down, and what it leaves loose.
#
# The whitepaper is the protocol's own account of itself, and nothing has ever
# tied a particular copy of it to a particular day. An edited copy handed to
# one reader would have been indistinguishable from the published one.
#
#     python3 hexis_bridge_v0.6.2.py --record-document <name>
#
# One `document_seal` event per deploy. That event becomes the chain head, and
# the head is what `seal_remote.py` signs from the machine holding the
# Foundation key — so there is no second signature scheme, no second key, and
# `verify_audit_chain.py` already checks the result without being told about
# any of this. A reader who wants to check the whitepaper needs nothing that
# is not already published.
#
# CANONICITY, which is the decision that makes it checkable at all
#
# The `.md` is the document. `whitepaper.html` is a viewer: a 9 KB JavaScript
# shell that fetches the `.md` at render time and contains no whitepaper. On
# 2026-08-19 a `grep` against that shell reported clean while the claim being
# searched for was live in the document underneath it. Sealing the viewer
# would seal the frame and not the painting. So the payload names exactly one
# artifact by URL: a reader verifying "the whitepaper" has one thing to fetch
# and one number to compare, and never has to work out which file counted.
#
# WHY THE HOST DOWNLOADS IT INSTEAD OF READING A FILE
#
# The hash is taken from the bytes this command pulls off the public URL —
# not from the working tree, and not from a number typed on the command line.
# Those three can disagree, and only one of them is what a reader receives.
# On 2026-08-17 the Pages build succeeded, the deploy failed with an upstream
# 503, and the site served the previous commit for a day while every local
# check passed. A hash taken from the tree that day would have described a
# document nobody could download, signed it, and been wrong in a way no
# verifier could catch.
#
# WHAT IT STILL CANNOT SHOW
#
# That the document already said this before the event was written. A seal
# starts a clock; it cannot wind one back. The same limit `verify_audit_chain.py`
# states about the chain, for the same reason: no anchor outside our control.
#
# No HTTP endpoint and no operator key, on the argument made above `fund_escrow`
# — shell access on this host is the trust boundary the system already has.
DOCUMENT_SEAL_ALLOWLIST = {
    "HEXIS_Whitepaper_v0.7.md":
        "https://hexisfoundation.org/HEXIS_Whitepaper_v0.7.md",
    # Added 2026-08-20, the day it was published. This is the record of what
    # this project claimed and got wrong, and it is the document with the most
    # reason of any of them to be provably unedited: a corrections file that
    # can be quietly revised is worse than none, because it reads as a promise
    # that it has not been. Its own entries argue exactly this about other
    # records — the point is not to be an exception to them.
    #
    # It changes more often than the whitepaper, so expect a seal per edit
    # rather than per release. That is the intended shape: an append-only
    # document sealed on every append.
    "CORRECTIONS.md":
        "https://hexisfoundation.org/CORRECTIONS.md",
    # Added 2026-08-21, the day it was published, for the same reason
    # CORRECTIONS.md was and one more. A list of open defects that can be
    # quietly edited is worse than no list, because it reads as a commitment
    # that it has not been edited — and the specific edit it invites is the
    # removal of whichever item has become embarrassing. Each entry carries a
    # closing condition, and a sealed copy is what makes a moved goalpost
    # visible.
    "OPEN.md":
        "https://hexisfoundation.org/OPEN.md",
}

# A URL argument instead of a name would let one slipped character record the
# hash of somebody else's page under our signature. The allowlist means the
# only thing the operator chooses is which of our documents to seal.

# Not a size check on the document — a check that we fetched a document at
# all. A 404 body, an error page or an empty response is small; hashing one
# and calling it the whitepaper is the failure this exists to refuse. The
# whitepaper is ~46 KB and the viewer that keeps getting mistaken for it is
# ~9 KB, so this sits below both.
DOCUMENT_SEAL_MIN_BYTES = 2048

# urllib's default User-Agent is "Python-urllib/X.Y", which Cloudflare answers
# with 403. See the Cloudflare section in DEPLOY.md; the same default has
# already turned away one of our own tools.
DOCUMENT_SEAL_USER_AGENT = "hexis-document-seal/1.0"


def record_document_seal(name: str) -> dict:
    """
    Download one allowlisted document and record the hash of what was served.

    Everything that can refuse, refuses before the write: an unknown name, a
    fetch that is not a clean 200, a body too small to be the document, or a
    hash identical to the newest one already recorded for this name. That last
    one is what makes "one seal per deploy" true rather than aspirational —
    running this twice on an unchanged document adds no event, because nothing
    was deployed and there is nothing new to attest.

    The event's `actor_id` is the document's name, so its whole history reads
    back from a URL a stranger can guess:

        GET /audit/HEXIS_Whitepaper_v0.7.md

    There is no operator identity in the payload, for the reason given above
    `fund_escrow`: the honest answer to "recorded by whom" is "whoever had a
    shell", and naming anyone would be a lie about how this is authorised.
    """
    import urllib.error
    import urllib.request

    name = (name or "").strip()
    url = DOCUMENT_SEAL_ALLOWLIST.get(name)
    if url is None:
        known = ", ".join(sorted(DOCUMENT_SEAL_ALLOWLIST)) or "(none)"
        raise ValueError(
            f"{name!r} is not an allowlisted document. Known: {known}")

    req = urllib.request.Request(
        url, headers={"Accept": "*/*", "User-Agent": DOCUMENT_SEAL_USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            status = resp.status
            content_type = resp.headers.get("Content-Type", "")
            body = resp.read()
    except urllib.error.HTTPError as e:
        raise ValueError(f"{url} returned HTTP {e.code} — nothing recorded")
    except urllib.error.URLError as e:
        raise ValueError(f"could not fetch {url}: {e.reason} — nothing recorded")

    if status != 200:
        raise ValueError(f"{url} returned HTTP {status} — nothing recorded")
    if len(body) < DOCUMENT_SEAL_MIN_BYTES:
        raise ValueError(
            f"{url} returned {len(body)} bytes, below the "
            f"{DOCUMENT_SEAL_MIN_BYTES}-byte floor. That is an error page or a "
            f"viewer shell, not the document — nothing recorded"
        )

    digest = hashlib.sha256(body).hexdigest()

    conn = sqlite3.connect(DB_PATH, timeout=10)
    try:
        prev = conn.execute(
            "SELECT sequence, event_id, payload FROM audit_log "
            "WHERE action_type = ? AND actor_id = ? "
            "ORDER BY sequence DESC LIMIT 1", ("document_seal", name)
        ).fetchone()
    finally:
        conn.close()

    if prev is not None:
        try:
            prev_sha = json.loads(prev[2]).get("sha256")
        except (TypeError, ValueError):
            prev_sha = None
        if prev_sha == digest:
            raise ValueError(
                f"{name} is byte-identical to the copy recorded at sequence "
                f"{prev[0]} ({digest[:16]}…). One seal per deploy: nothing was "
                f"deployed, so there is nothing new to attest — nothing recorded"
            )

    fetched_at = datetime.now(timezone.utc)
    res = audit.log_action(
        actor_id=name,
        action_type="document_seal",
        payload={
            # Named, not derived: whoever checks this should not have to know
            # how our site is laid out to find the bytes we hashed.
            "url": url,
            "sha256": digest,
            "bytes": len(body),
            "content_type": content_type,
            "fetched_at": fetched_at.isoformat(),
            # Said in the payload as well as in the docs, because the payload
            # is the part that travels. A reader with only this row should
            # still know which file counts and what the row does not prove.
            "canonical": "the .md at this url is the document; any .html view "
                         "of it is a viewer and is not covered by this hash",
            "attests": "these bytes were served from this url at fetched_at",
            "does_not_attest": "that the document said this any earlier",
            # Algorithm first, commands second, and both platforms named.
            # Until 2026-08-20 this field read `curl -s {url} | sha256sum`.
            # `sha256sum` is coreutils; stock macOS and the BSDs do not have
            # it, so the instruction answered "command not found" for a whole
            # class of reader — and an instruction that does not run has not
            # been followed. Rows 295 and 313 carry the old wording and are
            # not edited; they are signed.
            #
            # The general rule this is an instance of: a signed payload must
            # never depend on one operating system's binary name. The
            # algorithm is the fact. A command is one way to compute it.
            "check": "sha256 of the bytes served at url",
            "how_to_check": (
                f"sha256 of the bytes at url. "
                f"macOS/BSD: curl -s {url} | shasum -a 256 . "
                f"Linux/coreutils: curl -s {url} | sha256sum"
            ),
        },
    )

    return {
        "document":     name,
        "url":          url,
        "sha256":       digest,
        "bytes":        len(body),
        "fetched_at":   fetched_at.isoformat(),
        "previous":     (f"sequence {prev[0]}" if prev is not None else "none"),
        "event_id":     res["event_id"],
        "sequence":     res["sequence"],
        "signature":    "NOT YET — run seal_remote.py to sign this head",
    }


# --- Successor key designation (2026-08-21) --------------------------------
#
# One key seals this chain. It is held by one person on one laptop. That is the
# property which makes the seal worth anything and the property which makes
# this event necessary.
#
# What this records is a DESIGNATION and not a handover. It names which key
# would be the successor, and it grants that key nothing at all. Signatures by
# the designated key are not valid seals and must not be accepted by anything.
#
# Why record it now, when it confers no power: because the strongest attack on
# any succession scheme is somebody turning up after the fact and announcing
# their own successor key. A designation fixed at a date that a Bitcoin block
# proves defeats that, and it defeats it before any activation machinery
# exists. The anchoring layer built yesterday is exactly what makes the date
# credible — without it, a designation would be the operator's claim about the
# operator's own timeline.
#
# What is DELIBERATELY not decided here: how the designated key ever becomes
# active. Four mechanisms were assessed and none of them is adoptable at n=1
# (SUCCESSOR_PROPOSAL.md, private). Recording an undecided mechanism as
# undecided is honest; leaving it unmentioned lets a later reader assume one
# existed.
#
#     python3 hexis_bridge_v0.6.2.py --record-successor <fingerprint>
#
# CLI-only, on the argument above `fund_escrow`.

# sha256 of the 32 raw bytes of an Ed25519 public key, RFC 8032 encoding,
# lowercase hex. Spelled out because a hash commitment binds nothing if the
# input is ambiguous: sha256 of a PEM file, of a DER blob and of the raw key
# are three different numbers, and a successor arriving in ten years must be
# able to reproduce exactly one of them. DEPLOY.md carries the command.
SUCCESSOR_FINGERPRINT_ALGORITHM = (
    "sha256 of the 32 raw bytes of the ed25519 public key (RFC 8032), "
    "lowercase hex"
)
SUCCESSOR_ACTOR = "foundation_key_succession"


# sha256 of nothing. It is a valid-looking 64-hex string and it is what a
# broken pipeline produces: on 2026-08-21 the documented one-liner for this
# emitted zero bytes under LibreSSL and `shasum` hashed them without complaint.
# Refused by name so the failure cannot be committed to the chain.
EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()

# The unit file sets HEXIS_SEAL_PUBKEY_PATH; a shell over ssh does not, and
# this function is called from the CLI. Falling back to the conventional
# location beside this module is what makes the check work in both.
SEAL_PUBKEY_FALLBACK = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "foundation_seal.pub")


def _current_seal_key_fingerprint() -> Optional[str]:
    """Fingerprint of the seal key in force, by the same rule, or None.

    None means *could not determine*, never *no match*. The caller must treat
    it as a refusal. See the comment at the call site — a fail-open version of
    this cost two junk rows in the production chain on the day it was written.
    """
    from cryptography.hazmat.primitives import serialization

    def _fp(pub):
        raw = pub.public_bytes(serialization.Encoding.Raw,
                               serialization.PublicFormat.Raw)
        return hashlib.sha256(raw).hexdigest() if len(raw) == 32 else None

    try:
        import hexis_audit as _audit_mod
        pub, _pem, err = _audit_mod.load_published_pubkey()
        if not err and pub is not None:
            return _fp(pub)
    except Exception:                                         # noqa: BLE001
        pass
    try:
        with open(SEAL_PUBKEY_FALLBACK, "rb") as f:
            return _fp(serialization.load_pem_public_key(f.read()))
    except Exception:                                         # noqa: BLE001
        return None


def record_successor_designation(fingerprint: str) -> dict:
    """Designate a successor seal key by fingerprint. Grants nothing."""
    fingerprint = (fingerprint or "").strip().lower().replace(":", "")
    if not re.fullmatch(r"[0-9a-f]{64}", fingerprint):
        raise ValueError(
            f"{fingerprint[:80]!r} is not a 64-hex fingerprint. Expected "
            f"{SUCCESSOR_FINGERPRINT_ALGORITHM}")

    if fingerprint == EMPTY_SHA256:
        raise ValueError(
            "that is sha256 of the empty string, which is what a broken "
            "fingerprint command produces rather than a key. Use "
            "key_fingerprint.py — nothing recorded")

    # Designating the key already in force would look identical to a real
    # designation and would mean nothing. It is the one error this host can
    # actually detect, because verifying seals is the one thing it uses the
    # public key for.
    #
    # FAIL CLOSED. The first version of this read `if current is not None and
    # current == fingerprint`, which skips the check whenever the key cannot
    # be read — and under the CLI it never could, because the unit file sets
    # HEXIS_SEAL_PUBKEY_PATH and an ssh shell does not. It wrote two junk rows
    # into the production chain before anybody noticed, and rows do not come
    # back out. **A check that cannot run must refuse, not shrug**; that
    # sentence was written into backup_hexis.sh the day before and violated
    # here the day after.
    current = _current_seal_key_fingerprint()
    if current is None:
        raise ValueError(
            "cannot read the seal public key, so cannot check that this "
            f"fingerprint is not the key already in force. Set "
            f"HEXIS_SEAL_PUBKEY_PATH or put the key at {SEAL_PUBKEY_FALLBACK}. "
            f"Refusing rather than skipping the check — nothing recorded")
    if current == fingerprint:
        raise ValueError(
            "that fingerprint is the seal key currently in force. A key cannot "
            "succeed itself — nothing recorded")

    conn = sqlite3.connect(DB_PATH, timeout=10)
    try:
        prior = conn.execute(
            "SELECT sequence, payload FROM audit_log "
            "WHERE action_type = ? AND actor_id = ? ORDER BY sequence DESC",
            ("successor_designation", SUCCESSOR_ACTOR)).fetchall()
        voids = conn.execute(
            "SELECT sequence, payload FROM audit_log "
            "WHERE action_type = ? AND actor_id = ?",
            ("successor_designation_void", SUCCESSOR_ACTOR)).fetchall()
    finally:
        conn.close()

    # A value that was retracted is a value somebody already decided was
    # wrong. Re-committing it is far more likely to be a copy-paste out of the
    # history than an intention.
    for seq, payload in voids:
        try:
            parsed = json.loads(payload)
        except (TypeError, ValueError):
            continue
        if parsed.get("voided_fingerprint") == fingerprint:
            raise ValueError(
                f"that fingerprint was voided at sequence {seq} "
                f"({parsed.get('reason', 'no reason recorded')!r}) — nothing "
                f"recorded")

    supersedes = None
    for seq, payload in prior:
        try:
            parsed = json.loads(payload)
        except (TypeError, ValueError):
            continue
        if parsed.get("fingerprint") == fingerprint:
            raise ValueError(
                f"this fingerprint is already designated, at sequence {seq}. "
                f"Re-running is safe and adds nothing")
        if supersedes is None:
            supersedes = seq

    res = audit.log_action(
        actor_id=SUCCESSOR_ACTOR,
        action_type="successor_designation",
        payload={
            "fingerprint": fingerprint,
            "fingerprint_algorithm": SUCCESSOR_FINGERPRINT_ALGORITHM,
            "supersedes_sequence": supersedes,
            "designates": (
                "the key that would be treated as this project's successor "
                "seal key, if and when an activation mechanism is chosen and "
                "published"),
            # The load-bearing sentence. Everything else here is context.
            "grants": (
                "NOTHING. this event confers no authority whatsoever. a "
                "signature by the designated key is not a valid seal and must "
                "not be accepted by any verifier. the seal key in force is "
                "unchanged and is the only one"),
            "activation_mechanism": (
                "undecided. none exists. deliberation is deliberately not "
                "published while it is deliberation; the designation is "
                "published because it is a commitment"),
            "attests": (
                "that the operator declared this fingerprint as the designated "
                "successor at this timestamp, and — once this row is anchored "
                "— that the declaration is at least as old as a bitcoin block"),
            "does_not_attest": (
                "that this host ever saw the key, or that any key with this "
                "fingerprint exists. the host cannot check a fingerprint "
                "against a key it has never held. this row records a "
                "declaration, and the declaration is the thing being dated"),
            "how_to_check_at_activation": (
                "the successor publishes the full ed25519 public key; sha256 "
                "of its 32 raw bytes must equal this fingerprint, and this row "
                "must predate the dispute — which the ots_anchor covering it "
                "establishes against bitcoin"),
            "open_limitation": (
                "this provides no revocation. if the seal key in force is "
                "STOLEN rather than lost, nothing here helps: the thief seals "
                "validly and this row cannot say otherwise. revocation needs a "
                "threshold scheme and independent holders, neither of which "
                "exists at one person. accepted, and recorded as open"),
        },
    )

    return {
        "fingerprint": fingerprint,
        "supersedes_sequence": supersedes,
        "sequence": res["sequence"],
        "event_id": res["event_id"],
    }


def record_successor_void(sequence: int, reason: str) -> dict:
    """Retract a `successor_designation`. Appends; never edits.

    Needed the day designations shipped, which is the argument for it existing
    at all: a fingerprint is 64 characters typed by a human under ceremony
    conditions, and the first two ever written on this chain were both wrong.
    There has to be a way to say so that does not involve touching a row.

    A void is not a deletion. Sequence 350 still says what it said, still
    hashes the same, still verifies. The void is a later row that a reader
    encounters after it and which says: that one was a mistake, here is why.
    That is what an append-only record can offer instead of an eraser, and it
    is strictly more informative than an eraser would be.
    """
    try:
        sequence = int(sequence)
    except (TypeError, ValueError):
        raise ValueError(f"{sequence!r} is not a sequence number")
    reason = (reason or "").strip()
    if len(reason) < 12:
        raise ValueError(
            "a void needs a reason, and a short one is not a reason. Say what "
            "was wrong with the designation — nothing recorded")

    conn = sqlite3.connect(DB_PATH, timeout=10)
    try:
        row = conn.execute(
            "SELECT action_type, actor_id, payload FROM audit_log "
            "WHERE sequence = ?", (sequence,)).fetchone()
        voids = conn.execute(
            "SELECT sequence, payload FROM audit_log "
            "WHERE action_type = ? AND actor_id = ?",
            ("successor_designation_void", SUCCESSOR_ACTOR)).fetchall()
    finally:
        conn.close()

    if row is None:
        raise ValueError(f"no event at sequence {sequence} — nothing recorded")
    if row[0] != "successor_designation" or row[1] != SUCCESSOR_ACTOR:
        raise ValueError(
            f"sequence {sequence} is a {row[0]!r}, not a successor "
            f"designation. This voids designations and nothing else")
    for seq, payload in voids:
        try:
            if json.loads(payload).get("voided_sequence") == sequence:
                raise ValueError(
                    f"sequence {sequence} was already voided, at sequence "
                    f"{seq} — nothing recorded")
        except (TypeError, ValueError) as e:
            if "already voided" in str(e):
                raise
            continue

    try:
        voided_fp = json.loads(row[2]).get("fingerprint")
    except (TypeError, ValueError):
        voided_fp = None

    res = audit.log_action(
        actor_id=SUCCESSOR_ACTOR,
        action_type="successor_designation_void",
        payload={
            "voided_sequence": sequence,
            "voided_fingerprint": voided_fp,
            "reason": reason,
            "effect": (
                "the designation at voided_sequence is retracted and must not "
                "be treated as naming any successor key. it is not deleted, "
                "because nothing in this chain is: it still hashes, still "
                "verifies, and is still part of the history"),
            "attests": "that the operator retracted that designation, at this "
                       "timestamp",
            "does_not_attest": "anything about the key the voided row named",
        },
    )
    return {"voided_sequence": sequence, "voided_fingerprint": voided_fp,
            "sequence": res["sequence"], "event_id": res["event_id"]}


def audit_successor_designation_call_sites() -> List[str]:
    return (audit_cli_only_call_sites("record_successor_designation")
            + audit_cli_only_call_sites("record_successor_void"))


def audit_cli_only_call_sites(target: str) -> List[str]:
    """
    Refuse to start if `target` is reachable from anything but the CLI — the
    same scan, and the same argument, as `fund_escrow` above.

    Written once and parameterised rather than copied a third time. Copying it
    per function is the shape that let twelve audit action types disappear: a
    fix per instance, with the pattern still in place. `_walk_with_function`
    skips the `if __name__ == "__main__":` block, so the CLI's own call is
    invisible to this and every other caller is not.
    """
    import ast

    path = os.path.abspath(__file__)
    try:
        tree = ast.parse(open(path, encoding="utf-8").read(), path)
    except (OSError, SyntaxError) as e:
        return [f"cannot read {os.path.basename(path)}: {e} — a scan that "
                f"skips what it cannot read is not a scan"]

    name = os.path.basename(path)
    problems = []
    for node, fn in _walk_with_function(tree, "<module>"):
        if isinstance(node, ast.Call):
            called = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
            if called == target:
                problems.append(f"{name}:{node.lineno} in {fn}()")
        elif (isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                and node.name == target and node.decorator_list):
            problems.append(
                f"{name}:{node.lineno} {target} carries "
                f"{len(node.decorator_list)} decorator(s) — a route decorator "
                f"here would publish the write path"
            )
    return problems


def audit_document_seal_call_sites() -> List[str]:
    return audit_cli_only_call_sites("record_document_seal")


def audit_ots_anchor_call_sites() -> List[str]:
    return audit_cli_only_call_sites("record_ots_anchor")


# --- Bitcoin anchors (2026-08-20) ------------------------------------------
#
# The chain has hash linkage and a daily Ed25519 seal. Both are ours. Neither
# answers the question `verify_audit_chain.py` has always said it cannot:
# whether the events existed before the first seal ran. A signature starts a
# clock; it cannot wind one back.
#
# OpenTimestamps puts a head hash into a Bitcoin block, and the block's
# timestamp is not ours. That is the whole of what this event records.
#
# The stamping and the proof live on the laptop and in the public repository —
# NOT here. This host runs no calendar client and gains no dependency: it holds
# the chain, and the fewer moving parts on it the better. The consequence is
# stated plainly in the payload rather than glossed: **this event is a pointer,
# not a proof.** The host does not and cannot verify the Bitcoin attestation.
# The `.ots` file is the evidence, it is published, and a reader checks it with
# `ots verify` against a blockchain neither they nor we control. An event that
# claimed more than that would be the operator vouching for the operator, which
# is the arrangement this whole layer exists to escape.
#
#     python3 hexis_bridge_v0.6.2.py --record-ots-anchor <event_id> <height>
#                                    <proof_name> <proof_sha256>
#
# CLI-only, on the argument above `fund_escrow`.
OTS_PROOF_NAME_RE = re.compile(r"^seal-\d{6}-[0-9a-f]{16}\.txt\.ots$")
OTS_PROOF_BASE_URL = "https://hexisfoundation.org/ots/"


def record_ots_anchor(anchored_event_id: str, block_height: int,
                      proof_name: str, proof_sha256: str) -> dict:
    """Record that one chain head has been committed to a Bitcoin block."""
    anchored_event_id = (anchored_event_id or "").strip().lower()
    proof_sha256 = (proof_sha256 or "").strip().lower()
    proof_name = (proof_name or "").strip()

    if not re.fullmatch(r"[0-9a-f]{64}", anchored_event_id):
        raise ValueError(f"{anchored_event_id[:80]!r} is not a 64-hex event_id")
    if not re.fullmatch(r"[0-9a-f]{64}", proof_sha256):
        raise ValueError(f"{proof_sha256[:80]!r} is not a 64-hex sha256")
    if not OTS_PROOF_NAME_RE.match(proof_name):
        raise ValueError(
            f"{proof_name!r} is not a proof filename of the published shape "
            f"seal-<6 digits>-<16 hex>.txt.ots")
    try:
        block_height = int(block_height)
    except (TypeError, ValueError):
        raise ValueError(f"{block_height!r} is not a block height")
    if block_height <= 0:
        raise ValueError(f"block height {block_height} is not a block height")

    conn = sqlite3.connect(DB_PATH, timeout=10)
    try:
        row = conn.execute(
            "SELECT sequence FROM audit_log WHERE event_id = ?",
            (anchored_event_id,)).fetchone()
        prev = conn.execute(
            "SELECT sequence, payload FROM audit_log "
            "WHERE action_type = ? AND actor_id = ? ORDER BY sequence DESC",
            ("ots_anchor", "audit_chain")).fetchall()
    finally:
        conn.close()

    # An anchor for a head this chain does not contain is either a typo or a
    # claim about somebody else's chain. Both are worth refusing rather than
    # storing: the payload would look identical to a real one.
    if row is None:
        raise ValueError(
            f"no event with id {anchored_event_id[:16]}… exists in this chain — "
            f"nothing recorded")
    anchored_sequence = int(row[0])

    for seq, payload in prev:
        try:
            parsed = json.loads(payload)
        except (TypeError, ValueError):
            continue
        if parsed.get("anchored_event_id") == anchored_event_id:
            raise ValueError(
                f"sequence {anchored_sequence} is already anchored, at sequence "
                f"{seq}. One anchor per head — re-running is safe and adds "
                f"nothing")

    res = audit.log_action(
        actor_id="audit_chain",
        action_type="ots_anchor",
        payload={
            "anchored_event_id": anchored_event_id,
            "anchored_sequence": anchored_sequence,
            "bitcoin_block_height": block_height,
            "proof_file": proof_name,
            "proof_url": OTS_PROOF_BASE_URL + proof_name,
            "proof_sha256": proof_sha256,
            # The head is hash-linked, so anchoring it anchors everything
            # before it. Said here because a reader holding one row should not
            # have to derive it.
            "covers": f"sequences 0..{anchored_sequence} inclusive, because the "
                      f"chain is hash-linked and this head commits to all of them",
            "attests": "the bytes of anchored_event_id existed before the "
                       "bitcoin block named here was mined",
            "does_not_attest": (
                "that this host verified the bitcoin proof — it did not, and "
                "runs no calendar client. this row is a pointer to a proof "
                "published at proof_url. the proof is the evidence"),
            "check": "sha256 of the file at proof_url equals proof_sha256; then "
                     "the OpenTimestamps proof commits anchored_event_id to the "
                     "named bitcoin block",
            # Corrected 2026-08-23. This field said "`ots verify` exits 0 for a
            # pending proof too", which is false — it exits 1 — and false in the
            # direction that makes a healthy proof look broken. The wrong
            # sentence was measured off the end of a pipe, so `$?` reported the
            # exit code of `tail`. It is now in three chain rows (345, 388, 389)
            # and cannot be removed from any of them; only the rows written from
            # here on can be right. `--no-bitcoin` is spelled out for the same
            # reason the sha256sum note is: without it a reader who runs no
            # bitcoind gets a cookie-file error and rc 1, which reads as our
            # proof being bad rather than their node being absent.
            #
            # Extended 2026-08-27. The instructions told a reader how to run
            # the check but not what the check buys them, and `--no-bitcoin`
            # silently swaps the trust anchor: instead of validating the chain,
            # the reader compares a merkle root against whatever a website says
            # is in that block. That is still worth doing — it catches a forged
            # or mismatched proof — but a reader must not walk away believing
            # they verified against Bitcoin when they verified against an
            # explorer. Every other verify instruction in this repo states its
            # limit; this one now does too.
            "how_to_check": (
                f"fetch {OTS_PROOF_BASE_URL}{proof_name} and the .txt beside it, "
                f"then `ots --no-bitcoin verify {proof_name}`. read the block "
                f"height out of the output, never the exit code: `ots verify` "
                f"exits 1 for a pending proof, for a broken proof, and for a "
                f"missing bitcoin node alike. `ots info {proof_name}` exits 0, "
                f"needs no node and no network, and a "
                f"BitcoinBlockHeaderAttestation(N) line in it is the "
                f"confirmation. what that proves: the proof commits these bytes "
                f"to the merkle root a block explorer publishes for that height, "
                f"so a forged or altered proof fails. what it does not prove: "
                f"that the block is real — `--no-bitcoin` trusts the explorer, "
                f"not the bitcoin network, and validating the chain itself "
                f"needs a full node (`ots verify` against bitcoind). check the "
                f"height on two independent explorers to weaken that trust, or "
                f"run a node to remove it"),
        },
    )

    return {
        "anchored_event_id": anchored_event_id,
        "anchored_sequence": anchored_sequence,
        "bitcoin_block_height": block_height,
        "proof_file": proof_name,
        "sequence": res["sequence"],
        "event_id": res["event_id"],
    }


# --- Stale-lock sweep (2026-08-12) -----------------------------------------
# Gap #3: a counterparty vanishes and the other side's stake stays locked for
# good. `expire_stale()` fixes that, and used to be reachable at
# POST /stake/expire — an unscoped sweep over everyone's locks that no
# caller's signature could authorise. Gating it closed the hole and left the
# gap open, because nothing called the method any more.
#
# This closes both: the sweep runs in-process on a timer. No endpoint, no
# operator key, nothing to authenticate, and nothing anyone outside can
# trigger.
STAKE_SWEEP_INTERVAL_S = 6 * 3600


async def stake_expiry_sweep():
    """
    Release locks past their TTL, every six hours, and say so in the chain.

    Sleeps before the first run rather than sweeping at boot. `Restart=always`
    with `RestartSec=5` means a bridge that cannot stay up restarts endlessly,
    and a money-moving sweep on the startup path would run every five seconds
    for as long as that lasted. Nothing here is urgent to the second: the TTL
    is an hour at minimum, so a lock that needs sweeping can wait for the next
    tick.

    One audit event per sweep, including the sweeps that expire nothing. A
    sweep that only records itself when it acts is indistinguishable from a
    sweep that stopped running, and the second of those is exactly the failure
    this is here to prevent. `expire_stale()` writes its own per-lock events;
    this one is the proof the sweep itself happened.
    """
    while True:
        await asyncio.sleep(STAKE_SWEEP_INTERVAL_S)
        started = time.time()
        try:
            # expire_stale is synchronous SQLite over an unbounded number of
            # locks. Off the event loop, or it stalls every request while it
            # runs.
            result = await asyncio.to_thread(stake.expire_stale)
            payload = {
                "expired_count": result["expired_count"],
                "ttl_s":         result["ttl_s"],
                "expired":       result["expired"],
                "duration_s":    round(time.time() - started, 3),
                "interval_s":    STAKE_SWEEP_INTERVAL_S,
            }
            STATE.log(f"Stale-lock sweep: expired {result['expired_count']} lock(s).")
        except asyncio.CancelledError:
            raise
        except Exception as e:
            # A failed sweep is recorded too. Silence here would look exactly
            # like a quiet system.
            payload = {"error": f"{type(e).__name__}: {e}",
                       "duration_s": round(time.time() - started, 3),
                       "interval_s": STAKE_SWEEP_INTERVAL_S}
            STATE.log(f"Stale-lock sweep failed: {e}", level="warn")
        try:
            audit.log_action(actor_id="system", counterparty_id=None,
                             action_type="stake_expiry_sweep", payload=payload)
        except Exception as e:
            STATE.log(f"Stale-lock sweep audit write failed: {e}", level="warn")


app.include_router(get_dispute_router(stake, write_deps={
    # The party raising the dispute is the actor acting.
    "/job/{job_id}/dispute": [Depends(require_signature(
        lambda request, payload: payload.get("raised_by")))],
}))
app.include_router(get_severity_router(
    severity, stake,
    # The actor repaying its own debt is the actor acting.
    write_dependencies=[Depends(require_signature(
        lambda request, payload: payload.get("actor_id")))],
))

# === Geo-Economics + Hardware Floor (wired 2026-07-24) ===
# "Nang luong & timing la cot loi" — C dong do bang joules/24h,
# san phan cung do bang sustained timing. HHI cong khai.
geo = GeoEconomics(db_path=DB_PATH,
                   audit_fn=_stake_audit_fn)
bench = BenchmarkGate(db_path=DB_PATH)
app.include_router(get_geo_router(geo))


# === Proof-of-Sampling (PoSP, wired 2026-07-24) ===
# Lop verify #4: validator node re-execute σ% job. Clawback post-hoc khi fraud.
# GATE: sigma=0 mac dinh -> zero anh huong toi khi UPDATE sampling_config.
def _posp_reward(validator_id, _country, fee, audit_id, caught):
    # auditor duoc tra audit_fee (escrow) + mine HEXIS (audit la behavior §5.1)
    try:
        # The only issuance path left now that /stake/credit is gone. Named,
        # so the audit chain says where the ECU came from.
        stake.credit(validator_id, fee, reason=f"posp_audit_fee:{audit_id}")
    except Exception:
        pass
    vc = STATE.workers.get(validator_id, {}).get("country", "??")
    try:
        STATE.mine_hexis_for_job(
            worker_id=validator_id, worker_country=vc,
            job_value_ecu=fee, job_id=f"audit_{audit_id}", sensitivity_tier=1)
    except Exception:
        pass
    STATE.log(f"PoSP reward {validator_id[:16]}...: {fee} ECU + HEXIS "
              f"({'caught fraud' if caught else 'verified honest'})", level="hexis")

def _posp_clawback(worker_id, worker_country, job_id, job_value, tier, wr, vr):
    # fraud confirmed: doi lai fee tu escrow + severity classify (quarantine/debt)
    amt = 0.0
    try:
        bal = stake.balance(worker_id)
        amt = min(float(job_value), bal)
        if amt > 0:
            stake.transfer(worker_id, "SEVERITY_RESERVE", amt)
    except Exception:
        pass
    try:
        severity.record_incident({
            "job_id": job_id, "actor_id": worker_id, "role": "worker",
            "sensitivity_tier": tier, "job_value_ecu": job_value,
            "slashed_amount": amt, "reason": "posp_fraud: workload mismatch"})
    except Exception:
        pass
    STATE.log(f"PoSP FRAUD {worker_id[:16]}... job {job_id[:16]}...: "
              f"clawed {amt} ECU + severity classify (worker!=validator result)",
              level="warn")

def _posp_same_pair(a, b):
    """R4 pair-check (patch #12, v0.8): True neu a & b TUNG la doi tac.

    Validator khong duoc audit worker minh tung giao dich — cap quen nhau la
    cap co the thong dong. stake.have_transacted() soi pair_activity CA HAI
    chieu (validator co the tung dong vai consumer HOAC worker); window 0 =
    "da tung" (nghiem nhat, khong quen).

    Loi tra ve True (fail-closed): khong xac dinh duoc quan he -> tu choi
    assign, audit o lai PENDING. Tha bo lo mot audit con hon giao cho ke
    co the thong dong.
    """
    try:
        return stake.have_transacted(a, b)
    except Exception as e:
        STATE.log(f"posp pair-check failed for {str(a)[:12]}/{str(b)[:12]}: {e} "
                  f"- refusing assignment (fail-closed)", level="warn")
        return True

AUDIT_STAKE_POOL = "AUDIT_STAKE_POOL"   # escrow noi bo giu stake validator


def _posp_validator_stake(action, validator_id, amount, audit_id):
    """Stake cua validator khi nhan audit (patch #13, v0.8 audit bilateral).

    lock    -> tru escrow validator, giu o AUDIT_STAKE_POOL. False neu thieu
               tien (sampling se tu choi giao audit, tra ve 402).
    release -> hoan lai validator (verdict dung, hoac audit khong nga ngu).
    slash   -> chuyen sang SEVERITY_RESERVE (verdict lech dong thuan).
    """
    try:
        amount = float(amount)
        if amount <= 0:
            return True
        if action == "lock":
            if stake.balance(validator_id) < amount:
                return False
            stake.transfer(validator_id, AUDIT_STAKE_POOL, amount)
            STATE.log(f"PoSP validator {validator_id[:16]}... khoa {amount} ECU "
                      f"cho audit {audit_id[:8]}", level="hexis")
            return True
        if action == "release":
            stake.transfer(AUDIT_STAKE_POOL, validator_id, amount)
            return True
        if action == "slash":
            stake.transfer(AUDIT_STAKE_POOL, "SEVERITY_RESERVE", amount)
            STATE.log(f"PoSP validator SLASH {validator_id[:16]}...: {amount} ECU "
                      f"(verdict lech dong thuan, audit {audit_id[:8]})", level="warn")
            return True
    except Exception as e:
        STATE.log(f"PoSP validator stake {action} that bai "
                  f"({validator_id[:12]}, {amount}): {e}", level="warn")
        return False
    return False


def _posp_independent(validator_id: str, worker_id: str):
    """
    R4 (2026-08-17): is this validator measurably independent of this worker?

    Replaces `validator_country != worker_country`. That gate read a 2-3
    character string the actor typed at registration, which nothing verifies —
    the same trusted input as the Context multiplier, guarding something more
    dangerous. Two identities held by one person, declaring two countries,
    passed it completely. Measured over the whole history, it never refused
    anyone: 2 audits ever, both already cross-country, 0 blocked.

    Returns (ok, reason). Fail-closed on error, like `_posp_same_pair`: an
    unanswerable question about a relationship is not a yes.

    Three signals, and they are not of equal strength — which is stated here
    rather than hidden behind one boolean:

      1. **Money between them.** Has any ECU ever moved between these two
         accounts? Read from `ledger_entries`, which is what makes this
         answerable at all — before 2026-08-16 escrow was a running total with
         no record of movements, so this question had no answer. Hard refusal.
      2. **Shared transaction history.** `_posp_same_pair`, already in place.
         Checked by the engine itself, so it is not repeated here.
      3. **Benchmark fingerprint.** Weak, and treated as weak: two identical
         laptops produce identical timings, so proximity is evidence of the
         same *kind* of machine, not the same machine. It is recorded and
         reported, and it does not refuse on its own. It cannot yet do
         anything at all — `elapsed_s` only started being written today, so
         no actor registered before now has one.
    """
    try:
        if validator_id == worker_id:
            return False, "same actor"

        # (1) Has ECU ever moved between them, in either direction?
        conn = sqlite3.connect(DB_PATH, timeout=10)
        try:
            row = conn.execute(
                "SELECT COUNT(*) FROM ledger_entries a "
                "JOIN ledger_entries b ON a.op_id = b.op_id "
                "WHERE a.account = ? AND b.account = ? "
                "  AND a.actor_id = ? AND b.actor_id = ? AND a.delta * b.delta < 0",
                (ledger_entries.ESCROW, ledger_entries.ESCROW,
                 validator_id, worker_id),
            ).fetchone()
        finally:
            conn.close()
        if row and row[0]:
            return False, "ECU has moved between these accounts"

        # (3) Benchmark fingerprint, when both have one. Reported, not decisive.
        try:
            fv, fw = bench.fingerprint(validator_id), bench.fingerprint(worker_id)
            if fv and fw and fv["elapsed_s"] > 0 and fw["elapsed_s"] > 0:
                rel = abs(fv["elapsed_s"] - fw["elapsed_s"]) / max(
                    fv["elapsed_s"], fw["elapsed_s"])
                if rel < 0.01:
                    # Under 1% apart on a 20-60s sustained run. Not proof, and
                    # deliberately not a refusal — it goes in the chain so that
                    # a pattern of them can be looked at later by someone who
                    # has more than the two data points this network has now.
                    STATE.log(
                        f"PoSP: benchmark timings within {rel:.1%} for "
                        f"{validator_id[:12]}/{worker_id[:12]} — assigned anyway, "
                        f"recorded for review", level="warn")
        except Exception:
            pass                      # a weak signal must not fail the check

        return True, "independent"
    except Exception as e:
        return False, f"independence unknown: {e}"


sampling = SamplingEngine(
    db_path=DB_PATH,
    audit_fn=_stake_audit_fn,
    reward_fn=_posp_reward,
    clawback_fn=_posp_clawback,
    same_pair_fn=_posp_same_pair,
    stake_fn=_posp_validator_stake,
    independence_fn=_posp_independent,
)
app.include_router(get_sampling_router(sampling))


class AuditClaimReq(BaseModel):
    validator_id: str


class AuditVerdictReq(BaseModel):
    validator_id: str
    result: str


@app.post("/audit/claim", dependencies=[Depends(require_signature(
    # The validator claiming the audit is the actor acting.
    lambda request, payload: payload.get("validator_id")))])
async def audit_claim(req: AuditClaimReq):
    """Validator node nhan 1 audit job (R4: cheo vung, khong tu audit)."""
    w = STATE.workers.get(req.validator_id)
    if not w:
        raise HTTPException(status_code=404, detail="validator not registered as worker")
    if not w.get("benchmark_passed"):
        raise HTTPException(status_code=403, detail="validator must pass hardware benchmark first")
    country = w.get("country", "??")
    a = sampling.claim(req.validator_id, country)
    if not a:
        return {"status": "no_audit_available"}
    return a


@app.post("/audit/{audit_id}/verdict", dependencies=[Depends(require_signature(
    lambda request, payload: payload.get("validator_id")))])
async def audit_verdict(audit_id: str, req: AuditVerdictReq):
    """Validator nop ket qua re-run. Match->pass+reward; mismatch->clawback."""
    return sampling.submit_verdict(audit_id, req.validator_id, req.result)


class BenchmarkSubmitReq(BaseModel):
    results: List[str]


@app.post("/worker/{address}/benchmark", dependencies=[Depends(require_signature(
    lambda request, payload: request.path_params.get("address")))])
async def worker_benchmark(address: str, req: BenchmarkSubmitReq):
    """Nop ket qua benchmark. Pass -> worker duoc nhan job."""
    # verify spot-check ~5-10s sha256 -> chay trong executor, khong block loop
    out = await asyncio.get_event_loop().run_in_executor(
        None, bench.verify, address, req.results
    )   # raise 4xx neu fail
    w = STATE.workers.get(address)
    if w is not None:
        w["benchmark_passed"] = True
    await write_queue.submit(
        "UPDATE workers SET benchmark_passed=1 WHERE address=?", (address,)
    )
    STATE.log(f"Benchmark PASSED for {address[:16]}... "
              f"({out['elapsed_s']}s) — worker active")
    return out

# === Wire audit to STATE.log() (added 2026-05-30) ===
def _audit_derive_action(msg, level, data, state_ref):
    """Map STATE.log() args to audit.log_action() kwargs. Returns dict or None."""
    data = data or {}
    if level in ("warn", "error"):
        return None
    if msg.startswith("Genesis block initialized"):
        return None
    if "demo mode" in msg.lower():
        return None

    if msg.startswith("Worker registered:") and "address" in data:
        addr = data["address"]
        info = state_ref.workers.get(addr, {})
        return {
            "actor_id": addr,
            "action_type": "worker_register",
            "payload": {
                "country": info.get("country"),
                "hardware_tier": info.get("hardware_tier"),
                "faucet_ok": data.get("faucet_ok"),
            },
        }

    if msg.startswith("Job created:") and "job_id" in data:
        job = state_ref.jobs.get(data["job_id"])
        if not job:
            return None
        trust = data.get("trust")
        return {
            "actor_id": job["consumer_address"],
            "counterparty_id": job["worker_address"],
            "action_type": "job_request",
            "payload": {
                "job_id": data["job_id"],
                "task_type": job.get("task_type"),
                "fee_ecu": job.get("fee_ecu"),
                "compute_units": job.get("compute_units"),
                "sensitivity_tier": job.get("sensitivity_tier"),
                "trust_grade": trust.get("grade") if isinstance(trust, dict) else None,
            },
        }

    if msg.startswith("Job complete:") and "job_id" in data:
        job = state_ref.jobs.get(data["job_id"])
        if not job:
            return None
        hexis = data.get("hexis")
        if not isinstance(hexis, dict):
            hexis = {}
        return {
            "actor_id": job["worker_address"],
            "counterparty_id": job["consumer_address"],
            "action_type": "job_complete",
            "payload": {
                "job_id": data["job_id"],
                "fee_ecu": job.get("fee_ecu"),
                "completed_at": job.get("completed_at"),
                "hexis_mined": hexis.get("hexis_raw"),
            },
        }

    if msg.startswith("HEXIS mined for") and isinstance(data, dict):
        actor = data.get("actor_id") or data.get("worker_id")
        if not actor:
            return None
        return {
            "actor_id": actor,
            "action_type": "hexis_mint",
            "payload": data,
        }

    return None


@app.middleware("http")
async def _wire_audit_to_state_log(request, call_next):
    if getattr(STATE, "_audit_wired", False):
        return await call_next(request)
    _original_log = STATE.log

    def _wrapped_log(msg, level="info", data=None):
        entry = _original_log(msg, level=level, data=data)
        try:
            spec = _audit_derive_action(msg, level, data, STATE)
            if spec:
                audit.log_action(**spec)
        except Exception as _e:
            print(f"[audit-wire] exception: {type(_e).__name__}: {_e}")
        return entry

    STATE.log = _wrapped_log
    STATE._audit_wired = True
    print("[audit-wire] wrapped")
    return await call_next(request)




# ===========================================================
# MIDDLEWARE: capacity guard + rate limiting
# ===========================================================

@app.middleware("http")
async def guard_middleware(request: Request, call_next):
    # Bypass infrastructure endpoints
    if request.url.path in ("/health", "/metrics", "/favicon.ico"):
        return await call_next(request)

    # Capacity guard ("Say No" 503)
    if not await capacity.acquire():
        return JSONResponse(
            {"error": "service_overloaded", "retry_after": 5},
            status_code=503,
            headers={"Retry-After": "5"},
        )

    try:
        # IP rate limit (per second)
        # Sau reverse proxy tin cay (nginx localhost): dung IP THAT tu header
        # X-Real-IP (nginx set tu CF-Connecting-IP). Peer non-local -> giu
        # peer that, khong tin header (chong spoof). Xem patch_bridge_realip.
        _peer = request.client.host if request.client else "unknown"
        if _peer in ("127.0.0.1", "::1", "localhost"):
            ip = (request.headers.get("x-real-ip")
                  or request.headers.get("cf-connecting-ip")
                  or _peer)
        else:
            ip = _peer
        ok, retry = await rate_limiter.check(
            f"ip_sec:{ip}", RATE_LIMIT_PER_IP_PER_SEC, 1.0
        )
        if not ok:
            return JSONResponse(
                {"error": "rate_limited", "retry_after": retry},
                status_code=429,
                headers={"Retry-After": str(retry)},
            )

        # IP rate limit (per minute)
        ok, retry = await rate_limiter.check(
            f"ip_min:{ip}", RATE_LIMIT_PER_IP_PER_MIN, 60.0
        )
        if not ok:
            return JSONResponse(
                {"error": "rate_limited", "retry_after": retry},
                status_code=429,
                headers={"Retry-After": str(retry)},
            )

        return await call_next(request)
    finally:
        await capacity.release()


# ===========================================================
# INFRA ENDPOINTS
# ===========================================================

@app.get("/health")
async def health():
    # 200 with "degraded" in the body, not a 5xx. This repo's own rule is to
    # verify by content and never by status code, and a load balancer pulling
    # the service out of rotation would remove the endpoint that explains what
    # is wrong. A mismatch found at boot already refuses to start; one found
    # while running is reported, here.
    led = ledger_state.as_dict()
    return {
        "status": "degraded" if led.get("ok") is False else "ok",
        "version": SERVER_VERSION,
        "ledger": led,
    }


@app.get("/metrics")
async def metrics():
    return {
        "version": SERVER_VERSION,
        "cache": {
            "size": len(cache.cache),
            "hits": cache.hits,
            "misses": cache.misses,
            "max": cache.max_size,
            "ttl_sec": cache.ttl,
        },
        "capacity": {
            "current": capacity.current,
            "max": capacity.max,
        },
        "queue": {
            "size": len(write_queue.queue),
            "max": write_queue.max_size,
            "flushed_total": write_queue.flushed_total,
        },
        "newflow": {
            "chain_height": STATE.chain.height if STATE else 0,
            "total_workers": len(STATE.workers) if STATE else 0,
            "total_jobs": len(STATE.jobs) if STATE else 0,
        } if STATE else {},
        "ledger": ledger_state.as_dict(),
    }


# ===========================================================
# STATUS + EVENTS (from v0.1)
# ===========================================================

@app.get("/status")
async def status():
    global _status_cache, _status_cache_ts
    _now = time.time()
    if _status_cache is not None and _now - _status_cache_ts < STATUS_CACHE_TTL:
        return _status_cache
    workers_summary = []
    for addr, w in list(STATE.workers.items())[:200]:
        hexis = STATE.get_hexis_score(addr)
        trust = STATE.get_trust_grade(hexis)
        balance = 0
        try:
            balance = STATE.chain.get_balance(addr)
        except Exception:
            pass
        workers_summary.append({
            "address":      addr[:20] + "...",
            "country":      w.get("country"),
            "tier":         w.get("hardware_tier"),
            "jobs":         w.get("jobs_completed", 0),
            "hexis":        round(hexis, 6),
            "grade":        trust["grade"],
            "balance_ecu":  balance,
        })

    completed = sum(1 for j in STATE.jobs.values() if j.get("status") == "completed")

    _result = {
        "newflow": {
            "chain_height":   STATE.chain.height,
            "total_workers":  len(STATE.workers),
            "total_jobs":     len(STATE.jobs),
            "jobs_completed": completed,
        },
        "hexis": {
            "total_records": STATE.hexis_index.index.get("total_minted", 0),
            "unique_actors": len(set(
                r["actor_id"] for r in STATE.hexis_index.index.get("records", [])
            )),
            # What is and is not on IPFS, reported rather than implied. Three
            # numbers because there are three situations and collapsing them
            # would hide the worst one: `pinned` resolves from any gateway,
            # `pending` has retained content and no CID yet, and
            # `unpinned_legacy` is the 36 records minted before 2026-08-16 —
            # no retained content, so nothing to pin as minted.
            "pins": dict(STATE.hexis_index.pin_summary(),
                         **STATE.pin_service.status()),
        },
        "scs": {
            "ecu_minted_phase0":   round(STATE.scs.total_minted, 4),
            "energy_unit_genesis": ENERGY_UNIT_GENESIS,
            "halving_phase":       STATE.scs.halving_phase,
        },
        "workers":       workers_summary,
        "recent_events": STATE.events[-10:],
        # The books behind the numbers above. `escrow` and `locked` are
        # recomputed from entries; `chain` is this process's own history and
        # says so — see `durability` below and the module docstring in
        # hexis_ledger_entries.py.
        "ledger":        ledger_state.as_dict(),
        # Which of the safety mechanisms above have never actually bitten.
        # Reported for the same reason C's disclosure is: a number that has
        # never moved should say so rather than be discovered later by
        # somebody reading SQL.
        "dormant":       dormancy_block(),
        # Which of the above reset at restart. This response is the one that
        # mixes the two hardest: `total_jobs` is read from memory and reports 0
        # after a restart, while `workers[].jobs` beside it is read from SQLite
        # and reports the real count.
        "durability":    durability_block("/status"),
    }
    _status_cache = _result
    _status_cache_ts = _now
    return _result


@app.get("/events")
async def get_events():
    return {"events": STATE.events[-50:]}


# ===========================================================
# WORKER REGISTRATION
# ===========================================================

@app.post("/worker/register")
async def worker_register(request: Request, req: WorkerRegisterReq):
    """
    Register an actor. The actor brings its own key and proves it holds it.

    Reversed from the previous flow, which generated the keypair server-side
    and handed the actor an address. That meant this server held the private
    key, so the identity was never actually the actor's — and a separate
    bind_pubkey step then attached a SECOND, unrelated key that the address
    had no connection to. Two keypairs, and the one that named you was ours.

    Now: the client generates one Ed25519 keypair, keeps the private half,
    and signs this very request with it. The signature is the proof of
    possession. The address is derived from the key rather than asserted, so
    address and key cannot drift apart.

    This is the ONE endpoint that takes the public key from the request body
    instead of the workers table, because there is nothing to look up yet.
    That is safe here: the signature covers Content-Digest, the digest is
    recomputed from the received bytes, and the body containing the key is
    therefore part of what was signed. Substituting a different key means
    producing a different signature, which requires the private half.
    """
    # Derive first — the address is a consequence of the key, never an input.
    address = address_for_pubkey(req.client_pubkey)

    body = await request.body()
    verify_signed_write(
        method       = request.method,
        url          = signing_url(request),
        headers      = request.headers,
        body         = body,
        pubkey_hex   = req.client_pubkey,
        expect_keyid = address,   # client must sign under the derived address
        db_path      = DB_PATH,
        path         = request.url.path,
    )

    # Same key registering twice. Not an error worth punishing, but it must
    # not silently reset an existing actor's country, tier or score.
    if address in STATE.workers:
        raise HTTPException(
            status_code=409,
            detail={"error": "already_registered", "address": address},
        )

    # Faucet: give new worker some ECU for staking
    faucet_ok = False
    try:
        tx = Transfer(
            sender=STATE.faucet_wallet.address,
            sender_pubkey=STATE.faucet_wallet.public_key.hex(),
            receiver=address,
            amount=FAUCET_AMOUNT,
            fee=0,
            nonce=STATE.chain.get_nonce(STATE.faucet_wallet.address),
            timestamp=int(time.time()),
        )
        tx.sign(STATE.faucet_wallet)
        STATE.chain_transfer(tx, STATE.validator.address, reason="faucet:worker_register")
        faucet_ok = True
    except Exception as e:
        STATE.log(f"Faucet failed for new worker: {e}", level="warn")

    STATE.workers[address] = {
        "address":        address,
        "country":        req.country,
        "hardware_tier":  req.hardware_tier,
        "registered_at":  datetime.now(timezone.utc).isoformat(),
        "jobs_completed": 0,
        "hexis_score":    0.0,
        "pubkey":         req.client_pubkey,
        "benchmark_passed": False,
        # No wallet. This server no longer holds any actor's private key, and
        # nothing here ever needed one: a worker only RECEIVES ECU, and
        # receiving requires no signature. The consumer side was the exception
        # this comment pointed at — it held a server-generated wallet until 3e
        # (2026-08-15) removed both it and the transfer that used it.
        "wallet":         None,
    }

    STATE.log(
        f"Worker registered: {address[:20]}... "
        f"(tier={req.hardware_tier}, country={req.country}, key-bound)",
        data={"address": address, "faucet_ok": faucet_ok},
    )

    # Queue async insert into SQLite
    await write_queue.submit(
        "INSERT OR REPLACE INTO workers (address, country, hardware_tier, registered_at, jobs_completed, pubkey, benchmark_passed) VALUES (?, ?, ?, ?, 0, ?, 0)",
        (address, req.country, req.hardware_tier, datetime.now(timezone.utc).isoformat(), req.client_pubkey),
    )

    bench_challenge = bench.issue(address)

    return {
        "address":        address,
        "pubkey":         req.client_pubkey,
        "benchmark":      bench_challenge,
        "benchmark_required": True,
        "balance_ecu":    STATE.chain.get_balance(address),
        "hardware_tier":  req.hardware_tier,
        "country":        req.country,
        "hexis_score":    0.0,
        "trust_grade":    "Unverified",
        "geo_multiplier": GEO_MULTIPLIER.get(req.country, DEFAULT_GEO_MULT),
        "faucet_ok":      faucet_ok,
        "note":           "Submit compute jobs to earn HEXIS trust score.",
    }


# ===========================================================
# CONSUMER REGISTRATION (3e, 2026-08-15)
# ===========================================================

@app.post("/consumer/register")
async def consumer_register(request: Request, req: ConsumerRegisterReq):
    """
    Register a consumer. Same shape as /worker/register, and for the same
    reason — this is the half of the identity layer that was missing.

    Until today a consumer had no key, so two writes a consumer initiates
    (`/job/request`, `/stake/lock`) were signed by the *worker* named in them.
    Both carried a comment saying so and calling it a placeholder. The
    consequence was not academic: a worker could open a job naming any
    consumer, and could lock stake that debits that consumer's escrow.

    When no consumer address was supplied at all, `/job/request` generated a
    `Wallet()` server-side and used it. That is precisely the arrangement
    /worker/register was rewritten to end: a keypair the server holds is the
    server's identity wearing the actor's name. It is gone; see that endpoint.

    As there, the public key comes from the request body because there is
    nothing yet to look up, and that is safe for the same reason: the
    signature covers Content-Digest, the digest is recomputed from the bytes
    received, so the body carrying the key is part of what was signed.
    Substituting a key means producing a new signature, which needs the
    private half.
    """
    address = address_for_pubkey(req.client_pubkey)

    body = await request.body()
    verify_signed_write(
        method       = request.method,
        url          = signing_url(request),
        headers      = request.headers,
        body         = body,
        pubkey_hex   = req.client_pubkey,
        expect_keyid = address,
        db_path      = DB_PATH,
        path         = request.url.path,
    )

    # A consumer address must not also be a worker address, and the same key
    # must not name two actors. The boot validator refuses to start on either;
    # this refuses to create one, so the two agree.
    if address in STATE.consumers:
        raise HTTPException(
            status_code=409,
            detail={"error": "already_registered", "address": address},
        )
    if address in STATE.workers:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "address_is_a_worker",
                "address": address,
                "reason": "one address, one role, one key — an actor in both "
                          "registries would resolve to whichever the lookup "
                          "reached first",
            },
        )

    registered_at = datetime.now(timezone.utc).isoformat()

    faucet_ok = False
    try:
        tx = Transfer(
            sender=STATE.faucet_wallet.address,
            sender_pubkey=STATE.faucet_wallet.public_key.hex(),
            receiver=address,
            amount=FAUCET_AMOUNT,
            fee=0,
            nonce=STATE.chain.get_nonce(STATE.faucet_wallet.address),
            timestamp=int(time.time()),
        )
        tx.sign(STATE.faucet_wallet)
        STATE.chain_transfer(tx, STATE.validator.address, reason="faucet:consumer_register")
        faucet_ok = True
    except Exception as e:
        STATE.log(f"Faucet failed for new consumer: {e}", level="warn")

    STATE.consumers[address] = {
        "address":        address,
        "country":        req.country,
        "registered_at":  registered_at,
        "pubkey":         req.client_pubkey,
        "jobs_requested": 0,
    }

    await write_queue.submit(
        "INSERT OR REPLACE INTO consumers (address, country, registered_at, "
        "pubkey, jobs_requested) VALUES (?, ?, ?, ?, 0)",
        (address, req.country, registered_at, req.client_pubkey),
    )

    # `consumer_register` has been in VALID_ACTIONS since the allowlist was
    # written and emitted by nothing. It is emitted now.
    try:
        audit.log_action(actor_id=address, counterparty_id=None,
                         action_type="consumer_register",
                         payload={"country": req.country,
                                  "pubkey": req.client_pubkey,
                                  "registered_at": registered_at})
    except Exception as e:
        STATE.log(f"Consumer register audit write failed: {e}", level="warn")

    STATE.log(
        f"Consumer registered: {address[:20]}... "
        f"(country={req.country}, key-bound)",
        data={"address": address, "faucet_ok": faucet_ok},
    )

    return {
        "address":     address,
        "pubkey":      req.client_pubkey,
        "country":     req.country,
        "balance_ecu": STATE.chain.get_balance(address),
        "faucet_ok":   faucet_ok,
        "next":        "POST /stake/lock, then POST /job/request — both must "
                       "be signed by this key.",
        # Said here rather than discovered at a 402: the faucet credits the
        # chain balance, and staking spends escrow. There is no public path
        # from one to the other — /stake/credit created escrow out of nothing
        # and has been 410 since 2026-08-12. Funding escrow from a source that
        # is actually debited is open work, not part of 3e.
        "note":        "faucet credits chain ECU; escrow has no public funding "
                       "path (see /stake/credit, gone 2026-08-12)",
    }


# ===========================================================
# PROOF-OF-DELIVERY: bind worker pubkey (wired 2026-07-23)
# ===========================================================

@app.post("/worker/{address}/bind_pubkey")
async def worker_bind_pubkey(address: str, req: BindPubkeyReq):
    """
    Gone as of 2026-08-11. Key binding happens at registration or not at all.

    The route is kept, rather than deleted, so an old client gets an answer
    that explains itself instead of a bare 404 that looks like a typo.

    Why this had to go, beyond being redundant: it accepted ANY valid Ed25519
    key for ANY address, with no proof the caller held the private half and no
    check that the key had anything to do with the address. Two consequences.
    A key could be bound that the address never committed to. And anyone who
    knew an unbound address could bind a key they controlled — or simply a
    random one — locking the real holder out permanently, since a second bind
    with a different key returns 409 and there is no unbind.

    Registration closes both: the address is DERIVED from the key, and the
    request is signed by that key.
    """
    raise HTTPException(
        status_code=410,
        detail={
            "error": "endpoint_removed",
            "reason": "public keys are bound at registration and cannot be "
                      "changed afterwards; an actor IS its key",
            "use_instead": "POST /worker/register with client_pubkey, signed "
                           "by that key (RFC 9421)",
        },
    )


# ===========================================================
# TRUST QUERY (x402 compatible, cached)
# ===========================================================

# ===========================================================
# PUBLIC RECORD READS (2026-08-23, closes OPEN.md #5)
#
# The pinning work ended on "the CID is the source of truth" while no public
# read returned a CID — a third party could verify a record against its
# address only if we handed them the address, which is exactly the trust
# content addressing exists to remove. These three routes are that read.
#
# Read-only, no signature, no key: the records are public claims about public
# actors, and the chain has committed their proof_hash since the mint.
# ===========================================================

@app.get("/hexis/records")
async def hexis_records_list():
    """Every mined record: its address, and whether the content is held."""
    rows = []
    for r in STATE.hexis_index.index.get("records", []):
        rows.append({
            "event_id":    r["event_id"],
            "actor_id":    r["actor_id"],
            "hexis_raw":   r["hexis_raw"],
            "cid":         r.get("cid"),
            "pin_status":  r.get("pin_status"),
            "has_content": bool(r.get("record")),
        })
    return {
        "total": len(rows),
        "records": rows,
        "note": (
            "has_content=false marks the 36 records minted before 2026-08-16: "
            "their canonical content was never built, so they cannot be served "
            "or pinned as minted — only their index metadata survives. That is "
            "a permanent loss, recorded in CORRECTIONS.md, not a backlog."),
    }


@app.get("/hexis/record/{event_id}")
async def hexis_record(event_id: str):
    """
    One record, with the CID computed from its bytes — not read from a field.

    The CID returned here is arithmetic: CIDv1(raw, sha2-256) over the exact
    bytes that /raw serves. Recompute it and it either matches or this
    response is wrong in a way anyone can prove.
    """
    entry = STATE.hexis_index.get_record(event_id)
    if not entry:
        raise HTTPException(404, f"no record with event_id {event_id}")
    record = entry.get("record")
    if not record:
        return {
            "event_id":   event_id,
            "actor_id":   entry.get("actor_id"),
            "hexis_raw":  entry.get("hexis_raw"),
            "content":    None,
            "reason": (
                "minted before 2026-08-16, when no canonical content was "
                "retained. The index metadata above is all that exists; "
                "serving a reconstruction as the record would be a different "
                "claim than serving the record."),
        }
    blob = hexis_cid.canonical_bytes(record)
    out = {
        "event_id":      event_id,
        "record":        record,
        "cid":           hexis_cid.cid_v1_raw(blob),
        "pin_status":    entry.get("pin_status"),
        "how_to_verify": {
            "bytes": f"GET /hexis/record/{event_id}/raw serves the exact "
                     f"bytes; their sha256 is the digest inside the cid",
            "record_hash": "sha256 of the record minus its record_hash field, "
                           "same JSON form (sorted keys, ascii, default "
                           "spacing) — must equal record.record_hash",
            "chain": f"GET /audit/{entry.get('actor_id')} — the hexis_mint "
                     f"event for this event_id commits proof_hash; recompute "
                     f"it from the scores and it must match",
        },
    }
    if entry.get("cid_provider"):
        out["cid_provider"] = entry["cid_provider"]
        out["cid_provider_note"] = (
            "an earlier pin of this record went through a provider API that "
            "re-encoded the JSON server-side, so the bytes at cid_provider "
            "are a reformatting of this record and do not match its "
            "record_hash. Kept because it was published; superseded by cid.")
    return out


@app.get("/hexis/record/{event_id}/raw")
async def hexis_record_raw(event_id: str):
    """
    The canonical bytes themselves. sha256 of this body == the digest in the
    CID. This is the strongest form of the read: verifiable with curl and
    shasum, no software of ours involved.
    """
    entry = STATE.hexis_index.get_record(event_id)
    if not entry or not entry.get("record"):
        raise HTTPException(404, f"no retained content for {event_id}")
    return Response(content=hexis_cid.canonical_bytes(entry["record"]),
                    media_type="application/json")


@app.get("/trust/{actor_id}")
async def get_trust(actor_id: str):
    # Cache check
    cached = await cache.get(f"trust:{actor_id}")
    if cached is not None:
        return cached

    # Per-actor rate limit
    ok, retry = await rate_limiter.check(
        f"actor:{actor_id}", RATE_LIMIT_PER_ACTOR_PER_MIN, 60.0
    )
    if not ok:
        raise HTTPException(
            status_code=429,
            detail={"error": "actor_rate_limited", "retry_after": retry},
            headers={"Retry-After": str(retry)},
        )

    hexis_score = STATE.get_hexis_score(actor_id)
    trust = STATE.get_trust_grade(hexis_score)
    records = STATE.hexis_index.get_by_actor(actor_id)
    worker_info = STATE.workers.get(actor_id, {})

    balance_ecu = 0
    try:
        balance_ecu = STATE.chain.get_balance(actor_id)
    except Exception:
        pass

    country = worker_info.get("country", "??")
    geo_mult = GEO_MULTIPLIER.get(country, DEFAULT_GEO_MULT)

    response = {
        "actor_id":              actor_id,
        "hexis_total":           round(hexis_score, 8),
        "record_count":          len(records),
        "grade":                 trust["grade"],
        "accept":                trust["accept"],
        "collateral_multiplier": trust["collateral_mult"],
        "newflow_balance_ecu":   balance_ecu,
        "jobs_completed":        worker_info.get("jobs_completed", 0),
        "hardware_tier":         worker_info.get("hardware_tier", 0),
        "country":               country,
        "geo_multiplier":        geo_mult,
        "verified_at":           datetime.now(timezone.utc).isoformat(),
        "x402_headers": {
            "X-Hexis-Score":           round(hexis_score, 8),
            "X-Hexis-Grade":           trust["grade"],
            "X-Hexis-Accept":          str(trust["accept"]),
            "X-Hexis-Collateral-Mult": trust["collateral_mult"],
            "X-Hexis-Records":         len(records),
            "X-Hexis-Geo-Mult":        geo_mult,
        },
        # `newflow_balance_ecu` is the one number here held in memory only. It
        # reads 0 for every worker after a restart, including workers whose
        # escrow row in SQLite holds thousands of ECU — a different pot, but
        # not one a reader of this response would know to go and look at.
        "durability": durability_block("/trust/{actor_id}"),
    }

    await cache.set(f"trust:{actor_id}", response)
    return response


# ===========================================================
# JOB REQUEST
# ===========================================================

@app.post("/job/request", dependencies=[Depends(require_signature(
    # 3e, 2026-08-15. Bound to the consumer, which is the actor this write acts
    # for. It was bound to `worker_address` as an admitted placeholder: the
    # consumer initiates a job and held no key, so the worker signed for it —
    # meaning a worker could open jobs naming any consumer it liked.
    lambda request, payload: payload.get("consumer_address")))])
async def job_request(req: JobRequestReq):
    worker_addr = req.worker_address
    # HARDWARE FLOOR (wired 2026-07-24): worker da dang ky nhung chua pass
    # sustained-timing benchmark -> khong nhan job (403).
    _wrk = STATE.workers.get(worker_addr)
    if _wrk is not None and not _wrk.get("benchmark_passed"):
        raise HTTPException(
            status_code=403,
            detail="worker has not passed hardware benchmark - "
                   "solve challenge via POST /worker/{address}/benchmark",
        )
    hexis_score = STATE.get_hexis_score(worker_addr)
    trust = STATE.get_trust_grade(hexis_score)

    # New worker w/ no history -> allow in demo mode
    if not trust["accept"] and hexis_score == 0.0:
        trust = {"grade": "Unverified-Demo", "collateral_mult": 5.0, "accept": True}
        STATE.log(
            f"New worker {worker_addr[:16]}... allowed in demo mode (no HEXIS history)"
        )

    if not trust["accept"]:
        raise HTTPException(
            status_code=403,
            detail={
                "status": "rejected",
                "reason": f"HEXIS score too low: {hexis_score:.6f} ({trust['grade']})",
                "hexis":  hexis_score,
                "grade":  trust["grade"],
            },
        )

    job_id = req.job_id
    if stake is not None:
        stake.require_both_locked(job_id)  # 402 neu chua lock ca hai ben

    # The consumer is whoever signed this request — `require_signature` above
    # has already established that the key belongs to this address. There is
    # no longer a branch that mints one: it used to build a `Wallet()` here
    # when the field was empty, keep the private key in `STATE.jobs`, and use
    # it at completion to sign the consumer's payment. Removed with 3e, for
    # the reason /worker/register was rewritten on 2026-08-11 — a keypair the
    # server holds makes the identity the server's, whatever it is named.
    consumer_addr = req.consumer_address
    if consumer_addr not in STATE.consumers:
        # Unreachable through the signature guard, which already 404s an
        # unregistered actor. Kept because that guard binds to a field of the
        # payload, and this reads the registry the rest of the handler uses.
        raise HTTPException(
            status_code=404,
            detail={"error": "consumer_not_registered", "actor_id": consumer_addr,
                    "reason": "POST /consumer/register first"},
        )

    worker_info = STATE.workers.get(worker_addr, {})
    # `worker_wallet` used to be built here — `worker_info.get("wallet") or
    # Wallet()`. Since 3a nulled the stored wallet, that `or` fired every time
    # and generated a fresh keypair per job, which was then stored in
    # STATE.jobs and read by nothing. Dead, and a private key the server held
    # for no reason: removed with 3e.

    STATE.jobs[job_id] = {
        "job_id":            job_id,
        "worker_address":    worker_addr,
        "consumer_address":  consumer_addr,
        "task_type":         req.task_type,
        "compute_units":     req.compute_units,
        "fee_ecu":           req.fee_ecu,
        "sensitivity_tier":  req.sensitivity_tier,
        "hexis_at_start":    hexis_score,
        "trust_grade":       trust["grade"],
        "collateral_mult":   trust["collateral_mult"],
        "status":            "pending",
        "created_at":        datetime.now(timezone.utc).isoformat(),
        # No consumer_wallet. The server holds no actor's private key as of
        # 3e — this was the last one.
    }

    STATE.log(
        f"Job created: {job_id[:16]}... worker={worker_addr[:16]}... "
        f"fee={req.fee_ecu} ECU trust={trust['grade']} tier={req.sensitivity_tier}",
        data={"job_id": job_id, "trust": trust},
    )

    await write_queue.submit(
        "INSERT OR REPLACE INTO jobs (job_id, worker_address, consumer_address, task_type, compute_units, fee_ecu, hexis_at_start, trust_grade, status, created_at, completed_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)",
        (
            job_id, worker_addr, consumer_addr, req.task_type,
            req.compute_units, req.fee_ecu, hexis_score, trust["grade"],
            "pending", datetime.now(timezone.utc).isoformat(),
        ),
    )

    return {
        "status":           "accepted",
        "job_id":           job_id,
        "worker_address":   worker_addr,
        "consumer_address": consumer_addr,
        "fee_ecu":          req.fee_ecu,
        "trust_grade":      trust["grade"],
        "hexis_score":      hexis_score,
        "sensitivity_tier": req.sensitivity_tier,
        "collateral_usdc":  round(req.fee_ecu * trust["collateral_mult"], 2),
        "next":             f"POST /job/{job_id}/complete to simulate completion",
    }


# ===========================================================
# JOB COMPLETION (auto-mines HEXIS)
# ===========================================================

@app.post("/job/{job_id}/complete", dependencies=[Depends(require_signature(
    # The worker the job was assigned to — taken from the job, never from the
    # request, so a caller cannot nominate whose key gets checked.
    lambda request, payload: (STATE.jobs.get(
        request.path_params.get("job_id"), {}) or {}).get("worker_address")))])
async def job_complete(job_id: str, req: Optional[JobCompleteReq] = None):
    job = STATE.jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job["status"] != "pending":
        raise HTTPException(status_code=400, detail=f"Job already {job['status']}")

    # PROOF-OF-DELIVERY: superseded 2026-08-12 by the route's signature guard.
    #
    # The old check verified an Ed25519 signature over the job_id alone, and
    # only when a pubkey happened to be bound — so an actor with no key
    # skipped it entirely, and the signature said nothing about the request
    # that carried it. The same signature was valid on any request for that
    # job, forever, from anyone who had seen it once.
    #
    # The guard on this route covers the method, the authority, the path, the
    # query and a digest of the body, expires in five minutes, and cannot be
    # replayed. req.signature is left in the model so older clients still
    # parse; it is no longer read.

    # GUARD (gap #5, wired 2026-07-22): job chi duoc complete khi cap stake
    # van con LOCKED. Stake da slashed/aborted/expired -> tu choi, KHONG
    # mine HEXIS. Tai dung StakeManager.require_both_locked (hexis_stake v3).
    # Job danh dau "void" de bien khoi poll pending — worker khong retry.
    if stake is not None:
        try:
            stake.require_both_locked(job_id)
        except HTTPException:
            job["status"] = "void"
            job["completed_at"] = datetime.now(timezone.utc).isoformat()
            await write_queue.submit(
                "UPDATE jobs SET status=?, completed_at=? WHERE job_id=?",
                ("void", job["completed_at"], job_id),
            )
            STATE.log(f"Complete rejected for {job_id[:16]}...: stake not locked "
                      f"(slashed/aborted/expired). Job voided.", level="warn")
            raise HTTPException(
                status_code=409,
                detail=f"stake for job {job_id} is no longer locked "
                       f"(slashed/aborted/expired) - completion rejected, no HEXIS mined",
            )

    worker_addr     = job["worker_address"]
    consumer_addr   = job["consumer_address"]
    fee_ecu         = int(job["fee_ecu"])
    task_type       = job["task_type"]
    sens_tier       = job.get("sensitivity_tier", 1)

    # ECU PAYMENT — removed 2026-08-15 with 3e, and worth reading before it is
    # put back.
    #
    # What stood here signed a `Transfer` of `fee_ecu` from consumer to worker
    # using a private key this server held. 3e ends the server holding one, so
    # the code could not survive as written; the question was whether to
    # replace it with something the consumer signs.
    #
    # It should not be replaced, because the fee is already paid, twice over:
    #
    #   `StakeManager.lock()`  debits the consumer `consumer_amount +
    #                          job_value_ecu` — the fee goes into escrow when
    #                          the job is locked.
    #   `settle_complete()`    credits the worker `ws.amount + fee` — escrow
    #                          pays it out on completion, below.
    #
    # Both of those are rows in `bridge.db` and survive a restart. The
    # `Transfer` moved the same fee a second time through `ChainState`, which
    # is memory only and resets to genesis on every start (see BridgeState).
    # So the fee was counted in two ledgers, one durable and one not, with
    # nothing reconciling them — which is the standing OPEN item in
    # CORRECTIONS.md, reached from the other end.
    #
    # `payment_ok` stays in the response and now reports the escrow settlement,
    # which is the payment. Removing the field instead would have been a
    # breaking change to say something the field can say honestly.
    payment_ok = False

    # SCS: mint ECU from compute energy
    tflops     = job["compute_units"] * 10.0
    energy_j   = tflops * 0.0001 * JOULES_PER_KWH * 1000
    ecu_minted = STATE.scs.preview_mint(energy_j)

    # Mine HEXIS event with adversarial frame + sensitivity tier
    worker_info  = STATE.workers.get(worker_addr, {})
    country      = worker_info.get("country", "IN")
    # GEO: ghi nang luong tieu thu vao so cai vung (dynamic C do bang joules)
    try:
        geo.record_energy(worker_addr, country, energy_j)
    except Exception:
        pass
    hexis_result = STATE.mine_hexis_for_job(
        worker_id        = worker_addr,
        worker_country   = country,
        job_value_ecu    = float(fee_ecu),
        job_id           = job_id,
        sensitivity_tier = sens_tier,
    )

    new_hexis = STATE.get_hexis_score(worker_addr)
    new_trust = STATE.get_trust_grade(new_hexis)

    stake_result = None
    if stake is not None:
        try:
            stake_result = stake.settle_complete(job_id)
            # This is the payment: escrow released the fee to the worker, in
            # SQLite, in one transaction. See the note where the ChainState
            # transfer used to be.
            payment_ok = bool(stake_result)
        except Exception as e:
            STATE.log(f"Stake settle failed: {e}", level="warn")

    job["status"]       = "completed"
    job["completed_at"] = datetime.now(timezone.utc).isoformat()

    if worker_addr in STATE.workers:
        STATE.workers[worker_addr]["jobs_completed"] += 1
        STATE.workers[worker_addr]["hexis_score"]    = new_hexis

    # Invalidate cache for this actor
    await cache.invalidate(f"trust:{worker_addr}")

    STATE.log(
        f"Job complete: {job_id[:16]}... "
        f"payment={'ok' if payment_ok else 'skipped'} "
        f"hexis_mined={hexis_result.get('hexis_raw', 0):.6f} "
        f"new_score={new_hexis:.6f}",
        level="success",
        data={"job_id": job_id, "hexis": hexis_result},
    )

    await write_queue.submit(
        "UPDATE jobs SET status=?, completed_at=? WHERE job_id=?",
        ("completed", job["completed_at"], job_id),
    )

    # PoSP (wired 2026-07-24): sau khi settle xong, voi xac suat sigma (tat dinh
    # theo H(job_id)) mo audit cho validator node re-execute. sigma=0 -> no-op.
    _sampled = False
    if sampling is not None:
        try:
            _res = (req.result if req else None) or ""
            _open = sampling.maybe_open(
                job_id, worker_addr, country, _res,
                job.get("compute_units", 100), float(fee_ecu), sens_tier)
            _sampled = _open is not None
        except Exception as _se:
            STATE.log(f"Sampling maybe_open failed: {_se}", level="warn")

    return {
        "status":                   "completed",
        "job_id":                   job_id,
        "payment_ok":               payment_ok,
        "fee_paid_ecu":             fee_ecu,
        "ecu_minted_scs":           round(ecu_minted, 6),
        "hexis_mined":              hexis_result.get("hexis_raw", 0),
        "hexis_total":              round(new_hexis, 8),
        "trust_grade_new":          new_trust["grade"],
        "worker_balance":           STATE.chain.get_balance(worker_addr),
        "next_job_collateral_mult": new_trust["collateral_mult"],
        "consumer_hexis_gain": (stake_result or {}).get("hexis_gain"),
        "sampled": _sampled,
    }


# ===========================================================
# WORKER JOB POLL (wired 2026-07-22 — cho worker_node v0.8)
# ===========================================================

@app.get("/worker/{address}/jobs")
async def worker_jobs(address: str, status: str = "pending", limit: int = 20):
    """
    Worker poll jobs duoc gan cho minh (flow bilateral stake:
    consumer khoi tao job, worker poll roi /complete).
    Chi tra field JSON-safe — KHONG tra wallet objects.
    """
    out = []
    for j in STATE.jobs.values():
        if j.get("worker_address") != address or j.get("status") != status:
            continue
        out.append({
            "job_id":           j.get("job_id"),
            "consumer_address": j.get("consumer_address"),
            "task_type":        j.get("task_type"),
            "compute_units":    j.get("compute_units"),
            "fee_ecu":          j.get("fee_ecu"),
            "sensitivity_tier": j.get("sensitivity_tier", 1),
            "created_at":       j.get("created_at"),
        })
        if len(out) >= limit:
            break
    return {
        "worker_address": address,
        "status":         status,
        "count":          len(out),
        "jobs":           out,
    }


# ===========================================================
# UI
# ===========================================================

UI_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>HEXIS x NEWFLOW Bridge v0.8.0</title>
<style>
  :root {
    --ink: #0D0D0D; --paper: #F5F0E8; --amber: #B8721A;
    --blue: #1E3A5F; --green: #2C6E49; --red: #8B1A1A;
    --muted: #666; --line: #DDD8CC;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { background: var(--paper); color: var(--ink); font-family: 'Georgia', serif; padding: 20px; }
  h1 { font-size: 28px; color: var(--ink); letter-spacing: 2px; margin-bottom: 4px; }
  .sub { font-size: 11px; color: var(--muted); font-family: monospace; letter-spacing: 1px; margin-bottom: 24px; }
  .flow { display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 16px; font-family: monospace; font-size: 11px; }
  .flow-box { background: white; border: 1px solid var(--line); padding: 6px 10px; }
  .flow-arrow { color: var(--amber); padding: 6px 4px; }
  .grid { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; margin-bottom: 16px; }
  @media (max-width: 700px) { .grid { grid-template-columns: 1fr; } }
  .card { background: white; border: 1px solid var(--line); padding: 16px; }
  .card h2 { font-size: 13px; color: var(--amber); letter-spacing: 2px; font-family: monospace; margin-bottom: 12px; border-bottom: 1px solid var(--line); padding-bottom: 8px; }
  .btn { background: var(--ink); color: var(--paper); border: none; padding: 8px 16px; cursor: pointer; font-family: monospace; font-size: 11px; letter-spacing: 1px; width: 100%; margin-top: 8px; }
  .btn:hover { background: var(--amber); }
  input, select { width: 100%; padding: 6px 8px; font-family: monospace; font-size: 11px; border: 1px solid var(--line); background: var(--paper); margin-bottom: 6px; }
  label { font-size: 10px; color: var(--muted); font-family: monospace; display: block; margin-bottom: 2px; }
  .output { background: #111; color: #0f0; font-family: monospace; font-size: 10px; padding: 12px; min-height: 80px; margin-top: 8px; white-space: pre-wrap; overflow-y: auto; max-height: 240px; }
  .panel { background: white; border: 1px solid var(--line); padding: 16px; margin-top: 16px; }
  .panel h2 { font-size: 13px; color: var(--amber); letter-spacing: 2px; font-family: monospace; margin-bottom: 12px; border-bottom: 1px solid var(--line); padding-bottom: 8px; }
  .stat { display: inline-block; margin-right: 16px; margin-bottom: 6px; font-family: monospace; font-size: 11px; }
  .stat-label { color: var(--muted); }
  .stat-value { color: var(--ink); font-weight: bold; }
  .events { font-family: monospace; font-size: 10px; max-height: 200px; overflow-y: auto; margin-top: 12px; padding-top: 8px; border-top: 1px solid var(--line); }
  .event { padding: 3px 0; border-bottom: 1px solid var(--line); }
  .event.success { color: var(--green); }
  .event.hexis   { color: var(--amber); }
  .event.warn    { color: var(--red); }
  .footer { font-family: monospace; font-size: 10px; color: var(--muted); text-align: center; margin-top: 24px; padding-top: 16px; border-top: 1px solid var(--line); }
</style>
</head>
<body>
<h1>HEXIS x NEWFLOW</h1>
<div class="sub">TRUST CREDENTIAL LAYER FOR AI COMPUTE ECONOMY - BRIDGE v0.8.0</div>

<div class="flow">
  <div class="flow-box">AI Agent</div>
  <div class="flow-arrow">-&gt;</div>
  <div class="flow-box">GET /trust/{worker}</div>
  <div class="flow-arrow">-&gt;</div>
  <div class="flow-box">HEXIS Score</div>
  <div class="flow-arrow">-&gt;</div>
  <div class="flow-box">POST /job/request</div>
  <div class="flow-arrow">-&gt;</div>
  <div class="flow-box">Mine HEXIS</div>
</div>

<div class="grid">
  <div class="card">
    <h2>STEP 1 - REGISTER WORKER NODE</h2>
    <label>Country (ISO)</label>
    <select id="reg-country">
      <option value="VN">Vietnam (VN)</option>
      <option value="IN">India (IN)</option>
      <option value="ID">Indonesia (ID)</option>
      <option value="PH">Philippines (PH)</option>
      <option value="NG">Nigeria (NG)</option>
      <option value="US">United States (US)</option>
      <option value="GB">United Kingdom (GB)</option>
      <option value="DE">Germany (DE)</option>
      <option value="KR">South Korea (KR)</option>
      <option value="JP">Japan (JP)</option>
    </select>
    <label>Hardware Tier</label>
    <select id="reg-tier">
      <option value="1">1 - LOW (Phone/Edge)</option>
      <option value="2" selected>2 - MEDIUM (RTX 3080, 320W)</option>
      <option value="3">3 - HIGH (A100, 400W)</option>
      <option value="4">4 - EXTREME (H100, 700W)</option>
    </select>
    <button class="btn" onclick="registerWorker()">REGISTER WORKER NODE</button>
    <div class="output" id="reg-out">Waiting...</div>
  </div>

  <div class="card">
    <h2>STEP 2 - QUERY TRUST (x402 COMPATIBLE)</h2>
    <label>Actor ID (worker address or any ID)</label>
    <input type="text" id="trust-id" placeholder="paste worker address">
    <button class="btn" onclick="queryTrust()">GET /trust/{actor_id}</button>
    <div class="output" id="trust-out">Waiting...</div>
  </div>

  <div class="card">
    <h2>STEP 3 - REQUEST COMPUTE JOB</h2>
    <label>Worker Address</label>
    <input type="text" id="job-worker" placeholder="paste worker address">
    <label>Task Type</label>
    <input type="text" id="job-task" value="llm_inference_mid_1B_tokens">
    <label>Compute Units</label>
    <input type="number" id="job-units" value="100">
    <label>Fee (ECU)</label>
    <input type="number" id="job-fee" value="50" step="any">
    <label>Sensitivity Tier (1=commodity, 4=life-critical)</label>
    <select id="job-tier">
      <option value="1" selected>1 - Low (commodity)</option>
      <option value="2">2 - Standard (medium-value)</option>
      <option value="3">3 - High (financial/health)</option>
      <option value="4">4 - Extreme (life-critical)</option>
    </select>
    <button class="btn" onclick="requestJob()">POST /job/request</button>
    <div class="output" id="job-out">Waiting...</div>
  </div>

  <div class="card">
    <h2>STEP 4 - COMPLETE JOB + MINE HEXIS</h2>
    <label>Job ID</label>
    <input type="text" id="comp-id" placeholder="paste job_id from step 3">
    <button class="btn" onclick="completeJob()">POST /job/{job_id}/complete</button>
    <div class="output" id="comp-out">Waiting...</div>
  </div>
</div>

<div class="panel">
  <h2>NETWORK STATUS</h2>
  <div id="status-stats">Loading...</div>
  <div class="events" id="status-events"></div>
</div>

<div class="footer">
  HEXIS x NEWFLOW Bridge v0.8.0 - Production-grade backbone + NEWFLOW economy<br>
  
</div>

<script>
async function api(method, path, body) {
  try {
    const opts = { method: method, headers: { "Content-Type": "application/json" } };
    if (body) opts.body = JSON.stringify(body);
    const res = await fetch(path, opts);
    return await res.json();
  } catch (e) {
    return { error: e.message };
  }
}

async function registerWorker() {
  const country = document.getElementById('reg-country').value;
  const tier = parseInt(document.getElementById('reg-tier').value);
  document.getElementById('reg-out').textContent = 'Registering...';
  const r = await api('POST', '/worker/register', { country: country, hardware_tier: tier });
  document.getElementById('reg-out').textContent = JSON.stringify(r, null, 2);
  if (r.address) {
    document.getElementById('trust-id').value = r.address;
    document.getElementById('job-worker').value = r.address;
  }
  refreshStatus();
}

async function queryTrust() {
  const id = document.getElementById('trust-id').value;
  if (!id) { document.getElementById('trust-out').textContent = 'Enter actor ID'; return; }
  document.getElementById('trust-out').textContent = 'Querying...';
  const r = await api('GET', '/trust/' + encodeURIComponent(id));
  document.getElementById('trust-out').textContent = JSON.stringify(r, null, 2);
}

async function requestJob() {
  const body = {
    worker_address:   document.getElementById('job-worker').value,
    task_type:        document.getElementById('job-task').value,
    compute_units:    parseInt(document.getElementById('job-units').value) || 100,
    fee_ecu:          parseFloat(document.getElementById('job-fee').value) || 0,
    sensitivity_tier: parseInt(document.getElementById('job-tier').value) || 1,
  };
  document.getElementById('job-out').textContent = 'Requesting...';
  const r = await api('POST', '/job/request', body);
  document.getElementById('job-out').textContent = JSON.stringify(r, null, 2);
  if (r.job_id) document.getElementById('comp-id').value = r.job_id;
}

async function completeJob() {
  const id = document.getElementById('comp-id').value;
  if (!id) { document.getElementById('comp-out').textContent = 'Enter job_id'; return; }
  document.getElementById('comp-out').textContent = 'Completing...';
  const r = await api('POST', '/job/' + encodeURIComponent(id) + '/complete', {});
  document.getElementById('comp-out').textContent = JSON.stringify(r, null, 2);
  refreshStatus();
}

async function refreshStatus() {
  const r = await api('GET', '/status');
  if (r.error) return;
  const s = document.getElementById('status-stats');
  s.innerHTML =
    '<div class="stat"><span class="stat-label">Workers:</span> <span class="stat-value">' + r.newflow.total_workers + '</span></div>' +
    '<div class="stat"><span class="stat-label">Jobs:</span> <span class="stat-value">' + r.newflow.total_jobs + '</span></div>' +
    '<div class="stat"><span class="stat-label">Completed:</span> <span class="stat-value">' + r.newflow.jobs_completed + '</span></div>' +
    '<div class="stat"><span class="stat-label">Chain height:</span> <span class="stat-value">' + r.newflow.chain_height + '</span></div>' +
    '<div class="stat"><span class="stat-label">HEXIS records:</span> <span class="stat-value">' + r.hexis.total_records + '</span></div>' +
    '<div class="stat"><span class="stat-label">Unique actors:</span> <span class="stat-value">' + r.hexis.unique_actors + '</span></div>' +
    '<div class="stat"><span class="stat-label">ECU minted (SCS):</span> <span class="stat-value">' + r.scs.ecu_minted_phase0 + '</span></div>';
  const events = document.getElementById('status-events');
  events.innerHTML = r.recent_events.map(function(e) {
    return '<div class="event ' + e.level + '">[' + e.ts.slice(11,19) + '] ' + e.msg + '</div>';
  }).join('');
}

setInterval(refreshStatus, 5000);
refreshStatus();
</script>
</body>
</html>"""


@app.get("/", response_class=HTMLResponse)
async def index():
    return UI_HTML


# ===========================================================
# MAIN
# ===========================================================

if __name__ == "__main__":
    # Escrow funding lives here rather than on the network, in the
    # `--create-key` tradition of hexis_api_v0.6.1.py: the trust boundary is
    # shell access on this host, and inventing a second credential to protect
    # would be a worse trade. Read the section above `fund_escrow` before
    # moving this anywhere — `audit_fund_escrow_call_sites()` will refuse to
    # start the service if it is called from anywhere else.
    #
    #     python3 hexis_bridge_v0.6.2.py --fund-escrow <actor_id> <amount>
    #
    # Safe to run while the service is up: the balance and both ledger legs go
    # in under BEGIN IMMEDIATE, and the running process reads escrow from
    # SQLite on every operation rather than caching it.
    if "--fund-escrow" in sys.argv:
        _i = sys.argv.index("--fund-escrow")
        if len(sys.argv) < _i + 3:
            print("usage: --fund-escrow <actor_id> <amount>")
            print(f"       amount must be > 0 and <= {FUND_ESCROW_MAX_PER_GRANT}")
            sys.exit(2)
        try:
            _res = fund_escrow(sys.argv[_i + 1], sys.argv[_i + 2])
        except Exception as _e:
            # Nothing moved: every check in fund_escrow runs before the
            # transaction, and the transaction is all-or-nothing.
            print(f"refused: {_e}")
            sys.exit(1)
        for _k, _v in _res.items():
            print(f"{_k:15}: {_v}")
        print("Recorded as one balanced ledger operation (issuance -> escrow) "
              "and one audit event. It does not say who ran this; there is no "
              "operator identity here to name.")
        sys.exit(0)

    # One document_seal per deploy. Run it after the document is live, never
    # before: it hashes what the public URL serves, which is the only copy a
    # reader can check. Read the section above `record_document_seal` — the
    # boot audit refuses to start the service if it is called anywhere else.
    #
    #     python3 hexis_bridge_v0.6.2.py --record-document <name>
    #
    # The event lands as the new chain head and carries no signature yet. It
    # is worth something only once the head is signed, which happens off this
    # machine:  python3 seal_remote.py
    if "--record-document" in sys.argv:
        _i = sys.argv.index("--record-document")
        if len(sys.argv) < _i + 2:
            print("usage: --record-document <name>")
            print("known:", ", ".join(sorted(DOCUMENT_SEAL_ALLOWLIST)) or "(none)")
            sys.exit(2)
        try:
            _res = record_document_seal(sys.argv[_i + 1])
        except Exception as _e:
            # Every refusal above is a refusal before the write, so a failure
            # here means the chain is exactly as it was.
            print(f"refused: {_e}")
            sys.exit(1)
        for _k, _v in _res.items():
            print(f"{_k:12}: {_v}")
        print("\nThe hash is of the bytes served from that URL just now, not of "
              "any file on this host. It attests what was published, not when "
              "it was written.")
        print("Sign the head next, from the machine that holds the key:")
        print("    HEXIS_BRIDGE_URL=https://bridge.hexisfoundation.org \\")
        # [generalised] A literal path to the Foundation private key stood
        # here. It was a hint string and nothing has ever read it —
        # seal_remote.py takes the path from HEXIS_SEAL_KEY_PATH in the
        # environment — so removing it changes no behaviour on any host. It is
        # generalised rather than deleted because this repository is public and
        # the precedent already set in CORRECTIONS.md is that a key path is
        # generalised in place, with a marker, so the reader can see that
        # something was withheld instead of finding a gap they cannot name.
        print("    HEXIS_SEAL_KEY_PATH=<path to the Foundation private key> \\")
        print("    python3 seal_remote.py")
        sys.exit(0)

    # --- Bitcoin anchor ---------------------------------------------------
    #     python3 hexis_bridge_v0.6.2.py --record-ots-anchor \
    #         <anchored_event_id> <block_height> <proof_name> <proof_sha256>
    #
    # Written by ots_anchor.py on the laptop over ssh, once a pending proof
    # reaches a block. Not driven from here, because the calendar client and
    # the proofs live where the seal key lives and this host stays lean.
    if "--record-ots-anchor" in sys.argv:
        _i = sys.argv.index("--record-ots-anchor")
        if len(sys.argv) < _i + 5:
            print("usage: --record-ots-anchor <event_id> <block_height> "
                  "<proof_name> <proof_sha256>")
            sys.exit(2)
        try:
            _res = record_ots_anchor(sys.argv[_i + 1], sys.argv[_i + 2],
                                     sys.argv[_i + 3], sys.argv[_i + 4])
        except Exception as _e:                               # noqa: BLE001
            print(f"refused: {_e}")
            sys.exit(1)
        print(f"anchored head   : {_res['anchored_event_id']}")
        print(f"at sequence     : {_res['anchored_sequence']} "
              f"(covers 0..{_res['anchored_sequence']})")
        print(f"bitcoin block   : {_res['bitcoin_block_height']}")
        print(f"proof           : {_res['proof_file']}")
        print(f"event sequence  : {_res['sequence']}")
        print(f"event_id        : {_res['event_id']}")
        print("\nThis row is a pointer to a published proof. This host did not "
              "verify the Bitcoin attestation and holds no client that could; "
              "the .ots file is the evidence and anyone can check it without "
              "us. Sign the new head with seal_remote.py.")
        sys.exit(0)

    # --- Successor designation --------------------------------------------
    #     python3 hexis_bridge_v0.6.2.py --record-successor <fingerprint>
    #
    # The key itself is generated offline and never reaches this host, this
    # repository, or the person running this command in any form other than a
    # fingerprint. That is the point: the designation is a commitment to WHICH
    # key, made in public, and it is not a copy of the key.
    if "--record-successor" in sys.argv:
        _i = sys.argv.index("--record-successor")
        if len(sys.argv) < _i + 2:
            print("usage: --record-successor <fingerprint>")
            print(f"       {SUCCESSOR_FINGERPRINT_ALGORITHM}")
            sys.exit(2)
        try:
            _res = record_successor_designation(sys.argv[_i + 1])
        except Exception as _e:                               # noqa: BLE001
            print(f"refused: {_e}")
            sys.exit(1)
        print(f"fingerprint     : {_res['fingerprint']}")
        print(f"algorithm       : {SUCCESSOR_FINGERPRINT_ALGORITHM}")
        print(f"supersedes      : "
              f"{_res['supersedes_sequence'] if _res['supersedes_sequence'] is not None else 'nothing — this is the first'}")
        print(f"sequence        : {_res['sequence']}")
        print(f"event_id        : {_res['event_id']}")
        print("\nThis grants the designated key NOTHING. A signature by it is "
              "not a valid seal and no verifier should accept one. The seal key "
              "in force is unchanged.")
        print("Activation is undecided and is recorded as undecided. There is "
              "no revocation path: a STOLEN seal key is not covered by this.")
        print("Sign the new head with seal_remote.py — the anchor that follows "
              "is what dates the designation against something outside our "
              "control.")
        sys.exit(0)

    # --- Retract a designation --------------------------------------------
    #     python3 hexis_bridge_v0.6.2.py --void-successor <sequence> <reason>
    if "--void-successor" in sys.argv:
        _i = sys.argv.index("--void-successor")
        if len(sys.argv) < _i + 3:
            print("usage: --void-successor <sequence> <reason>")
            print("       the reason is written into the chain; say what was "
                  "wrong with the designation")
            sys.exit(2)
        try:
            _res = record_successor_void(sys.argv[_i + 1],
                                         " ".join(sys.argv[_i + 2:]))
        except Exception as _e:                               # noqa: BLE001
            print(f"refused: {_e}")
            sys.exit(1)
        print(f"voided sequence : {_res['voided_sequence']}")
        print(f"its fingerprint : {_res['voided_fingerprint']}")
        print(f"void recorded at: {_res['sequence']}")
        print(f"event_id        : {_res['event_id']}")
        print("\nNothing was deleted. The voided row still hashes and still "
              "verifies; it is history. This is a later row saying it was a "
              "mistake, which is what an append-only record has instead of an "
              "eraser. Sign the new head.")
        sys.exit(0)

    uvicorn.run(
        app,
        host=BIND_HOST,
        port=BIND_PORT,
        log_level="info",
        # Left off deliberately, but know what it costs: this process keeps no
        # record of any request it serves. The request log is nginx's
        # /var/log/nginx/access.log and nothing else, which is only a complete
        # record because BIND_HOST is loopback — read the note there before
        # changing either line. nginx logs the request line, not the body and
        # not the Signature header, so it answers "was this called" and never
        # "what did it carry".
        access_log=False,
    )
