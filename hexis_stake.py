"""
hexis_stake.py — P1 Bilateral Stake (substance v0.7) — v3.2

Thay doi v3.2 (2026-08-12 — money-printer gating):
  - POST /stake/credit -> 410 Gone, internal-only. credit() cong escrow ma
    KHONG tru o dau — no TAO ECU. Signature (3c) chi lam call attributable,
    khong lam no hop le: actor ky cho actor_id cua chinh minh la ky de tu
    tao balance. Authentication khong phai authorisation.
    credit() giu nguyen la method — bridge van goi noi bo (PoSP audit reward).
  - POST /stake/expire -> 410 Gone, internal-only. Sweep tren MOI lock qua
    TTL, khong scope theo actor nao ca -> chu ky chi chung minh "mot actor
    da dang ky nao do" goi. Cung hinh dang /stake/release. expire_stale()
    giu nguyen la method; CHUA co caller noi bo nao — xem CORRECTIONS.md.
  - ecu_total(): tong ECU module dang giu (escrow + stake locked + fee
    locked), doc thang tu bang. Dung cho supply-conservation check luc
    startup: moi operation chuyen tien phai giu nguyen so nay, operation
    burn phai lam no giam DUNG bang so da burn.

Thay doi v3.1 (di cung hexis_severity.py — Severity Tiers v0.8 bootstrap):
  - 2 injected fn moi (DI pattern giong audit_fn):
      eligibility_fn(actor_id) -> raise 403 neu actor bi quarantine/blacklist/
        debt (severity.check_eligible). Goi trong lock() cho CA HAI ben.
      incident_fn(incident_dict) -> classify + record incident
        (severity.record_incident). Goi sau dispute_slash; ket qua severity
        tra kem trong response.
    Ca hai optional — None thi stake chay nhu v3.
  - transfer(from_id, to_id, amount): chuyen escrow atomic (dung cho
    /severity/repay va cac dong tien noi bo sau nay).

---- lich su v3 ----

Thay doi so voi v2 (nghiem thu E2E 22-23/7 loi ra 3 gap kinh te):

  1. FEE SETTLE ATOMIC (gap #1 — truoc day fee bi MINT tu faucet, khong transfer).
     lock():            debit consumer = stake + fee (fee giu trong escrow-lock)
     settle_complete(): hoan stake ca hai + chuyen FEE sang worker — atomic,
                        cung transaction voi release. Client KHONG con tu tra
                        fee qua /stake/credit nua.
     dispute_slash():   fee di theo ben trung thuc:
                          worker phan boi   -> fee HOAN ve consumer
                          consumer phan boi -> fee TRA cho worker (den bu cong suat)
                        Stake ben phan boi van burn (khong hoan) nhu v2.

  2. GATE /stake/release (gap #2 — endpoint khong auth, sau Block 0 se thanh
     lo mint HEXIS consumer tuy tien vi settle_complete goi hexis_settle_fn).
     v3: settle_complete CHI duoc goi noi bo tu bridge (/job/{id}/complete).
     Public router thay bang:
       POST /stake/abort  — unwind lock ma job CHUA TON TAI trong bang jobs
                            (job_request that bai sau khi lock). Dieu kien:
                            khong co row jobs + lock du 30s tuoi (cho write
                            queue flush). KHONG mint HEXIS — day la property
                            an toan chinh.
       POST /stake/release — tra 410 Gone + huong dan (giu path de client cu
                            khong silent-fail).

  3. LOCK EXPIRY (gap #3 — consumer lock roi bien mat = stake worker ket vinh vien).
     POST /stake/expire — release moi lock qua LOCK_TTL_S (mac dinh 3600s):
       job completed (settle truoc do that bai)  -> fee sang worker (nhu settle,
                                                    khong mint HEXIS — co hoi mint
                                                    da qua, chi cuu tien)
       job pending / khong ton tai               -> fee hoan consumer (nhu abort)
     Deterministic policy — v3 cho public-callable. SAI: xem v3.2, gio
     internal-only (410). ttl toi thieu 600s.

KHONG doi so voi v2 (bridge wiring giu nguyen, KHONG can patch lai bridge):
  - Constructor: StakeManager(db_path, audit_fn, hexis_settle_fn, hexis_wipe_fn)
  - require_both_locked(job_id)  — COUPLING A
  - settle_complete(job_id)      — COUPLING B (gio la internal-only qua bridge)
  - get_stake_router / get_dispute_router
  - Schema tables: KHONG them cot nao — khong can migration tren bridge.db
  - GATE BLOCK 0 giu nguyen: hexis_settle_fn / hexis_wipe_fn = None cho toi
    khi Genesis Block 0 mint (State v4 §0).

LOOP: Audit (detect) -> Stake (enforce, module nay) -> Severity (classify, v0.8+).
"""

import hashlib
import json
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from typing import Callable, Optional, Dict, Any, Iterator, List

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

import hexis_ledger_entries as ledger


# ----------------------------------------------------------------------------
# CONFIG (v0.7 — tat ca tunable, doi sang config file sau khi co data that)
# ----------------------------------------------------------------------------
SLASH_FRACTION = 1.0          # uniform 100% ECU stake, CHUA tier
PAIR_CAP_COUNT = 5            # max so job giua cung 1 cap (consumer, worker)
PAIR_CAP_WINDOW_S = 86400     # trong cua so 24h (rolling)

ABORT_MIN_AGE_S = 30          # lock phai du tuoi nay moi abort duoc
                              # (cho bridge write-queue flush bang jobs ~100ms,
                              #  30s la du an toan nhieu bac)
LOCK_TTL_S = 3600             # lock qua tuoi nay thi /stake/expire release duoc
EXPIRE_TTL_MIN_S = 600        # ttl truyen vao expire khong duoc thap hon

# Trang thai stake
LOCKED = "locked"

# P1 bilateral stake (2026-08-23). Terms lifecycle, distinct from stake status.
TERMS_PROPOSED = "proposed"   # consumer has signed; worker has not
TERMS_ACCEPTED = "accepted"   # both sides signed the same numbers
TERMS_TTL_S    = 3600.0       # a proposal a worker never answers goes stale
RELEASED = "released"
SLASHED = "slashed"
ABORTED = "aborted"
EXPIRED = "expired"

# Phia phan boi hop le trong dispute
SIDE_CONSUMER = "consumer"
SIDE_WORKER = "worker"


# ----------------------------------------------------------------------------
# StakeManager — engine doc lap
# ----------------------------------------------------------------------------
class StakeManager:
    """
    Quan ly bilateral stake. Tu so huu tables trong cung bridge.db.
    Doc them bang `jobs` (bridge so huu) de phan biet abort/expire cases.

    Injected dependencies (bind o buoc wire trong bridge):
      audit_fn(action, actor_id, counterparty, data) -> ghi vao audit chain hien co.
      hexis_settle_fn(consumer_id, worker_id, job_value_ecu, sensitivity_tier, job_id)
                -> mint HEXIS cho consumer qua formula that (BehaviorEvent).
      hexis_wipe_fn(actor_id, reason)
                -> xoa sach HEXIS standing ve 0 (append offsetting record).
    Ca 3 optional: neu None thi module van chay (no-op).
    GATE BLOCK 0: 2 ham hexis PHAI None cho toi khi Genesis Block 0 mint.
    """

    def __init__(
        self,
        db_path: str,
        audit_fn: Optional[Callable[..., Any]] = None,
        hexis_settle_fn: Optional[Callable[..., Any]] = None,
        hexis_wipe_fn: Optional[Callable[..., Any]] = None,
        eligibility_fn: Optional[Callable[[str], None]] = None,
        incident_fn: Optional[Callable[[Dict[str, Any]], Any]] = None,
    ):
        self.db_path = db_path
        self.audit_fn = audit_fn
        self.hexis_settle_fn = hexis_settle_fn
        self.hexis_wipe_fn = hexis_wipe_fn
        self.eligibility_fn = eligibility_fn
        self.incident_fn = incident_fn
        # The ledger operation currently open on this thread, if any. Thread
        # local because one StakeManager is shared by every request handler:
        # a plain attribute would let one request's legs land in another's
        # operation, and the imbalance would surface on whichever committed
        # second.
        self._tl = threading.local()
        self._init_schema()

    # --- ket noi: moi thao tac mo connection rieng (an toan voi FastAPI threadpool) ---
    def _conn(self) -> sqlite3.Connection:
        c = sqlite3.connect(self.db_path, timeout=10)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA journal_mode=WAL;")
        return c

    def _init_schema(self) -> None:
        with self._conn() as c:
            c.executescript(
                """
                CREATE TABLE IF NOT EXISTS stake_escrow (
                    actor_id   TEXT PRIMARY KEY,
                    balance    REAL NOT NULL DEFAULT 0,
                    updated_at REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS consumer_stake (
                    id               TEXT PRIMARY KEY,
                    consumer_id      TEXT NOT NULL,
                    worker_id        TEXT,
                    job_id           TEXT NOT NULL,
                    amount           REAL NOT NULL,
                    job_value_ecu    REAL NOT NULL DEFAULT 0,
                    sensitivity_tier INTEGER NOT NULL DEFAULT 1,
                    status           TEXT NOT NULL,
                    locked_at        REAL NOT NULL,
                    updated_at       REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS ix_cs_job  ON consumer_stake(job_id);
                CREATE INDEX IF NOT EXISTS ix_cs_cons ON consumer_stake(consumer_id);

                CREATE TABLE IF NOT EXISTS worker_stake (
                    id         TEXT PRIMARY KEY,
                    worker_id  TEXT NOT NULL,
                    consumer_id TEXT,
                    job_id     TEXT NOT NULL,
                    amount     REAL NOT NULL,
                    status     TEXT NOT NULL,
                    locked_at  REAL NOT NULL,
                    updated_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS ix_ws_job ON worker_stake(job_id);
                CREATE INDEX IF NOT EXISTS ix_ws_wrk ON worker_stake(worker_id);

                CREATE TABLE IF NOT EXISTS slash_log (
                    id             TEXT PRIMARY KEY,
                    job_id         TEXT NOT NULL,
                    slashed_party  TEXT NOT NULL,   -- actor_id bi slash
                    slashed_role   TEXT NOT NULL,   -- consumer | worker
                    counterparty   TEXT,            -- actor_id duoc release
                    amount         REAL NOT NULL,
                    fraction       REAL NOT NULL,
                    hexis_wiped    REAL NOT NULL DEFAULT 0,
                    reason         TEXT,
                    raised_by      TEXT,
                    slashed_at     REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS ix_slash_job ON slash_log(job_id);

                -- log moi cap (consumer, worker) de tinh pair-frequency cap chong collusion
                CREATE TABLE IF NOT EXISTS pair_activity (
                    id          TEXT PRIMARY KEY,
                    consumer_id TEXT NOT NULL,
                    worker_id   TEXT NOT NULL,
                    job_id      TEXT NOT NULL,
                    ts          REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS ix_pair ON pair_activity(consumer_id, worker_id, ts);

                -- P1 BILATERAL STAKE (2026-08-23, OPEN.md #1).
                --
                -- lock() debits both parties. Until now one signature -- the
                -- consumer's -- moved the worker's money too, and there was
                -- nowhere in the flow the worker had agreed: /stake/lock
                -- happens BEFORE /job/request, so no job row existed to carry
                -- consent. This table is that missing place.
                --
                -- terms_hash is over the exact numbers both sides are agreeing
                -- to. The worker accepts by making its own signed call, so the
                -- signature is produced at the moment of consent rather than
                -- collected and forwarded by the counterparty -- one of the
                -- four cheap fixes OPEN.md rules out in advance. Because that
                -- call is RFC 9421 over a URL containing this job_id, and its
                -- nonce is recorded, the acceptance cannot be replayed onto
                -- another job.
                CREATE TABLE IF NOT EXISTS stake_terms (
                    job_id           TEXT PRIMARY KEY,
                    consumer_id      TEXT NOT NULL,
                    worker_id        TEXT NOT NULL,
                    consumer_amount  REAL NOT NULL,
                    worker_amount    REAL NOT NULL,
                    job_value_ecu    REAL NOT NULL,
                    sensitivity_tier INTEGER NOT NULL DEFAULT 1,
                    expiry           REAL NOT NULL,
                    terms_hash       TEXT NOT NULL,
                    status           TEXT NOT NULL,
                    proposed_at      REAL NOT NULL,
                    accepted_at      REAL
                );
                CREATE INDEX IF NOT EXISTS ix_terms_worker ON stake_terms(worker_id, status);
                """
            )
            # The entry ledger these balances are checked against. Created
            # here rather than by the bridge so that a StakeManager built for
            # a test has it too — an escrow mutation with nowhere to write its
            # entry is the state this whole module now refuses to be in.
            ledger.init_schema(c)

    # ------------------------------------------------------------------
    # LEDGER — every escrow mutation is one leg of a balanced operation
    # ------------------------------------------------------------------
    @contextmanager
    def _ledger(self, c: sqlite3.Connection, op: str, *,
                job_id: Optional[str] = None,
                reason: Optional[str] = None) -> Iterator[ledger.LedgerOp]:
        """
        Open a ledger operation for the duration of a balance change.

        Enter this *before* the first statement of the transaction. It takes
        `BEGIN IMMEDIATE`, which cannot be done once a read has already opened
        a deferred transaction on the same connection.

        `_debit` and `_credit_in_tx` add their own escrow legs. The caller adds
        the counter legs — where the money went, or came from — and the legs
        are checked against each other before anything is written. An operation
        that does not balance raises out of this block, so the escrow UPDATE it
        was paired with rolls back with it.
        """
        if getattr(self._tl, "op", None) is not None:
            raise RuntimeError(
                f"ledger operation {self._tl.op.op!r} is already open on this "
                f"thread; {op!r} would nest inside it"
            )
        with ledger.ledger_op(c, op, job_id=job_id, reason=reason) as op_obj:
            self._tl.op = op_obj
            try:
                yield op_obj
            finally:
                self._tl.op = None

    def _leg(self, account: str, actor_id: str, delta: float) -> None:
        op = getattr(self._tl, "op", None)
        if op is None:
            raise RuntimeError(
                "escrow was about to change outside a ledger operation "
                f"({account}:{actor_id} {delta:+}). Wrap the transaction in "
                "`with self._ledger(c, '<op>')` — a movement with no entry is "
                "the thing this ledger exists to make impossible."
            )
        op.leg(account, actor_id, delta)

    # ------------------------------------------------------------------
    # ESCROW balance (ECU) — module la authority. Bridge credit khi nap.
    # ------------------------------------------------------------------
    def credit(self, actor_id: str, amount: float, reason: str = "") -> float:
        """
        Create escrow balance. Internal callers only — see the router.

        Audited as of 2026-08-12. Until then this was the one money path that
        wrote nothing anywhere: the forensic review of the open /stake/credit
        endpoint could establish what escrow holds today, and had no record of
        issuance to check it against. An unaudited mint cannot be reconciled,
        only believed.
        """
        if amount <= 0:
            raise ValueError("credit amount must be > 0")
        now = time.time()
        with self._conn() as c:
            # `issuance` is the counter-account because that is what this is:
            # ECU appearing where there was none. The ledger names the mint
            # rather than letting it read as an ordinary increase.
            with self._ledger(c, "escrow_credit", reason=reason or "unspecified") as op:
                self._credit_in_tx(c, actor_id, amount, now)
                op.leg(ledger.ISSUANCE, "", -amount)
            row = c.execute(
                "SELECT balance FROM stake_escrow WHERE actor_id=?", (actor_id,)
            ).fetchone()
        self._audit("escrow_credit", actor_id, None,
                    {"amount": amount, "balance_after": row["balance"],
                     "reason": reason or "unspecified"})
        return row["balance"]

    def balance(self, actor_id: str) -> float:
        with self._conn() as c:
            row = c.execute(
                "SELECT balance FROM stake_escrow WHERE actor_id=?", (actor_id,)
            ).fetchone()
        return row["balance"] if row else 0.0

    def ecu_total(self) -> float:
        """
        Every ECU this module is holding, spendable or locked.

        Escrow that has been debited into a lock has left `stake_escrow` but
        has not left the system, so a total that counted only balances would
        fall on every lock and rise on every release. Locked stake and the
        escrowed fee are added back to make the figure stable across an
        operation — which is the whole point of having it: an operation that
        moves money must leave this number alone, and one that burns money
        must move it by exactly the amount burned.

        Read straight from the tables, never cached and never stored. A total
        supply figure kept in a variable is a figure that can disagree with
        the rows it claims to summarise, and the disagreement is invisible
        precisely when it matters.
        """
        with self._conn() as c:
            escrow = c.execute(
                "SELECT COALESCE(SUM(balance),0) AS n FROM stake_escrow"
            ).fetchone()["n"]
            consumer_locked = c.execute(
                "SELECT COALESCE(SUM(amount + job_value_ecu),0) AS n "
                "FROM consumer_stake WHERE status=?", (LOCKED,)
            ).fetchone()["n"]
            worker_locked = c.execute(
                "SELECT COALESCE(SUM(amount),0) AS n "
                "FROM worker_stake WHERE status=?", (LOCKED,)
            ).fetchone()["n"]
        return round(escrow + consumer_locked + worker_locked, 8)

    def _debit(self, c: sqlite3.Connection, actor_id: str, amount: float) -> None:
        row = c.execute(
            "SELECT balance FROM stake_escrow WHERE actor_id=?", (actor_id,)
        ).fetchone()
        bal = row["balance"] if row else 0.0
        if bal < amount:
            raise HTTPException(
                status_code=402,
                detail=f"insufficient ECU escrow: have {bal}, need {amount}",
            )
        c.execute(
            "UPDATE stake_escrow SET balance = balance - ?, updated_at=? WHERE actor_id=?",
            (amount, time.time(), actor_id),
        )
        self._leg(ledger.ESCROW, actor_id, -amount)

    def _credit_in_tx(self, c, actor_id, amount, now):
        c.execute(
            """INSERT INTO stake_escrow(actor_id,balance,updated_at) VALUES(?,?,?)
               ON CONFLICT(actor_id) DO UPDATE SET
                 balance = balance + excluded.balance, updated_at = excluded.updated_at""",
            (actor_id, amount, now),
        )
        self._leg(ledger.ESCROW, actor_id, +amount)

    def transfer(self, from_id: str, to_id: str, amount: float) -> Dict[str, Any]:
        """Chuyen escrow atomic (v3.1 — dung cho /severity/repay va noi bo)."""
        if amount <= 0:
            raise HTTPException(status_code=400, detail="transfer amount must be > 0")
        now = time.time()
        with self._conn() as c:
            # Two escrow legs, one out and one in: they balance each other, so
            # this operation needs no counter-account. ECU moves, none is made.
            with self._ledger(c, "escrow_transfer"):
                self._debit(c, from_id, amount)      # raise 402 neu thieu
                self._credit_in_tx(c, to_id, amount, now)
        self._audit("escrow_transfer", from_id, to_id, {"amount": amount})
        return {"from": from_id, "to": to_id, "amount": amount}

    # ------------------------------------------------------------------
    # JOBS TABLE (bridge so huu — chi DOC, de phan biet abort/expire)
    # ------------------------------------------------------------------
    def _job_status(self, c: sqlite3.Connection, job_id: str) -> Optional[str]:
        """None = job chua ton tai trong bang jobs (request chua accept/flush)."""
        try:
            row = c.execute(
                "SELECT status FROM jobs WHERE job_id=?", (job_id,)
            ).fetchone()
        except sqlite3.OperationalError:
            return None  # bang jobs chua ton tai (test module doc lap)
        return row["status"] if row else None

    # ------------------------------------------------------------------
    # PAIR-FREQUENCY CAP (chong collusion)
    # ------------------------------------------------------------------
    def have_transacted(self, a: str, b: str, window_s: float = 0.0) -> bool:
        """Hai actor da tung la doi tac cua nhau chua? (CA HAI chieu)

        Dung cho PoSP R4 (hexis_sampling.same_pair_fn): validator KHONG duoc
        audit worker ma minh tung giao dich — cap quen nhau la cap co the
        thong dong. Kiem ca hai chieu vi validator co the tung dong vai
        consumer HOAC worker.

        window_s <= 0 -> khong gioi han thoi gian ("da tung" = mai mai).
        """
        if not a or not b or a == b:
            return bool(a and b and a == b)
        since = (time.time() - window_s) if window_s and window_s > 0 else 0.0
        with self._conn() as c:
            row = c.execute(
                """SELECT 1 FROM pair_activity
                   WHERE ts >= ?
                     AND ((consumer_id=? AND worker_id=?)
                       OR (consumer_id=? AND worker_id=?))
                   LIMIT 1""",
                (since, a, b, b, a),
            ).fetchone()
        return row is not None

    # ------------------------------------------------------------------
    def _pair_count(self, c: sqlite3.Connection, consumer_id: str, worker_id: str) -> int:
        since = time.time() - PAIR_CAP_WINDOW_S
        row = c.execute(
            """SELECT COUNT(*) AS n FROM pair_activity
               WHERE consumer_id=? AND worker_id=? AND ts>=?""",
            (consumer_id, worker_id, since),
        ).fetchone()
        return row["n"]

    # ------------------------------------------------------------------
    # LOCK — ca hai phia khoa TRUOC khi job chay.
    # v3: consumer bi debit stake + FEE (fee giu trong escrow-lock).
    # ------------------------------------------------------------------
    # ------------------------------------------------------------------
    # P1 BILATERAL STAKE — the consent step lock() was missing
    # ------------------------------------------------------------------
    @staticmethod
    def terms_hash(job_id: str, consumer_id: str, worker_id: str,
                   consumer_amount: float, worker_amount: float,
                   job_value_ecu: float, sensitivity_tier: int,
                   expiry: float) -> str:
        """
        The exact numbers both parties are agreeing to, canonically.

        Every field that decides how much moves, and from whom, is in here.
        Anything left out could be changed between acceptance and lock without
        invalidating the worker's consent, which would make the consent
        decorative — the failure this mechanism exists to prevent. Amounts are
        rounded to 8 places so float formatting cannot make two identical
        agreements hash differently.
        """
        canonical = json.dumps({
            "job_id":           job_id,
            "consumer_id":      consumer_id,
            "worker_id":        worker_id,
            "consumer_amount":  round(float(consumer_amount), 8),
            "worker_amount":    round(float(worker_amount), 8),
            "job_value_ecu":    round(float(job_value_ecu), 8),
            "sensitivity_tier": int(sensitivity_tier),
            "expiry":           round(float(expiry), 3),
        }, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def propose_terms(self, job_id: str, consumer_id: str, worker_id: str,
                      consumer_amount: float, worker_amount: float,
                      job_value_ecu: float, sensitivity_tier: int = 1,
                      ttl_s: float = TERMS_TTL_S) -> Dict[str, Any]:
        """
        The consumer proposes. NOTHING MOVES HERE — this writes terms and a
        hash and debits nobody. The endpoint above it is signed by the
        consumer, so the proposal is attributable to the party making it.
        """
        if consumer_amount <= 0 or worker_amount <= 0:
            raise HTTPException(400, "stake amounts must be > 0")
        if job_value_ecu <= 0:
            raise HTTPException(400, "job_value_ecu must be > 0")
        if consumer_id == worker_id:
            raise HTTPException(400, "consumer and worker must differ")
        now = time.time()
        expiry = now + max(60.0, float(ttl_s))
        th = self.terms_hash(job_id, consumer_id, worker_id, consumer_amount,
                             worker_amount, job_value_ecu, sensitivity_tier,
                             expiry)
        with self._conn() as c:
            row = c.execute("SELECT status FROM stake_terms WHERE job_id=?",
                            (job_id,)).fetchone()
            if row:
                raise HTTPException(409,
                    f"job {job_id} already has terms in status '{row[0]}'; "
                    f"terms are proposed once, not renegotiated in place")
            c.execute(
                "INSERT INTO stake_terms(job_id, consumer_id, worker_id, "
                "consumer_amount, worker_amount, job_value_ecu, "
                "sensitivity_tier, expiry, terms_hash, status, proposed_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (job_id, consumer_id, worker_id, consumer_amount,
                 worker_amount, job_value_ecu, int(sensitivity_tier), expiry,
                 th, TERMS_PROPOSED, now))
        if self.audit_fn:
            self.audit_fn("stake_terms_proposed", consumer_id, worker_id,
                          {"job_id": job_id, "terms_hash": th,
                           "consumer_amount": consumer_amount,
                           "worker_amount": worker_amount,
                           "job_value_ecu": job_value_ecu,
                           "expiry": expiry,
                           "moves": "nothing. this is a proposal"})
        return {"job_id": job_id, "terms_hash": th, "status": TERMS_PROPOSED,
                "expiry": expiry,
                "next": "the worker accepts by signing "
                        "POST /stake/terms/{job_id}/accept with this terms_hash"}

    def accept_terms(self, job_id: str, terms_hash: str) -> Dict[str, Any]:
        """
        The worker accepts. Still nothing moves.

        `terms_hash` is required and must match. A worker that accepts without
        naming what it accepted has agreed to whatever the row happens to say,
        and that is not consent. The endpoint above this is signed by the
        worker named in the terms, so the signature is made by the consenting
        party at the moment of consent — not collected and forwarded by its
        counterparty, which OPEN.md rules out in advance.
        """
        now = time.time()
        with self._conn() as c:
            row = c.execute(
                "SELECT worker_id, terms_hash, status, expiry FROM stake_terms "
                "WHERE job_id=?", (job_id,)).fetchone()
            if not row:
                raise HTTPException(404, f"no terms proposed for job {job_id}")
            w_id, th, status, expiry = row
            if status == TERMS_ACCEPTED:
                raise HTTPException(409, f"job {job_id} already accepted")
            if now > expiry:
                raise HTTPException(410,
                    f"terms for job {job_id} expired; propose again")
            if not terms_hash or terms_hash.strip().lower() != th:
                raise HTTPException(409, {
                    "error": "terms_hash_mismatch",
                    "reason": "the hash you signed is not the hash on record. "
                              "accepting a different set of numbers than the "
                              "ones stored is exactly what this field catches",
                    "on_record": th})
            c.execute("UPDATE stake_terms SET status=?, accepted_at=? "
                      "WHERE job_id=?", (TERMS_ACCEPTED, now, job_id))
        if self.audit_fn:
            self.audit_fn("stake_terms_accepted", w_id, None,
                          {"job_id": job_id, "terms_hash": th,
                           "moves": "nothing. lock() moves the money"})
        return {"job_id": job_id, "terms_hash": th, "status": TERMS_ACCEPTED}

    def _require_accepted_terms(self, c, job_id, consumer_id, worker_id,
                                consumer_amount, worker_amount,
                                job_value_ecu, sensitivity_tier):
        """
        The gate lock() was missing. Frozen unless both sides signed THESE
        numbers.

        423 Locked rather than 400: the request is not malformed. It is a
        well-formed instruction to move somebody else's money that nobody has
        agreed to. Nothing is debited on any path out of this method.
        """
        row = c.execute(
            "SELECT consumer_id, worker_id, consumer_amount, worker_amount, "
            "job_value_ecu, sensitivity_tier, expiry, terms_hash, status "
            "FROM stake_terms WHERE job_id=?", (job_id,)).fetchone()
        if not row:
            raise HTTPException(423, {
                "error": "terms_not_agreed",
                "reason": "frozen: lock() debits both parties and no agreed "
                          "terms exist for this job. the consumer proposes at "
                          "POST /stake/terms; the worker accepts at "
                          "POST /stake/terms/{job_id}/accept",
                "job_id": job_id})
        if row[8] != TERMS_ACCEPTED:
            raise HTTPException(423, {
                "error": "worker_has_not_accepted",
                "reason": "frozen: the consumer proposed and the worker has "
                          "not signed. one signature does not move two "
                          "parties' money",
                "job_id": job_id, "status": row[8]})
        if time.time() > row[6]:
            raise HTTPException(410, {
                "error": "terms_expired",
                "reason": "the accepted terms have expired; propose again",
                "job_id": job_id})
        want = self.terms_hash(job_id, consumer_id, worker_id, consumer_amount,
                               worker_amount, job_value_ecu, sensitivity_tier,
                               row[6])
        if want != row[7]:
            raise HTTPException(409, {
                "error": "lock_differs_from_agreed_terms",
                "reason": "frozen: these are not the numbers the worker "
                          "accepted. consent is to an amount, a counterparty "
                          "and an expiry — not to the idea of a lock",
                "agreed": row[7], "requested": want})
        return row[7]

    def lock(
        self,
        job_id: str,
        consumer_id: str,
        worker_id: str,
        consumer_amount: float,
        worker_amount: float,
        job_value_ecu: float,
        sensitivity_tier: int = 1,
    ) -> Dict[str, Any]:
        if consumer_amount <= 0 or worker_amount <= 0:
            raise HTTPException(status_code=400, detail="stake amounts must be > 0")
        if job_value_ecu <= 0:
            raise HTTPException(status_code=400, detail="job_value_ecu must be > 0")
        # SEVERITY GATE (v3.1): ca hai ben phai eligible (khong quarantine/
        # blacklist/debt) truoc khi khoa tien. Raise 403 tu severity engine.
        if self.eligibility_fn:
            self.eligibility_fn(consumer_id)
            self.eligibility_fn(worker_id)
        now = time.time()
        # The ledger operation opens with the transaction, not partway through
        # it: it takes BEGIN IMMEDIATE, which SQLite will not accept once a
        # read has already opened one on this connection.
        with self._conn() as c, self._ledger(c, "stake_lock", job_id=job_id) as op:
            # idempotency: 1 job chi lock 1 lan
            dup = c.execute(
                "SELECT 1 FROM consumer_stake WHERE job_id=? AND status=?",
                (job_id, LOCKED),
            ).fetchone()
            if dup:
                raise HTTPException(status_code=409, detail=f"job {job_id} already locked")

            # P1 BILATERAL STAKE (2026-08-23, OPEN.md #1). Both sides must
            # have signed THESE numbers. Raises 423 and debits nothing if not.
            # Placed before the pair cap and before any _debit, so a frozen
            # lock leaves the ledger exactly as it was.
            _th = self._require_accepted_terms(
                c, job_id, consumer_id, worker_id, consumer_amount,
                worker_amount, job_value_ecu, sensitivity_tier)

            # pair-frequency cap
            n = self._pair_count(c, consumer_id, worker_id)
            if n >= PAIR_CAP_COUNT:
                raise HTTPException(
                    status_code=429,
                    detail=f"pair cap exceeded: {n} jobs in {PAIR_CAP_WINDOW_S}s "
                    f"between {consumer_id} and {worker_id} (max {PAIR_CAP_COUNT})",
                )

            # debit: consumer = stake + fee (v3), worker = stake. Raise 402 neu thieu.
            self._debit(c, consumer_id, consumer_amount + job_value_ecu)
            self._debit(c, worker_id, worker_amount)
            # Where the debited ECU went. The consumer's side carries the fee
            # as well as the stake, because `lock()` debits both and holds
            # both until the job ends one way or another.
            op.leg(ledger.LOCKED, consumer_id, consumer_amount + job_value_ecu)
            op.leg(ledger.LOCKED, worker_id, worker_amount)

            cs_id = uuid.uuid4().hex
            ws_id = uuid.uuid4().hex
            c.execute(
                """INSERT INTO consumer_stake
                   (id,consumer_id,worker_id,job_id,amount,job_value_ecu,
                    sensitivity_tier,status,locked_at,updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (cs_id, consumer_id, worker_id, job_id, consumer_amount,
                 job_value_ecu, sensitivity_tier, LOCKED, now, now),
            )
            c.execute(
                """INSERT INTO worker_stake
                   (id,worker_id,consumer_id,job_id,amount,status,locked_at,updated_at)
                   VALUES(?,?,?,?,?,?,?,?)""",
                (ws_id, worker_id, consumer_id, job_id, worker_amount, LOCKED, now, now),
            )
            c.execute(
                "INSERT INTO pair_activity(id,consumer_id,worker_id,job_id,ts) VALUES(?,?,?,?,?)",
                (uuid.uuid4().hex, consumer_id, worker_id, job_id, now),
            )

        self._audit("stake_lock", consumer_id, worker_id,
                    {"job_id": job_id, "consumer_amount": consumer_amount,
                     "worker_amount": worker_amount, "job_value_ecu": job_value_ecu,
                     "fee_escrowed": job_value_ecu,
                     "sensitivity_tier": sensitivity_tier})
        self._audit("stake_lock", worker_id, consumer_id,
                    {"job_id": job_id, "side": "worker"})
        return {"job_id": job_id, "status": LOCKED,
                "consumer_amount": consumer_amount, "worker_amount": worker_amount,
                "fee_escrowed": job_value_ecu}

    def require_both_locked(self, job_id: str) -> None:
        """COUPLING A: goi trong /job/request TRUOC khi accept. Raise 402 neu chua lock."""
        with self._conn() as c:
            cs = c.execute(
                "SELECT 1 FROM consumer_stake WHERE job_id=? AND status=?", (job_id, LOCKED)
            ).fetchone()
            ws = c.execute(
                "SELECT 1 FROM worker_stake WHERE job_id=? AND status=?", (job_id, LOCKED)
            ).fetchone()
        if not (cs and ws):
            raise HTTPException(
                status_code=402,
                detail=f"job {job_id} requires both consumer+worker stake locked before request",
            )

    # ------------------------------------------------------------------
    # Helper chung: lay cap stake dang LOCKED cua 1 job (404 neu khong co)
    # ------------------------------------------------------------------
    def _locked_pair(self, c: sqlite3.Connection, job_id: str):
        cs = c.execute(
            "SELECT * FROM consumer_stake WHERE job_id=? AND status=?", (job_id, LOCKED)
        ).fetchone()
        ws = c.execute(
            "SELECT * FROM worker_stake WHERE job_id=? AND status=?", (job_id, LOCKED)
        ).fetchone()
        if not (cs and ws):
            raise HTTPException(status_code=404, detail=f"no locked stake for job {job_id}")
        return cs, ws

    # ------------------------------------------------------------------
    # SETTLE — job hoan thanh sach. INTERNAL-ONLY tu v3: chi bridge goi
    # (trong /job/{id}/complete). KHONG con expose qua public router —
    # vi ham nay mint HEXIS consumer (sau Block 0), de public la lo mint.
    # v3: chuyen FEE sang worker atomic cung transaction voi release.
    # ------------------------------------------------------------------
    def settle_complete(self, job_id: str) -> Dict[str, Any]:
        """COUPLING B: goi trong /job/{id}/complete. Release + fee + consumer mint."""
        now = time.time()
        with self._conn() as c, self._ledger(c, "stake_release", job_id=job_id) as op:
            cs, ws = self._locked_pair(c, job_id)
            fee = cs["job_value_ecu"]
            # hoan stake ca hai + fee sang worker — 1 transaction
            self._credit_in_tx(c, cs["consumer_id"], cs["amount"], now)
            self._credit_in_tx(c, ws["worker_id"], ws["amount"] + fee, now)
            # The worker is credited its own stake plus the fee, but the fee
            # was never the worker's to lock: it came out of the consumer's
            # locked balance. Two separate legs, so the ledger shows the fee
            # crossing between the parties instead of appearing on one side.
            op.leg(ledger.LOCKED, cs["consumer_id"], -(cs["amount"] + fee))
            op.leg(ledger.LOCKED, ws["worker_id"], -ws["amount"])
            c.execute("UPDATE consumer_stake SET status=?, updated_at=? WHERE id=?",
                      (RELEASED, now, cs["id"]))
            c.execute("UPDATE worker_stake SET status=?, updated_at=? WHERE id=?",
                      (RELEASED, now, ws["id"]))
            consumer_id = cs["consumer_id"]
            worker_id = ws["worker_id"]
            job_value_ecu = cs["job_value_ecu"]
            sensitivity_tier = cs["sensitivity_tier"]

        # consumer-side HEXIS mint — formula that (BehaviorEvent qua bridge).
        # GATE BLOCK 0: hexis_settle_fn = None cho toi khi Genesis Block 0 mint.
        hexis_gain = 0.0
        if self.hexis_settle_fn:
            try:
                result = self.hexis_settle_fn(
                    consumer_id, worker_id, job_value_ecu, sensitivity_tier, job_id
                )
                if isinstance(result, dict):
                    hexis_gain = result.get("hexis_raw", 0.0) or 0.0
            except Exception:
                pass  # mint that bai khong duoc lam hong release ECU
        self._audit("stake_release", consumer_id, worker_id,
                    {"job_id": job_id, "hexis_gain": hexis_gain,
                     "fee_to_worker": fee})
        return {"job_id": job_id, "status": RELEASED, "hexis_gain": hexis_gain,
                "fee_to_worker": fee}

    # ------------------------------------------------------------------
    # ABORT — unwind lock ma job KHONG ton tai (job_request that bai sau lock).
    # Thay the /stake/release public cua v2. KHONG mint HEXIS — day la
    # property an toan chinh (khong co HEXIS tu job chua tung chay).
    # ------------------------------------------------------------------
    def abort_unstarted(self, job_id: str) -> Dict[str, Any]:
        now = time.time()
        with self._conn() as c, self._ledger(c, "stake_abort", job_id=job_id) as op:
            cs, ws = self._locked_pair(c, job_id)

            job_status = self._job_status(c, job_id)
            if job_status is not None:
                raise HTTPException(
                    status_code=409,
                    detail=f"job {job_id} exists (status={job_status}) — "
                    f"abort is only for locks whose job was never accepted; "
                    f"use dispute or wait for completion",
                )
            age = now - cs["locked_at"]
            if age < ABORT_MIN_AGE_S:
                raise HTTPException(
                    status_code=425,
                    detail=f"lock only {age:.0f}s old — abort requires >= "
                    f"{ABORT_MIN_AGE_S}s (job table may still be flushing); retry later",
                )

            fee = cs["job_value_ecu"]
            # hoan het: consumer = stake + fee, worker = stake
            self._credit_in_tx(c, cs["consumer_id"], cs["amount"] + fee, now)
            self._credit_in_tx(c, ws["worker_id"], ws["amount"], now)
            # Nothing was earned, so both sides simply come back out of the
            # locked account with the fee going home to the consumer.
            op.leg(ledger.LOCKED, cs["consumer_id"], -(cs["amount"] + fee))
            op.leg(ledger.LOCKED, ws["worker_id"], -ws["amount"])
            c.execute("UPDATE consumer_stake SET status=?, updated_at=? WHERE id=?",
                      (ABORTED, now, cs["id"]))
            c.execute("UPDATE worker_stake SET status=?, updated_at=? WHERE id=?",
                      (ABORTED, now, ws["id"]))
            consumer_id, worker_id = cs["consumer_id"], ws["worker_id"]

        self._audit("stake_abort", consumer_id, worker_id,
                    {"job_id": job_id, "fee_refunded": fee})
        return {"job_id": job_id, "status": ABORTED,
                "consumer_refunded": True, "worker_refunded": True,
                "hexis_gain": 0.0}

    # ------------------------------------------------------------------
    # EXPIRE — don lock qua TTL (gap #3: consumer/worker bien mat).
    # Deterministic policy -> public-callable duoc. KHONG mint HEXIS.
    # ------------------------------------------------------------------
    def expire_stale(self, ttl_s: float = LOCK_TTL_S) -> Dict[str, Any]:
        ttl_s = max(float(ttl_s), EXPIRE_TTL_MIN_S)
        cutoff = time.time() - ttl_s
        expired: List[Dict[str, Any]] = []
        now = time.time()
        with self._conn() as c:
            rows = c.execute(
                "SELECT job_id FROM consumer_stake WHERE status=? AND locked_at<?",
                (LOCKED, cutoff),
            ).fetchall()
            for r in rows:
                job_id = r["job_id"]
                try:
                    cs, ws = self._locked_pair(c, job_id)
                except HTTPException:
                    continue  # worker side khong locked (trang thai lech) — bo qua
                # One ledger operation per job, not one for the sweep. The
                # sweep is a loop over unrelated jobs; a single operation
                # covering all of them would let one job's legs be balanced by
                # another's, which is precisely the error the check is for.
                with self._ledger(c, "stake_expire", job_id=job_id) as op:
                    fee = cs["job_value_ecu"]
                    job_status = self._job_status(c, job_id)
                    if job_status == "completed":
                        # settle truoc do that bai giua chung — cuu tien, fee sang
                        # worker (job DA giao). Khong mint HEXIS: co hoi da qua.
                        self._credit_in_tx(c, cs["consumer_id"], cs["amount"], now)
                        self._credit_in_tx(c, ws["worker_id"], ws["amount"] + fee, now)
                        fee_route = "worker"
                    else:
                        # pending / khong ton tai — nhu abort: fee hoan consumer
                        self._credit_in_tx(c, cs["consumer_id"], cs["amount"] + fee, now)
                        self._credit_in_tx(c, ws["worker_id"], ws["amount"], now)
                        fee_route = "consumer"
                    op.leg(ledger.LOCKED, cs["consumer_id"], -(cs["amount"] + fee))
                    op.leg(ledger.LOCKED, ws["worker_id"], -ws["amount"])
                    c.execute("UPDATE consumer_stake SET status=?, updated_at=? WHERE id=?",
                              (EXPIRED, now, cs["id"]))
                    c.execute("UPDATE worker_stake SET status=?, updated_at=? WHERE id=?",
                              (EXPIRED, now, ws["id"]))
                expired.append({"job_id": job_id, "fee_to": fee_route,
                                "job_status": job_status})

        for e in expired:
            self._audit("stake_expire", "system", None, e)
        return {"expired_count": len(expired), "ttl_s": ttl_s, "expired": expired}

    # ------------------------------------------------------------------
    # DISPUTE / SLASH — uniform 100% ECU ben phan boi + WIPE HEXIS (gated).
    # v3: fee di theo ben trung thuc (worker phan boi -> hoan consumer;
    # consumer phan boi -> tra worker). Stake ben phan boi burn nhu v2.
    # ------------------------------------------------------------------
    def dispute_slash(
        self, job_id: str, faulting_role: str, raised_by: str, reason: str
    ) -> Dict[str, Any]:
        if faulting_role not in (SIDE_CONSUMER, SIDE_WORKER):
            raise HTTPException(status_code=400, detail="faulting_role must be consumer|worker")
        now = time.time()
        with self._conn() as c, self._ledger(c, "slash", job_id=job_id,
                                             reason=reason) as op:
            cs, ws = self._locked_pair(c, job_id)
            fee = cs["job_value_ecu"]

            if faulting_role == SIDE_CONSUMER:
                slashed_party, slashed_amt, slashed_row, tbl = cs["consumer_id"], cs["amount"], cs, "consumer_stake"
                honest_party, honest_amt, honest_row, htbl = ws["worker_id"], ws["amount"], ws, "worker_stake"
                fee_to, fee_recipient = "worker", ws["worker_id"]
            else:
                slashed_party, slashed_amt, slashed_row, tbl = ws["worker_id"], ws["amount"], ws, "worker_stake"
                honest_party, honest_amt, honest_row, htbl = cs["consumer_id"], cs["amount"], cs, "consumer_stake"
                fee_to, fee_recipient = "consumer", cs["consumer_id"]

            slash_amount = round(SLASH_FRACTION * slashed_amt, 8)
            # ben phan boi: stake bi slash (KHONG hoan escrow) -> status SLASHED
            c.execute(f"UPDATE {tbl} SET status=?, updated_at=? WHERE id=?",
                      (SLASHED, now, slashed_row["id"]))
            # ben trung thuc: hoan escrow + status RELEASED; fee ve ben trung thuc
            self._credit_in_tx(c, honest_party, honest_amt, now)
            self._credit_in_tx(c, fee_recipient, fee, now)
            # Out of locked: the honest party's stake, the fee (always the
            # consumer's — only the consumer ever locks one), and the slashed
            # stake, which goes to `burn` because nobody receives it.
            #
            # The burn leg is `slashed_amt`, the whole locked amount, not
            # `slash_amount`. They are equal while SLASH_FRACTION is 1.0. If
            # that constant is ever lowered, this code as written still
            # returns nothing to the slashed party, so the whole amount really
            # is destroyed and the ledger says so — while `slash_log.amount`
            # would report only the fraction. The two disagreeing is the
            # useful outcome; a burn leg written from `slash_amount` would
            # have hidden the difference.
            op.leg(ledger.LOCKED, honest_party, -honest_amt)
            op.leg(ledger.LOCKED, cs["consumer_id"], -fee)
            op.leg(ledger.LOCKED, slashed_party, -slashed_amt)
            op.leg(ledger.BURN, "", +slashed_amt)
            c.execute(f"UPDATE {htbl} SET status=?, updated_at=? WHERE id=?",
                      (RELEASED, now, honest_row["id"]))

        # HAI HINH PHAT khac loai (Appendix D): (1) mat ECU locked (tien),
        # (2) HEXIS WIPE VE 0 (danh tieng). GATE BLOCK 0: wipe_fn = None.
        hexis_wiped = 0.0
        if self.hexis_wipe_fn:
            try:
                wipe_result = self.hexis_wipe_fn(slashed_party, f"slash:{reason}" if reason else "slash")
                if isinstance(wipe_result, dict):
                    hexis_wiped = wipe_result.get("wiped", 0.0) or 0.0
            except Exception:
                pass

        # SEVERITY CLASSIFY (v3.1) — hinh phat thu 3: dinh gia thiet hai vuot
        # fee + leo thang (quarantine/debt/blacklist). Classify that bai khong
        # duoc lam hong slash — slash da chot o transaction tren.
        severity_result = None
        if self.incident_fn:
            try:
                severity_result = self.incident_fn({
                    "job_id":           job_id,
                    "actor_id":         slashed_party,
                    "role":             faulting_role,
                    "victim_id":        honest_party,   # ben trung thuc — nhan payout phan vuot fee
                    "sensitivity_tier": cs["sensitivity_tier"],
                    "job_value_ecu":    cs["job_value_ecu"],
                    "slashed_amount":   slash_amount,
                    "reason":           reason,
                })
            except Exception:
                pass

        with self._conn() as c:
            c.execute(
                """INSERT INTO slash_log
                   (id,job_id,slashed_party,slashed_role,counterparty,amount,fraction,
                    hexis_wiped,reason,raised_by,slashed_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                (uuid.uuid4().hex, job_id, slashed_party, faulting_role, honest_party,
                 slash_amount, SLASH_FRACTION, hexis_wiped, reason, raised_by, now),
            )

        self._audit("slash", slashed_party, honest_party,
                    {"job_id": job_id, "amount": slash_amount, "fraction": SLASH_FRACTION,
                     "reason": reason, "raised_by": raised_by, "role": faulting_role,
                     "hexis_wiped": hexis_wiped, "fee_to": fee_to})
        return {"job_id": job_id, "slashed_party": slashed_party, "slashed_role": faulting_role,
                "amount": slash_amount, "counterparty_released": honest_party,
                "fee_to": fee_to, "hexis_wiped": hexis_wiped,
                "severity": severity_result}

    # ------------------------------------------------------------------
    def status(self, job_id: str) -> Dict[str, Any]:
        with self._conn() as c:
            cs = c.execute("SELECT * FROM consumer_stake WHERE job_id=?", (job_id,)).fetchall()
            ws = c.execute("SELECT * FROM worker_stake WHERE job_id=?", (job_id,)).fetchall()
            sl = c.execute("SELECT * FROM slash_log WHERE job_id=?", (job_id,)).fetchall()
        return {
            "job_id": job_id,
            "consumer_stake": [dict(r) for r in cs],
            "worker_stake": [dict(r) for r in ws],
            "slash_log": [dict(r) for r in sl],
        }

    # ------------------------------------------------------------------
    def _audit(self, action: str, actor_id: str, counterparty: Optional[str], data: dict) -> None:
        if not self.audit_fn:
            return
        try:
            self.audit_fn(action=action, actor_id=actor_id,
                          counterparty=counterparty, data=data)
        except Exception:
            # Audit that bai khong duoc lam hong giao dich kinh te.
            # DINH CHINH 2026-08-12: comment cu o day noi "chain verify se lo
            # gap" — SAI. Event bi tu choi thi khong duoc ghi, sequence van
            # lien tuc, khong co gap nao de lo ca. Su that: 6 action_type
            # (escrow_transfer, stake_abort, stake_expire, severity_*) bi
            # VALID_ACTIONS tu choi va roi im lang suot tu dau. Xem
            # CORRECTIONS.md.
            pass


# ----------------------------------------------------------------------------
# Request models
# ----------------------------------------------------------------------------
class StakeLockReq(BaseModel):
    job_id: str
    consumer_id: str
    worker_id: str
    consumer_amount: float = Field(gt=0)
    worker_amount: float = Field(gt=0)
    job_value_ecu: float = Field(gt=0)
    sensitivity_tier: int = Field(1, ge=1, le=4)


class StakeTermsReq(BaseModel):
    """The consumer's proposal. Same shape as StakeLockReq on purpose: what is
    agreed here is precisely what lock() will later be checked against."""
    job_id: str
    consumer_id: str
    worker_id: str
    consumer_amount: float = Field(gt=0)
    worker_amount: float = Field(gt=0)
    job_value_ecu: float = Field(gt=0)
    sensitivity_tier: int = Field(1, ge=1, le=4)


class StakeTermsAcceptReq(BaseModel):
    """The worker names what it is accepting. Required — see accept_terms."""
    terms_hash: str = Field(min_length=64, max_length=64)


class StakeAbortReq(BaseModel):
    job_id: str


class DisputeReq(BaseModel):
    faulting_role: str   # "consumer" | "worker"
    raised_by: str
    reason: str = ""


# ----------------------------------------------------------------------------
# Router — v3: /release GATED (410), them /abort + /expire
# ----------------------------------------------------------------------------
def get_stake_router(stake: StakeManager, write_deps=None) -> APIRouter:
    """
    write_deps maps a route path to its signature dependency (2026-08-12).

    Passed in rather than imported so this module keeps knowing nothing about
    the bridge, and applied per-route because each write here binds to a
    different actor. GET routes stay open.
    """
    write_deps = write_deps or {}
    r = APIRouter(prefix="/stake", tags=["stake"])

    @r.post("/lock", dependencies=write_deps.get("/stake/lock", []))
    def stake_lock(req: StakeLockReq):
        return stake.lock(req.job_id, req.consumer_id, req.worker_id,
                          req.consumer_amount, req.worker_amount,
                          req.job_value_ecu, req.sensitivity_tier)

    @r.post("/terms", dependencies=write_deps.get("/stake/terms", []))
    def stake_propose_terms(req: StakeTermsReq):
        return stake.propose_terms(
            req.job_id, req.consumer_id, req.worker_id, req.consumer_amount,
            req.worker_amount, req.job_value_ecu, req.sensitivity_tier)

    @r.post("/terms/{job_id}/accept",
            dependencies=write_deps.get("/stake/terms/accept", []))
    def stake_accept_terms(job_id: str, req: StakeTermsAcceptReq):
        return stake.accept_terms(job_id, req.terms_hash)

    @r.get("/terms/{job_id}")
    def stake_get_terms(job_id: str):
        with stake._conn() as c:
            row = c.execute(
                "SELECT job_id, consumer_id, worker_id, consumer_amount, "
                "worker_amount, job_value_ecu, sensitivity_tier, expiry, "
                "terms_hash, status, proposed_at, accepted_at "
                "FROM stake_terms WHERE job_id=?", (job_id,)).fetchone()
        if not row:
            raise HTTPException(404, f"no terms for job {job_id}")
        keys = ("job_id", "consumer_id", "worker_id", "consumer_amount",
                "worker_amount", "job_value_ecu", "sensitivity_tier",
                "expiry", "terms_hash", "status", "proposed_at", "accepted_at")
        out = dict(zip(keys, row))
        out["moves_money"] = False
        return out

    @r.post("/release")
    def stake_release_gone():
        # GATED v3 (gap #2): settle_complete mint HEXIS consumer (sau Block 0)
        # nen khong duoc public. Settle chi chay noi bo qua /job/{id}/complete.
        raise HTTPException(
            status_code=410,
            detail="gone: /stake/release is internal-only since v3. "
            "Use POST /stake/abort for locks whose job was never accepted. "
            "(/stake/expire was the other suggestion here and is itself "
            "internal-only since 2026-08-12.)",
        )

    @r.post("/abort", dependencies=write_deps.get("/stake/abort", []))
    def stake_abort(req: StakeAbortReq):
        return stake.abort_unstarted(req.job_id)

    @r.post("/expire")
    def stake_expire_gone():
        # GATED 2026-08-12. expire_stale() sweeps every lock past its TTL and
        # moves escrow on all of them. The sweep names no actor, so a signature
        # can only prove SOME registered actor called it — authentication with
        # no authorisation behind it. Same shape as /stake/release, same
        # treatment. The method stays; it is internal-only now.
        raise HTTPException(
            status_code=410,
            detail="gone: /stake/expire is internal-only since 2026-08-12. "
            "The sweep is unscoped — it acts on every stale lock in the "
            "system, so no caller's signature authorises it. "
            "Use POST /stake/abort for a single lock whose job was never "
            "accepted.",
        )

    @r.post("/credit")
    def stake_credit_gone():
        # GATED 2026-08-12. credit() increments escrow with nothing debited
        # anywhere: it creates ECU. A signature binds the call to an actor and
        # makes it attributable, and an attributable actor minting its own
        # balance is still minting its own balance. Authentication is not
        # authorisation. Internal-only; deposits belong on a path that debits
        # a real source.
        raise HTTPException(
            status_code=410,
            detail="gone: /stake/credit is internal-only since 2026-08-12. "
            "It created escrow balance with no debit anywhere, so no caller "
            "may reach it. Escrow is credited only by settlement, abort, "
            "expiry and audit reward, all internal.",
        )

    @r.get("/balance/{actor_id}")
    def stake_balance(actor_id: str):
        return {"actor_id": actor_id, "balance": stake.balance(actor_id)}

    @r.get("/status/{job_id}")
    def stake_status(job_id: str):
        return stake.status(job_id)

    return r


def get_dispute_router(stake: StakeManager, write_deps=None) -> APIRouter:
    """Tach rieng vi path la /job/{job_id}/dispute (khong thuoc prefix /stake)."""
    r = APIRouter(tags=["stake"])

    write_deps = write_deps or {}

    @r.post("/job/{job_id}/dispute",
            dependencies=write_deps.get("/job/{job_id}/dispute", []))
    def job_dispute(job_id: str, req: DisputeReq):
        return stake.dispute_slash(job_id, req.faulting_role, req.raised_by, req.reason)

    return r
