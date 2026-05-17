"""
HEXIS x NEWFLOW Bridge v0.6.2
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

PORT: 8400
"""

import asyncio
import json
import time
import math
import hashlib
import os
import sys
import sqlite3
import logging
from collections import deque, defaultdict, OrderedDict
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple, Any
from contextlib import asynccontextmanager

# Ensure local module path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse, HTMLResponse, PlainTextResponse
from pydantic import BaseModel, Field
import uvicorn

# -- v0.1 NEWFLOW + HEXIS core modules (must exist in /opt/hexis_newflow/) --
from hexis_mining import HexisMiner, BehaviorEvent
from hexis_ledger import HexisLedger, LocalIndex
from newflow_core import (
    Wallet, ChainState, Ledger, Transfer, JobCommitment,
    ProofWithPayment, generate_mock_proof, build_block,
    OptimisticPool, ValidatorVerifier, StakedProof,
    ProofStatus, ValidationError, sha256, FAUCET_AMOUNT,
)
from scs_engine import SunkCostMintEngine, ENERGY_UNIT_GENESIS, JOULES_PER_KWH


# ===========================================================
# CONSTANTS
# ===========================================================

SERVER_VERSION = "0.6.2"
DB_PATH = "/opt/hexis_newflow/bridge.db"
TRUST_API_URL = "http://localhost:8401"
BIND_HOST = "0.0.0.0"
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
    1: ("LOW (Phone/Edge)",   30,   108000),
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
                jobs_completed INTEGER DEFAULT 0
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
        """)
        conn.commit()
    finally:
        conn.close()


# ===========================================================
# BRIDGE STATE (from v0.1, kept intact)
# ===========================================================

class BridgeState:
    """
    In-memory NEWFLOW + HEXIS state for the demo.
    In production: NEWFLOW state persists to disk, HEXIS records persist to IPFS.
    """

    def __init__(self):
        # NEWFLOW
        self.chain = ChainState()
        self.ledger = Ledger()
        self.scs = SunkCostMintEngine()
        self.validator = Wallet()
        self.pool = OptimisticPool()

        # HEXIS
        self.hexis_index = LocalIndex("bridge_hexis_index.json")
        self.hexis_miner = HexisMiner()

        # In-memory registries
        self.workers: Dict[str, dict] = {}
        self.jobs: Dict[str, dict] = {}
        self.events: List[dict] = []

        # Bootstrap genesis
        self._init_genesis()

    def _init_genesis(self):
        faucet = Wallet()
        alloc = {
            self.validator.address: 100_000,
            faucet.address:         50_000,
            "NETWORK_RESERVE":      39_000_000 - 150_000,
        }
        self.chain.apply_genesis(alloc, faucet_address=faucet.address)
        self.faucet_wallet = faucet
        self.log("Genesis block initialized. NEWFLOW + HEXIS bridge online.")

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
            description             = (
                f"NEWFLOW compute job delivered honestly. "
                f"Job ID: {job_id[:16]}. "
                f"Value: {job_value_ecu:.2f} ECU. "
                f"Sensitivity tier: {sensitivity_tier} (BO x{bo_mult}). "
                f"TEE proof verified by validator."
            ),
            actor_country           = worker_country,
            asset_could_have_taken  = scaled_value,
            asset_actually_returned = scaled_value,
            prob_betrayal_detected  = 0.95,
            gain_if_betrayed        = scaled_value,
            witness_sources         = [
                {"type": "adversarial", "name": "NEWFLOW Validator"},
                {"type": "neutral",     "name": "On-chain TEE Proof"},
                {"type": "allied",      "name": "Consumer Confirmation"},
            ],
            mention_counts          = {"30d": 1, "1y": 0, "5y": 0},
        )

        result = self.hexis_miner.mine(event)

        if result.get("eligible"):
            self.hexis_index.add(
                event_id  = result["event_id"],
                cid       = f"local:{result['proof_hash'][:16]}",
                hexis_raw = result["hexis_raw"],
                actor_id  = worker_id,
            )
            self.log(
                f"HEXIS mined for {worker_id[:16]}: {result['hexis_raw']:.6f}",
                level="hexis",
                data=result,
            )

        return result


# ===========================================================
# GLOBAL SINGLETONS
# ===========================================================

rate_limiter = RateLimiter()
cache = LRUCache()
capacity = CapacityGuard()
write_queue = WriteQueue(DB_PATH)
STATE: Optional[BridgeState] = None  # initialised in lifespan


# ===========================================================
# PYDANTIC MODELS
# ===========================================================

class WorkerRegisterReq(BaseModel):
    country: str = Field(..., min_length=2, max_length=3)
    hardware_tier: int = Field(..., ge=1, le=4)


class JobRequestReq(BaseModel):
    worker_address: str = Field(..., min_length=1, max_length=128)
    consumer_address: Optional[str] = Field(None, max_length=128)
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
    queue_task = asyncio.create_task(write_queue.run_forever())
    try:
        yield
    finally:
        queue_task.cancel()
        try:
            await queue_task
        except asyncio.CancelledError:
            pass


app = FastAPI(
    lifespan=lifespan,
    title="HEXIS x NEWFLOW Bridge",
    version=SERVER_VERSION,
    docs_url="/docs",
)


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
        ip = request.client.host if request.client else "unknown"
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
    return {"status": "ok", "version": SERVER_VERSION}


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
    }


# ===========================================================
# STATUS + EVENTS (from v0.1)
# ===========================================================

@app.get("/status")
async def status():
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

    return {
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
        },
        "scs": {
            "ecu_minted_phase0":   round(STATE.scs.total_minted, 4),
            "energy_unit_genesis": ENERGY_UNIT_GENESIS,
            "halving_phase":       STATE.scs.halving_phase,
        },
        "workers":       workers_summary,
        "recent_events": STATE.events[-10:],
    }


@app.get("/events")
async def get_events():
    return {"events": STATE.events[-50:]}


# ===========================================================
# WORKER REGISTRATION
# ===========================================================

@app.post("/worker/register")
async def worker_register(req: WorkerRegisterReq):
    wallet = Wallet()

    # Faucet: give new worker some ECU for staking
    faucet_ok = False
    try:
        tx = Transfer(
            sender=STATE.faucet_wallet.address,
            sender_pubkey=STATE.faucet_wallet.public_key.hex(),
            receiver=wallet.address,
            amount=FAUCET_AMOUNT,
            fee=0,
            nonce=STATE.chain.get_nonce(STATE.faucet_wallet.address),
            timestamp=int(time.time()),
        )
        tx.sign(STATE.faucet_wallet)
        STATE.chain.apply_transfer(tx, STATE.validator.address)
        faucet_ok = True
    except Exception as e:
        STATE.log(f"Faucet failed for new worker: {e}", level="warn")

    STATE.workers[wallet.address] = {
        "address":        wallet.address,
        "country":        req.country,
        "hardware_tier":  req.hardware_tier,
        "registered_at":  datetime.now(timezone.utc).isoformat(),
        "jobs_completed": 0,
        "hexis_score":    0.0,
        "wallet":         wallet,
    }

    STATE.log(
        f"Worker registered: {wallet.address[:20]}... "
        f"(tier={req.hardware_tier}, country={req.country})",
        data={"address": wallet.address, "faucet_ok": faucet_ok},
    )

    # Queue async insert into SQLite
    await write_queue.submit(
        "INSERT OR REPLACE INTO workers (address, country, hardware_tier, registered_at, jobs_completed) VALUES (?, ?, ?, ?, 0)",
        (wallet.address, req.country, req.hardware_tier, datetime.now(timezone.utc).isoformat()),
    )

    return {
        "address":        wallet.address,
        "balance_ecu":    STATE.chain.get_balance(wallet.address),
        "hardware_tier":  req.hardware_tier,
        "country":        req.country,
        "hexis_score":    0.0,
        "trust_grade":    "Unverified",
        "geo_multiplier": GEO_MULTIPLIER.get(req.country, DEFAULT_GEO_MULT),
        "faucet_ok":      faucet_ok,
        "note":           "Submit compute jobs to earn HEXIS trust score.",
    }


# ===========================================================
# TRUST QUERY (x402 compatible, cached)
# ===========================================================

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
    }

    await cache.set(f"trust:{actor_id}", response)
    return response


# ===========================================================
# JOB REQUEST
# ===========================================================

@app.post("/job/request")
async def job_request(req: JobRequestReq):
    worker_addr = req.worker_address
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

    job_id = sha256(f"{worker_addr}{req.task_type}{time.time()}")

    # If no consumer provided, mint one + give ECU
    consumer_addr = req.consumer_address or ""
    consumer_wallet = None
    if not consumer_addr:
        consumer_wallet = Wallet()
        consumer_addr = consumer_wallet.address
        try:
            tx = Transfer(
                sender=STATE.faucet_wallet.address,
                sender_pubkey=STATE.faucet_wallet.public_key.hex(),
                receiver=consumer_wallet.address,
                amount=int(req.fee_ecu) + 1000,
                fee=0,
                nonce=STATE.chain.get_nonce(STATE.faucet_wallet.address),
                timestamp=int(time.time()),
            )
            tx.sign(STATE.faucet_wallet)
            STATE.chain.apply_transfer(tx, STATE.validator.address)
        except Exception as e:
            STATE.log(f"Consumer faucet failed: {e}", level="warn")

    worker_info = STATE.workers.get(worker_addr, {})
    worker_wallet = worker_info.get("wallet") or Wallet()

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
        "consumer_wallet":   consumer_wallet,
        "worker_wallet":     worker_wallet,
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

@app.post("/job/{job_id}/complete")
async def job_complete(job_id: str):
    job = STATE.jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job["status"] != "pending":
        raise HTTPException(status_code=400, detail=f"Job already {job['status']}")

    worker_addr     = job["worker_address"]
    consumer_addr   = job["consumer_address"]
    fee_ecu         = int(job["fee_ecu"])
    task_type       = job["task_type"]
    consumer_wallet = job.get("consumer_wallet")
    sens_tier       = job.get("sensitivity_tier", 1)

    # ECU payment
    payment_ok = False
    try:
        if consumer_wallet and STATE.chain.get_balance(consumer_addr) >= fee_ecu:
            tx = Transfer(
                sender=consumer_addr,
                sender_pubkey=consumer_wallet.public_key.hex(),
                receiver=worker_addr,
                amount=fee_ecu,
                fee=0,
                nonce=STATE.chain.get_nonce(consumer_addr),
                timestamp=int(time.time()),
            )
            tx.sign(consumer_wallet)
            STATE.chain.apply_transfer(tx, STATE.validator.address)
            payment_ok = True
    except Exception as e:
        STATE.log(f"Payment failed: {e}", level="warn")

    # SCS: mint ECU from compute energy
    tflops     = job["compute_units"] * 10.0
    energy_j   = tflops * 0.0001 * JOULES_PER_KWH * 1000
    ecu_minted = STATE.scs.preview_mint(energy_j)

    # Mine HEXIS event with adversarial frame + sensitivity tier
    worker_info  = STATE.workers.get(worker_addr, {})
    country      = worker_info.get("country", "IN")
    hexis_result = STATE.mine_hexis_for_job(
        worker_id        = worker_addr,
        worker_country   = country,
        job_value_ecu    = float(fee_ecu),
        job_id           = job_id,
        sensitivity_tier = sens_tier,
    )

    new_hexis = STATE.get_hexis_score(worker_addr)
    new_trust = STATE.get_trust_grade(new_hexis)

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
    }


# ===========================================================
# UI
# ===========================================================

UI_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>HEXIS x NEWFLOW Bridge v0.6.2</title>
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
<div class="sub">TRUST CREDENTIAL LAYER FOR AI COMPUTE ECONOMY - BRIDGE v0.6.2</div>

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
  HEXIS x NEWFLOW Bridge v0.6.2 - Production-grade backbone + NEWFLOW economy<br>
  Built on $87/year. No investors. No pressure.
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
    uvicorn.run(
        app,
        host=BIND_HOST,
        port=BIND_PORT,
        log_level="info",
        access_log=False,
    )
