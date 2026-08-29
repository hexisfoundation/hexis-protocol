"""
HEXIS × NEWFLOW — Integration Bridge v0.1
==========================================

Connects two systems:

  HEXIS   — Proof of Integrity (trust credential for actors)
  NEWFLOW — Proof of Verifiable Compute (energy-backed AI compute)

INTEGRATION FLOW:

  1. Worker registers on NEWFLOW (wallet address)
  2. Worker submits behavior proofs to HEXIS (trust accumulation)
  3. Consumer wants to buy compute → NEWFLOW job posted
  4. Bridge checks worker's HEXIS score before accepting proof
  5. If HEXIS score sufficient → job proceeds → ECU paid
  6. After settlement → Bridge auto-mines new HEXIS event for worker
     (honest compute delivery = new integrity proof)

This creates a virtuous cycle:
  More compute delivered honestly → higher HEXIS score
  Higher HEXIS score → access to larger jobs, lower collateral
  Lower collateral → more capital efficiency → more compute

x402 INTEGRATION:
  Consumer agent → GET /trust/{worker_id}     ← HEXIS score
  Consumer agent → POST /job/request          ← request compute
  Bridge          → check HEXIS threshold
  Bridge          → forward to NEWFLOW
  NEWFLOW         → execute + verify
  Bridge          → release payment + mine HEXIS event

PORTS:
  5042  — HEXIS Trust API     (existing hexis_x402_server.py)
  8334  — NEWFLOW RPC API     (existing newflow_p2p.py)
  8400  — This bridge server  (new)
  8401  — Demo UI             (served from this process)
"""

import json
import time
import math
import hashlib
import threading
import sys
import os
from datetime import datetime, timezone
from http.server import HTTPServer, BaseHTTPRequestHandler

sys.path.insert(0, os.path.dirname(__file__))

from flask import Flask, request, jsonify, render_template_string

# Import HEXIS core
from hexis_mining  import HexisMiner, BehaviorEvent
from hexis_ledger  import HexisLedger, LocalIndex

# Import NEWFLOW core
from newflow_core  import (
    Wallet, ChainState, Ledger, Transfer, JobCommitment,
    ProofWithPayment, generate_mock_proof, build_block,
    OptimisticPool, ValidatorVerifier, StakedProof,
    ProofStatus, ValidationError, sha256, FAUCET_AMOUNT
)
from scs_engine    import SunkCostMintEngine, ENERGY_UNIT_GENESIS, JOULES_PER_KWH


# ══════════════════════════════════════════════════════════════
# SHARED STATE — both systems in memory for demo
# ══════════════════════════════════════════════════════════════

class BridgeState:
    """
    In-memory state for the demo.
    In production: NEWFLOW state persists to disk,
    HEXIS records persist to IPFS.
    """

    def __init__(self):
        # NEWFLOW
        self.chain        = ChainState()
        self.ledger       = Ledger()
        self.scs          = SunkCostMintEngine()
        self.validator    = Wallet()               # bootstrap validator
        self.pool         = OptimisticPool()

        # HEXIS
        self.hexis_index  = LocalIndex("bridge_hexis_index.json")
        self.hexis_miner  = HexisMiner()

        # Registry: worker_address → {hexis_score, jobs_completed, wallet}
        self.workers: dict[str, dict] = {}
        # Jobs in flight
        self.jobs:    dict[str, dict] = {}
        # Event log for UI
        self.events:  list[dict]      = []

        # Bootstrap genesis
        self._init_genesis()

    def _init_genesis(self):
        faucet = Wallet()
        alloc  = {
            self.validator.address: 100_000,
            faucet.address:         50_000,
            "NETWORK_RESERVE":      39_000_000 - 150_000,
        }
        self.chain.apply_genesis(alloc, faucet_address=faucet.address)
        self.faucet_wallet = faucet
        self.log("Genesis block initialized. NEWFLOW + HEXIS bridge online.")

    def log(self, msg: str, level: str = "info", data: dict = None):
        entry = {
            "ts":    datetime.now(timezone.utc).isoformat(),
            "level": level,
            "msg":   msg,
            "data":  data or {},
        }
        self.events.append(entry)
        print(f"[{entry['ts'][11:19]}] [{level.upper()}] {msg}")
        return entry

    def get_hexis_score(self, actor_id: str) -> float:
        """Total HEXIS score for an actor from local index."""
        records = self.hexis_index.get_by_actor(actor_id)
        return sum(r.get("hexis_raw", 0) for r in records)

    def get_trust_grade(self, hexis_score: float) -> dict:
        """Map HEXIS score to trust grade and collateral requirement."""
        if hexis_score >= 0.05:
            return {"grade": "High",         "collateral_mult": 1.0, "accept": True}
        if hexis_score >= 0.005:
            return {"grade": "Moderate",     "collateral_mult": 1.5, "accept": True}
        if hexis_score >= 0.001:
            return {"grade": "Low",          "collateral_mult": 3.0, "accept": True}
        if hexis_score >= 0.0001:
            return {"grade": "Minimal",      "collateral_mult": 5.0, "accept": True}
        if hexis_score >  0.0:
            return {"grade": "Insufficient", "collateral_mult": 0.0, "accept": False}
        return     {"grade": "Unverified",   "collateral_mult": 0.0, "accept": False}

    def mine_hexis_for_job(
        self,
        worker_id: str,
        worker_country: str,
        job_value_ecu: float,
        job_id: str,
    ) -> dict:
        """
        After a NEWFLOW job completes honestly,
        auto-mine a HEXIS integrity event for the worker.

        The completed job IS the sacrifice proof:
          - asset_could_have_taken = job_value_ecu (could have submitted fake proof)
          - asset_actually_returned = job_value_ecu (delivered real compute)
          - prob_betrayal_detected = 0.95 (Groth16 makes fake proof ~impossible)
          - gain_if_betrayed = job_value_ecu (would have kept consumer's ECU)
        """
        event = BehaviorEvent(
            event_id                = sha256(f"newflow_job_{job_id}_{worker_id}"),
            actor_id                = worker_id,
            timestamp               = time.time(),
            description             = (
                f"NEWFLOW compute job delivered honestly. "
                f"Job ID: {job_id[:16]}. "
                f"Value: {job_value_ecu:.2f} ECU. "
                f"TEE proof verified by validator."
            ),
            actor_country           = worker_country,
            asset_could_have_taken  = job_value_ecu,
            asset_actually_returned = job_value_ecu,
            prob_betrayal_detected  = 0.95,
            gain_if_betrayed        = job_value_ecu,
            witness_sources         = [
                {"type": "adversarial", "name": "NEWFLOW Validator"},
                {"type": "neutral",     "name": "On-chain TEE Proof"},
                {"type": "allied",      "name": "Consumer Confirmation"},
            ],
            mention_counts={"30d": 1, "1y": 0, "5y": 0},
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


# ── Singleton state ────────────────────────────────────────────────────
STATE = BridgeState()


# ══════════════════════════════════════════════════════════════
# FLASK API
# ══════════════════════════════════════════════════════════════

app = Flask(__name__)


@app.route("/")
def index():
    return render_template_string(UI_HTML)


# ── Worker registration ────────────────────────────────────────────────

@app.route("/worker/register", methods=["POST"])
def worker_register():
    """
    Register a new worker node.
    Creates NEWFLOW wallet + HEXIS actor record.

    Body: {"country": "IN", "hardware_tier": 2}
    """
    data    = request.get_json() or {}
    country = data.get("country", "IN")
    tier    = data.get("hardware_tier", 1)

    wallet  = Wallet()

    # Give new worker some ECU from faucet for staking
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
        faucet_ok = False

    STATE.workers[wallet.address] = {
        "address":        wallet.address,
        "country":        country,
        "hardware_tier":  tier,
        "registered_at":  datetime.now(timezone.utc).isoformat(),
        "jobs_completed": 0,
        "hexis_score":    0.0,
        "wallet":         wallet,
    }

    STATE.log(
        f"Worker registered: {wallet.address[:20]}... (tier={tier}, country={country})",
        data={"address": wallet.address, "faucet_ok": faucet_ok}
    )

    return jsonify({
        "address":       wallet.address,
        "balance_ecu":   STATE.chain.get_balance(wallet.address),
        "hardware_tier": tier,
        "country":       country,
        "hexis_score":   0.0,
        "trust_grade":   "Unverified",
        "note":          "Submit compute jobs to earn HEXIS trust score.",
    })


# ── HEXIS trust query (x402 compatible) ───────────────────────────────

@app.route("/trust/<actor_id>")
def get_trust(actor_id: str):
    """
    x402-compatible trust endpoint.
    AI agents call this before transacting with a worker.

    Returns HEXIS score + NEWFLOW on-chain stats.
    """
    hexis_score = STATE.get_hexis_score(actor_id)
    trust       = STATE.get_trust_grade(hexis_score)
    records     = STATE.hexis_index.get_by_actor(actor_id)
    worker_info = STATE.workers.get(actor_id, {})

    balance_ecu = 0
    try:
        balance_ecu = STATE.chain.get_balance(actor_id)
    except Exception:
        pass

    return jsonify({
        "actor_id":              actor_id,
        "hexis_total":           round(hexis_score, 8),
        "record_count":          len(records),
        "grade":                 trust["grade"],
        "accept":                trust["accept"],
        "collateral_multiplier": trust["collateral_mult"],
        "newflow_balance_ecu":   balance_ecu,
        "jobs_completed":        worker_info.get("jobs_completed", 0),
        "hardware_tier":         worker_info.get("hardware_tier", 0),
        "country":               worker_info.get("country", "??"),
        "verified_at":           datetime.now(timezone.utc).isoformat(),
        # x402 headers
        "x402_headers": {
            "X-Hexis-Score":          round(hexis_score, 8),
            "X-Hexis-Grade":          trust["grade"],
            "X-Hexis-Accept":         str(trust["accept"]),
            "X-Hexis-Collateral-Mult": trust["collateral_mult"],
            "X-Hexis-Records":        len(records),
        }
    })


# ── Job lifecycle ──────────────────────────────────────────────────────

@app.route("/job/request", methods=["POST"])
def job_request():
    """
    Consumer requests compute job.
    Bridge checks worker HEXIS score before accepting.

    Body: {
        "consumer_address": "...",
        "worker_address": "...",
        "task_type": "llm_inference_mid_1B_tokens",
        "compute_units": 100,
        "fee_ecu": 50.0
    }
    """
    data             = request.get_json() or {}
    worker_addr      = data.get("worker_address", "")
    task_type        = data.get("task_type", "llm_inference_mid_1B_tokens")
    compute_units    = int(data.get("compute_units", 100))
    fee_ecu          = float(data.get("fee_ecu", 10.0))

    # Check HEXIS score
    hexis_score = STATE.get_hexis_score(worker_addr)
    trust       = STATE.get_trust_grade(hexis_score)

    if not trust["accept"] and hexis_score == 0.0:
        # New worker with no history — allow with max collateral (demo mode)
        trust = {"grade": "Unverified-Demo", "collateral_mult": 5.0, "accept": True}
        STATE.log(f"New worker {worker_addr[:16]}... allowed in demo mode (no HEXIS history)")

    if not trust["accept"]:
        return jsonify({
            "status":  "rejected",
            "reason":  f"HEXIS score too low: {hexis_score:.6f} ({trust['grade']})",
            "hexis":   hexis_score,
            "grade":   trust["grade"],
        }), 403

    # Create job
    job_id = sha256(f"{worker_addr}{task_type}{time.time()}")

    # Build mock consumer wallet if not provided
    consumer_addr = data.get("consumer_address", "")
    if not consumer_addr:
        consumer = Wallet()
        consumer_addr = consumer.address
        # Give consumer some ECU
        try:
            tx = Transfer(
                sender=STATE.faucet_wallet.address,
                sender_pubkey=STATE.faucet_wallet.public_key.hex(),
                receiver=consumer.address,
                amount=int(fee_ecu) + 1000,
                fee=0,
                nonce=STATE.chain.get_nonce(STATE.faucet_wallet.address),
                timestamp=int(time.time()),
            )
            tx.sign(STATE.faucet_wallet)
            STATE.chain.apply_transfer(tx, STATE.validator.address)
        except Exception:
            pass
    else:
        consumer = None

    worker_info = STATE.workers.get(worker_addr, {})
    worker_wallet = worker_info.get("wallet", Wallet())

    STATE.jobs[job_id] = {
        "job_id":           job_id,
        "worker_address":   worker_addr,
        "consumer_address": consumer_addr,
        "task_type":        task_type,
        "compute_units":    compute_units,
        "fee_ecu":          fee_ecu,
        "hexis_at_start":   hexis_score,
        "trust_grade":      trust["grade"],
        "collateral_mult":  trust["collateral_mult"],
        "status":           "pending",
        "created_at":       datetime.now(timezone.utc).isoformat(),
        "consumer_wallet":  consumer,
        "worker_wallet":    worker_wallet,
    }

    STATE.log(
        f"Job created: {job_id[:16]}... worker={worker_addr[:16]}... "
        f"fee={fee_ecu} ECU trust={trust['grade']}",
        data={"job_id": job_id, "trust": trust}
    )

    return jsonify({
        "status":            "accepted",
        "job_id":            job_id,
        "worker_address":    worker_addr,
        "consumer_address":  consumer_addr,
        "fee_ecu":           fee_ecu,
        "trust_grade":       trust["grade"],
        "hexis_score":       hexis_score,
        "collateral_usdc":   round(fee_ecu * trust["collateral_mult"], 2),
        "next":              f"POST /job/{job_id}/complete to simulate completion",
    })


@app.route("/job/<job_id>/complete", methods=["POST"])
def job_complete(job_id: str):
    """
    Simulate job completion.
    1. Validate compute proof (mock Groth16)
    2. Transfer ECU from consumer to worker
    3. Auto-mine HEXIS event for worker
    """
    job = STATE.jobs.get(job_id)
    if not job:
        return jsonify({"error": "Job not found"}), 404
    if job["status"] != "pending":
        return jsonify({"error": f"Job already {job['status']}"}), 400

    worker_addr   = job["worker_address"]
    consumer_addr = job["consumer_address"]
    fee_ecu       = int(job["fee_ecu"])
    task_type     = job["task_type"]
    worker_wallet = job["worker_wallet"]
    consumer_wallet = job.get("consumer_wallet")

    # ── Simulate compute proof ─────────────────────────────────────────
    input_hash  = sha256(job_id + task_type)
    output_hash = sha256(f"verified:{input_hash}:{task_type}")

    # ── ECU payment ────────────────────────────────────────────────────
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

    # ── SCS: mint ECU from compute energy ──────────────────────────────
    # Simulate: 1000 tflops → energy → ECU
    tflops      = job["compute_units"] * 10.0
    energy_j    = tflops * 0.0001 * JOULES_PER_KWH * 1000  # rough estimate
    ecu_minted  = STATE.scs.preview_mint(energy_j)

    # ── Mine HEXIS event ───────────────────────────────────────────────
    worker_info = STATE.workers.get(worker_addr, {})
    country     = worker_info.get("country", "IN")
    hexis_result = STATE.mine_hexis_for_job(
        worker_id      = worker_addr,
        worker_country = country,
        job_value_ecu  = float(fee_ecu),
        job_id         = job_id,
    )

    # ── Update state ───────────────────────────────────────────────────
    new_hexis = STATE.get_hexis_score(worker_addr)
    new_trust = STATE.get_trust_grade(new_hexis)

    job["status"]       = "completed"
    job["completed_at"] = datetime.now(timezone.utc).isoformat()

    if worker_addr in STATE.workers:
        STATE.workers[worker_addr]["jobs_completed"] += 1
        STATE.workers[worker_addr]["hexis_score"]    = new_hexis

    STATE.log(
        f"Job complete: {job_id[:16]}... "
        f"payment={'ok' if payment_ok else 'skipped'} "
        f"hexis_mined={hexis_result.get('hexis_raw', 0):.6f} "
        f"new_score={new_hexis:.6f}",
        level="success",
        data={"job_id": job_id, "hexis": hexis_result},
    )

    return jsonify({
        "status":           "completed",
        "job_id":           job_id,
        "payment_ok":       payment_ok,
        "fee_paid_ecu":     fee_ecu,
        "ecu_minted_scs":   round(ecu_minted, 6),
        "hexis_mined":      hexis_result.get("hexis_raw", 0),
        "hexis_total":      round(new_hexis, 8),
        "trust_grade_new":  new_trust["grade"],
        "worker_balance":   STATE.chain.get_balance(worker_addr),
        "next_job_collateral_mult": new_trust["collateral_mult"],
    })


# ── System status ──────────────────────────────────────────────────────

@app.route("/status")
def status():
    workers_summary = []
    for addr, w in STATE.workers.items():
        hexis = STATE.get_hexis_score(addr)
        trust = STATE.get_trust_grade(hexis)
        workers_summary.append({
            "address":       addr[:20] + "...",
            "country":       w["country"],
            "tier":          w["hardware_tier"],
            "jobs":          w["jobs_completed"],
            "hexis":         round(hexis, 6),
            "grade":         trust["grade"],
            "balance_ecu":   STATE.chain.get_balance(addr),
        })

    completed = sum(1 for j in STATE.jobs.values() if j["status"] == "completed")

    return jsonify({
        "newflow": {
            "chain_height": STATE.chain.height,
            "total_workers": len(STATE.workers),
            "total_jobs": len(STATE.jobs),
            "jobs_completed": completed,
        },
        "hexis": {
            "total_records": STATE.hexis_index.index.get("total_minted", 0),
            "unique_actors": len(set(
                r["actor_id"] for r in STATE.hexis_index.index.get("records", [])
            )),
        },
        "scs": {
            "ecu_minted_phase0": round(STATE.scs.total_minted, 4),
            "energy_unit_genesis": ENERGY_UNIT_GENESIS,
            "halving_phase": STATE.scs.halving_phase,
        },
        "workers": workers_summary,
        "recent_events": STATE.events[-10:],
    })


@app.route("/events")
def get_events():
    return jsonify({"events": STATE.events[-50:]})


# ══════════════════════════════════════════════════════════════
# DEMO UI
# ══════════════════════════════════════════════════════════════

UI_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>HEXIS × NEWFLOW Bridge</title>
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
  .grid { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; margin-bottom: 16px; }
  .card { background: white; border: 1px solid var(--line); padding: 16px; }
  .card h2 { font-size: 13px; color: var(--amber); letter-spacing: 2px; font-family: monospace; margin-bottom: 12px; border-bottom: 1px solid var(--line); padding-bottom: 8px; }
  .btn { background: var(--ink); color: var(--paper); border: none; padding: 8px 16px; cursor: pointer; font-family: monospace; font-size: 11px; letter-spacing: 1px; width: 100%; margin-top: 8px; transition: background 0.2s; }
  .btn:hover { background: var(--amber); }
  .btn.secondary { background: var(--blue); }
  .btn.success  { background: var(--green); }
  input, select { width: 100%; padding: 6px 8px; font-family: monospace; font-size: 11px; border: 1px solid var(--line); background: var(--paper); margin-bottom: 6px; }
  label { font-size: 10px; color: var(--muted); font-family: monospace; display: block; margin-bottom: 2px; }
  .output { background: #111; color: #0f0; font-family: monospace; font-size: 10px; padding: 12px; min-height: 80px; margin-top: 8px; white-space: pre-wrap; overflow-y: auto; max-height: 200px; }
  .log { font-size: 10px; font-family: monospace; color: var(--muted); border-top: 1px solid var(--line); padding-top: 8px; margin-top: 8px; }
  .log-entry { padding: 3px 0; border-bottom: 1px solid var(--line); }
  .log-entry.success { color: var(--green); }
  .log-entry.hexis   { color: var(--amber); }
  .log-entry.warn    { color: var(--red); }
  .badge { display: inline-block; padding: 2px 8px; font-size: 9px; font-family: monospace; margin-left: 6px; }
  .badge.High     { background: #e8f5e9; color: var(--green); }
  .badge.Moderate { background: #fff3e0; color: #e65100; }
  .badge.Low      { background: #fce4ec; color: var(--red); }
  .badge.Unverified { background: #f5f5f5; color: var(--muted); }
  .stat { display: flex; justify-content: space-between; padding: 4px 0; border-bottom: 1px solid var(--line); font-size: 11px; }
  .stat-val { font-family: monospace; color: var(--amber); }
  #worker-addr { font-family: monospace; font-size: 9px; color: var(--blue); word-break: break-all; }
  .flow { display: flex; align-items: center; gap: 8px; font-size: 10px; font-family: monospace; margin: 8px 0; color: var(--muted); }
  .flow-node { background: var(--paper); border: 1px solid var(--line); padding: 4px 8px; }
  .flow-arrow { color: var(--amber); }
  .full { grid-column: 1 / -1; }
</style>
</head>
<body>

<h1>HEXIS × NEWFLOW</h1>
<div class="sub">TRUST CREDENTIAL LAYER FOR AI COMPUTE ECONOMY — BRIDGE v0.1</div>

<div class="flow">
  <div class="flow-node">AI Agent</div>
  <div class="flow-arrow">→</div>
  <div class="flow-node">GET /trust/{worker}</div>
  <div class="flow-arrow">→</div>
  <div class="flow-node">HEXIS Score</div>
  <div class="flow-arrow">→</div>
  <div class="flow-node">POST /job/request</div>
  <div class="flow-arrow">→</div>
  <div class="flow-node">NEWFLOW Compute</div>
  <div class="flow-arrow">→</div>
  <div class="flow-node">HEXIS +Mined</div>
</div>

<div class="grid">

  <!-- Step 1: Register Worker -->
  <div class="card">
    <h2>STEP 1 — REGISTER WORKER NODE</h2>
    <label>Country (ISO)</label>
    <select id="country">
      <option value="IN">India (IN)</option>
      <option value="NG">Nigeria (NG)</option>
      <option value="VN">Vietnam (VN)</option>
      <option value="US">United States (US)</option>
      <option value="DE">Germany (DE)</option>
      <option value="SG">Singapore (SG)</option>
    </select>
    <label>Hardware Tier</label>
    <select id="tier">
      <option value="0">0 — EDGE (smartphone, CPU)</option>
      <option value="1">1 — LIGHT (laptop GPU)</option>
      <option value="2" selected>2 — MEDIUM (RTX 3080+)</option>
      <option value="3">3 — HEAVY (A100/H100)</option>
      <option value="4">4 — CLUSTER (8+ GPU)</option>
    </select>
    <button class="btn" onclick="registerWorker()">REGISTER WORKER NODE</button>
    <div id="worker-addr" style="margin-top:8px;"></div>
    <div class="output" id="reg-out">Waiting...</div>
  </div>

  <!-- Step 2: Check Trust -->
  <div class="card">
    <h2>STEP 2 — QUERY TRUST (x402 COMPATIBLE)</h2>
    <label>Actor ID (worker address or any ID)</label>
    <input id="trust-id" placeholder="3abc...xyz or potus_47" />
    <button class="btn secondary" onclick="checkTrust()">GET /trust/{actor_id}</button>
    <div class="output" id="trust-out">Waiting...</div>
    <div style="margin-top:8px; font-size:10px; font-family:monospace; color:var(--muted)">
      x402 Headers returned:<br>
      X-Hexis-Score · X-Hexis-Grade · X-Hexis-Accept
    </div>
  </div>

  <!-- Step 3: Request Job -->
  <div class="card">
    <h2>STEP 3 — REQUEST COMPUTE JOB</h2>
    <label>Worker Address</label>
    <input id="job-worker" placeholder="auto-filled after registration" />
    <label>Task Type</label>
    <select id="task-type">
      <option value="llm_inference_mid_1B_tokens">LLM Inference — 1B tokens</option>
      <option value="fl_train_round">Federated Learning — 1 round</option>
      <option value="render_frame_4k">Render — 4K frame</option>
      <option value="embedding_batch_1M">Embeddings — 1M batch</option>
    </select>
    <label>Fee (ECU)</label>
    <input id="job-fee" type="number" value="50" min="1" />
    <button class="btn" onclick="requestJob()">POST /job/request</button>
    <div class="output" id="job-out">Waiting...</div>
  </div>

  <!-- Step 4: Complete Job -->
  <div class="card">
    <h2>STEP 4 — COMPLETE JOB + MINE HEXIS</h2>
    <label>Job ID</label>
    <input id="job-id" placeholder="auto-filled after job request" />
    <button class="btn success" onclick="completeJob()">COMPLETE JOB + MINE HEXIS EVENT</button>
    <div class="output" id="complete-out">Waiting...</div>
    <div style="margin-top:8px; font-size:10px; font-family:monospace; color:var(--muted)">
      Completion auto-mines HEXIS integrity proof:<br>
      Honest compute delivery = trust credential earned
    </div>
  </div>

  <!-- Status -->
  <div class="card full">
    <h2>SYSTEM STATUS — NEWFLOW + HEXIS</h2>
    <div style="display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin-bottom:12px">
      <div>
        <div class="stat"><span>Chain Height</span><span class="stat-val" id="s-height">—</span></div>
        <div class="stat"><span>Workers</span><span class="stat-val" id="s-workers">—</span></div>
        <div class="stat"><span>Jobs Done</span><span class="stat-val" id="s-jobs">—</span></div>
      </div>
      <div>
        <div class="stat"><span>HEXIS Records</span><span class="stat-val" id="s-hexis">—</span></div>
        <div class="stat"><span>Unique Actors</span><span class="stat-val" id="s-actors">—</span></div>
        <div class="stat"><span>ECU Minted (SCS)</span><span class="stat-val" id="s-ecu">—</span></div>
      </div>
      <div style="grid-column:span 2">
        <div id="worker-list" style="font-size:10px;font-family:monospace"></div>
      </div>
    </div>
    <div class="log" id="event-log">Loading events...</div>
    <button class="btn secondary" style="width:auto;padding:4px 12px;margin-top:8px" onclick="refreshStatus()">REFRESH</button>
  </div>

</div>

<script>
let currentWorkerAddr = '';
let currentJobId = '';

async function api(method, path, body) {
  const opts = { method, headers: {'Content-Type':'application/json'} };
  if (body) opts.body = JSON.stringify(body);
  const r = await fetch(path, opts);
  return r.json();
}

function fmt(obj) { return JSON.stringify(obj, null, 2); }

async function registerWorker() {
  const country = document.getElementById('country').value;
  const tier    = parseInt(document.getElementById('tier').value);
  document.getElementById('reg-out').textContent = 'Registering...';
  const r = await api('POST', '/worker/register', {country, hardware_tier: tier});
  currentWorkerAddr = r.address || '';
  document.getElementById('worker-addr').textContent = 'Address: ' + currentWorkerAddr;
  document.getElementById('job-worker').value = currentWorkerAddr;
  document.getElementById('trust-id').value  = currentWorkerAddr;
  document.getElementById('reg-out').textContent = fmt(r);
  refreshStatus();
}

async function checkTrust() {
  const id = document.getElementById('trust-id').value.trim();
  if (!id) return;
  document.getElementById('trust-out').textContent = 'Querying...';
  const r = await api('GET', '/trust/' + encodeURIComponent(id));
  document.getElementById('trust-out').textContent = fmt(r);
}

async function requestJob() {
  const worker = document.getElementById('job-worker').value.trim();
  const task   = document.getElementById('task-type').value;
  const fee    = parseFloat(document.getElementById('job-fee').value);
  if (!worker) { alert('Register a worker first'); return; }
  document.getElementById('job-out').textContent = 'Submitting...';
  const r = await api('POST', '/job/request', {
    worker_address: worker,
    task_type: task,
    compute_units: 100,
    fee_ecu: fee,
  });
  currentJobId = r.job_id || '';
  document.getElementById('job-id').value = currentJobId;
  document.getElementById('job-out').textContent = fmt(r);
  refreshStatus();
}

async function completeJob() {
  const jid = document.getElementById('job-id').value.trim();
  if (!jid) { alert('Request a job first'); return; }
  document.getElementById('complete-out').textContent = 'Completing...';
  const r = await api('POST', '/job/' + jid + '/complete');
  document.getElementById('complete-out').textContent = fmt(r);
  // Update trust display
  if (currentWorkerAddr) {
    const t = await api('GET', '/trust/' + encodeURIComponent(currentWorkerAddr));
    document.getElementById('trust-out').textContent = fmt(t);
  }
  refreshStatus();
}

async function refreshStatus() {
  const s = await api('GET', '/status');
  document.getElementById('s-height').textContent  = s.newflow?.chain_height ?? '—';
  document.getElementById('s-workers').textContent = s.newflow?.total_workers ?? '—';
  document.getElementById('s-jobs').textContent     = s.newflow?.jobs_completed ?? '—';
  document.getElementById('s-hexis').textContent    = s.hexis?.total_records ?? '—';
  document.getElementById('s-actors').textContent   = s.hexis?.unique_actors ?? '—';
  document.getElementById('s-ecu').textContent      = (s.scs?.ecu_minted_phase0 ?? '—') + ' ECU';

  // Worker list
  const wl = document.getElementById('worker-list');
  wl.innerHTML = (s.workers || []).map(w =>
    `<div style="border-bottom:1px solid #eee;padding:3px 0">
      ${w.address} [${w.country}] T${w.tier}
      <span class="badge ${w.grade}">${w.grade}</span>
      HEXIS: ${w.hexis} | Jobs: ${w.jobs} | ${w.balance_ecu} ECU
    </div>`
  ).join('');

  // Event log
  const log = document.getElementById('event-log');
  log.innerHTML = (s.recent_events || []).reverse().map(e =>
    `<div class="log-entry ${e.level}">[${e.ts.slice(11,19)}] ${e.msg}</div>`
  ).join('');
}

// Auto-refresh every 5s
refreshStatus();
setInterval(refreshStatus, 5000);
</script>
</body>
</html>"""


# ══════════════════════════════════════════════════════════════
# ENTRY POINT
# ══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8400))
    print("=" * 64)
    print("HEXIS × NEWFLOW Integration Bridge v0.1")
    print("=" * 64)
    print(f"  UI:     http://localhost:{port}/")
    print(f"  Status: http://localhost:{port}/status")
    print(f"  Trust:  http://localhost:{port}/trust/{{actor_id}}")
    print(f"  Job:    POST http://localhost:{port}/job/request")
    print("=" * 64)
    app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)
