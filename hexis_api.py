"""
HEXIS Trust API — Standalone Server v0.1
=========================================
Port 8401 — độc lập hoàn toàn với NEWFLOW

Endpoint duy nhất mà AI agents, Pay.sh, x402,
OKX APP cần để verify trust trước khi giao dịch.

ENDPOINTS:
    GET  /                          UI + docs
    GET  /trust/{actor_id}          Trust score + x402 headers
    POST /integrity/submit          Submit integrity event
    GET  /integrity/{actor_id}      History của actor
    GET  /leaderboard               Top actors by score
    GET  /status                    System status

TÍCH HỢP:
    x402:    dùng X-Hexis-* headers để kiểm soát collateral
    Pay.sh:  check trust trước khi route job
    OKX APP: verify agent trước khi settle

DÙNG NGAY:
    curl http://174.138.9.102:8401/trust/any_agent_id

CHẠY:
    pip install flask cryptography
    python3 hexis_api.py
"""

import json
import math
import hashlib
import time
import os
from datetime import datetime, timezone
from flask import Flask, request, jsonify, render_template_string

app = Flask(__name__)

# ══════════════════════════════════════════════════════════════
# PROTOCOL CONSTANTS
# ══════════════════════════════════════════════════════════════

TOTAL_SUPPLY    = 12_800_000
WALLET_CAP      =     10_000
REFERENCE_GDP   =     12_000

GDP_PER_CAPITA = {
    "US": 80_000, "GB": 48_000, "DE": 52_000, "JP": 34_000,
    "KR": 33_000, "SG": 88_000, "FR": 44_000, "CA": 55_000,
    "CN": 13_000, "BR":  9_000, "RU": 14_000, "IN":  2_500,
    "VN":  4_200, "ID":  4_900, "PH":  3_700, "TH":  7_000,
    "MY": 12_000, "NG":  2_200, "KE":  2_100, "ZA":  6_000,
    "AU": 65_000, "NZ": 48_000, "SE": 56_000, "NO": 100_000,
    "DEFAULT": 12_000,
}

GRADE_THRESHOLDS = [
    (0.05000, "High",     1.0),
    (0.00500, "Moderate", 1.5),
    (0.00100, "Low",      3.0),
    (0.00010, "Minimal",  5.0),
]


# ══════════════════════════════════════════════════════════════
# IN-MEMORY LEDGER
# ══════════════════════════════════════════════════════════════

class HexisLedger:
    def __init__(self):
        self.actors    = {}   # actor_id → {score, events, country, first_seen}
        self.events    = []   # all events chronologically
        self.total_mined = 0.0

    def get_or_create(self, actor_id: str, country: str = "DEFAULT") -> dict:
        if actor_id not in self.actors:
            self.actors[actor_id] = {
                "actor_id":   actor_id,
                "country":    country,
                "hexis_total": 0.0,
                "event_count": 0,
                "first_seen":  datetime.now(timezone.utc).isoformat(),
                "last_seen":   datetime.now(timezone.utc).isoformat(),
            }
        return self.actors[actor_id]

    def record(self, actor_id: str, hexis_amount: float,
               country: str, source: str, metadata: dict) -> dict:
        actor = self.get_or_create(actor_id, country)

        new_total = actor["hexis_total"] + hexis_amount
        # Wallet cap
        new_total = min(new_total, WALLET_CAP)

        actor["hexis_total"]  = new_total
        actor["event_count"] += 1
        actor["last_seen"]    = datetime.now(timezone.utc).isoformat()

        event = {
            "id":          hashlib.sha256(
                               f"{actor_id}{hexis_amount}{time.time()}".encode()
                           ).hexdigest()[:16],
            "actor_id":    actor_id,
            "hexis_mined": hexis_amount,
            "hexis_total": new_total,
            "source":      source,
            "country":     country,
            "ts":          datetime.now(timezone.utc).isoformat(),
            "metadata":    metadata,
        }
        self.events.append(event)
        self.total_mined += hexis_amount
        return event

    def get_grade(self, score: float) -> tuple:
        for threshold, grade, collateral in GRADE_THRESHOLDS:
            if score >= threshold:
                return grade, collateral
        return "Unverified", 10.0

    def get_trust(self, actor_id: str) -> dict:
        if actor_id not in self.actors:
            grade, collateral = "Unverified", 10.0
            return {
                "actor_id":         actor_id,
                "hexis_total":      0.0,
                "grade":            grade,
                "event_count":      0,
                "collateral_mult":  collateral,
                "x402_accept":      False,
                "x402_headers": {
                    "X-Hexis-Score":          "0.000000",
                    "X-Hexis-Grade":          grade,
                    "X-Hexis-Accept":         "False",
                    "X-Hexis-Collateral-Mult": str(collateral),
                    "X-Hexis-Records":        "0",
                },
                "message": "No integrity records found. Submit events to build trust.",
            }

        actor  = self.actors[actor_id]
        score  = actor["hexis_total"]
        grade, collateral = self.get_grade(score)
        accept = score >= 0.00010

        return {
            "actor_id":        actor_id,
            "hexis_total":     round(score, 6),
            "grade":           grade,
            "event_count":     actor["event_count"],
            "collateral_mult": collateral,
            "x402_accept":     accept,
            "country":         actor.get("country", "DEFAULT"),
            "first_seen":      actor["first_seen"],
            "last_seen":       actor["last_seen"],
            "x402_headers": {
                "X-Hexis-Score":           f"{score:.6f}",
                "X-Hexis-Grade":           grade,
                "X-Hexis-Accept":          str(accept),
                "X-Hexis-Collateral-Mult": str(collateral),
                "X-Hexis-Records":         str(actor["event_count"]),
            },
        }


ledger = HexisLedger()


# ══════════════════════════════════════════════════════════════
# HEXIS FORMULA
# ══════════════════════════════════════════════════════════════

def context_multiplier(country: str) -> float:
    gdp = GDP_PER_CAPITA.get(country.upper(), GDP_PER_CAPITA["DEFAULT"])
    c   = math.sqrt(REFERENCE_GDP / gdp)
    return max(0.5, min(2.0, c))


def mine_hexis(sacrifice: float, betrayal_opportunity: float,
               witness_score: float, tdr: float,
               timing: float, country: str) -> float:
    """HEXIS(h) = S × BO × W × TDR × T × C"""
    s   = max(0.0, min(1.0, sacrifice))
    bo  = max(0.0, min(1.0, betrayal_opportunity))
    w   = max(0.0, min(1.0, witness_score))
    t   = max(0.0, min(1.0, tdr))
    ti  = max(0.0, min(1.0, timing))
    c   = context_multiplier(country)
    return round(s * bo * w * t * ti * c, 8)


def compute_job_hexis(fee_ecu: float, country: str,
                      prob_detection: float = 0.95) -> float:
    """Mine HEXIS từ một compute job hoàn thành honest."""
    # S = 1.0 (could submit fake proof, delivered honest output)
    s  = 1.0
    # BO = log(gain × (1 - prob_detected) + 1) / log(1B + 1)
    bo = math.log(fee_ecu * (1 - prob_detection) + 1) / math.log(1_000_000_000 + 1)
    # W = 3 witnesses: validator(adversarial), proof(neutral), consumer(allied)
    weighted = 3.0 + 2.0 + 1.0
    w  = math.log(weighted + 1) / math.log(1_000_000 + 1)
    # TDR low for fresh events
    tdr  = 0.05
    # T = 1.0 (submitted at job completion)
    ti   = 1.0
    return mine_hexis(s, bo, w, tdr, ti, country)


# ══════════════════════════════════════════════════════════════
# UI TEMPLATE
# ══════════════════════════════════════════════════════════════

UI = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>HEXIS Trust API</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: 'Courier New', monospace; background: #0a0a0a;
         color: #e0e0e0; min-height: 100vh; padding: 32px 24px; }
  h1 { font-size: 1.6rem; color: #f5c842; letter-spacing: 2px;
       margin-bottom: 4px; }
  .sub { color: #888; font-size: 0.85rem; margin-bottom: 32px; }
  .card { background: #111; border: 1px solid #222; border-radius: 8px;
          padding: 20px; margin-bottom: 20px; }
  .card h2 { color: #f5c842; font-size: 1rem; margin-bottom: 12px;
             text-transform: uppercase; letter-spacing: 1px; }
  .endpoint { background: #0d0d0d; border-left: 3px solid #f5c842;
              padding: 10px 14px; margin: 8px 0; border-radius: 4px; }
  .method { color: #4fc; font-size: 0.8rem; margin-right: 8px; }
  .path { color: #fff; font-size: 0.9rem; }
  .desc { color: #666; font-size: 0.78rem; margin-top: 4px; }
  input { background: #1a1a1a; border: 1px solid #333; color: #fff;
          padding: 10px 14px; border-radius: 4px; width: 100%;
          font-family: monospace; font-size: 0.9rem; margin-bottom: 10px; }
  button { background: #f5c842; color: #000; border: none;
           padding: 10px 20px; border-radius: 4px; cursor: pointer;
           font-weight: bold; font-size: 0.9rem; width: 100%; }
  button:hover { background: #ffe066; }
  pre { background: #0d0d0d; padding: 16px; border-radius: 6px;
        font-size: 0.8rem; overflow-x: auto; color: #4fc;
        min-height: 60px; white-space: pre-wrap; }
  .stat { display: inline-block; margin-right: 24px; }
  .stat .val { color: #f5c842; font-size: 1.3rem; font-weight: bold; }
  .stat .lbl { color: #666; font-size: 0.75rem; }
  .grade-high     { color: #4fc; }
  .grade-moderate { color: #f5c842; }
  .grade-low      { color: #fa8; }
  .grade-minimal  { color: #f66; }
  .grade-unverified { color: #666; }
</style>
</head>
<body>
<h1>HEXIS × Trust API</h1>
<p class="sub">Proof of Integrity — v0.1 Testnet · hexisfoundation.org</p>

<div class="card">
  <h2>System</h2>
  <div class="stat">
    <div class="val">{{ actors }}</div>
    <div class="lbl">Actors</div>
  </div>
  <div class="stat">
    <div class="val">{{ events }}</div>
    <div class="lbl">Integrity Events</div>
  </div>
  <div class="stat">
    <div class="val">{{ mined }}</div>
    <div class="lbl">HEXIS Mined</div>
  </div>
</div>

<div class="card">
  <h2>Query Trust Score</h2>
  <input id="actor" placeholder="Actor ID (wallet address, agent ID, any string)" />
  <button onclick="queryTrust()">GET /trust/{actor_id}</button>
  <pre id="trust_out">// Result appears here</pre>
</div>

<div class="card">
  <h2>Submit Integrity Event</h2>
  <input id="sub_actor"    placeholder="Actor ID" />
  <input id="sub_country"  placeholder="Country (VN, US, IN, CN...)" value="VN" />
  <input id="sub_fee"      placeholder="Fee / Stake amount (ECU or USD)" value="50" />
  <input id="sub_source"   placeholder="Source (compute_job, contract, etc)" value="compute_job" />
  <button onclick="submitEvent()">POST /integrity/submit</button>
  <pre id="sub_out">// Result appears here</pre>
</div>

<div class="card">
  <h2>API Endpoints</h2>
  <div class="endpoint">
    <span class="method">GET</span>
    <span class="path">/trust/{actor_id}</span>
    <div class="desc">Trust score + x402 headers. Use before any payment.</div>
  </div>
  <div class="endpoint">
    <span class="method">POST</span>
    <span class="path">/integrity/submit</span>
    <div class="desc">Submit integrity event. Auto-mines HEXIS.</div>
  </div>
  <div class="endpoint">
    <span class="method">GET</span>
    <span class="path">/integrity/{actor_id}</span>
    <div class="desc">Full integrity history for actor.</div>
  </div>
  <div class="endpoint">
    <span class="method">GET</span>
    <span class="path">/leaderboard</span>
    <div class="desc">Top actors by HEXIS score.</div>
  </div>
  <div class="endpoint">
    <span class="method">GET</span>
    <span class="path">/status</span>
    <div class="desc">System status.</div>
  </div>
</div>

<div class="card">
  <h2>x402 Integration</h2>
  <pre>// Before paying any AI agent or compute worker:
const trust = await fetch('http://174.138.9.102:8401/trust/' + agentId);
const data  = await trust.json();

if (data.x402_accept) {
  // Proceed — collateral: data.collateral_mult ×
  headers['X-Hexis-Score'] = data.x402_headers['X-Hexis-Score'];
} else {
  // Reject — no verified integrity history
}</pre>
</div>

<script>
async function queryTrust() {
  const id  = document.getElementById('actor').value.trim();
  const out = document.getElementById('trust_out');
  if (!id) { out.textContent = '// Enter an actor ID'; return; }
  try {
    const r = await fetch('/trust/' + encodeURIComponent(id));
    const d = await r.json();
    out.textContent = JSON.stringify(d, null, 2);
  } catch(e) { out.textContent = '// Error: ' + e.message; }
}

async function submitEvent() {
  const actor   = document.getElementById('sub_actor').value.trim();
  const country = document.getElementById('sub_country').value.trim() || 'VN';
  const fee     = parseFloat(document.getElementById('sub_fee').value) || 50;
  const source  = document.getElementById('sub_source').value.trim() || 'compute_job';
  const out     = document.getElementById('sub_out');
  if (!actor) { out.textContent = '// Enter an actor ID'; return; }
  try {
    const r = await fetch('/integrity/submit', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({actor_id: actor, country, fee_amount: fee, source})
    });
    const d = await r.json();
    out.textContent = JSON.stringify(d, null, 2);
  } catch(e) { out.textContent = '// Error: ' + e.message; }
}
</script>
</body>
</html>"""


# ══════════════════════════════════════════════════════════════
# ROUTES
# ══════════════════════════════════════════════════════════════

@app.route("/")
def index():
    return render_template_string(UI,
        actors=len(ledger.actors),
        events=len(ledger.events),
        mined=f"{ledger.total_mined:.4f}",
    )


@app.route("/trust/<actor_id>")
def get_trust(actor_id: str):
    data = ledger.get_trust(actor_id)
    resp = jsonify(data)
    # Inject x402 headers directly into HTTP response
    for k, v in data["x402_headers"].items():
        resp.headers[k] = v
    return resp


@app.route("/integrity/submit", methods=["POST"])
def submit_integrity():
    body = request.get_json(silent=True) or {}
    actor_id  = body.get("actor_id", "").strip()
    country   = body.get("country", "DEFAULT").upper()
    fee       = float(body.get("fee_amount", 50))
    source    = body.get("source", "manual")
    metadata  = body.get("metadata", {})

    if not actor_id:
        return jsonify({"error": "actor_id required"}), 400

    # Mine HEXIS
    if source == "compute_job":
        hexis = compute_job_hexis(fee, country)
    else:
        # Generic integrity event — simplified formula
        s  = float(body.get("sacrifice",   0.8))
        bo = float(body.get("betrayal_opp", 0.2))
        w  = float(body.get("witness",     0.3))
        tdr = float(body.get("tdr",        0.1))
        ti  = float(body.get("timing",     0.9))
        hexis = mine_hexis(s, bo, w, tdr, ti, country)

    event  = ledger.record(actor_id, hexis, country, source, metadata)
    trust  = ledger.get_trust(actor_id)

    return jsonify({
        "status":          "mined",
        "actor_id":        actor_id,
        "hexis_mined":     hexis,
        "hexis_total":     trust["hexis_total"],
        "grade":           trust["grade"],
        "collateral_mult": trust["collateral_mult"],
        "event_id":        event["id"],
        "message":         "Honest behavior = trust credential earned.",
    })


@app.route("/integrity/<actor_id>")
def get_history(actor_id: str):
    events = [e for e in ledger.events if e["actor_id"] == actor_id]
    trust  = ledger.get_trust(actor_id)
    return jsonify({
        "actor_id":    actor_id,
        "hexis_total": trust["hexis_total"],
        "grade":       trust["grade"],
        "event_count": len(events),
        "events":      events[-20:],  # last 20
    })


@app.route("/leaderboard")
def leaderboard():
    limit = min(int(request.args.get("limit", 20)), 100)
    sorted_actors = sorted(
        ledger.actors.values(),
        key=lambda a: a["hexis_total"],
        reverse=True
    )[:limit]
    return jsonify({
        "leaderboard": [
            {
                "rank":        i + 1,
                "actor_id":    a["actor_id"],
                "hexis_total": round(a["hexis_total"], 6),
                "grade":       ledger.get_grade(a["hexis_total"])[0],
                "events":      a["event_count"],
                "country":     a.get("country", "?"),
            }
            for i, a in enumerate(sorted_actors)
        ],
        "total_actors": len(ledger.actors),
        "total_mined":  round(ledger.total_mined, 6),
    })


@app.route("/status")
def status():
    return jsonify({
        "service":       "HEXIS Trust API",
        "version":       "0.1",
        "status":        "live",
        "testnet":       True,
        "port":          8401,
        "actors":        len(ledger.actors),
        "events":        len(ledger.events),
        "hexis_mined":   round(ledger.total_mined, 6),
        "total_supply":  TOTAL_SUPPLY,
        "wallet_cap":    WALLET_CAP,
        "formula":       "HEXIS = S × BO × W × TDR × T × C",
        "contact":       "contact@hexisfoundation.org",
        "website":       "hexisfoundation.org",
        "timestamp":     datetime.now(timezone.utc).isoformat(),
    })


# ══════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    print("=" * 56)
    print("HEXIS Trust API v0.1 — Standalone")
    print("=" * 56)
    print(f"  UI:          http://localhost:8401/")
    print(f"  Trust:       http://localhost:8401/trust/{{actor_id}}")
    print(f"  Submit:      POST http://localhost:8401/integrity/submit")
    print(f"  Leaderboard: http://localhost:8401/leaderboard")
    print(f"  Status:      http://localhost:8401/status")
    print("=" * 56)
    print("  Formula: HEXIS = S × BO × W × TDR × T × C")
    print("  Non-transferable. Cannot be bought. Only earned.")
    print("=" * 56)
    app.run(host="0.0.0.0", port=8401, debug=False)
