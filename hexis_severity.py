"""
hexis_severity.py — Severity Tiers (v0.8, bootstrap) — v1

Mat xich thu 3 cua loop: Audit (detect) -> Stake (enforce) -> SEVERITY (classify).

VAN DE (Appendix D, honest limit #1):
  "Made-whole holds only while damage <= fee. A Tier 4 leak can inflict
  damage far beyond the fee." Slash 100% stake lam ke phan boi EV am,
  nhung KHONG dinh gia duoc thiet hai vuot fee. Severity Tiers lam viec do:
  phan loai moi incident, dinh gia thiet hai, va leo thang hau qua.

THIET KE BOOTSTRAP (rule-based, calibrate sau):
  Whitepaper hen calibrate tren public incident corpus + protocol data.
  Chua co corpus -> v1 dung rule don gian, MOI THAM SO nam trong bang
  severity_config (DB) — calibrate = UPDATE rows, khong sua code.

  damage_est = job_value_ecu x BO_MULT[sensitivity_tier]   (1/5/20/100 —
               cung thang voi HEXIS mining, nhat quan §5)
  uncovered  = max(0, damage_est - slashed_amount)
  severity   = sensitivity_tier cua job bi phan boi
               +1 neu tai pham (>= recurrence_threshold incident trong 30d)
               cap tai 4
  Hau qua:
    S1: ghi nhan (slash cua stake da du gia)
    S2: quarantine 24h  — khong duoc lock job moi
    S3: quarantine 168h + DEBT = uncovered (phai tra het moi duoc vao lai)
    S4: BLACKLIST vinh vien (testnet: khong co duong ve) + DEBT

  Debt tra qua POST /severity/repay -> chuyen escrow actor ->
  SEVERITY_RESERVE (pool) -> CHUYEN NGAY sang nan nhan (victim_id, ben
  trung thuc cua job bi phan boi), phan bo theo incident cu nhat truoc.
  RESERVE chi la tai khoan qua canh: repay vao = payout ra, net 0. Dong
  vong "made-whole" cho phan thiet hai VUOT fee (v0.8).
  GET /severity/reserve soi ton dong + tong da tra.

COUPLING (DI pattern giong audit_fn/hexis_settle_fn — stake v3.1):
  - StakeManager(eligibility_fn=severity.check_eligible)  -> goi trong lock()
    cho CA HAI ben; raise 403 neu quarantine/blacklist/debt.
  - StakeManager(incident_fn=severity.record_incident)    -> goi sau
    dispute_slash() voi payload incident; tra ve ket qua classify.
  Ca hai optional — stake van chay doc lap neu None.

MODULE DOC LAP: tu so huu tables trong bridge.db, khong import stake/bridge.
Router nhan stake manager CHI de thuc hien repay (escrow transfer).
"""

import sqlite3
import time
import uuid
from typing import Callable, Optional, Dict, Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field


# ----------------------------------------------------------------------------
# DEFAULTS — seed vao severity_config, doc tu DB khi chay (calibrate = UPDATE)
# ----------------------------------------------------------------------------
DEFAULT_CONFIG = {
    # damage multiplier theo sensitivity tier (thang BO §5, nhat quan mining)
    "damage_mult_t1": 1.0,
    "damage_mult_t2": 5.0,
    "damage_mult_t3": 20.0,
    "damage_mult_t4": 100.0,
    # quarantine gio theo severity (-1 = blacklist vinh vien)
    "quarantine_h_s1": 0.0,
    "quarantine_h_s2": 24.0,
    "quarantine_h_s3": 168.0,
    "quarantine_h_s4": -1.0,
    # debt bat dau tu severity nao
    "debt_from_severity": 3.0,
    # tai pham: so incident trong window de bi +1 severity
    "recurrence_threshold": 2.0,
    "recurrence_window_s": 2592000.0,   # 30d
}

SEVERITY_RESERVE = "SEVERITY_RESERVE"   # pool escrow nhan tien tra debt


class SeverityEngine:
    """Phan loai incident + giu trang thai eligibility cua actor."""

    def __init__(self, db_path: str, audit_fn: Optional[Callable[..., Any]] = None):
        self.db_path = db_path
        self.audit_fn = audit_fn
        self._init_schema()

    def _conn(self) -> sqlite3.Connection:
        c = sqlite3.connect(self.db_path, timeout=10)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA journal_mode=WAL;")
        return c

    def _init_schema(self) -> None:
        with self._conn() as c:
            c.executescript(
                """
                CREATE TABLE IF NOT EXISTS severity_config (
                    key   TEXT PRIMARY KEY,
                    value REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS incidents (
                    id               TEXT PRIMARY KEY,
                    job_id           TEXT NOT NULL,
                    actor_id         TEXT NOT NULL,
                    role             TEXT NOT NULL,      -- consumer | worker
                    victim_id        TEXT,              -- ben trung thuc, nhan payout
                    sensitivity_tier INTEGER NOT NULL,
                    job_value_ecu    REAL NOT NULL,
                    slashed_amount   REAL NOT NULL,
                    damage_est       REAL NOT NULL,
                    uncovered        REAL NOT NULL,
                    severity         INTEGER NOT NULL,
                    quarantine_until REAL NOT NULL DEFAULT 0,
                    debt             REAL NOT NULL DEFAULT 0,
                    debt_paid        REAL NOT NULL DEFAULT 0,  -- da chuyen cho victim
                    reason           TEXT,
                    ts               REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS ix_inc_actor ON incidents(actor_id, ts);

                CREATE TABLE IF NOT EXISTS actor_flags (
                    actor_id         TEXT PRIMARY KEY,
                    blacklisted      INTEGER NOT NULL DEFAULT 0,
                    quarantine_until REAL NOT NULL DEFAULT 0,
                    total_debt       REAL NOT NULL DEFAULT 0,
                    incident_count   INTEGER NOT NULL DEFAULT 0,
                    updated_at       REAL NOT NULL
                );
                """
            )
            # MIGRATION (v0.8 victim payout): them cot vao incidents cu neu thieu.
            # CREATE TABLE IF NOT EXISTS khong sua duoc schema bang da ton tai.
            _cols = {r["name"] for r in c.execute("PRAGMA table_info(incidents)")}
            if "victim_id" not in _cols:
                c.execute("ALTER TABLE incidents ADD COLUMN victim_id TEXT")
            if "debt_paid" not in _cols:
                c.execute("ALTER TABLE incidents ADD COLUMN debt_paid REAL NOT NULL DEFAULT 0")
            # seed config neu chua co (khong overwrite calibration)
            for k, v in DEFAULT_CONFIG.items():
                c.execute(
                    "INSERT OR IGNORE INTO severity_config(key, value) VALUES(?,?)",
                    (k, v),
                )

    # ------------------------------------------------------------------
    def _cfg(self, c: sqlite3.Connection) -> Dict[str, float]:
        rows = c.execute("SELECT key, value FROM severity_config").fetchall()
        cfg = dict(DEFAULT_CONFIG)
        cfg.update({r["key"]: r["value"] for r in rows})
        return cfg

    # ------------------------------------------------------------------
    # CLASSIFY + RECORD — goi tu stake.dispute_slash qua incident_fn
    # ------------------------------------------------------------------
    def record_incident(self, incident: Dict[str, Any]) -> Dict[str, Any]:
        """
        incident = {job_id, actor_id, role, sensitivity_tier, job_value_ecu,
                    slashed_amount, reason}
        Tra ve {severity, damage_est, uncovered, quarantine_until, debt,
                blacklisted}.
        """
        now = time.time()
        job_id = incident["job_id"]
        actor_id = incident["actor_id"]
        role = incident.get("role", "?")
        victim_id = incident.get("victim_id")   # ben trung thuc — nhan payout
        tier = int(incident.get("sensitivity_tier", 1))
        tier = min(max(tier, 1), 4)
        value = float(incident.get("job_value_ecu", 0.0))
        slashed = float(incident.get("slashed_amount", 0.0))
        reason = incident.get("reason", "")

        with self._conn() as c:
            cfg = self._cfg(c)

            damage_est = value * cfg[f"damage_mult_t{tier}"]
            uncovered = max(0.0, damage_est - slashed)

            # tai pham trong window?
            since = now - cfg["recurrence_window_s"]
            prior = c.execute(
                "SELECT COUNT(*) AS n FROM incidents WHERE actor_id=? AND ts>=?",
                (actor_id, since),
            ).fetchone()["n"]

            severity = tier
            if prior >= int(cfg["recurrence_threshold"]):
                severity = min(severity + 1, 4)

            q_hours = cfg[f"quarantine_h_s{severity}"]
            blacklisted = 1 if q_hours < 0 else 0
            quarantine_until = 0.0 if blacklisted else (
                now + q_hours * 3600.0 if q_hours > 0 else 0.0
            )
            debt = uncovered if severity >= int(cfg["debt_from_severity"]) else 0.0

            c.execute(
                """INSERT INTO incidents
                   (id, job_id, actor_id, role, victim_id, sensitivity_tier,
                    job_value_ecu, slashed_amount, damage_est, uncovered, severity,
                    quarantine_until, debt, debt_paid, reason, ts)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,0,?,?)""",
                (uuid.uuid4().hex, job_id, actor_id, role, victim_id, tier, value,
                 slashed, damage_est, uncovered, severity,
                 quarantine_until, debt, reason, now),
            )
            # cap nhat flags: quarantine lay max, debt cong don
            c.execute(
                """INSERT INTO actor_flags
                   (actor_id, blacklisted, quarantine_until, total_debt,
                    incident_count, updated_at)
                   VALUES(?,?,?,?,1,?)
                   ON CONFLICT(actor_id) DO UPDATE SET
                     blacklisted      = MAX(blacklisted, excluded.blacklisted),
                     quarantine_until = MAX(quarantine_until, excluded.quarantine_until),
                     total_debt       = total_debt + excluded.total_debt,
                     incident_count   = incident_count + 1,
                     updated_at       = excluded.updated_at""",
                (actor_id, blacklisted, quarantine_until, debt, now),
            )

        result = {
            "actor_id": actor_id,
            "severity": severity,
            "damage_est": round(damage_est, 8),
            "uncovered": round(uncovered, 8),
            "quarantine_until": quarantine_until,
            "debt": round(debt, 8),
            "blacklisted": bool(blacklisted),
            "recurrence_prior": prior,
        }
        self._audit("severity_classify", actor_id, None,
                    {"job_id": job_id, "role": role, **result})
        return result

    # ------------------------------------------------------------------
    # ELIGIBILITY — goi tu stake.lock qua eligibility_fn (CA HAI ben)
    # ------------------------------------------------------------------
    def check_eligible(self, actor_id: str) -> None:
        """Raise HTTPException 403 neu actor blacklist / quarantine / con debt."""
        now = time.time()
        with self._conn() as c:
            row = c.execute(
                "SELECT * FROM actor_flags WHERE actor_id=?", (actor_id,)
            ).fetchone()
        if not row:
            return
        if row["blacklisted"]:
            raise HTTPException(
                status_code=403,
                detail=f"actor {actor_id} is blacklisted (severity S4 incident)",
            )
        if row["quarantine_until"] > now:
            hours_left = (row["quarantine_until"] - now) / 3600.0
            raise HTTPException(
                status_code=403,
                detail=f"actor {actor_id} quarantined for {hours_left:.1f}h more "
                       f"(severity incident)",
            )
        if row["total_debt"] > 0:
            raise HTTPException(
                status_code=403,
                detail=f"actor {actor_id} owes {row['total_debt']:.2f} ECU damage "
                       f"debt — POST /severity/repay before new jobs",
            )

    # ------------------------------------------------------------------
    # DEBT REPAYMENT — router goi sau khi da chuyen escrow thanh cong
    # ------------------------------------------------------------------
    def apply_repayment(self, actor_id: str, amount: float) -> Dict[str, Any]:
        """Ghi nhan tra debt + phan bo cho nan nhan (oldest incident truoc).

        Tra ve payout plan: router thuc hien chuyen RESERVE -> tung victim.
        `unallocated` > 0 chi khi incident co debt nhung THIEU victim_id
        (khong the quy trach) -> tien do o lai RESERVE, ghi nhan trung thuc.
        """
        if amount <= 0:
            raise HTTPException(status_code=400, detail="repay amount must be > 0")
        now = time.time()
        payouts = []
        with self._conn() as c:
            row = c.execute(
                "SELECT total_debt FROM actor_flags WHERE actor_id=?", (actor_id,)
            ).fetchone()
            debt = row["total_debt"] if row else 0.0
            if debt <= 0:
                raise HTTPException(status_code=400,
                                    detail=f"actor {actor_id} has no debt")
            if amount > debt + 1e-9:
                raise HTTPException(
                    status_code=400,
                    detail=f"repay {amount} exceeds debt {debt} — pay exact or less",
                )
            c.execute(
                "UPDATE actor_flags SET total_debt = total_debt - ?, updated_at=? "
                "WHERE actor_id=?",
                (amount, now, actor_id),
            )
            remaining = round(debt - amount, 8)

            # phan bo `amount` cho cac incident con no (debt > debt_paid),
            # co victim_id, cu nhat truoc.
            left = amount
            rows = c.execute(
                """SELECT id, victim_id, debt, debt_paid FROM incidents
                   WHERE actor_id=? AND debt > debt_paid
                   ORDER BY ts ASC""",
                (actor_id,),
            ).fetchall()
            for r in rows:
                if left <= 1e-9:
                    break
                owed = round(r["debt"] - r["debt_paid"], 8)
                if owed <= 0 or not r["victim_id"]:
                    continue  # thieu victim_id -> khong quy trach duoc, bo qua
                pay = round(min(left, owed), 8)
                c.execute(
                    "UPDATE incidents SET debt_paid = debt_paid + ? WHERE id=?",
                    (pay, r["id"]),
                )
                payouts.append({"incident_id": r["id"],
                                "victim_id": r["victim_id"], "amount": pay})
                left = round(left - pay, 8)

        forwarded = round(sum(p["amount"] for p in payouts), 8)
        unallocated = round(amount - forwarded, 8)
        self._audit("severity_repay", actor_id, None,
                    {"amount": amount, "remaining_debt": remaining,
                     "forwarded_to_victims": forwarded, "unallocated": unallocated})
        for p in payouts:
            self._audit("severity_victim_payout", p["victim_id"], actor_id,
                        {"incident_id": p["incident_id"], "amount": p["amount"]})
        return {"actor_id": actor_id, "repaid": amount,
                "remaining_debt": remaining,
                "payouts": payouts,
                "forwarded_to_victims": forwarded,
                "unallocated": unallocated}

    # ------------------------------------------------------------------
    def reserve_status(self) -> Dict[str, Any]:
        """Ton dong quy: tong da tra victim + con no (chua tra), theo victim."""
        with self._conn() as c:
            rows = c.execute(
                """SELECT victim_id,
                          SUM(debt)               AS owed_total,
                          SUM(debt_paid)          AS paid_total,
                          SUM(debt - debt_paid)   AS outstanding
                   FROM incidents
                   WHERE debt > 0 AND victim_id IS NOT NULL
                   GROUP BY victim_id""",
            ).fetchall()
            orphan = c.execute(
                """SELECT COALESCE(SUM(debt - debt_paid),0) AS n FROM incidents
                   WHERE debt > debt_paid AND victim_id IS NULL""",
            ).fetchone()["n"]
        claims = [
            {"victim_id": r["victim_id"],
             "owed_total": round(r["owed_total"], 8),
             "paid_total": round(r["paid_total"], 8),
             "outstanding": round(r["outstanding"], 8)}
            for r in rows
        ]
        return {
            "total_forwarded": round(sum(c["paid_total"] for c in claims), 8),
            "total_outstanding": round(sum(c["outstanding"] for c in claims), 8),
            "unattributable_outstanding": round(orphan, 8),
            "victim_claims": claims,
        }

    # ------------------------------------------------------------------
    def actor_status(self, actor_id: str) -> Dict[str, Any]:
        now = time.time()
        with self._conn() as c:
            row = c.execute(
                "SELECT * FROM actor_flags WHERE actor_id=?", (actor_id,)
            ).fetchone()
            incs = c.execute(
                "SELECT * FROM incidents WHERE actor_id=? ORDER BY ts DESC LIMIT 20",
                (actor_id,),
            ).fetchall()
        if not row:
            return {"actor_id": actor_id, "eligible": True, "incidents": []}
        quarantined = row["quarantine_until"] > now
        return {
            "actor_id": actor_id,
            "eligible": not (row["blacklisted"] or quarantined
                             or row["total_debt"] > 0),
            "blacklisted": bool(row["blacklisted"]),
            "quarantined_until": row["quarantine_until"] if quarantined else 0,
            "total_debt": row["total_debt"],
            "incident_count": row["incident_count"],
            "incidents": [dict(r) for r in incs],
        }

    def get_config(self) -> Dict[str, float]:
        with self._conn() as c:
            return self._cfg(c)

    # ------------------------------------------------------------------
    def _audit(self, action: str, actor_id: str, counterparty, data: dict) -> None:
        if not self.audit_fn:
            return
        try:
            self.audit_fn(action=action, actor_id=actor_id,
                          counterparty=counterparty, data=data)
        except Exception:
            pass


# ----------------------------------------------------------------------------
# Router — stake truyen vao CHI de repay chuyen escrow (stake.transfer)
# ----------------------------------------------------------------------------
class RepayReq(BaseModel):
    actor_id: str
    amount: float = Field(gt=0)


def get_severity_router(severity: SeverityEngine, stake=None,
                        write_dependencies=None) -> APIRouter:
    """
    write_dependencies is attached to the POST route only (added 2026-08-12).

    Passed in rather than imported so this module stays independent of the
    bridge, and applied per-route rather than to the whole router because the
    GET routes here are public reads that must not start demanding signatures.
    """
    r = APIRouter(prefix="/severity", tags=["severity"])

    @r.get("/actor/{actor_id}")
    def severity_actor(actor_id: str):
        return severity.actor_status(actor_id)

    @r.get("/config")
    def severity_config():
        return severity.get_config()

    @r.get("/reserve")
    def severity_reserve():
        return severity.reserve_status()

    @r.post("/repay", dependencies=write_dependencies or [])
    def severity_repay(req: RepayReq):
        # 1) actor -> RESERVE (atomic trong stake). stake None (test doc lap)
        #    -> chi ghi nhan, khong chuyen tien.
        if stake is not None:
            stake.transfer(req.actor_id, SEVERITY_RESERVE, req.amount)
        # 2) ghi nhan tra debt + tinh payout plan (cap nhat debt_paid).
        result = severity.apply_repayment(req.actor_id, req.amount)
        # 3) RESERVE -> tung nan nhan. RESERVE da du tien tu buoc 1; tong plan
        #    <= amount nen khong the thieu. Phan `unallocated` (thieu victim_id)
        #    o lai RESERVE.
        if stake is not None:
            for p in result["payouts"]:
                stake.transfer(SEVERITY_RESERVE, p["victim_id"], p["amount"])
        return result

    return r
