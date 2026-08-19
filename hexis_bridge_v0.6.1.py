"""
HEXIS x NEWFLOW Bridge v0.6.1
==============================
Production-grade upgrade to match Trust API v0.6.1 architecture.

Endpoints:
  GET  /                    HTML UI (4-step workflow)
  GET  /health              Load balancer health check
  GET  /status              Bridge stats
  GET  /metrics             Cache, capacity, queue metrics
  POST /worker/register     Register new worker node
  GET  /worker/{worker_id}  Worker info
  GET  /trust/{actor_id}    Proxy to Trust API for trust query
  POST /job/request         Request new compute job
  POST /job/complete        Complete job, trigger HEXIS mining via API
  GET  /job/{job_id}        Job status

Same hardening as API v0.6.1:
  - Rate limiting (per IP and per actor)
  - Capacity guard (503 with Retry-After)
  - LRU+TTL cache
  - Async write queue for jobs
  - Connection pool to SQLite WAL

HEXIS Foundation — no legal entity, by design.
Authenticity is cryptographic, not jurisdictional.
"""

import asyncio
import hashlib
import logging
import os
import secrets
import sqlite3
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from typing import Optional, Dict, List, Tuple, Any

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("bridge")

# CONFIGURATION
DB_PATH = "/opt/hexis_newflow/bridge.db"
SERVER_VERSION = "0.6.1"
TRUST_API_URL = "http://localhost:8401"

# Rate limits
RATE_LIMIT_PER_IP_PER_SEC = 10
RATE_LIMIT_PER_IP_PER_MIN = 300
RATE_LIMIT_PER_WORKER_PER_MIN = 30

# Capacity
MAX_CONCURRENT_REQUESTS = 5000
MAX_QUEUE_SIZE = 10000
CACHE_MAX_ENTRIES = 100000
CACHE_TTL_SEC = 30
DB_READ_POOL_SIZE = 20
DB_WRITE_BATCH_INTERVAL_MS = 100
DB_WRITE_BATCH_MAX_SIZE = 500

# ECU rate per hardware tier (1 hour at rated wattage in joules)
HARDWARE_TIERS = {
    1: ("LOW (Phone/Edge)", 30, 108000),
    2: ("MEDIUM (RTX 3080)", 320, 1152000),
    3: ("HIGH (A100)", 400, 1440000),
    4: ("EXTREME (H100)", 700, 2520000),
}

# Country GDP-adjusted C multiplier
COUNTRY_C = {
    "US": 0.85, "GB": 0.90, "DE": 0.92, "JP": 0.95,
    "KR": 1.00, "CN": 1.20, "BR": 1.35, "MX": 1.40,
    "IN": 1.55, "VN": 1.69, "ID": 1.62, "PH": 1.58,
    "NG": 1.85, "PK": 1.78, "BD": 1.80, "ET": 1.95,
}


# RATE LIMITER
class RateLimiter:
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
                retry_after = int(bucket[0] + window_sec - now) + 1
                return False, retry_after
            bucket.append(now)
            return True, 0


rate_limiter = RateLimiter()


# CACHE
class TTLCache:
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


# CAPACITY GUARD
class CapacityGuard:
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


# DATABASE
class DBPool:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self.read_pool: asyncio.Queue = asyncio.Queue(maxsize=DB_READ_POOL_SIZE)
        self.write_lock = asyncio.Lock()
        self._initialized = False

    async def init(self):
        if self._initialized:
            return
        conn = sqlite3.connect(self.db_path, isolation_level=None)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA cache_size=-64000")
        conn.execute("PRAGMA temp_store=MEMORY")
        self._create_schema(conn)
        conn.close()
        for _ in range(self.read_pool.maxsize):
            c = sqlite3.connect(self.db_path, isolation_level=None, check_same_thread=False)
            c.execute("PRAGMA query_only=ON")
            c.row_factory = sqlite3.Row
            await self.read_pool.put(c)
        self._initialized = True

    def _create_schema(self, conn):
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS workers (
                worker_id TEXT PRIMARY KEY,
                country TEXT,
                hardware_tier INTEGER,
                wattage INTEGER,
                joules_per_hour INTEGER,
                registered_at INTEGER,
                last_active INTEGER,
                jobs_completed INTEGER DEFAULT 0,
                ecu_earned REAL DEFAULT 0
            );
            CREATE INDEX IF NOT EXISTS idx_workers_country ON workers(country);

            CREATE TABLE IF NOT EXISTS jobs (
                job_id TEXT PRIMARY KEY,
                worker_id TEXT,
                task_type TEXT,
                fee_ecu REAL,
                status TEXT DEFAULT 'pending',
                created_at INTEGER,
                completed_at INTEGER,
                hexis_minted REAL DEFAULT 0,
                FOREIGN KEY(worker_id) REFERENCES workers(worker_id)
            );
            CREATE INDEX IF NOT EXISTS idx_jobs_worker ON jobs(worker_id);
            CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);

            CREATE TABLE IF NOT EXISTS bridge_stats (
                key TEXT PRIMARY KEY,
                value REAL
            );
            INSERT OR IGNORE INTO bridge_stats(key, value) VALUES('total_workers', 0);
            INSERT OR IGNORE INTO bridge_stats(key, value) VALUES('total_jobs', 0);
            INSERT OR IGNORE INTO bridge_stats(key, value) VALUES('total_ecu_minted', 0);
            INSERT OR IGNORE INTO bridge_stats(key, value) VALUES('total_hexis_mined', 0);
        """)

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


# WRITE QUEUE for job updates
class WriteQueue:
    def __init__(self, max_size: int = MAX_QUEUE_SIZE):
        self.queue: asyncio.Queue = asyncio.Queue(maxsize=max_size)
        self.dropped = 0
        self.processed = 0

    async def submit(self, item: dict) -> bool:
        try:
            self.queue.put_nowait(item)
            return True
        except asyncio.QueueFull:
            self.dropped += 1
            return False

    async def start(self):
        asyncio.create_task(self._worker())

    async def _worker(self):
        while True:
            try:
                batch = []
                first = await self.queue.get()
                batch.append(first)
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
                for item in batch:
                    if item["op"] == "register_worker":
                        conn.execute("""
                            INSERT OR REPLACE INTO workers
                            (worker_id, country, hardware_tier, wattage, joules_per_hour, registered_at, last_active)
                            VALUES(?,?,?,?,?,?,?)
                        """, (item["worker_id"], item["country"], item["tier"],
                              item["wattage"], item["joules_per_hour"],
                              item["timestamp"], item["timestamp"]))
                        conn.execute("UPDATE bridge_stats SET value = value + 1 WHERE key = 'total_workers'")
                    elif item["op"] == "create_job":
                        conn.execute("""
                            INSERT INTO jobs(job_id, worker_id, task_type, fee_ecu, status, created_at)
                            VALUES(?,?,?,?,'pending',?)
                        """, (item["job_id"], item["worker_id"], item["task_type"],
                              item["fee_ecu"], item["timestamp"]))
                        conn.execute("UPDATE bridge_stats SET value = value + 1 WHERE key = 'total_jobs'")
                    elif item["op"] == "complete_job":
                        conn.execute("""
                            UPDATE jobs SET status = 'completed',
                                completed_at = ?, hexis_minted = ?
                            WHERE job_id = ?
                        """, (item["timestamp"], item["hexis_minted"], item["job_id"]))
                        conn.execute("""
                            UPDATE workers SET jobs_completed = jobs_completed + 1,
                                ecu_earned = ecu_earned + ?, last_active = ?
                            WHERE worker_id = ?
                        """, (item["fee_ecu"], item["timestamp"], item["worker_id"]))
                        conn.execute("UPDATE bridge_stats SET value = value + ? WHERE key = 'total_ecu_minted'",
                                     (item["fee_ecu"],))
                        conn.execute("UPDATE bridge_stats SET value = value + ? WHERE key = 'total_hexis_mined'",
                                     (item["hexis_minted"],))
                conn.execute("COMMIT")
                for item in batch:
                    if "worker_id" in item:
                        asyncio.create_task(cache.invalidate(f"worker:{item['worker_id']}"))
                    if item.get("op") in ("create_job", "complete_job"):
                        asyncio.create_task(cache.invalidate(f"job:{item.get('job_id')}"))
                asyncio.create_task(cache.invalidate("status"))
            except Exception as e:
                conn.execute("ROLLBACK")
                log.error("flush failed: %s", e)


write_queue = WriteQueue()


# MODELS
class WorkerRegister(BaseModel):
    country: str = Field(..., min_length=2, max_length=3)
    hardware_tier: int = Field(..., ge=1, le=4)


class JobRequest(BaseModel):
    worker_id: str = Field(..., min_length=1, max_length=128)
    task_type: str = Field(..., max_length=64)
    fee_ecu: float = Field(..., gt=0)


class JobComplete(BaseModel):
    job_id: str = Field(..., min_length=1, max_length=128)


# MIDDLEWARE
async def rate_limit_middleware(request: Request, call_next):
    if not await capacity_guard.acquire():
        return JSONResponse(
            {"error": "service_overloaded", "retry_after": 5},
            status_code=503, headers={"Retry-After": "5"}
        )
    try:
        if request.url.path in ("/health", "/metrics", "/favicon.ico"):
            return await call_next(request)
        ip = request.client.host if request.client else "unknown"
        ok, retry = await rate_limiter.check(f"ip_sec:{ip}", RATE_LIMIT_PER_IP_PER_SEC, 1.0)
        if not ok:
            return JSONResponse({"error": "rate_limited", "retry_after": retry},
                                status_code=429, headers={"Retry-After": str(retry)})
        ok, retry = await rate_limiter.check(f"ip_min:{ip}", RATE_LIMIT_PER_IP_PER_MIN, 60.0)
        if not ok:
            return JSONResponse({"error": "rate_limited", "retry_after": retry},
                                status_code=429, headers={"Retry-After": str(retry)})
        return await call_next(request)
    finally:
        await capacity_guard.release()


# APP
@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("Bridge v%s starting", SERVER_VERSION)
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    await db.init()
    await write_queue.start()
    log.info("Ready. Trust API: %s", TRUST_API_URL)
    yield
    log.info("Shutting down")


app = FastAPI(
    title="HEXIS x NEWFLOW Bridge",
    description="Trust credential layer for AI compute economy v0.6.1",
    version=SERVER_VERSION,
    lifespan=lifespan,
)
app.middleware("http")(rate_limit_middleware)
app.add_middleware(CORSMiddleware, allow_origins=["*"],
                   allow_methods=["GET", "POST"], allow_headers=["*"])


# HTML UI
HTML_UI = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>HEXIS x NEWFLOW Bridge</title>
<style>
* { box-sizing: border-box; }
body { background: #f5f0e6; color: #2a2a2a; font-family: Georgia, serif; margin: 0; padding: 20px; max-width: 1200px; margin: 0 auto; }
h1 { font-size: 32px; margin-bottom: 5px; letter-spacing: -1px; }
.subtitle { text-transform: uppercase; letter-spacing: 2px; font-size: 13px; color: #666; margin-bottom: 25px; font-family: 'SF Mono', Monaco, monospace; }
.flow { display: flex; gap: 10px; flex-wrap: wrap; margin-bottom: 25px; font-family: 'SF Mono', Monaco, monospace; font-size: 12px; }
.flow-item { padding: 8px 12px; border: 1px solid #c4b896; border-radius: 4px; background: #fff; }
.flow-arrow { padding: 8px 4px; color: #999; }
.grid { display: grid; grid-template-columns: 1fr 1fr; gap: 20px; }
@media (max-width: 768px) { .grid { grid-template-columns: 1fr; } }
.step { background: #fff; border: 1px solid #c4b896; padding: 20px; border-radius: 4px; }
.step-title { color: #8b6914; font-size: 13px; letter-spacing: 1px; margin-bottom: 15px; font-family: 'SF Mono', Monaco, monospace; }
label { display: block; font-size: 13px; margin-bottom: 5px; margin-top: 10px; color: #555; font-family: 'SF Mono', Monaco, monospace; }
input, select { background: #faf6ed; border: 1px solid #d0c4a8; padding: 8px; font-family: 'SF Mono', Monaco, monospace; font-size: 14px; width: 100%; border-radius: 4px; }
button { background: #2a2a2a; color: #fff; border: none; padding: 12px; font-family: 'SF Mono', Monaco, monospace; font-weight: bold; cursor: pointer; width: 100%; border-radius: 4px; margin-top: 15px; font-size: 13px; letter-spacing: 1px; }
button:hover { background: #4a4a4a; }
button.green { background: #2d5a3d; }
button.green:hover { background: #3a6a4a; }
button.blue { background: #1e3a5f; }
button.blue:hover { background: #2a4a70; }
.result { background: #1a1a1a; color: #5ed; padding: 15px; font-family: 'SF Mono', Monaco, monospace; font-size: 11px; min-height: 60px; margin-top: 10px; border-radius: 4px; white-space: pre-wrap; overflow-x: auto; }
.note { font-size: 12px; color: #666; margin-top: 10px; font-family: 'SF Mono', Monaco, monospace; }
</style>
</head>
<body>
<h1>HEXIS x NEWFLOW</h1>
<div class="subtitle">TRUST CREDENTIAL LAYER FOR AI COMPUTE ECONOMY - BRIDGE v0.6.1</div>

<div class="flow">
<div class="flow-item">AI Agent</div><span class="flow-arrow">&rarr;</span>
<div class="flow-item">GET /trust/{worker}</div><span class="flow-arrow">&rarr;</span>
<div class="flow-item">HEXIS Score</div><span class="flow-arrow">&rarr;</span>
<div class="flow-item">POST /job/request</div><span class="flow-arrow">&rarr;</span>
<div class="flow-item">Mine HEXIS</div>
</div>

<div class="grid">

<div class="step">
<div class="step-title">STEP 1 - REGISTER WORKER NODE</div>
<label>Country (ISO)</label>
<select id="reg-country">
<option value="VN">Vietnam (VN)</option>
<option value="IN">India (IN)</option>
<option value="ID">Indonesia (ID)</option>
<option value="PH">Philippines (PH)</option>
<option value="BR">Brazil (BR)</option>
<option value="US">United States (US)</option>
<option value="GB">United Kingdom (GB)</option>
<option value="DE">Germany (DE)</option>
</select>
<label>Hardware Tier</label>
<select id="reg-tier">
<option value="1">1 - LOW (Phone/Edge, 30W)</option>
<option value="2" selected>2 - MEDIUM (RTX 3080, 320W)</option>
<option value="3">3 - HIGH (A100, 400W)</option>
<option value="4">4 - EXTREME (H100, 700W)</option>
</select>
<button onclick="registerWorker()">REGISTER WORKER NODE</button>
<div class="result" id="reg-result">Waiting...</div>
</div>

<div class="step">
<div class="step-title">STEP 2 - QUERY TRUST (x402 COMPATIBLE)</div>
<label>Actor ID (worker address or any ID)</label>
<input type="text" id="query-id" placeholder="3abc...xyz or potus_47">
<button class="blue" onclick="queryTrust()">GET /trust/{actor_id}</button>
<div class="result" id="query-result">Waiting...</div>
<div class="note">x402 Headers returned: X-Hexis-Score, X-Hexis-Grade, X-Hexis-Accept</div>
</div>

<div class="step">
<div class="step-title">STEP 3 - REQUEST COMPUTE JOB</div>
<label>Worker Address</label>
<input type="text" id="job-worker" placeholder="auto-filled after register">
<label>Task Type</label>
<select id="job-task">
<option value="llm_inference">LLM Inference</option>
<option value="image_generation">Image Generation</option>
<option value="model_training">Model Training</option>
<option value="data_processing">Data Processing</option>
</select>
<label>Fee (ECU)</label>
<input type="number" id="job-fee" value="50" step="any">
<button onclick="requestJob()">POST /job/request</button>
<div class="result" id="job-result">Waiting...</div>
</div>

<div class="step">
<div class="step-title">STEP 4 - COMPLETE JOB + MINE HEXIS</div>
<label>Job ID</label>
<input type="text" id="complete-id" placeholder="auto-filled after job request">
<button class="green" onclick="completeJob()">COMPLETE JOB + MINE HEXIS EVENT</button>
<div class="result" id="complete-result">Waiting...</div>
</div>

</div>

<script>
async function registerWorker() {
  const el = document.getElementById('reg-result');
  el.textContent = 'Registering...';
  try {
    const r = await fetch('/worker/register', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        country: document.getElementById('reg-country').value,
        hardware_tier: parseInt(document.getElementById('reg-tier').value)
      })
    });
    const d = await r.json();
    el.textContent = JSON.stringify(d, null, 2);
    if (d.worker_id) { document.getElementById('job-worker').value = d.worker_id; }
  } catch(e) { el.textContent = 'Error: ' + e.message; }
}

async function queryTrust() {
  const id = document.getElementById('query-id').value.trim();
  if (!id) return;
  const el = document.getElementById('query-result');
  el.textContent = 'Querying...';
  try {
    const r = await fetch('/trust/' + encodeURIComponent(id));
    el.textContent = JSON.stringify(await r.json(), null, 2);
  } catch(e) { el.textContent = 'Error: ' + e.message; }
}

async function requestJob() {
  const el = document.getElementById('job-result');
  el.textContent = 'Requesting...';
  try {
    const r = await fetch('/job/request', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        worker_id: document.getElementById('job-worker').value.trim(),
        task_type: document.getElementById('job-task').value,
        fee_ecu: parseFloat(document.getElementById('job-fee').value) || 0
      })
    });
    const d = await r.json();
    el.textContent = JSON.stringify(d, null, 2);
    if (d.job_id) { document.getElementById('complete-id').value = d.job_id; }
  } catch(e) { el.textContent = 'Error: ' + e.message; }
}

async function completeJob() {
  const id = document.getElementById('complete-id').value.trim();
  if (!id) return;
  const el = document.getElementById('complete-result');
  el.textContent = 'Completing...';
  try {
    const r = await fetch('/job/complete', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ job_id: id })
    });
    el.textContent = JSON.stringify(await r.json(), null, 2);
  } catch(e) { el.textContent = 'Error: ' + e.message; }
}
</script>
</body>
</html>"""


# ENDPOINTS
@app.get("/", response_class=HTMLResponse)
async def root():
    return HTML_UI


@app.get("/health")
async def health():
    return {"status": "ok", "version": SERVER_VERSION}


@app.get("/status")
async def status():
    cached = await cache.get("status", 5.0)
    if cached is not None:
        return cached
    async with db.read() as conn:
        rows = conn.execute("SELECT key, value FROM bridge_stats").fetchall()
    stats = {row["key"]: row["value"] for row in rows}
    result = {
        "version": SERVER_VERSION,
        "trust_api": TRUST_API_URL,
        "total_workers": int(stats.get("total_workers", 0)),
        "total_jobs": int(stats.get("total_jobs", 0)),
        "total_ecu_minted": round(stats.get("total_ecu_minted", 0), 6),
        "total_hexis_mined": round(stats.get("total_hexis_mined", 0), 6),
        "in_flight": capacity_guard.in_flight,
        "queue_pending": write_queue.queue.qsize(),
    }
    await cache.set("status", result)
    return result


@app.get("/metrics")
async def metrics():
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
    }


@app.post("/worker/register")
async def register_worker(req: WorkerRegister):
    tier_info = HARDWARE_TIERS.get(req.hardware_tier)
    if not tier_info:
        raise HTTPException(400, "invalid hardware_tier")
    tier_name, wattage, joules_per_hour = tier_info
    worker_id = "w_" + secrets.token_hex(8)
    now = int(time.time())
    queued = await write_queue.submit({
        "op": "register_worker",
        "worker_id": worker_id,
        "country": req.country.upper(),
        "tier": req.hardware_tier,
        "wattage": wattage,
        "joules_per_hour": joules_per_hour,
        "timestamp": now,
    })
    if not queued:
        raise HTTPException(503, "write queue full", headers={"Retry-After": "5"})
    return {
        "status": "registered",
        "worker_id": worker_id,
        "country": req.country.upper(),
        "hardware": tier_name,
        "wattage": wattage,
        "joules_per_hour": joules_per_hour,
        "c_multiplier": COUNTRY_C.get(req.country.upper(), 1.0),
    }


@app.get("/worker/{worker_id}")
async def get_worker(worker_id: str):
    cached = await cache.get(f"worker:{worker_id}", CACHE_TTL_SEC)
    if cached is not None:
        return cached
    async with db.read() as conn:
        row = conn.execute("SELECT * FROM workers WHERE worker_id = ?", (worker_id,)).fetchone()
    if not row:
        raise HTTPException(404, "worker not found")
    result = dict(row)
    await cache.set(f"worker:{worker_id}", result)
    return result


@app.get("/trust/{actor_id}")
async def proxy_trust(actor_id: str):
    """Proxy to Trust API for trust score lookup."""
    cached = await cache.get(f"trust:{actor_id}", CACHE_TTL_SEC)
    if cached is not None:
        return cached
    async with httpx.AsyncClient(timeout=5.0) as client:
        try:
            r = await client.get(f"{TRUST_API_URL}/trust/{actor_id}")
            data = r.json()
            await cache.set(f"trust:{actor_id}", data)
            return data
        except httpx.RequestError as e:
            raise HTTPException(503, f"trust API unreachable: {e}")


@app.post("/job/request")
async def request_job(req: JobRequest):
    ok, retry = await rate_limiter.check(
        f"worker:{req.worker_id}", RATE_LIMIT_PER_WORKER_PER_MIN, 60.0
    )
    if not ok:
        raise HTTPException(429, "worker rate limit exceeded",
                            headers={"Retry-After": str(retry)})
    job_id = "j_" + secrets.token_hex(8)
    now = int(time.time())
    queued = await write_queue.submit({
        "op": "create_job",
        "job_id": job_id,
        "worker_id": req.worker_id,
        "task_type": req.task_type,
        "fee_ecu": req.fee_ecu,
        "timestamp": now,
    })
    if not queued:
        raise HTTPException(503, "write queue full", headers={"Retry-After": "5"})
    return {
        "status": "created",
        "job_id": job_id,
        "worker_id": req.worker_id,
        "task_type": req.task_type,
        "fee_ecu": req.fee_ecu,
        "created_at": now,
    }


@app.post("/job/complete")
async def complete_job(req: JobComplete):
    async with db.read() as conn:
        job = conn.execute("SELECT * FROM jobs WHERE job_id = ?", (req.job_id,)).fetchone()
    if not job:
        raise HTTPException(404, "job not found")
    if job["status"] == "completed":
        raise HTTPException(400, "job already completed")
    worker_id = job["worker_id"]
    fee_ecu = job["fee_ecu"]
    async with db.read() as conn:
        worker = conn.execute("SELECT * FROM workers WHERE worker_id = ?",
                              (worker_id,)).fetchone()
    if not worker:
        raise HTTPException(404, "worker not found")
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            r = await client.post(f"{TRUST_API_URL}/integrity/submit", json={
                "actor_id": worker_id,
                "country": worker["country"],
                "fee": fee_ecu,
                "event_type": job["task_type"],
                "tier": 1,
            })
            api_response = r.json()
            hexis_minted = api_response.get("hexis_minted", 0)
        except httpx.RequestError as e:
            raise HTTPException(503, f"trust API unreachable: {e}")
    queued = await write_queue.submit({
        "op": "complete_job",
        "job_id": req.job_id,
        "worker_id": worker_id,
        "fee_ecu": fee_ecu,
        "hexis_minted": hexis_minted,
        "timestamp": int(time.time()),
    })
    if not queued:
        raise HTTPException(503, "write queue full", headers={"Retry-After": "5"})
    return {
        "status": "completed",
        "job_id": req.job_id,
        "worker_id": worker_id,
        "ecu_paid": fee_ecu,
        "hexis_minted": hexis_minted,
        "trust_api_response": api_response,
    }


@app.get("/job/{job_id}")
async def get_job(job_id: str):
    cached = await cache.get(f"job:{job_id}", CACHE_TTL_SEC)
    if cached is not None:
        return cached
    async with db.read() as conn:
        row = conn.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
    if not row:
        raise HTTPException(404, "job not found")
    result = dict(row)
    await cache.set(f"job:{job_id}", result)
    return result


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8400, log_level="info", access_log=False)
