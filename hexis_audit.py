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
    GET  /audit/{actor_id}?start=<ISO>&end=<ISO>&format=json|csv&limit=N
    GET  /audit/{actor_id}/{event_id}
    GET  /audit/chain/verify
    GET  /audit/chain/full
    GET  /audit/chain/head     <- what to sign, for the offline signer
    POST /audit/seal           <- submit a signature made off this machine
    GET  /audit/pubkey
    GET  /audit/seals
    GET  /audit/export/{actor_id}

SPLIT SEALING
    seal() below needs the private key on the same machine as the database.
    On a host with no endpoint authentication that is the wrong place for it,
    so sealing is also offered in two halves:

        GET  /audit/chain/head  -> {event_id, sequence}   the string to sign
        POST /audit/seal        <- {event_id, signature}  the result

    The server holds only the PUBLIC key and verifies before writing. It can
    check a seal, and it can never mint one. seal() is kept for local testing
    against a scratch database, where the key and the data are both local
    anyway.
"""

import csv
import hashlib
import io
import json
import logging
import os
import sqlite3
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field

try:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import (
        Ed25519PrivateKey, Ed25519PublicKey,
    )
    from cryptography.hazmat.primitives import serialization
    from cryptography.exceptions import InvalidSignature
    CRYPTO_AVAILABLE = True
except ImportError:
    CRYPTO_AVAILABLE = False


log = logging.getLogger("hexis.audit")

GENESIS_PREV_HASH = "GENESIS"
SCHEMA_VERSION = "v1"

# Path to the Foundation Ed25519 PUBLIC key, in PEM. Read from the environment
# with no default: an operator must decide the location explicitly. The private
# key is never read by the server process — only by the offline seal entrypoint.
PUBKEY_PATH_ENV = "HEXIS_SEAL_PUBKEY_PATH"

# Largest page an external verifier may request from /audit/chain/full.
CHAIN_PAGE_MAX = 5000

# An action type missing from this set is not logged. `_insert` raises, and
# every caller in hexis_stake.py, hexis_severity.py and hexis_sampling.py
# routes through an `except Exception: pass` — so the event vanishes with no
# error anywhere.
#
# This has now happened twice, which is why it is no longer defended by a
# comment. On 2026-08-12 six types were found missing (escrow_transfer,
# stake_abort, stake_expire, severity_classify, severity_repay,
# severity_victim_payout) and added. On 2026-08-13 a scan of the source rather
# than of memory found six more, the entire sampling module, listed below —
# including `sampling_validator_slash`, which moves money, and
# `sampling_verdict`, which had already recorded one caught fraud that the
# chain does not contain.
#
# The list you are reading is no longer maintained by hand. `audit_action_types()`
# in hexis_bridge_v0.6.2.py parses every loaded module for the action types the
# code can emit and refuses to boot if any of them is missing here. Adding a
# `_audit("something_new", ...)` call without adding it here now fails at
# startup instead of silently writing nothing.
VALID_ACTIONS = {
    "genesis",
    "worker_register", "consumer_register",
    "job_request", "job_complete", "job_dispute",
    "stake_lock", "stake_release", "slash",
    "stake_abort", "stake_expire", "stake_expiry_sweep",
    # P1 bilateral stake (2026-08-23, OPEN.md #1). Two rows per job before any
    # money moves: the consumer's proposal and the worker's acceptance of the
    # same terms_hash. Both carry "moves": "nothing" in the payload, because a
    # consent record that looked like a transfer would be worse than none.
    "stake_terms_proposed", "stake_terms_accepted",
    # PoSP commit-reveal (2026-08-23). The epoch commitment lands in the chain
    # before any job of the epoch exists; the reveal lands after it ends. The
    # ordering is the proof that selection could not be retroactively aimed.
    "sampling_epoch", "sampling_epoch_reveal",
    "escrow_credit", "escrow_transfer",
    "severity_classify", "severity_repay", "severity_victim_payout",
    # PoSP sampling. Dropped in silence from the day the module shipped until
    # 2026-08-13; production had run two audits and caught one fraud, and the
    # chain recorded neither.
    "sampling_open", "sampling_claim_denied", "sampling_assign",
    "sampling_verdict", "sampling_stake_refund", "sampling_validator_slash",
    # 2026-08-17, with the independence gate that replaced the self-declared
    # country check. Emitted when a validator asked for work and every waiting
    # audit refused it. Without this, "the gate refuses everyone" and "nobody
    # is asking for audits" are the same silence from outside.
    "posp_claim_refused",
    "hexis_mint", "ecu_transfer",
    # The bridge ledger (2026-08-16). `ledger_opening` is emitted at most once
    # per database — it records the balances that predate the ledger — so it
    # will show as "allowed but not emitted" on any host that started with the
    # ledger already in place. That is the correct reading, not a gap.
    "ledger_reconcile", "ledger_opening",
    "token_consumption", "token_topup",
    # The sha256 of a published document, as served (2026-08-19). Written by
    # `--record-document` on the host, one per deploy. The event is a claim
    # about bytes at a URL and nothing else; what makes it worth anything is
    # `daily_seal` signing the head afterwards.
    "document_seal",
    # One chain head committed to a Bitcoin block (2026-08-20). Written by
    # `--record-ots-anchor` on the host, driven from the laptop once an
    # OpenTimestamps proof stops being pending. It is a pointer to a published
    # .ots file, never a verification performed here — the host runs no
    # calendar client, and a row asserting more than that would be the operator
    # vouching for the operator, which is what the anchor exists to escape.
    "ots_anchor",
    # Names the key that would be treated as this project's successor seal key
    # (2026-08-21). Written by `--record-successor` on the host. It grants that
    # key NOTHING — a signature by it is not a valid seal — and exists so that
    # the designation is dated before any dispute, which the ots_anchor
    # covering it establishes against Bitcoin rather than against our word.
    "successor_designation",
    # Retracts a designation (2026-08-21, the same day, for the reason recorded
    # in CORRECTIONS.md). It appends; it never edits. The voided row still
    # hashes and still verifies — an append-only record answers a mistake with
    # a later row saying so, which is more informative than an eraser.
    "successor_designation_void",
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


def load_published_pubkey():
    """
    Load the Foundation PUBLIC key named by HEXIS_SEAL_PUBKEY_PATH.

    Returns (public_key, pem_bytes, None) on success, or (None, None, error)
    where error is a dict carrying reason, detail and the HTTP status a route
    should use.

    This is the only place the server reads key material. It reads a public
    key and refuses anything else, so a mistyped path cannot turn the server
    into a private-key leak.
    """
    if not CRYPTO_AVAILABLE:
        return None, None, {
            "reason": "crypto_unavailable", "http_status": 503,
            "detail": "cryptography library not installed"}

    path = os.environ.get(PUBKEY_PATH_ENV)
    if not path:
        return None, None, {
            "reason": "pubkey_not_configured", "http_status": 503,
            "detail": f"{PUBKEY_PATH_ENV} is not set"}

    try:
        with open(os.path.expanduser(path), "rb") as f:
            pem = f.read()
    except FileNotFoundError:
        log.error("pubkey: %s points at a missing file: %s",
                  PUBKEY_PATH_ENV, path)
        return None, None, {
            "reason": "pubkey_missing", "http_status": 503,
            "detail": "configured public key file not found"}
    except PermissionError:
        log.error("pubkey: %s is not readable: %s", PUBKEY_PATH_ENV, path)
        return None, None, {
            "reason": "pubkey_unreadable", "http_status": 503,
            "detail": "configured public key file not readable"}

    # Hard stop against a misconfigured env var pointing at the private key.
    # Refuse whatever the file happens to be called.
    if b"PRIVATE KEY" in pem.upper():
        log.critical("pubkey: %s points at a PRIVATE key (%s) — refusing",
                     PUBKEY_PATH_ENV, path)
        return None, None, {
            "reason": "private_key_configured", "http_status": 500,
            "detail": "configured file contains a private key; refusing to use it"}

    try:
        pub = serialization.load_pem_public_key(pem)
    except Exception:
        log.error("pubkey: file at %s is not a valid PEM public key", path)
        return None, None, {
            "reason": "pubkey_unparseable", "http_status": 500,
            "detail": "configured file is not a valid PEM public key"}

    if not isinstance(pub, Ed25519PublicKey):
        return None, None, {
            "reason": "wrong_key_type", "http_status": 500,
            "detail": "configured public key is not Ed25519"}

    return pub, pem, None


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

    def _unsigned_head(self, conn):
        """
        The newest event that carries no signature — the row a seal covers.

        Both halves of sealing must agree on this row exactly. If the local
        seal() and the remote GET /audit/chain/head ever disagreed, the
        offline signer would sign one row while the server wrote the
        signature onto another, and every later verification would fail with
        no obvious cause. One query, one definition, called from both.
        """
        return conn.execute(
            "SELECT event_id, sequence FROM audit_log "
            "WHERE signature IS NULL "
            "ORDER BY sequence DESC LIMIT 1"
        ).fetchone()

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

    def seal_status(self):
        """
        Freshness of the seal, so a stalled scheduler is visible instead of
        silent. days_since_last_seal is None when no seal has ever run.

        Timing is taken from the 'daily_seal' event, NOT from the signed row:
        the signed row carries the timestamp of the business event it records,
        which may predate the sealing run by an arbitrary amount.
        """
        conn = self._connect()
        conn.row_factory = sqlite3.Row
        try:
            last = conn.execute(
                "SELECT sequence, timestamp_iso, timestamp_unix, payload "
                "FROM audit_log WHERE action_type = 'daily_seal' "
                "ORDER BY sequence DESC LIMIT 1"
            ).fetchone()
            (signed,) = conn.execute(
                "SELECT COUNT(*) FROM audit_log WHERE signature IS NOT NULL"
            ).fetchone()
            (total,) = conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()
        finally:
            conn.close()

        if last is None:
            return {
                "ever_sealed": False,
                "days_since_last_seal": None,
                "last_seal_iso": None,
                "last_seal_sequence": None,
                "sealed_event_id": None,
                "total_seals": 0,
                "signed_events": signed,
                "total_events": total,
            }

        now_unix = int(datetime.now(timezone.utc).timestamp())
        elapsed_days = (now_unix - last["timestamp_unix"]) / 86400.0
        try:
            sealed_event_id = json.loads(last["payload"]).get("sealed_event_id")
        except Exception:
            sealed_event_id = None

        return {
            "ever_sealed": True,
            "days_since_last_seal": round(elapsed_days, 3),
            "last_seal_iso": last["timestamp_iso"],
            "last_seal_sequence": last["sequence"],
            "sealed_event_id": sealed_event_id,
            "total_seals": signed,
            "signed_events": signed,
            "total_events": total,
        }

    def get_chain_page(self, from_sequence=0, limit=1000):
        """
        A contiguous slice of the chain, every column included, ordered by
        sequence. Unfiltered on purpose: a hash chain can only be recomputed
        over consecutive records, so any per-actor filter makes independent
        verification impossible.
        """
        limit = max(1, min(int(limit), CHAIN_PAGE_MAX))
        conn = self._connect()
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                "SELECT event_id, sequence, prev_hash, content_hash, actor_id, "
                "       counterparty_id, action_type, payload, "
                "       timestamp_iso, timestamp_unix, signature "
                "FROM audit_log WHERE sequence >= ? "
                "ORDER BY sequence ASC LIMIT ?",
                (int(from_sequence), limit),
            ).fetchall()
            bounds = conn.execute(
                "SELECT MIN(sequence), MAX(sequence), COUNT(*) FROM audit_log"
            ).fetchone()
        finally:
            conn.close()

        min_seq, max_seq, total = bounds[0], bounds[1], bounds[2]
        events = [dict(r) for r in rows]   # payload stays a raw JSON string
        last_returned = events[-1]["sequence"] if events else None
        more = last_returned is not None and max_seq is not None \
            and last_returned < max_seq

        return {
            "schema_version": SCHEMA_VERSION,
            "chain_min_sequence": min_seq,
            "chain_max_sequence": max_seq,
            "total_events": total,
            "from_sequence": int(from_sequence),
            "returned": len(events),
            "has_more": more,
            "next_from_sequence": (last_returned + 1) if more else None,
            # Everything an outsider needs to recompute without our code.
            # payload is returned as the exact stored string: re-serialising it
            # would change byte order and break the hash.
            "hash_spec": {
                "content_hash": (
                    'sha256(json.dumps({"action_type","actor_id",'
                    '"counterparty_id","payload","timestamp_unix"}, '
                    'sort_keys=True, separators=(",",":"), ensure_ascii=False))'
                    " — counterparty_id is the empty string when null;"
                    " payload is the PARSED object, not the string"
                ),
                "event_id": 'sha256("{prev_hash}|{content_hash}|{sequence}|{timestamp_unix}")',
                "linkage": "prev_hash of each row equals event_id of the previous row",
                "genesis_prev_hash": GENESIS_PREV_HASH,
                "signature": "ed25519 over the ASCII bytes of event_id; hex-encoded",
                "encoding": "utf-8 throughout",
            },
            "events": events,
        }

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

    # ---------- split seal: server half (public key only) ----------

    def head_to_sign(self):
        """
        The exact string an offline signer must sign, and nothing more.

        Returns status 'ready' with the event_id and sequence, or
        'nothing_to_sign' when every event already carries a signature.
        """
        conn = self._connect()
        try:
            row = self._unsigned_head(conn)
            (total,) = conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()
        finally:
            conn.close()

        if row is None:
            return {"status": "nothing_to_sign", "total_events": total}

        event_id, sequence = row
        return {
            "status": "ready",
            "event_id": event_id,
            "sequence": sequence,
            "total_events": total,
            # Spelled out so a signer written by someone else, in another
            # language, signs the same bytes. The message is the 64-character
            # hex TEXT of the event_id, not the 32 raw bytes it encodes.
            "signing_recipe": {
                "message": "the event_id field of this response",
                "message_encoding": "utf-8 bytes of the 64-char lowercase hex string",
                "algorithm": "Ed25519",
                "signature_encoding": "lowercase hex, 128 characters",
                "submit_to": "POST /audit/seal with {event_id, signature}",
            },
        }

    def seal_with_signature(self, event_id, signature_hex):
        """
        Record a seal signed somewhere else. Verify first, write second.

        The server holds only the public key, so this can confirm a signature
        and can never produce one. An attacker who reaches this endpoint
        without the Foundation private key gets a rejection, not a seal.

        Refused unless all of these hold:
          - the signature verifies against the published public key
          - event_id is the current unsigned head
          - that row does not already carry a signature

        The last two stop replay and double-sealing: a captured (event_id,
        signature) pair is only ever accepted once, because writing it moves
        the head somewhere else.
        """
        pub, _pem, err = load_published_pubkey()
        if err is not None:
            log.error("SEAL REJECTED: %s", err["detail"])
            return {"status": "error", **err}

        if not isinstance(event_id, str) or len(event_id) != 64:
            return {"status": "rejected", "reason": "bad_event_id",
                    "http_status": 400,
                    "detail": "event_id must be a 64-character hex string"}

        try:
            sig = bytes.fromhex(signature_hex)
        except (TypeError, ValueError):
            return {"status": "rejected", "reason": "bad_signature_encoding",
                    "http_status": 400,
                    "detail": "signature must be hex"}
        if len(sig) != 64:
            return {"status": "rejected", "reason": "bad_signature_length",
                    "http_status": 400,
                    "detail": f"Ed25519 signature must be 64 bytes, got {len(sig)}"}

        # Verify before touching the database at all. A bad signature must
        # never reach the write path.
        try:
            pub.verify(sig, event_id.encode("utf-8"))
        except InvalidSignature:
            log.warning("SEAL REJECTED: signature does not verify for %s", event_id)
            return {"status": "rejected", "reason": "signature_invalid",
                    "http_status": 400,
                    "detail": "signature does not verify against the published "
                              "Foundation public key"}

        conn = self._connect()
        try:
            # BEGIN IMMEDIATE takes the write lock now, so the head check and
            # the write cannot be split by a second request arriving between
            # them. Without it two concurrent posts could both see the same
            # head and both write.
            conn.execute("BEGIN IMMEDIATE")

            row = self._unsigned_head(conn)
            if row is None:
                conn.rollback()
                return {"status": "rejected", "reason": "nothing_to_seal",
                        "http_status": 409,
                        "detail": "every event already carries a signature"}

            head_event_id, sequence = row
            if head_event_id != event_id:
                conn.rollback()
                log.warning("SEAL REJECTED: %s is not the current head (%s)",
                            event_id, head_event_id)
                return {"status": "rejected", "reason": "not_current_head",
                        "http_status": 409,
                        "detail": "event_id is not the current unsigned head; "
                                  "the chain moved on — fetch the head again",
                        "current_head_event_id": head_event_id,
                        "current_head_sequence": sequence}

            # Conditional UPDATE as a second line of defence: even if the
            # lock above were ever lost, this refuses to overwrite a
            # signature that is already there.
            cur = conn.execute(
                "UPDATE audit_log SET signature = ? "
                "WHERE event_id = ? AND signature IS NULL",
                (signature_hex, event_id),
            )
            if cur.rowcount != 1:
                conn.rollback()
                log.warning("SEAL REJECTED: %s already signed", event_id)
                return {"status": "rejected", "reason": "already_signed",
                        "http_status": 409,
                        "detail": "that row already carries a signature"}

            self._insert(
                conn,
                actor_id="FOUNDATION",
                counterparty_id=None,
                action_type="daily_seal",
                payload={"sealed_event_id": event_id,
                         "sealed_sequence": sequence,
                         "signature": signature_hex},
            )
            conn.commit()
            log.info("SEALED remotely: sequence %s, event_id %s", sequence, event_id)
            return {"status": "sealed",
                    "sealed_event_id": event_id,
                    "sealed_sequence": sequence,
                    "signature": signature_hex}
        except Exception as exc:
            conn.rollback()
            log.error("SEAL FAILED during write: %s: %s", type(exc).__name__, exc)
            return {"status": "error", "reason": "write_failed",
                    "http_status": 500,
                    "detail": f"{type(exc).__name__}: {exc}"}
        finally:
            conn.close()

    # ---------- daily seal: local half (needs the private key here) ----------

    def seal(self, private_key_path):
        """
        Sign latest unsigned event with Foundation ed25519 key.
        Append a 'daily_seal' event recording the signature.
        Designed for cron at 00:00 UTC.
        """
        # Every failure below is logged and returned as a status dict rather
        # than raised. Under a scheduler an uncaught exception is invisible:
        # the seal simply stops happening and nothing says so. The signing
        # logic further down is untouched.
        if not CRYPTO_AVAILABLE:
            log.error("SEAL FAILED: cryptography library not installed")
            return {"status": "error", "reason": "crypto_unavailable",
                    "detail": "cryptography library not installed"}

        try:
            with open(private_key_path, "rb") as f:
                priv = serialization.load_pem_private_key(f.read(), password=None)
        except FileNotFoundError:
            log.error("SEAL FAILED: private key not found at %s", private_key_path)
            return {"status": "error", "reason": "key_not_found",
                    "detail": f"no such file: {private_key_path}"}
        except PermissionError:
            log.error("SEAL FAILED: private key not readable at %s", private_key_path)
            return {"status": "error", "reason": "key_unreadable",
                    "detail": f"permission denied: {private_key_path}"}
        except Exception as exc:
            # Deliberately does not echo the exception text, which can quote
            # bytes of the key file.
            log.error("SEAL FAILED: private key could not be parsed (%s)",
                      type(exc).__name__)
            return {"status": "error", "reason": "key_unparseable",
                    "detail": f"{type(exc).__name__} while loading PEM"}

        if not isinstance(priv, Ed25519PrivateKey):
            log.error("SEAL FAILED: key at %s is %s, not Ed25519",
                      private_key_path, type(priv).__name__)
            return {"status": "error", "reason": "wrong_key_type",
                    "detail": "Foundation key must be Ed25519"}

        conn = self._connect()
        try:
            row = self._unsigned_head(conn)
            if row is None:
                log.info("seal: nothing to do, no unsigned events")
                return {"status": "nothing_to_seal"}
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
            log.info("seal OK: sequence %s, event_id %s", sequence, event_id)
            return {"status": "sealed",
                    "sealed_event_id": event_id,
                    "sealed_sequence": sequence,
                    "signature": sig}
        except Exception as exc:
            conn.rollback()
            log.error("SEAL FAILED during write: %s: %s",
                      type(exc).__name__, exc)
            return {"status": "error", "reason": "write_failed",
                    "detail": f"{type(exc).__name__}: {exc}"}
        finally:
            conn.close()


# =====================================================================
# FastAPI router
# =====================================================================

class SealSubmission(BaseModel):
    """A signature produced somewhere else, offered for verification."""
    event_id: str = Field(..., min_length=64, max_length=64,
                          pattern="^[0-9a-f]{64}$",
                          description="the unsigned head returned by /audit/chain/head")
    signature: str = Field(..., min_length=128, max_length=128,
                           pattern="^[0-9a-f]{128}$",
                           description="Ed25519 signature over the event_id text, hex")


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

    # NOTE ON ROUTE ORDER: the catch-all "/{actor_id}" below would swallow
    # "/pubkey" and "/chain/full". FastAPI matches in registration order, so
    # every literal path must stay declared above it.

    @router.get("/chain/verify")
    def chain_verify():
        # Convenience only. This is us grading our own work — an external
        # reviewer should recompute from /audit/chain/full instead.
        return audit.verify_chain()

    @router.get("/chain/full")
    def chain_full(
        from_sequence: int = Query(0, ge=0,
                                   description="first sequence to return, inclusive"),
        limit: int = Query(1000, ge=1, le=CHAIN_PAGE_MAX,
                           description="page size"),
    ):
        """
        The complete audit chain, contiguous and unfiltered, for independent
        verification. Page with from_sequence + next_from_sequence until
        has_more is false.

        Deliberately not filtered by actor: a hash chain is only checkable
        over consecutive records, so a filtered view cannot be verified by
        anyone who did not already trust us.
        """
        return audit.get_chain_page(from_sequence=from_sequence, limit=limit)

    @router.get("/chain/head")
    def chain_head():
        """
        The event_id that currently needs signing, for a signer that holds
        the Foundation private key somewhere other than this machine.

        Read-only, and it reveals nothing private: the same event_id is
        already visible in /audit/chain/full.
        """
        return audit.head_to_sign()

    @router.post("/seal")
    def submit_seal(submission: SealSubmission):
        """
        Accept a seal signature made off this machine.

        The server verifies against the published PUBLIC key before writing.
        It cannot produce a signature, only check one, which is the whole
        point of the split: the private key never has to live here.

        Rejections are deliberate and explicit rather than silent, so a
        stalled signer shows up as failing calls instead of a chain that
        quietly stops being sealed.
        """
        result = audit.seal_with_signature(submission.event_id,
                                           submission.signature)
        if result.get("status") != "sealed":
            raise HTTPException(
                status_code=result.get("http_status", 400),
                detail={k: v for k, v in result.items() if k != "http_status"},
            )
        return result

    @router.get("/pubkey", response_class=PlainTextResponse)
    def foundation_pubkey():
        """
        The Foundation Ed25519 PUBLIC key, PEM encoded. A seal signature is
        worthless to a third party who has no trustworthy copy of this key.

        Path comes from HEXIS_SEAL_PUBKEY_PATH; there is no default, so an
        operator must choose it consciously. Loading and the refusal to serve
        a private key live in load_published_pubkey(), shared with the code
        that verifies submitted seals — so the key used to CHECK a seal is by
        construction the same key published for others to check it with.
        """
        pub, pem, err = load_published_pubkey()
        if err is not None:
            raise HTTPException(status_code=err["http_status"],
                                detail=err["detail"])
        return PlainTextResponse(pem.decode("ascii"),
                                 media_type="application/x-pem-file")

    @router.get("/seals")
    def list_seals():
        # seal_status carries days_since_last_seal so a stalled scheduler is
        # visible from the outside instead of failing quietly.
        status = audit.seal_status()
        return {"seals": audit.list_seals(), **status}

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
