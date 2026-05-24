"""
hexis_audit.py
P1.5 Audit & Compliance Layer
Tamper-proof SHA256 hash chain + ed25519 daily seal.

For HEXIS bridge v0.6.3+ (FastAPI + SQLite).

Architecture:
  - audit_log table lives in the same SQLite file as the bridge action tables.
    This guarantees that every action and its audit row commit in the SAME
    transaction (no async, no separate-file race). Tamper-proof by construction.
  - Daily seal signs the latest event with the Foundation ed25519 key,
    then appends a 'daily_seal' event recording the signature.
  - verify_chain() re-runs SHA256 over the entire chain end-to-end and
    reports the first sequence number where the chain breaks.

Supported action types:
    genesis, daily_seal
    worker_register, consumer_register
    job_request, job_complete, job_dispute
    stake_lock, stake_release, slash
    hexis_mint, ecu_transfer
    token_consumption, token_topup

Usage:
    from hexis_audit import AuditLogger, get_audit_router

    audit = AuditLogger("/opt/hexis_newflow/bridge.db")
    app.include_router(get_audit_router(audit))

    # standalone write (opens own connection):
    audit.log_action("worker_001", "worker_register", {"region": "VN"})

    # in-transaction write (atomic with bridge action):
    with sqlite3.connect("bridge.db") as conn:
        conn.execute("INSERT INTO jobs ...")
        audit.log_action_within(conn, "consumer_001", "job_request",
                                {"job_id": "J0001"},
                                counterparty_id="worker_001")
        conn.commit()  # both rows commit together or neither

    # token consumption (per session or per batch window):
    audit.log_action(
        actor_id="user_12345",
        action_type="token_consumption",
        payload={"tokens_in": 1234, "tokens_out": 567,
                 "model": "deepseek-v3.2", "session_id": "S-9f3a",
                 "cost_ecu": 0.034, "joules_estimated": 412},
        counterparty_id="provider_telechat",
    )

Endpoints exposed by get_audit_router():
    GET /audit/{actor_id}?start=<ISO>&end=<ISO>&format=json|csv&limit=N
    GET /audit/{actor_id}/{event_id}
    GET /audit/chain/verify
    GET /audit/seals
    GET /audit/export/{actor_id}
"""

import csv
import hashlib
import io
import json
import os
import sqlite3
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import PlainTextResponse

try:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives import serialization
    CRYPTO_AVAILABLE = True
except ImportError:
    CRYPTO_AVAILABLE = False


GENESIS_PREV_HASH = "GENESIS"
SCHEMA_VERSION = "v1"

VALID_ACTIONS = {
    "genesis",
    "worker_register", "consumer_register",
    "job_request", "job_complete", "job_dispute",
    "stake_lock", "stake_release", "slash",
    "hexis_mint", "ecu_transfer",
    "token_consumption", "token_topup",
    "daily_seal",
}

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS audit_log (
    event_id        TEXT PRIMARY KEY,
    sequence        INTEGER UNIQUE NOT NULL,
    prev_hash       TEXT NOT NULL,
    content_hash    TEXT NOT NULL,
    actor_id        TEXT NOT NULL,
    counterparty_id TEXT,
    action_type     TEXT NOT NULL,
    payload         TEXT NOT NULL,
    timestamp_iso   TEXT NOT NULL,
    timestamp_unix  INTEGER NOT NULL,
    signature       TEXT
);
CREATE INDEX IF NOT EXISTS idx_audit_actor  ON audit_log(actor_id, timestamp_unix);
CREATE INDEX IF NOT EXISTS idx_audit_cp     ON audit_log(counterparty_id, timestamp_unix);
CREATE INDEX IF NOT EXISTS idx_audit_seq    ON audit_log(sequence);
CREATE INDEX IF NOT EXISTS idx_audit_action ON audit_log(action_type, timestamp_unix);
"""


class AuditLogger:
    """Tamper-proof audit log. Hash chain + ed25519 seal."""

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._init_db()
        self._init_genesis_if_empty()

    # ---------- internal helpers ----------

    def _connect(self):
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    def _init_db(self):
        conn = self._connect()
        try:
            conn.executescript(SCHEMA_SQL)
            conn.commit()
        finally:
            conn.close()

    def _init_genesis_if_empty(self):
        conn = self._connect()
        try:
            (count,) = conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()
            if count == 0:
                self._insert(
                    conn,
                    actor_id="FOUNDATION",
                    counterparty_id=None,
                    action_type="genesis",
                    payload={"message": "HEXIS audit chain genesis",
                             "schema_version": SCHEMA_VERSION},
                )
                conn.commit()
        finally:
            conn.close()

    @staticmethod
    def _content_hash(action_type, actor_id, counterparty_id, payload, ts_unix):
        canonical = json.dumps(
            {
                "action_type": action_type,
                "actor_id": actor_id,
                "counterparty_id": counterparty_id or "",
                "payload": payload,
                "timestamp_unix": ts_unix,
            },
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    @staticmethod
    def _event_id(prev_hash, content_hash, sequence, ts_unix):
        material = f"{prev_hash}|{content_hash}|{sequence}|{ts_unix}"
        return hashlib.sha256(material.encode("utf-8")).hexdigest()

    def _last(self, conn):
        row = conn.execute(
            "SELECT sequence, event_id FROM audit_log "
            "ORDER BY sequence DESC LIMIT 1"
        ).fetchone()
        if row is None:
            return -1, GENESIS_PREV_HASH
        return row[0], row[1]

    def _insert(self, conn, *, actor_id, counterparty_id, action_type, payload):
        if action_type not in VALID_ACTIONS:
            raise ValueError(f"invalid action_type: {action_type}")

        now = datetime.now(timezone.utc)
        ts_iso = now.isoformat()
        ts_unix = int(now.timestamp())

        last_seq, last_event_id = self._last(conn)
        sequence = last_seq + 1
        prev_hash = last_event_id

        ch = self._content_hash(
            action_type, actor_id, counterparty_id, payload, ts_unix
        )
        eid = self._event_id(prev_hash, ch, sequence, ts_unix)
        payload_json = json.dumps(
            payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        )

        conn.execute(
            """
            INSERT INTO audit_log
              (event_id, sequence, prev_hash, content_hash, actor_id,
               counterparty_id, action_type, payload,
               timestamp_iso, timestamp_unix, signature)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)
            """,
            (eid, sequence, prev_hash, ch, actor_id,
             counterparty_id, action_type, payload_json, ts_iso, ts_unix),
        )

        return {
            "event_id": eid,
            "sequence": sequence,
            "prev_hash": prev_hash,
            "content_hash": ch,
            "timestamp_iso": ts_iso,
            "timestamp_unix": ts_unix,
        }

    # ---------- public write API ----------

    def log_action(self, actor_id, action_type, payload, counterparty_id=None):
        """Standalone write. Opens own connection and commits."""
        conn = self._connect()
        try:
            res = self._insert(
                conn,
                actor_id=actor_id,
                counterparty_id=counterparty_id,
                action_type=action_type,
                payload=payload,
            )
            conn.commit()
            return res
        finally:
            conn.close()

    def log_action_within(self, conn, actor_id, action_type, payload,
                          counterparty_id=None):
        """Write inside caller's open transaction. Caller commits."""
        return self._insert(
            conn,
            actor_id=actor_id,
            counterparty_id=counterparty_id,
            action_type=action_type,
            payload=payload,
        )

    # ---------- read API ----------

    @staticmethod
    def _row(row):
        d = dict(row)
        try:
            d["payload"] = json.loads(d["payload"])
        except Exception:
            pass
        return d

    def get_actor_events(self, actor_id, start_unix=None, end_unix=None,
                         limit=1000):
        conn = self._connect()
        conn.row_factory = sqlite3.Row
        try:
            q = ("SELECT * FROM audit_log "
                 "WHERE (actor_id = ? OR counterparty_id = ?)")
            params = [actor_id, actor_id]
            if start_unix is not None:
                q += " AND timestamp_unix >= ?"
                params.append(start_unix)
            if end_unix is not None:
                q += " AND timestamp_unix <= ?"
                params.append(end_unix)
            q += " ORDER BY sequence ASC LIMIT ?"
            params.append(limit)
            rows = conn.execute(q, params).fetchall()
            return [self._row(r) for r in rows]
        finally:
            conn.close()

    def get_event(self, event_id):
        conn = self._connect()
        conn.row_factory = sqlite3.Row
        try:
            row = conn.execute(
                "SELECT * FROM audit_log WHERE event_id = ?", (event_id,)
            ).fetchone()
            if row is None:
                return None
            event = self._row(row)
            prev = conn.execute(
                "SELECT event_id FROM audit_log WHERE sequence = ?",
                (event["sequence"] - 1,)
            ).fetchone()
            nxt = conn.execute(
                "SELECT event_id FROM audit_log WHERE sequence = ?",
                (event["sequence"] + 1,)
            ).fetchone()
            event["chain_proof"] = {
                "prev_event_id": prev["event_id"] if prev else None,
                "next_event_id": nxt["event_id"] if nxt else None,
            }
            return event
        finally:
            conn.close()

    def verify_chain(self):
        """Re-run SHA256 over entire chain. Returns dict."""
        conn = self._connect()
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                "SELECT * FROM audit_log ORDER BY sequence ASC"
            ).fetchall()
        finally:
            conn.close()

        expected_prev = GENESIS_PREV_HASH
        for r in rows:
            payload = json.loads(r["payload"])
            ch = self._content_hash(
                r["action_type"], r["actor_id"], r["counterparty_id"],
                payload, r["timestamp_unix"]
            )
            if ch != r["content_hash"]:
                return {"valid": False,
                        "broken_at_sequence": r["sequence"],
                        "reason": "content_hash mismatch",
                        "total_events": len(rows)}
            eid = self._event_id(r["prev_hash"], r["content_hash"],
                                 r["sequence"], r["timestamp_unix"])
            if eid != r["event_id"]:
                return {"valid": False,
                        "broken_at_sequence": r["sequence"],
                        "reason": "event_id mismatch",
                        "total_events": len(rows)}
            if r["prev_hash"] != expected_prev:
                return {"valid": False,
                        "broken_at_sequence": r["sequence"],
                        "reason": "prev_hash mismatch",
                        "total_events": len(rows)}
            expected_prev = r["event_id"]

        return {"valid": True,
                "broken_at_sequence": None,
                "total_events": len(rows)}

    def list_seals(self):
        conn = self._connect()
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                "SELECT sequence, event_id, signature, timestamp_iso "
                "FROM audit_log WHERE signature IS NOT NULL "
                "ORDER BY sequence ASC"
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def export_actor(self, actor_id):
        """Full JSON-LD export. Parseable offline by FSA reviewer."""
        events = self.get_actor_events(actor_id, limit=1_000_000)
        return {
            "@context": "https://hexisfoundation.org/schema/audit/v1",
            "@type": "AuditExport",
            "actor_id": actor_id,
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "event_count": len(events),
            "chain_verified": self.verify_chain(),
            "events": events,
        }

    # ---------- daily seal ----------

    def seal(self, private_key_path):
        """
        Sign latest unsigned event with Foundation ed25519 key.
        Append a 'daily_seal' event recording the signature.
        Designed for cron at 00:00 UTC.
        """
        if not CRYPTO_AVAILABLE:
            raise RuntimeError("cryptography lib not installed")
        with open(private_key_path, "rb") as f:
            priv = serialization.load_pem_private_key(f.read(), password=None)
        if not isinstance(priv, Ed25519PrivateKey):
            raise TypeError("Foundation key must be Ed25519")

        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT event_id, sequence FROM audit_log "
                "WHERE signature IS NULL "
                "ORDER BY sequence DESC LIMIT 1"
            ).fetchone()
            if row is None:
                return None
            event_id, sequence = row

            sig = priv.sign(event_id.encode("utf-8")).hex()
            conn.execute(
                "UPDATE audit_log SET signature = ? WHERE event_id = ?",
                (sig, event_id),
            )
            self._insert(
                conn,
                actor_id="FOUNDATION",
                counterparty_id=None,
                action_type="daily_seal",
                payload={"sealed_event_id": event_id,
                         "sealed_sequence": sequence,
                         "signature": sig},
            )
            conn.commit()
            return {"sealed_event_id": event_id,
                    "sealed_sequence": sequence,
                    "signature": sig}
        finally:
            conn.close()


# =====================================================================
# FastAPI router
# =====================================================================

def get_audit_router(audit: AuditLogger) -> APIRouter:
    router = APIRouter(prefix="/audit", tags=["audit"])

    def _parse_iso(s):
        try:
            return int(datetime.fromisoformat(
                s.replace("Z", "+00:00")
            ).timestamp())
        except Exception:
            raise HTTPException(
                status_code=400,
                detail=f"invalid ISO 8601 timestamp: {s}"
            )

    @router.get("/chain/verify")
    def chain_verify():
        return audit.verify_chain()

    @router.get("/seals")
    def list_seals():
        return {"seals": audit.list_seals()}

    @router.get("/export/{actor_id}")
    def export_actor(actor_id: str):
        return audit.export_actor(actor_id)

    @router.get("/{actor_id}")
    def actor_events(
        actor_id: str,
        start: Optional[str] = Query(None, description="ISO 8601 UTC"),
        end:   Optional[str] = Query(None, description="ISO 8601 UTC"),
        format: str = Query("json", pattern="^(json|csv)$"),
        limit: int = Query(1000, ge=1, le=100000),
    ):
        start_u = _parse_iso(start) if start else None
        end_u   = _parse_iso(end)   if end   else None
        events  = audit.get_actor_events(actor_id, start_u, end_u, limit)

        if format == "csv":
            buf = io.StringIO()
            w = csv.writer(buf)
            w.writerow(["sequence", "event_id", "prev_hash", "content_hash",
                        "actor_id", "counterparty_id", "action_type",
                        "timestamp_iso", "signature", "payload_json"])
            for e in events:
                w.writerow([
                    e["sequence"], e["event_id"], e["prev_hash"],
                    e["content_hash"], e["actor_id"],
                    e.get("counterparty_id") or "",
                    e["action_type"], e["timestamp_iso"],
                    e.get("signature") or "",
                    json.dumps(e["payload"], ensure_ascii=False,
                               sort_keys=True),
                ])
            return PlainTextResponse(buf.getvalue(), media_type="text/csv")

        return {
            "actor_id": actor_id,
            "start": start,
            "end": end,
            "event_count": len(events),
            "events": events,
        }

    @router.get("/{actor_id}/{event_id}")
    def actor_event(actor_id: str, event_id: str):
        e = audit.get_event(event_id)
        if e is None:
            raise HTTPException(status_code=404,
                                detail="event_id not found")
        if e["actor_id"] != actor_id and e.get("counterparty_id") != actor_id:
            raise HTTPException(
                status_code=404,
                detail="event_id does not belong to actor_id"
            )
        return e

    return router


# =====================================================================
# Self-test
# =====================================================================

if __name__ == "__main__":
    import tempfile

    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    audit = AuditLogger(tmp.name)

    print("after genesis ->", audit.verify_chain())

    audit.log_action("worker_001", "worker_register",
                     {"region": "VN", "capacity_kwh": 5})
    audit.log_action("consumer_001", "consumer_register",
                     {"sensitivity_tier": 2})
    audit.log_action("consumer_001", "job_request",
                     {"job_id": "J0001", "ecu_offer": 100},
                     counterparty_id="worker_001")
    audit.log_action("worker_001", "job_complete",
                     {"job_id": "J0001", "duration_ms": 1430},
                     counterparty_id="consumer_001")

    # Token consumption: per-session batched aggregate (1 row = N inference calls)
    audit.log_action("consumer_001", "token_consumption",
                     {"session_id": "S-9f3a",
                      "model": "deepseek-v3.2",
                      "tokens_in": 1234,
                      "tokens_out": 567,
                      "calls_in_batch": 8,
                      "window_seconds": 60,
                      "cost_ecu": 0.034,
                      "joules_estimated": 412},
                     counterparty_id="worker_001")

    # Token topup: subscription purchase (China Telecom analogue)
    audit.log_action("consumer_001", "token_topup",
                     {"package": "basic_10M_tokens",
                      "tokens_granted": 10_000_000,
                      "ecu_paid": 1.45,
                      "expires_unix": 1764547200},
                     counterparty_id="provider_telechat")

    print("after 6 events ->", audit.verify_chain())
    print("consumer_001 events:",
          len(audit.get_actor_events("consumer_001")))
    print("worker_001 events:",
          len(audit.get_actor_events("worker_001")))

    # Tamper test: rewrite a payload directly in DB
    conn = sqlite3.connect(tmp.name)
    conn.execute(
        "UPDATE audit_log SET payload = ? WHERE sequence = 3",
        (json.dumps({"job_id": "J0001", "ecu_offer": 999999}),),
    )
    conn.commit()
    conn.close()
    print("after tamper ->", audit.verify_chain())

    os.unlink(tmp.name)
