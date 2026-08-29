"""
hexis_sampling.py — Proof-of-Sampling (PoSP) — v1 (module #5)

Mat xich thu 4, khep vong: Audit(detect) -> Stake(enforce) -> Severity(classify)
-> SAMPLING(verify). Day la lop VERIFY AI-native cua v0.8 — KHONG dung Groth16
(quyet dinh Ha: nguyen ly §11 + zkML cham/dat). Thay bang kinh-te-hoc-lay-mau:
khong verify moi job, chi verify ngau nhien σ% — hinh phat du nang de gian lan
EV am (Appendix D: "khong co nguong Byzantine, chi co bang gia").

NGUYEN LY (khop tieu chi Ha "may re nhat, noi xa nhat"):
  - Worker overhead = 0 ngoai viec LAM job. Ganh verify chuyen sang validator
    node khac (re-execute). Chinh cac thiet bi re co the audit lan nhau.
  - Auditor la mot LOAI JOB: chay lai workload, duoc tra audit_fee, va hanh vi
    audit trung thuc CUNG mine HEXIS (§5.1: mot su kien lien chinh duoc witness).

WORKLOAD TAT DINH (mock testnet — TRUNG THUC ve gioi han):
  workload_result(job_id, iterations) = sha256 lap iterations lan tu H(job_id).
  Worker THAT phai chay (dot CPU ~ compute_units). Validator chay LAI doc lap.
  match -> worker trung thuc; mismatch -> fraud.
  Day CHUA phai proof-of-compute cho AI that (van cho real proof/TEE) — no
  chung minh CO CHE PoSP: assignment cheo cum, re-execution, phat hien, phat,
  thuong. Bridge CO AN KHONG tu tinh workload (du mock lam duoc) — enforce
  discipline: verify day ra network, khong tap trung.

MO HINH CLAWBACK (it xam lan — KHONG hoan settle):
  Job settle binh thuong (fee sang worker). Neu bi sample (tat dinh theo
  H(job_id) < σ), mo audit. Validator verdict:
    PASS  -> reward validator (audit_fee escrow + mine HEXIS).
    FAIL  -> clawback (post-hoc): stake.transfer(worker -> SEVERITY_RESERVE,
             slash) + severity.record_incident (quarantine/debt/blacklist) +
             wipe HEXIS record cua job gian lan; validator ("bat gian") duoc
             thuong. Danh dau job fraud_confirmed.

BI MAT KET QUA (v0.8 fix — lo hong chi mang da vá):
  claim() co y KHONG tra worker_result, NHUNG /sampling/audit/{job_id} (public)
  truoc day tra nguyen row -> lo worker_result. Validator chi can claim roi GET
  la COPY duoc, nop lai -> "khop" -> an audit_fee + HEXIS ma khong chay mot vong
  tinh nao; gian lan khong bao gio bi bat. NAY: audit chua nga ngu (pending/
  assigned/contested) -> an worker_result & validator_result. Xong roi -> cong
  khai, de bat ky ai tu chay workload_result(job_id, iterations) kiem chung
  CHINH validator. Bi mat khi can, minh bach khi xong.

VALIDATOR STAKE (v0.8 — audit tro thanh BILATERAL):
  Quorum giam quyen luc don phuong, nhung validator noi doi van KHONG mat gi.
  v0.7 bat CA HAI ben giao dich stake; quan he audit thi van mot chieu. Nay:
  validator KHOA `validator_stake` ECU khi claim; audit nga ngu ->
    dung  -> tra lai stake (+ audit_fee + HEXIS nhu cu)
    lech dong thuan -> SLASH ve SEVERITY_RESERVE.
  Workload tat dinh nen "sai" = noi doi hoac may hong; ca hai deu khong nen
  audit tiep. Vu oan tu CHO MIEN PHI thanh CO GIA.
  Khong giam tien vo thoi han: stake bi treo (validator bo di khong bo phieu,
  hoac audit contested mai khong ai xac nhan) duoc HOAN — khong ket toi ai thi
  khong phat ai (_refund_stale_stakes).
  GATE: validator_stake mac dinh = 0 -> hanh vi cu y nguyen.

QUORUM BAT DOI XUNG (v0.8 hardening — chong VU OAN):
  Validator KHONG stake gi, nhung verdict FAIL cua no huy diet worker
  (clawback + severity + wipe HEXIS). Mot validator ac y to cao sai la
  MIEN PHI — dung cai whitepaper len an o trang bia: "nguoi quyet dinh
  khong co skin in the game". Sua bang quorum BAT DOI XUNG, dua tren tinh
  TAT DINH cua workload:

    PASS can 1 phieu  — mot lan tai tao KHOP la BANG CHUNG worker dung
                        (workload tat dinh: dung thi ai chay cung ra the).
    FAIL can N phieu  — mot lan LECH khong chung minh duoc gi (co the
                        validator noi doi / may loi). Phai co `fail_quorum_n`
                        validator DOC LAP cung ra MOT ket qua khac worker
                        thi moi ket toi.

  Bat doi xung vi HAU QUA bat doi xung: bo lo mot gian lan -> lan sau lai
  bi sample; ket toi oan mot nguoi trung thuc -> mat trang danh tieng.
  Chua du quorum -> status CONTESTED, audit mo lai cho validator khac
  (nguoi da bo phieu KHONG duoc bo lai). Bat dong y duoc ghi lai —
  validator lech voi dong thuan lo dien.

  GATE AN TOAN: fail_quorum_n mac dinh = 1 -> y het hanh vi cu. Len 2 khi
  co node doc lap thu hai (khong sua code, chi UPDATE sampling_config).

R4 (doc lap cua auditor — chong lanh chua tu chung thuc):
  Validator KHONG duoc cung cum voi worker. Tu 2026-08-17 dieu kien la DOC LAP
  DO DUOC, khong phai country tu khai:

    - khong co ECU nao tung chay giua hai ben (doc tu ledger_entries — so cai
      nay moi ton tai tu 2026-08-16, truoc do cau hoi nay khong tra loi duoc)
    - khong phai cap da giao dich (same_pair_fn)
    - van tay benchmark khong trung (yeu — xem `independence_fn` o bridge)

  Cai bo: `validator_country != worker_country`. No gac mot thu nguy hiem —
  tinh doc lap cua cuoc audit — bang mot chuoi ma actor tu go va khong ai
  kiem. Hai danh tinh cua mot nguoi khai hai nuoc khac nhau la qua cua hoan
  toan. Do tren toan bo lich su: cong do CHUA TUNG tu choi mot ai (2 audit,
  ca hai cheo nuoc san, 0 lan chan).

  Thieu diversity -> audit "unassigned", LOG trung thuc (khong am tham bo qua)
  + su kien `posp_claim_refused` ghi LY DO: mot cong tu choi het moi ung vien
  trong giong het mot mang khong ai xin audit, va hai chuyen do khac han nhau.

GATE AN TOAN: sigma mac dinh = 0 -> KHONG job nao bi sample -> flow cu nguyen
ven. Bat dan qua UPDATE sampling_config.

MODULE DOC LAP #5: tu so huu sampling_audits + sampling_config trong bridge.db.
Callbacks injected (DI, giong severity/geo).
"""

import hashlib
import secrets
import sqlite3
import time
import uuid
from typing import Callable, Optional, Dict, Any, List, Tuple

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field


# ----------------------------------------------------------------------------
# WORKLOAD TAT DINH — dung chung worker client / validator / engine
# ----------------------------------------------------------------------------
def workload_result(job_id: str, iterations: int) -> str:
    """sha256 lap `iterations` lan tu H(job_id). Tat dinh, tai tao duoc."""
    h = hashlib.sha256(job_id.encode()).digest()
    for _ in range(int(iterations)):
        h = hashlib.sha256(h).digest()
    return h.hex()


def should_sample_epoch(secret_hex: str, job_id: str, sigma: float) -> bool:
    """
    Selection = sha256(secret || ":" || job_id || ":posp-sample") < sigma.

    Replaces should_sample() below, 2026-08-23. The old rule hashed the job_id
    alone, and the job_id is a caller-supplied string with no format check —
    so whoever named a job decided whether it could ever be audited. Measured:
    finding a never-sampled id took 1.1 attempts on average; against a caller
    who grinds ids, sigma = 0.1 was not a 10% audit rate but 0%.

    The secret is the entropy the caller does not control. Its lifecycle is a
    commit-reveal, and each step is in the audit chain, which is what makes
    this verifiable rather than merely opaque:

      1. At epoch start the server generates the secret and records
         sha256(secret) in the chain — BEFORE any job of that epoch exists,
         so the server cannot choose a secret that targets or spares anybody.
      2. During the epoch, selection uses the secret. A worker cannot compute
         it; grinding job_ids is now blind.
      3. At epoch end the secret is revealed — in the chain and at
         GET /sampling/epochs — and anyone can recompute the selection for
         every job completed in that window and compare it with the
         sampling_open events actually recorded. A job that should have been
         sampled and was not, or the reverse, is provable from public data.

    What this does NOT protect against, stated because it is real: the
    operator holds the secret during the epoch and could leak it to a
    favoured worker. At n=1 the operator already runs the validator and the
    mint, so this adds no new authority — but the limitation ends only with
    independent infrastructure, same as OPEN.md #7.
    """
    if sigma <= 0:
        return False
    if sigma >= 1:
        return True
    d = int(hashlib.sha256(
        (secret_hex + ":" + job_id + ":posp-sample").encode()
    ).hexdigest()[:8], 16)
    return (d / 0xFFFFFFFF) < sigma


def should_sample(job_id: str, sigma: float) -> bool:
    """
    RETIRED 2026-08-23 — kept only so old verification scripts still run
    against pre-epoch history. Selection was a pure function of a
    caller-chosen string and a published sigma; see should_sample_epoch.
    Nothing in this module calls it any more.
    """
    if sigma <= 0:
        return False
    if sigma >= 1:
        return True
    d = int(hashlib.sha256((job_id + ":posp-sample").encode()).hexdigest()[:8], 16)
    return (d / 0xFFFFFFFF) < sigma


DEFAULT_CONFIG = {
    "sigma":                0.0,      # ty le sample — MAC DINH 0 (gate an toan)
    "epoch_len_s":          86400.0,  # do dai epoch commit-reveal (24h)
    "workload_mult":        20000.0,  # iterations = compute_units × mult (~0.4s/2M)
    "audit_fee":            10.0,     # ECU thuong validator moi audit
    # RETIRED 2026-08-17. `strict_cross_country` gac "ai duoc audit ai" bang
    # dung cai chuoi country TU KHAI ma khong ai kiem — cung mot input khong
    # xac minh duoc nhu C, nhung gac mot thu nguy hiem hon: tinh doc lap cua
    # cuoc audit. Hai danh tinh cua mot nguoi, khai hai nuoc khac nhau, qua
    # cua hoan toan. Do tren lich su that: cong nay CHUA TUNG tu choi ai.
    # Row cu con trong DB thi vo hai — khong con ai doc no.
    "require_independence": 1.0,      # 1 = validator phai DOC LAP DO DUOC voi worker
    "audit_expiry_s":       3600.0,   # audit pending qua han -> unassigned lai
    "fail_quorum_n":        1.0,      # so validator DOC LAP phai KHOP NHAU de
                                      # ket toi fraud. 1 = hanh vi cu (gate an
                                      # toan). Len 2+ khi co node doc lap.
    "validator_stake":      0.0,      # ECU validator phai KHOA khi nhan audit.
                                      # Verdict lech dong thuan -> slash. 0 =
                                      # gate (hanh vi cu, validator khong stake).
    "contested_expiry_s":   86400.0,  # audit CONTESTED qua lau (khong ai xac
                                      # nhan doc lap) -> hoan stake, khong giam
                                      # tien ai vo thoi han.
}

SEVERITY_RESERVE = "SEVERITY_RESERVE"

PENDING = "pending"          # cho validator claim
ASSIGNED = "assigned"        # da giao, cho verdict
PASSED = "passed"
FAILED = "failed"            # fraud confirmed
CONTESTED = "contested"      # co to cao nhung CHUA du quorum -> cho validator khac


class SamplingEngine:
    def __init__(
        self,
        db_path: str,
        audit_fn: Optional[Callable[..., Any]] = None,
        reward_fn: Optional[Callable[..., Any]] = None,      # (validator, country, fee, audit_id, caught)
        clawback_fn: Optional[Callable[..., Any]] = None,    # (worker, country, job_id, value, tier, wr, vr)
        same_pair_fn: Optional[Callable[[str, str], bool]] = None,
        stake_fn: Optional[Callable[..., Any]] = None,   # (action, validator, amount, audit_id) -> bool
        # (validator, worker) -> (doc_lap?, ly do). Thay cho strict_cross_country
        # tu 2026-08-17. Bridge cung cap vi cau tra loi nam trong ledger_entries
        # va bang benchmark — module nay khong import bridge/stake, dung pattern.
        independence_fn: Optional[Callable[[str, str], Tuple[bool, str]]] = None,
    ):
        self.db_path = db_path
        self.audit_fn = audit_fn
        self.reward_fn = reward_fn
        self.clawback_fn = clawback_fn
        self.same_pair_fn = same_pair_fn
        self.stake_fn = stake_fn
        self.independence_fn = independence_fn
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
                CREATE TABLE IF NOT EXISTS sampling_config (
                    key   TEXT PRIMARY KEY,
                    value REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS sampling_audits (
                    id               TEXT PRIMARY KEY,
                    job_id           TEXT NOT NULL UNIQUE,
                    worker_id        TEXT NOT NULL,
                    worker_country   TEXT,
                    worker_result    TEXT NOT NULL,
                    iterations       INTEGER NOT NULL,
                    job_value_ecu    REAL NOT NULL DEFAULT 0,
                    sensitivity_tier INTEGER NOT NULL DEFAULT 1,
                    validator_id     TEXT,
                    validator_result TEXT,
                    status           TEXT NOT NULL,
                    created_at       REAL NOT NULL,
                    assigned_at      REAL,
                    verdict_at       REAL
                );
                CREATE INDEX IF NOT EXISTS ix_sa_status ON sampling_audits(status, created_at);
                CREATE INDEX IF NOT EXISTS ix_sa_worker ON sampling_audits(worker_id);

                -- Moi phieu cua moi validator (quorum bat doi xung, v0.8).
                -- UNIQUE(audit_id, validator_id): mot validator chi bo 1 phieu.
                CREATE TABLE IF NOT EXISTS sampling_verdicts (
                    id               TEXT PRIMARY KEY,
                    audit_id         TEXT NOT NULL,
                    validator_id     TEXT NOT NULL,
                    validator_result TEXT NOT NULL,
                    matched          INTEGER NOT NULL,   -- 1 = khop worker
                    ts               REAL NOT NULL,
                    UNIQUE(audit_id, validator_id)
                );
                CREATE INDEX IF NOT EXISTS ix_sv_audit ON sampling_verdicts(audit_id);

                -- Stake validator khoa khi nhan audit (v0.8: audit bilateral).
                -- status: locked | released (dung) | slashed (lech dong thuan)
                --       | refunded (audit khong ket luan duoc -> khong ai co loi)
                CREATE TABLE IF NOT EXISTS sampling_stakes (
                    id           TEXT PRIMARY KEY,
                    audit_id     TEXT NOT NULL,
                    validator_id TEXT NOT NULL,
                    amount       REAL NOT NULL,
                    status       TEXT NOT NULL,
                    locked_at    REAL NOT NULL,
                    resolved_at  REAL,
                    UNIQUE(audit_id, validator_id)
                );
                CREATE INDEX IF NOT EXISTS ix_ss_audit ON sampling_stakes(audit_id, status);

                -- COMMIT-REVEAL EPOCHS (2026-08-23). One secret per epoch;
                -- its hash goes into the audit chain before any job of the
                -- epoch exists, the secret itself after the epoch ends. The
                -- secret sits in this table during its epoch — same trust
                -- boundary as everything else on this host, and the
                -- commitment in the chain is what keeps us honest about it.
                CREATE TABLE IF NOT EXISTS sampling_epochs (
                    epoch_id   INTEGER PRIMARY KEY AUTOINCREMENT,
                    secret_hex TEXT NOT NULL,
                    commitment TEXT NOT NULL,
                    started_at REAL NOT NULL,
                    ends_at    REAL NOT NULL,
                    revealed   INTEGER NOT NULL DEFAULT 0
                );
                """
            )
            for k, v in DEFAULT_CONFIG.items():
                c.execute("INSERT OR IGNORE INTO sampling_config(key,value) VALUES(?,?)", (k, v))

    def _cfg(self, c) -> Dict[str, float]:
        cfg = dict(DEFAULT_CONFIG)
        cfg.update({r["key"]: r["value"] for r in
                    c.execute("SELECT key,value FROM sampling_config").fetchall()})
        return cfg

    def get_config(self) -> Dict[str, float]:
        with self._conn() as c:
            return self._cfg(c)

    def iterations_for(self, compute_units: int) -> int:
        with self._conn() as c:
            mult = self._cfg(c)["workload_mult"]
        return max(1, int(compute_units) * int(mult))

    # ------------------------------------------------------------------
    # EPOCH LIFECYCLE (2026-08-23) — the entropy the caller cannot control
    # ------------------------------------------------------------------
    def _current_epoch(self, c) -> sqlite3.Row:
        """
        The live epoch row, rolling over if the old one has ended.

        Rollover does three things in order: mark the ended epoch revealed,
        put its secret into the audit chain (the reveal — from here on anyone
        can recompute that epoch's entire selection), and create the next
        epoch, putting its COMMITMENT into the chain before any job of the
        new epoch can exist. The commitment landing in a sealed,
        Bitcoin-anchored chain before the jobs is the whole argument that the
        server did not pick a secret to target or spare anybody.
        """
        now = time.time()
        row = c.execute(
            "SELECT * FROM sampling_epochs ORDER BY epoch_id DESC LIMIT 1"
        ).fetchone()
        if row and now < row["ends_at"]:
            return row
        # Audit events are QUEUED here and emitted by the caller after the
        # write transaction closes. Found live on the first deploy: _audit
        # opens a second write connection to the same database, and inside
        # this `with` block the first one still holds the write lock — so the
        # chain write timed out and the swallow in _audit ate it. The epoch
        # existed, the commitment did not. sampling_open never had the bug
        # because it has always audited after its `with` closes; this now
        # follows the same pattern instead of relearning it.
        self._pending_audits = getattr(self, "_pending_audits", [])
        if row and not row["revealed"]:
            c.execute("UPDATE sampling_epochs SET revealed=1 WHERE epoch_id=?",
                      (row["epoch_id"],))
            self._pending_audits.append(("sampling_epoch_reveal", "posp_sampling", None, {
                "epoch_id":   row["epoch_id"],
                "secret":     row["secret_hex"],
                "commitment": row["commitment"],
                "covers":     f"jobs completed {row['started_at']:.0f}..{row['ends_at']:.0f} unix",
                "how_to_check": (
                    "sha256(secret) must equal the commitment recorded at "
                    "this epoch's start. then for every job_complete in the "
                    "window: sampled iff sha256(secret + ':' + job_id + "
                    "':posp-sample')[:8] as a fraction of 0xFFFFFFFF < sigma. "
                    "compare against the sampling_open events. a mismatch in "
                    "either direction is our misconduct, provable from "
                    "public data"),
            }))
        cfg = self._cfg(c)
        secret = secrets.token_hex(32)
        commitment = hashlib.sha256(secret.encode()).hexdigest()
        started = now
        ends = now + float(cfg.get("epoch_len_s", 86400.0))
        cur = c.execute(
            "INSERT INTO sampling_epochs(secret_hex, commitment, started_at, "
            "ends_at, revealed) VALUES(?,?,?,?,0)",
            (secret, commitment, started, ends))
        eid = cur.lastrowid
        self._pending_audits.append(("sampling_epoch", "posp_sampling", None, {
            "epoch_id":   eid,
            "commitment": commitment,
            "starts":     started,
            "ends":       ends,
            "attests": (
                "the sampling secret for this epoch was fixed before any job "
                "of the epoch existed. its hash is this commitment; the "
                "secret itself is published when the epoch ends"),
            "does_not_attest": (
                "that the operator cannot read the secret during the epoch — "
                "it is on the host, same trust boundary as the validator and "
                "the mint. what the commitment removes is retroactive choice"),
        }))
        return c.execute(
            "SELECT * FROM sampling_epochs WHERE epoch_id=?", (eid,)).fetchone()

    def _flush_epoch_audits(self) -> None:
        """
        Emit queued epoch events. Call ONLY with no open write transaction.

        The dispatch below is deliberately spelled out per action rather than
        `self._audit(*ev)`. The bridge's boot scanner reads every _audit call
        site and refuses to start on one whose action type it cannot see —
        it did exactly that to the starred version, on production, correctly:
        an action named at runtime is an action the allowlist cannot vouch
        for, and every caller swallows the rejection. An unknown queued
        action raises instead of forwarding, for the same reason the scanner
        exists.
        """
        for action, actor, cp, data in getattr(self, "_pending_audits", []):
            if action == "sampling_epoch":
                self._audit("sampling_epoch", actor, cp, data)
            elif action == "sampling_epoch_reveal":
                self._audit("sampling_epoch_reveal", actor, cp, data)
            else:
                raise RuntimeError(
                    f"unregistered epoch audit action {action!r} — add a "
                    f"literal dispatch arm; do not forward it blind")
        self._pending_audits = []

    # ------------------------------------------------------------------
    # INTAKE — goi tu /job/complete SAU settle.
    # Tat dinh theo H(epoch_secret || job_id) — xem should_sample_epoch.
    # ------------------------------------------------------------------
    def maybe_open(self, job_id: str, worker_id: str, worker_country: str,
                   worker_result: str, compute_units: int,
                   job_value_ecu: float, sensitivity_tier: int) -> Optional[Dict[str, Any]]:
        with self._conn() as c:
            cfg = self._cfg(c)
            epoch = self._current_epoch(c)
        self._flush_epoch_audits()
        if not should_sample_epoch(epoch["secret_hex"], job_id, cfg["sigma"]):
            return None
        if not worker_result:
            return None  # worker client cu chua submit result -> khong audit duoc
        iterations = self.iterations_for(compute_units)
        now = time.time()
        with self._conn() as c:
            dup = c.execute("SELECT 1 FROM sampling_audits WHERE job_id=?", (job_id,)).fetchone()
            if dup:
                return None
            aid = uuid.uuid4().hex
            c.execute(
                """INSERT INTO sampling_audits
                   (id,job_id,worker_id,worker_country,worker_result,iterations,
                    job_value_ecu,sensitivity_tier,status,created_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (aid, job_id, worker_id, worker_country or "??", worker_result,
                 iterations, job_value_ecu, sensitivity_tier, PENDING, now),
            )
        self._audit("sampling_open", worker_id, None,
                    {"job_id": job_id, "audit_id": aid, "iterations": iterations})
        return {"audit_id": aid, "job_id": job_id, "status": PENDING}

    # ------------------------------------------------------------------
    # CLAIM — validator node nhan 1 audit du dieu kien R4. Deterministic:
    # audit pending cu nhat khop tieu chi. KHONG tra worker_result (validator
    # phai tu tinh, khong copy).
    # ------------------------------------------------------------------
    def claim(self, validator_id: str, validator_country: str) -> Optional[Dict[str, Any]]:
        now = time.time()
        refused: List[Tuple[str, str]] = []   # (audit_id, ly do) — de log, xem duoi
        self._refund_stale_stakes()   # khong giam tien ai vo thoi han
        with self._conn() as c:
            cfg = self._cfg(c)
            # reclaim audit assigned qua han
            c.execute(
                "UPDATE sampling_audits SET status=?, validator_id=NULL, assigned_at=NULL "
                "WHERE status=? AND assigned_at < ?",
                (PENDING, ASSIGNED, now - cfg["audit_expiry_s"]),
            )
            rows = c.execute(
                "SELECT * FROM sampling_audits WHERE status IN (?,?) "
                "ORDER BY created_at ASC",
                (PENDING, CONTESTED),
            ).fetchall()
            for r in rows:
                if r["worker_id"] == validator_id:
                    continue  # khong tu audit chinh minh
                voted = c.execute(
                    "SELECT 1 FROM sampling_verdicts WHERE audit_id=? AND validator_id=?",
                    (r["id"], validator_id),
                ).fetchone()
                if voted:
                    continue  # da bo phieu audit nay -> khong bo lai (quorum that)
                # R4 (2026-08-17): doc lap DO DUOC, khong phai country tu khai.
                # `validator_country` khong con duoc doc o day. No van la tham
                # so cua ham vi router va bridge dang truyen vao, va vi bo no
                # la mot thay doi chu ky ham khong lien quan gi den bao mat.
                if cfg["require_independence"] >= 1 and self.independence_fn:
                    ok, why = self.independence_fn(validator_id, r["worker_id"])
                    if not ok:
                        # Ghi lai LY DO, khong im lang bo qua. Mot cong tu choi
                        # het moi ung vien trong va giong het mot mang khong ai
                        # xin audit — day la cho phan biet hai chuyen do.
                        refused.append((r["id"], why))
                        continue
                if self.same_pair_fn and self.same_pair_fn(validator_id, r["worker_id"]):
                    refused.append((r["id"], "shared transaction history"))
                    continue  # cap da giao dich -> chong thong dong
                c.execute(
                    "UPDATE sampling_audits SET status=?, validator_id=?, assigned_at=? WHERE id=?",
                    (ASSIGNED, validator_id, now, r["id"]),
                )
                picked = (r["id"], r["job_id"], r["iterations"], r["worker_id"], r["status"])
                break
            else:
                picked = None
        # -- HET transaction. stake_fn GHI vao cung file DB nen PHAI goi ngoai
        #    transaction, khong la ket khoa SQLite (database is locked).
        if not picked:
            # Khong claim duoc vi KHONG CO audit nao, hay vi cong doc lap tu
            # choi het? Hai chuyen khac han nhau va tu ben ngoai trong giong
            # het nhau. Chi ghi khi co ung vien bi tu choi — khong lam on.
            if refused:
                self._audit("posp_claim_refused", validator_id, None, {
                    "validator_id": validator_id,
                    "candidates_refused": len(refused),
                    "reasons": sorted({why for _, why in refused}),
                })
            return None
        audit_id, job_id, iterations, worker_id, prev_status = picked

        amount = float(cfg.get("validator_stake", 0.0) or 0.0)
        if amount > 0 and self.stake_fn:
            ok = False
            try:
                ok = bool(self.stake_fn("lock", validator_id, amount, audit_id))
            except Exception:
                ok = False
            if not ok:
                # khong du escrow -> tra audit ve trang thai cu, KHONG giao
                with self._conn() as c:
                    c.execute(
                        "UPDATE sampling_audits SET status=?, validator_id=NULL, "
                        "assigned_at=NULL WHERE id=?", (prev_status, audit_id))
                self._audit("sampling_claim_denied", validator_id, worker_id,
                            {"audit_id": audit_id, "reason": "insufficient stake",
                             "required": amount})
                raise HTTPException(
                    status_code=402,
                    detail=f"validator phai khoa {amount} ECU de nhan audit "
                           f"(escrow khong du) — verdict lech dong thuan se bi slash",
                )
            with self._conn() as c:
                c.execute(
                    """INSERT OR REPLACE INTO sampling_stakes
                       (id, audit_id, validator_id, amount, status, locked_at)
                       VALUES(?,?,?,?,?,?)""",
                    (uuid.uuid4().hex, audit_id, validator_id, amount, "locked", now),
                )

        self._audit("sampling_assign", validator_id, worker_id,
                    {"job_id": job_id, "audit_id": audit_id, "stake_locked": amount})
        return {
            "audit_id": audit_id,
            "job_id": job_id,
            "iterations": iterations,
            "stake_locked": amount,
            "note": "Chay workload_result(job_id, iterations) roi POST verdict. "
                    "KHONG duoc copy — server so voi ket qua worker doc lap."
                    + (f" Ban da khoa {amount} ECU: verdict lech dong thuan se bi slash."
                       if amount > 0 else ""),
        }

    # ------------------------------------------------------------------
    # VERDICT — validator nop ket qua re-run. So voi worker_result.
    # ------------------------------------------------------------------
    def submit_verdict(self, audit_id: str, validator_id: str,
                       validator_result: str) -> Dict[str, Any]:
        now = time.time()
        with self._conn() as c:
            r = c.execute("SELECT * FROM sampling_audits WHERE id=?", (audit_id,)).fetchone()
            if not r:
                raise HTTPException(status_code=404, detail="audit not found")
            if r["status"] != ASSIGNED:
                raise HTTPException(status_code=409, detail=f"audit is {r['status']}, not assigned")
            if r["validator_id"] != validator_id:
                raise HTTPException(status_code=403, detail="not the assigned validator")
            cfg = self._cfg(c)
            quorum_n = max(1, int(cfg["fail_quorum_n"]))
            match = (validator_result or "").lower() == (r["worker_result"] or "").lower()

            # ghi phieu (UNIQUE audit+validator chan bo phieu hai lan)
            c.execute(
                """INSERT INTO sampling_verdicts
                   (id, audit_id, validator_id, validator_result, matched, ts)
                   VALUES(?,?,?,?,?,?)""",
                (uuid.uuid4().hex, audit_id, validator_id,
                 validator_result or "", 1 if match else 0, now),
            )

            if match:
                # PASS can 1 phieu: workload TAT DINH -> mot lan tai tao khop
                # la bang chung worker dung. Ke ca audit dang CONTESTED, phieu
                # khop nay minh oan cho worker.
                new_status = PASSED
                agree_n = 1
            else:
                # FAIL can quorum: dem so validator DOC LAP cung ra DUNG ket qua
                # nay (khac worker). Mot minh mot phieu lech khong ket toi duoc.
                agree_n = c.execute(
                    "SELECT COUNT(DISTINCT validator_id) AS n FROM sampling_verdicts "
                    "WHERE audit_id=? AND matched=0 AND LOWER(validator_result)=LOWER(?)",
                    (audit_id, validator_result or ""),
                ).fetchone()["n"]
                new_status = FAILED if agree_n >= quorum_n else CONTESTED

            c.execute(
                "UPDATE sampling_audits SET status=?, validator_result=?, verdict_at=? WHERE id=?",
                (new_status, validator_result,
                 now if new_status in (PASSED, FAILED) else None, audit_id),
            )
            job_id = r["job_id"]; worker_id = r["worker_id"]
            worker_country = r["worker_country"]; wr = r["worker_result"]
            job_value = r["job_value_ecu"]; tier = r["sensitivity_tier"]

        # CHI ket toi (clawback) khi da du quorum. CONTESTED -> chua trung phat
        # ai ca, audit mo lai cho validator khac.
        caught = (new_status == FAILED)
        # reward validator (da lam viec audit that): fee + mine HEXIS
        if self.reward_fn:
            try:
                with self._conn() as c:
                    fee = self._cfg(c)["audit_fee"]
                self.reward_fn(validator_id, None, fee, audit_id, caught)
            except Exception:
                pass
        # fraud -> clawback post-hoc (slash escrow + severity + wipe record)
        if caught and self.clawback_fn:
            try:
                self.clawback_fn(worker_id, worker_country, job_id, job_value,
                                 tier, wr, validator_result)
            except Exception:
                pass

        # audit nga ngu -> giai quyet stake validator (dung: tra; lech: slash)
        stake_outcome = None
        if new_status in (PASSED, FAILED):
            try:
                stake_outcome = self._resolve_stakes(
                    audit_id, new_status,
                    r["worker_result"] if new_status == PASSED else validator_result)
            except Exception:
                pass

        self._audit("sampling_verdict", validator_id, worker_id,
                    {"job_id": job_id, "audit_id": audit_id,
                     "verdict": new_status, "caught": caught,
                     "matched": match, "agree_n": agree_n, "quorum_n": quorum_n,
                     "validator_stakes": stake_outcome})
        out = {"audit_id": audit_id, "job_id": job_id, "verdict": new_status,
               "caught_fraud": caught, "worker": worker_id,
               "matched": match, "agreeing_validators": agree_n,
               "fail_quorum_n": quorum_n, "validator_stakes": stake_outcome}
        if new_status == CONTESTED:
            out["note"] = (f"to cao ghi nhan nhung MOI {agree_n}/{quorum_n} validator "
                           f"dong y — chua trung phat ai. Audit mo lai cho validator "
                           f"khac xac nhan doc lap.")
        return out

    # ------------------------------------------------------------------
    def _refund_stale_stakes(self) -> int:
        """Hoan stake bi treo — KHONG giam tien validator vo thoi han.

        Hai truong hop tien co the ket vinh vien:
          1. Validator claim roi bo di khong bo phieu -> audit het han, quay ve
             PENDING, nhung stake van 'locked'.
          2. Audit CONTESTED ma khong bao gio co validator thu hai xac nhan ->
             chua bao gio nga ngu -> _resolve_stakes khong chay.
        Ca hai: khong ai bi ket toi, nen khong ai bi phat. Hoan lai.
        """
        now = time.time()
        with self._conn() as c:
            cfg = self._cfg(c)
            stale = c.execute(
                """SELECT s.audit_id, s.validator_id, s.amount
                   FROM sampling_stakes s JOIN sampling_audits a ON a.id = s.audit_id
                   WHERE s.status='locked' AND (
                       -- (1) audit ve lai pending/assigned qua han, chua bo phieu
                       (a.status IN (?,?) AND a.created_at < ?
                        AND NOT EXISTS (SELECT 1 FROM sampling_verdicts v
                                        WHERE v.audit_id=s.audit_id
                                          AND v.validator_id=s.validator_id))
                       -- (2) contested qua lau, khong ai xac nhan doc lap
                       OR (a.status=? AND a.created_at < ?)
                   )""",
                (PENDING, ASSIGNED, now - cfg["audit_expiry_s"],
                 CONTESTED, now - cfg["contested_expiry_s"]),
            ).fetchall()
        if not stale:
            return 0
        done = []
        for r in stale:                      # stake_fn ghi DB -> ngoai transaction
            try:
                if self.stake_fn:
                    self.stake_fn("release", r["validator_id"], r["amount"], r["audit_id"])
                done.append((r["audit_id"], r["validator_id"]))
            except Exception:
                pass
        with self._conn() as c:
            for aid, vid in done:
                c.execute("UPDATE sampling_stakes SET status='refunded', resolved_at=? "
                          "WHERE audit_id=? AND validator_id=?", (now, aid, vid))
        for aid, vid in done:
            self._audit("sampling_stake_refund", vid, None,
                        {"audit_id": aid, "reason": "audit khong nga ngu / bo dở"})
        return len(done)

    # ------------------------------------------------------------------
    # GIAI QUYET STAKE VALIDATOR khi audit nga ngu (v0.8: audit bilateral)
    # ------------------------------------------------------------------
    def _resolve_stakes(self, audit_id: str, final_status: str,
                        consensus_result: str) -> Dict[str, Any]:
        """Tra stake cho validator DUNG, slash validator LECH dong thuan.

        Su that sau cung:
          PASSED -> worker dung; ai bo phieu "lech" la to cao sai.
          FAILED -> ket qua dat quorum la dung; ai bo phieu khac (ke ca phieu
                    "khop worker") la sai.
        Workload TAT DINH nen sai = noi doi hoac may hong — ca hai deu khong
        duoc phep audit tiep. Slash chay vao SEVERITY_RESERVE.
        """
        now = time.time()
        with self._conn() as c:
            locks = c.execute(
                "SELECT validator_id, amount FROM sampling_stakes "
                "WHERE audit_id=? AND status='locked'", (audit_id,),
            ).fetchall()
            if not locks:
                return {"released": [], "slashed": []}
            votes = {v["validator_id"]: v for v in c.execute(
                "SELECT validator_id, validator_result, matched FROM sampling_verdicts "
                "WHERE audit_id=?", (audit_id,)).fetchall()}

        release, slash = [], []
        for lk in locks:
            v = votes.get(lk["validator_id"])
            if v is None:
                # da khoa nhung chua bo phieu (audit ket luan boi nguoi khac)
                # -> KHONG co loi, hoan lai.
                release.append((lk["validator_id"], lk["amount"], "refunded"))
                continue
            if final_status == PASSED:
                correct = bool(v["matched"])
            else:  # FAILED
                correct = (not v["matched"]) and \
                          (v["validator_result"] or "").lower() == (consensus_result or "").lower()
            (release if correct else slash).append(
                (lk["validator_id"], lk["amount"], "released" if correct else "slashed"))

        # stake_fn GHI DB -> goi NGOAI transaction
        done = []
        for vid, amt, kind in release + slash:
            action = "slash" if kind == "slashed" else "release"
            try:
                if self.stake_fn:
                    self.stake_fn(action, vid, amt, audit_id)
                done.append((vid, amt, kind))
            except Exception:
                pass  # that bai -> giu 'locked', khong mat dau vet
        with self._conn() as c:
            for vid, amt, kind in done:
                c.execute(
                    "UPDATE sampling_stakes SET status=?, resolved_at=? "
                    "WHERE audit_id=? AND validator_id=?", (kind, now, audit_id, vid))
        for vid, amt, kind in done:
            if kind == "slashed":
                self._audit("sampling_validator_slash", vid, None,
                            {"audit_id": audit_id, "amount": amt,
                             "reason": f"verdict lech dong thuan ({final_status})"})
        return {
            "released": [v for v, a, k in done if k != "slashed"],
            "slashed":  [v for v, a, k in done if k == "slashed"],
        }

    # ------------------------------------------------------------------
    def stats(self) -> Dict[str, Any]:
        with self._conn() as c:
            cfg = self._cfg(c)
            rows = c.execute(
                "SELECT status, COUNT(*) AS n FROM sampling_audits GROUP BY status"
            ).fetchall()
            counts = {r["status"]: r["n"] for r in rows}
            recent = c.execute(
                "SELECT job_id, worker_id, validator_id, status, created_at "
                "FROM sampling_audits ORDER BY created_at DESC LIMIT 20"
            ).fetchall()
            stake_counts = {r["status"]: r["n"] for r in c.execute(
                "SELECT status, COUNT(*) AS n FROM sampling_stakes GROUP BY status")}
        total = sum(counts.values())
        verified = counts.get(PASSED, 0) + counts.get(FAILED, 0)
        return {
            "sigma": cfg["sigma"],
            "strict_cross_country": bool(cfg["strict_cross_country"] >= 1),
            "audit_fee": cfg["audit_fee"],
            "fail_quorum_n": int(cfg["fail_quorum_n"]),
            "validator_stake": cfg["validator_stake"],
            "validator_stakes": stake_counts,
            "counts": counts,
            "total_audits": total,
            "fraud_caught": counts.get(FAILED, 0),
            "contested": counts.get(CONTESTED, 0),   # to cao chua du quorum
            "fraud_rate": round(counts.get(FAILED, 0) / verified, 4) if verified else 0.0,
            "recent": [dict(r) for r in recent],
        }

    def audit_status(self, job_id: str) -> Dict[str, Any]:
        with self._conn() as c:
            r = c.execute("SELECT * FROM sampling_audits WHERE job_id=?", (job_id,)).fetchone()
            if not r:
                return {"job_id": job_id, "status": "not_sampled"}
            votes = c.execute(
                "SELECT validator_id, matched, ts FROM sampling_verdicts "
                "WHERE audit_id=? ORDER BY ts ASC", (r["id"],),
            ).fetchall()
            quorum_n = int(self._cfg(c)["fail_quorum_n"])
        out = dict(r)
        # ---- RO RI CHI MANG (fix v0.8) --------------------------------------
        # Endpoint nay PUBLIC. Truoc day tra nguyen row -> lo worker_result khi
        # audit CHUA xong: validator chi can claim roi GET la copy duoc ket qua,
        # nop lai -> "khop" -> an audit_fee + HEXIS ma KHONG chay gi. Toan bo
        # PoSP thanh vo nghia (khong bao gio bat duoc gian lan).
        # Quy tac: audit chua nga ngu -> AN ket qua. Da xong -> CONG KHAI, de
        # bat ky ai cung tu chay workload_result(job_id, iterations) kiem tra
        # lai verdict — cong khai kiem chung CHINH validator.
        if r["status"] in (PENDING, ASSIGNED, CONTESTED):
            out["worker_result"] = None
            out["validator_result"] = None
            out["results_hidden"] = True
            out["hidden_reason"] = ("audit chua ket thuc — ket qua an de validator "
                                    "phai tu re-execute, khong copy duoc")
        else:
            out["results_hidden"] = False
        # KHONG lo validator_result cua tung phieu (validator sau con phai tu
        # chay doc lap — lo ra la moi copy, quorum thanh vo nghia).
        out["verdicts"] = [{"validator_id": v["validator_id"],
                            "matched": bool(v["matched"]), "ts": v["ts"]}
                           for v in votes]
        out["fail_quorum_n"] = quorum_n
        return out

    # ------------------------------------------------------------------
    def _audit(self, action, actor_id, counterparty, data) -> None:
        if not self.audit_fn:
            return
        try:
            self.audit_fn(action=action, actor_id=actor_id,
                          counterparty=counterparty, data=data)
        except Exception:
            pass


# ----------------------------------------------------------------------------
# Router — stats/config khong can STATE. claim/verdict o bridge patch (can
# STATE de lay country + xac thuc validator identity).
# ----------------------------------------------------------------------------
def get_sampling_router(sampling: SamplingEngine) -> APIRouter:
    r = APIRouter(tags=["sampling"])

    @r.get("/sampling/stats")
    def sampling_stats():
        return sampling.stats()

    @r.get("/sampling/epochs")
    def sampling_epochs():
        """
        The commit-reveal trail. Current epoch: commitment only. Ended
        epochs: secret included, so anyone can recompute the selection for
        every job in that window and check it against the sampling_open
        events in the audit chain. The commitments themselves are chain
        events (action sampling_epoch), sealed and Bitcoin-anchored, so the
        order — commitment first, jobs second, secret last — is provable.
        """
        with sampling._conn() as c:
            sampling._current_epoch(c)  # roll over if due, so reveals are not held
            rows = c.execute(
                "SELECT * FROM sampling_epochs ORDER BY epoch_id DESC LIMIT 50"
            ).fetchall()
        sampling._flush_epoch_audits()
        out = []
        for r0 in rows:
            e = {"epoch_id": r0["epoch_id"], "commitment": r0["commitment"],
                 "started_at": r0["started_at"], "ends_at": r0["ends_at"],
                 "revealed": bool(r0["revealed"])}
            if r0["revealed"]:
                e["secret"] = r0["secret_hex"]
                e["how_to_check"] = (
                    "sha256(secret) == commitment; sampled iff "
                    "int(sha256(secret+':' + job_id + ':posp-sample')"
                    ".hexdigest()[:8],16)/0xFFFFFFFF < sigma")
            out.append(e)
        return {"epochs": out, "selection": "should_sample_epoch — see hexis_sampling.py"}

    @r.get("/sampling/config")
    def sampling_config():
        return sampling.get_config()

    @r.get("/sampling/audit/{job_id}")
    def sampling_audit(job_id: str):
        return sampling.audit_status(job_id)

    return r
