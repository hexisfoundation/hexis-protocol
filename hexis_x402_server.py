
"""
HEXIS TRUST RAIL — x402 Integration Server v0.1
=================================================
Exposes Hexis as an HTTP API that AI agents query
before transacting via x402.

The flow:
    AI Agent A wants to hire Agent B via x402
    → A queries: GET /trust/{agent_id}
    → Hexis returns trust score + grade
    → A decides: accept, require collateral, or reject
    → A pays B via x402 (separate protocol)

This module:
    1. Hexis Trust API  — query trust score for any actor
    2. x402 Middleware  — intercept x402 requests, auto-check trust
    3. Agent Registry   — register AI agents and their behavior records

Run locally:
    pip install flask requests --break-system-packages
    python hexis_x402_server.py

Then query:
    curl http://localhost:5042/trust/agent_001
    curl http://localhost:5042/trust/potus_47
"""

import json
import time
import hashlib
import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

# ---- Try to import Flask ----
try:
    from flask import Flask, request, jsonify
    FLASK_AVAILABLE = True
except ImportError:
    FLASK_AVAILABLE = False
    print("[WARNING] Flask not installed. Run: pip install flask")
    print("          Showing demo output instead.\n")

# ---- Import Hexis core ----
try:
    from hexis_mining import HexisMiner, BehaviorEvent
    HEXIS_AVAILABLE = True
except ImportError:
    # Inline minimal version if hexis_mining.py not in path
    HEXIS_AVAILABLE = False


# ============================================================
# HEXIS TRUST SCORE ENGINE
# ============================================================

class HexisTrustEngine:
    """
    Computes aggregate trust score for an actor from their
    full behavior record stored on IPFS / local ledger.

    In production: pulls records from IPFS by actor_id.
    In MVP: uses local JSON ledger file.
    """

    TRUST_THRESHOLDS = {
        "high":     0.05,   # Accept at standard rate
        "moderate": 0.005,  # Accept with 1.5x collateral
        "low":      0.001,  # Accept with 3x collateral
        "minimal":  0.0001, # Escrow until delivery
        # below minimal: reject
    }

    def __init__(self, ledger_path: str = "hexis_index.json"):
        self.ledger_path = ledger_path
        self._cache = {}

    def _load_records(self, actor_id: str) -> list:
        """Load all hexis records for an actor from local ledger."""
        try:
            with open(self.ledger_path, "r") as f:
                ledger = json.load(f)
            return [r for r in ledger.get("records", [])
                    if r.get("actor_id") == actor_id]
        except (FileNotFoundError, json.JSONDecodeError):
            return []

    def get_trust_score(self, actor_id: str) -> dict:
        """
        Returns aggregate trust profile for an actor.

        Response format (compatible with x402 trust header):
        {
            "actor_id": "...",
            "hexis_total": 0.1234,
            "record_count": 47,
            "grade": "High",
            "recommendation": "accept" | "collateral_1.5x" |
                              "collateral_3x" | "escrow" | "reject",
            "collateral_multiplier": 1.0,
            "verified_at": "ISO timestamp",
            "ipfs_cids": ["bafkrei...", ...]
        }
        """
        records = self._load_records(actor_id)

        if not records:
            return {
                "actor_id":              actor_id,
                "hexis_total":           0.0,
                "record_count":          0,
                "grade":                 "Unverified",
                "recommendation":        "escrow",
                "collateral_multiplier": 5.0,
                "note": (
                    "No hexis records found. Actor has no verified "
                    "integrity history. Escrow recommended."
                ),
                "verified_at": time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                             time.gmtime()),
            }

        # Aggregate scores
        total_hexis = sum(r.get("hexis_raw", 0) for r in records)
        cids = [r.get("cid", "") for r in records if r.get("cid")]

        # Determine grade and recommendation
        if total_hexis >= self.TRUST_THRESHOLDS["high"]:
            grade = "High"
            recommendation = "accept"
            collateral = 1.0
        elif total_hexis >= self.TRUST_THRESHOLDS["moderate"]:
            grade = "Moderate"
            recommendation = "collateral_1.5x"
            collateral = 1.5
        elif total_hexis >= self.TRUST_THRESHOLDS["low"]:
            grade = "Low"
            recommendation = "collateral_3x"
            collateral = 3.0
        elif total_hexis >= self.TRUST_THRESHOLDS["minimal"]:
            grade = "Minimal"
            recommendation = "escrow"
            collateral = 5.0
        else:
            grade = "Insufficient"
            recommendation = "reject"
            collateral = 0.0

        return {
            "actor_id":              actor_id,
            "hexis_total":           round(total_hexis, 8),
            "record_count":          len(records),
            "grade":                 grade,
            "recommendation":        recommendation,
            "collateral_multiplier": collateral,
            "ipfs_cids":             cids[:5],  # first 5 for brevity
            "verified_at":           time.strftime(
                "%Y-%m-%dT%H:%M:%SZ", time.gmtime()
            ),
        }


# ============================================================
# x402 TRUST MIDDLEWARE
# ============================================================

class X402TrustMiddleware:
    """
    Intercepts x402 payment requests and injects Hexis trust
    check before processing.

    x402 standard flow (without Hexis):
        Client → POST /resource → 402 Payment Required
        Client → POST /resource + payment_header → 200 OK

    x402 + Hexis flow:
        Client → POST /resource → 402 + trust_required header
        Client → GET /trust/{agent_id} → trust score
        Client → POST /resource + payment_header + hexis_score → 200 OK

    The hexis_score is attached as a custom HTTP header:
        X-Hexis-Score: 0.0234
        X-Hexis-Grade: Moderate
        X-Hexis-Recommendation: collateral_1.5x
        X-Hexis-Records: 12
        X-Hexis-Verified: 2026-04-29T14:30:00Z
    """

    def __init__(self, engine: HexisTrustEngine):
        self.engine = engine

    def check_and_price(
        self,
        agent_id: str,
        base_price_usdc: float,
    ) -> dict:
        """
        Given an agent ID and base service price,
        returns adjusted price based on trust score.

        High trust    → pay base price
        Moderate      → pay base × 1.0 but post collateral × 1.5
        Low           → post collateral × 3.0
        Unverified    → escrow full payment until delivery
        Reject        → transaction refused
        """
        trust = self.engine.get_trust_score(agent_id)
        rec = trust["recommendation"]

        if rec == "reject":
            return {
                "status":        "rejected",
                "reason":        "Insufficient hexis score",
                "trust":         trust,
                "price_usdc":    0,
                "collateral":    0,
            }

        collateral = base_price_usdc * trust["collateral_multiplier"]

        return {
            "status":             "approved",
            "trust":              trust,
            "price_usdc":         base_price_usdc,
            "collateral_usdc":    round(collateral, 4),
            "payment_flow":       rec,
            "x402_headers": {
                "X-Hexis-Score":          trust["hexis_total"],
                "X-Hexis-Grade":          trust["grade"],
                "X-Hexis-Recommendation": trust["recommendation"],
                "X-Hexis-Records":        trust["record_count"],
                "X-Hexis-Verified":       trust["verified_at"],
            }
        }


# ============================================================
# FLASK API SERVER
# ============================================================

def create_app():
    app = Flask(__name__)
    engine = HexisTrustEngine()
    middleware = X402TrustMiddleware(engine)

    @app.route("/")
    def index():
        return jsonify({
            "protocol":    "Hexis Trust Rail v0.1",
            "description": "Trust verification layer for AI agent economies",
            "endpoints": {
                "GET /trust/{actor_id}":
                    "Get trust score for any actor",
                "POST /trust/price":
                    "Get x402-adjusted price for a transaction",
                "POST /trust/submit":
                    "Submit a new behavior event for mining",
                "GET /trust/leaderboard":
                    "Top trusted actors by hexis score",
            },
            "x402_integration": {
                "header":      "X-Hexis-Score",
                "docs":        "hexisfoundation.org/docs/x402",
            }
        })

    @app.route("/trust/<actor_id>")
    def get_trust(actor_id: str):
        """
        Primary endpoint: query trust score for any actor.

        AI agents call this before accepting a transaction.

        Example:
            GET /trust/agent_claude_001
            GET /trust/potus_47
            GET /trust/0xABC123...  (wallet address)
        """
        score = engine.get_trust_score(actor_id)
        return jsonify(score)

    @app.route("/trust/price", methods=["POST"])
    def get_adjusted_price():
        """
        Given agent_id and base_price, returns x402-adjusted
        price with collateral requirement.

        Body: {"agent_id": "...", "base_price_usdc": 10.0}
        """
        data = request.get_json()
        if not data or "agent_id" not in data:
            return jsonify({"error": "agent_id required"}), 400

        agent_id        = data["agent_id"]
        base_price_usdc = float(data.get("base_price_usdc", 1.0))

        result = middleware.check_and_price(agent_id, base_price_usdc)
        status_code = 200 if result["status"] == "approved" else 403
        return jsonify(result), status_code

    @app.route("/trust/leaderboard")
    def leaderboard():
        """Top actors by aggregate hexis score."""
        try:
            with open("hexis_index.json", "r") as f:
                ledger = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return jsonify({"leaderboard": [], "note": "No records yet"})

        from collections import defaultdict
        totals = defaultdict(float)
        counts = defaultdict(int)
        for r in ledger.get("records", []):
            aid = r.get("actor_id", "unknown")
            totals[aid] += r.get("hexis_raw", 0)
            counts[aid] += 1

        board = sorted(
            [{"actor_id": k, "hexis_total": round(v, 6),
              "record_count": counts[k]}
             for k, v in totals.items()],
            key=lambda x: x["hexis_total"], reverse=True
        )[:20]

        return jsonify({"leaderboard": board})

    return app


# ============================================================
# DEMO (no Flask needed)
# ============================================================

def run_demo():
    print("=" * 65)
    print("HEXIS TRUST RAIL — x402 Integration Demo")
    print("=" * 65)

    engine = HexisTrustEngine()
    middleware = X402TrustMiddleware(engine)

    # Demo with empty ledger (new actors)
    test_agents = [
        ("agent_new_001",    10.0,  "Brand new agent, no history"),
        ("potus_47",         500.0, "High-profile actor"),
        ("marcus_webb",      25.0,  "Community member"),
    ]

    for agent_id, price, label in test_agents:
        print(f"\n[{label}]")
        print(f"  Agent:      {agent_id}")
        print(f"  Service:    ${price} USDC")

        result = middleware.check_and_price(agent_id, price)
        trust  = result["trust"]

        print(f"  Hexis:      {trust['hexis_total']}")
        print(f"  Records:    {trust['record_count']}")
        print(f"  Grade:      {trust['grade']}")
        print(f"  Decision:   {result['status'].upper()}")

        if result["status"] == "approved":
            print(f"  Pay:        ${result['price_usdc']} USDC")
            print(f"  Collateral: ${result['collateral_usdc']} USDC")
            print(f"  Flow:       {result['payment_flow']}")
            print(f"\n  x402 Headers:")
            for k, v in result["x402_headers"].items():
                print(f"    {k}: {v}")
        else:
            print(f"  Reason:     {result.get('reason')}")

    print(f"""
{"=" * 65}
HOW THIS CONNECTS TO x402
{"=" * 65}

x402 Payment Flow (without Hexis):
    Agent A → POST /api/service → 402 Payment Required
    Agent A → POST /api/service + USDC payment → 200 OK

x402 + Hexis Trust Rail:
    Agent A → GET hexisfoundation.org/trust/{{agent_b_id}}
            ← trust score + collateral requirement
    Agent A → POST /api/service + USDC + collateral → 200 OK

The trust query adds ~50ms latency.
The collateral adjustment adds zero latency.
No human approval required at any step.

This is what "trust rail" means in practice:
    Every x402 transaction is priced by behavior history,
    not by brand, not by platform, not by jurisdiction.
    An agent in Lagos with 1,000 verified commitments
    gets the same rate as an agent in San Francisco.
{"=" * 65}
""")


if __name__ == "__main__":
    if FLASK_AVAILABLE:
        print("Starting Hexis Trust Rail API on port 5042...")
        print("Endpoints:")
        print("  GET  http://localhost:5042/trust/{actor_id}")
        print("  POST http://localhost:5042/trust/price")
        print("  GET  http://localhost:5042/trust/leaderboard")
        print()
        app = create_app()
        app.run(host="0.0.0.0", port=5042, debug=False)
    else:
        run_demo()
Content is user-generated and unverified.
