"""
hexis_geo.py — Geo-Economics & Hardware Floor (v0.8) — v1

Chong "lanh chua khu vuc" (quyet dinh Ha 23-24/7). NGUYEN LY GOC:
  1. May tinh re nhat (laptop/desktop), noi xa nhat cung tham gia duoc —
     nhung DIEN THOAI BI LOAI (phone farm = cong cu lanh chua re nhat).
  2. NANG LUONG & TIMING la cot loi — moi thu khac chi la giam thieu:
     - C dong do bang JOULES tieu thu trong cua so thoi gian (khong dem job)
     - San phan cung gate bang SUSTAINED TIMING (thermal throttling cua
       phone khong gia duoc bang burst)
     - Trust tich bang thoi-gian-khong-nen-duoc (pair cap + rate limit da co)

BA CO CHE:

A. DYNAMIC C (R1) — tro gia cho khan hiem phai tu tat khi het khan hiem:
     damping(country) = min(1, Q_baseline / V_epoch)
     c_eff = 1 + (C_base - 1) x damping        (chi ap cho C_base > 1;
                                                C_base <= 1 giu nguyen —
                                                penalty khong duoc decay)
   V_epoch = tong energy_j cua country trong cua so 24h rolling.
   Datacenter do bo -> V vuot Q -> boost xep ve 1.0 cho CA VUNG (ke ca no)
   -> dong co arbitrage geo tu triet tieu. Ap dung: bridge scale hexis_raw
   theo (c_eff / C_base) tai mint site — formula §5 KHONG bi sua.

B. MINT SHARE CAP (R2) — "compute thi ban duoc, trust thi khong":
   Moi danh tinh mint toi da mint_share_cap (2%) tong HEXIS cua vung/cua so.
   FREE ALLOWANCE: mint_free_allowance (20) su kien dau moi cua so LUON
   duoc phep — nguoi tien phong don doc o vung trong khong bi cap 100%.
   Vuot cap: job van nhan fee (cho mo), chi KHONG mint them trust.

C. BENCHMARK GATE (san phan cung) — timing la thuoc do:
   Dang ky 2 buoc: register -> nhan challenge (K chains sha256 lap L lan,
   seed ngau nhien — khong precompute duoc) -> nop ket qua trong T_max.
   - Laptop CPU: ~20-60s. Phone: cham hon nhieu + thermal throttle -> qua T_max.
   - Server verify bang SPOT-CHECK R chains ngau nhien (asymmetry K/R lan)
     — dung nguyen ly sampling cua mang.
   Khong chong gia tuyet doi; cong voi stake floor thi du dat (bang gia,
   khong phai buc tuong).

R4 (audit cheo vung) di cung module sampling PoSP — chua o day.
R5 (HHI) o day: concentration() cho GET /concentration.

MODULE DOC LAP thu 4, dung pattern: tu so huu tables trong bridge.db,
khong import bridge/stake/severity. Config trong geo_config — calibrate
bang UPDATE, khong sua code.
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
# DEFAULTS — seed vao geo_config (calibrate = UPDATE row)
# ----------------------------------------------------------------------------
DEFAULT_CONFIG = {
    "epoch_window_s":       86400.0,   # cua so rolling 24h (dong bo pair cap)
    # RETIRED 2026-08-23 (OPEN.md #9). An absolute joule quota per region:
    # far above organic traffic then, and below it for any network worth
    # attacking, at which point damping would be permanently on. There is no
    # traffic level at which that number is right for long. Kept only so that
    # an operator reading an old config sees why it stopped being read.
    "baseline_energy_j":    2.0e10,
    # Its replacement. A region is damped by its SHARE of network energy in
    # the window, not by an amount. 0.5 = a region may account for half of
    # everything before the boost starts shrinking, and the trigger means the
    # same thing at any network size.
    "damping_share_cap":    0.5,
    "mint_share_cap":       0.02,      # 2% tong HEXIS vung/cua so moi danh tinh
    # RETIRED 2026-08-23, same reason: 20 events is an amount. Replaced by a
    # count of participants, below.
    "mint_free_allowance":  20.0,
    # Concentration is a comparison. A region with fewer than this many
    # distinct actors has nobody to be concentrated against, so the cap does
    # not apply there — which is what the free allowance was reaching for.
    "mint_cap_min_actors":  3.0,
    "bench_K":              64.0,       # so chains
    "bench_L":              2000000.0,  # do dai moi chain (sha256 lap) — sustained ~25-90s laptop
    "bench_R":              3.0,        # so chains server spot-check
    "bench_T_max_s":        180.0,      # laptop qua duoc; phone throttle/cham -> fail
    "bench_expiry_s":       900.0,     # challenge het han
}


def bench_chain_end(seed: str, index: int, length: int) -> str:
    """Chain sha256 lap — DUNG CHUNG client/server (worker client copy ham nay)."""
    h = hashlib.sha256(f"{seed}:{index}".encode()).digest()
    for _ in range(length):
        h = hashlib.sha256(h).digest()
    return h.hex()


class GeoEconomics:
    """C dong + mint cap + HHI. Nang luong (joules) la don vi ke toan."""

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
                CREATE TABLE IF NOT EXISTS geo_config (
                    key   TEXT PRIMARY KEY,
                    value REAL NOT NULL
                );

                -- so cai nang luong + mint: moi dong 1 su kien
                CREATE TABLE IF NOT EXISTS geo_activity (
                    id       TEXT PRIMARY KEY,
                    actor_id TEXT NOT NULL,
                    country  TEXT NOT NULL,
                    kind     TEXT NOT NULL,       -- 'energy' (joules) | 'mint' (hexis)
                    amount   REAL NOT NULL,
                    ts       REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS ix_geo_ck ON geo_activity(country, kind, ts);
                CREATE INDEX IF NOT EXISTS ix_geo_ak ON geo_activity(actor_id, kind, ts);

                CREATE TABLE IF NOT EXISTS benchmark_challenges (
                    address   TEXT PRIMARY KEY,
                    seed      TEXT NOT NULL,
                    issued_at REAL NOT NULL,
                    status    TEXT NOT NULL       -- 'pending' | 'passed' | 'failed'
                );
                """
            )
            # 2026-08-17. `verify()` da tinh `elapsed` tu dau roi VUT DI. Do la
            # phep do phan cung DUY NHAT ma he thong nay tung thuc hien, va no
            # khong duoc ghi lai o dau ca — dung mot kieu mat mat nhu 36 record
            # HEXIS khong con noi dung canonical: khong backfill duoc, chi bat
            # dau ghi tu bay gio duoc.
            #
            # Dung lam VAN TAY YEU cho cong doc lap PoSP. Yeu that: hai laptop
            # giong nhau cho elapsed giong nhau. Nen no khong tu minh chan ai
            # (xem `_posp_independent` o bridge) — no chi bat dau ton tai.
            cols = {r[1] for r in c.execute(
                "PRAGMA table_info(benchmark_challenges)").fetchall()}
            for col, decl in (("elapsed_s", "REAL"), ("verified_at", "REAL")):
                if col not in cols:
                    c.execute(
                        f"ALTER TABLE benchmark_challenges ADD COLUMN {col} {decl}")
            for k, v in DEFAULT_CONFIG.items():
                c.execute("INSERT OR IGNORE INTO geo_config(key,value) VALUES(?,?)", (k, v))

    def _cfg(self, c) -> Dict[str, float]:
        cfg = dict(DEFAULT_CONFIG)
        cfg.update({r["key"]: r["value"] for r in
                    c.execute("SELECT key,value FROM geo_config").fetchall()})
        return cfg

    # ------------------------------------------------------------------
    # SO CAI — energy ghi tu /job/complete, mint ghi tu mint site
    # ------------------------------------------------------------------
    def record_energy(self, actor_id: str, country: str, energy_j: float) -> None:
        if energy_j <= 0:
            return
        with self._conn() as c:
            c.execute(
                "INSERT INTO geo_activity(id,actor_id,country,kind,amount,ts) VALUES(?,?,?,?,?,?)",
                (uuid.uuid4().hex, actor_id, country or "??", "energy", energy_j, time.time()),
            )

    def record_mint(self, actor_id: str, country: str, hexis: float) -> None:
        if hexis <= 0:
            return
        with self._conn() as c:
            c.execute(
                "INSERT INTO geo_activity(id,actor_id,country,kind,amount,ts) VALUES(?,?,?,?,?,?)",
                (uuid.uuid4().hex, actor_id, country or "??", "mint", hexis, time.time()),
            )

    # ------------------------------------------------------------------
    # A. DYNAMIC C — tro gia tu tat theo NANG LUONG trong cua so
    # ------------------------------------------------------------------
    def damping(self, country: str) -> float:
        """
        min(1, cap / share) — share = this region's fraction of NETWORK energy.

        Relative since 2026-08-23 (OPEN.md #9). This used to be min(1, Q/V)
        against `baseline_energy_j`, an absolute quota of roughly 55 standard
        jobs per region per day. An absolute trigger is wrong at every scale
        except the one it was guessed for: far above organic traffic then, and
        below it for any network worth attacking, at which point damping would
        have been permanently on and nobody would have known why.

        A share means the same thing at every size, so it needs no
        recalibration as the network grows — which was the actual defect, not
        the particular number chosen.

        **Damping requires at least two active regions.** Not a threshold in
        disguise: concentration is a comparison, and with one region there is
        nothing to compare. A lone region has share 1.0 and would otherwise be
        damped forever for being the only participant.
        """
        with self._conn() as c:
            cfg = self._cfg(c)
            since = time.time() - cfg["epoch_window_s"]
            rows = c.execute(
                "SELECT country, COALESCE(SUM(amount),0) AS v FROM geo_activity "
                "WHERE kind='energy' AND ts>=? GROUP BY country",
                (since,),
            ).fetchall()
        by_country = {r["country"]: r["v"] for r in rows if r["v"] > 0}
        if len(by_country) < 2:
            return 1.0
        total = sum(by_country.values())
        v = by_country.get(country, 0.0)
        if total <= 0 or v <= 0:
            return 1.0
        share = v / total
        cap = float(cfg.get("damping_share_cap", 0.5))
        if cap <= 0 or share <= cap:
            return 1.0
        return cap / share

    def c_scale(self, country: str, c_base: float) -> float:
        """
        He so nhan vao hexis_raw tai mint site: c_eff / c_base.
        C_base <= 1 (vung giau): giu nguyen — penalty khong decay.
        """
        if c_base <= 1.0:
            return 1.0
        d = self.damping(country)
        c_eff = 1.0 + (c_base - 1.0) * d
        return c_eff / c_base

    # ------------------------------------------------------------------
    # B. MINT SHARE CAP — cap trust, khong cap thuong mai
    # ------------------------------------------------------------------
    def mint_allowed(self, actor_id: str, country: str) -> Tuple[bool, str]:
        with self._conn() as c:
            cfg = self._cfg(c)
            since = time.time() - cfg["epoch_window_s"]
            n_actor = c.execute(
                "SELECT COUNT(*) AS n FROM geo_activity "
                "WHERE actor_id=? AND kind='mint' AND ts>=?",
                (actor_id, since),
            ).fetchone()["n"]
            # Relative since 2026-08-23 (OPEN.md #9). This was
            # `n_actor < mint_free_allowance` — the first 20 mints of a window
            # always allowed, an amount rather than a condition. What it was
            # reaching for is that a lone pioneer in an empty region should not
            # be capped at a share of themselves, and that is a statement about
            # how many people are there, not about how many events happened.
            n_actors = c.execute(
                "SELECT COUNT(DISTINCT actor_id) AS n FROM geo_activity "
                "WHERE country=? AND kind='mint' AND ts>=?",
                (country, since),
            ).fetchone()["n"]
            if n_actors < int(cfg.get("mint_cap_min_actors", 3)):
                return True, "region_too_small_to_concentrate"
            actor_sum = c.execute(
                "SELECT COALESCE(SUM(amount),0) AS s FROM geo_activity "
                "WHERE actor_id=? AND country=? AND kind='mint' AND ts>=?",
                (actor_id, country, since),
            ).fetchone()["s"]
            region_sum = c.execute(
                "SELECT COALESCE(SUM(amount),0) AS s FROM geo_activity "
                "WHERE country=? AND kind='mint' AND ts>=?",
                (country, since),
            ).fetchone()["s"]
            cap = cfg["mint_share_cap"]
        if region_sum <= 0:
            return True, "empty_region"
        share = actor_sum / region_sum
        if share >= cap:
            return False, f"mint share {share:.1%} >= cap {cap:.0%} in {country}/24h"
        return True, f"share {share:.1%}"

    # ------------------------------------------------------------------
    # R5. CONCENTRATION — HHI cong khai
    # ------------------------------------------------------------------
    def concentration(self) -> Dict[str, Any]:
        with self._conn() as c:
            cfg = self._cfg(c)
            since = time.time() - cfg["epoch_window_s"]
            out: Dict[str, Any] = {"window_s": cfg["epoch_window_s"], "regions": {}}
            for kind in ("energy", "mint"):
                rows = c.execute(
                    "SELECT country, actor_id, SUM(amount) AS s FROM geo_activity "
                    "WHERE kind=? AND ts>=? GROUP BY country, actor_id",
                    (kind, since),
                ).fetchall()
                by_country: Dict[str, Dict[str, float]] = {}
                for r in rows:
                    by_country.setdefault(r["country"], {})[r["actor_id"]] = r["s"]
                for country, actors in by_country.items():
                    total = sum(actors.values())
                    if total <= 0:
                        continue
                    hhi = sum((v / total) ** 2 for v in actors.values())
                    top = sorted(actors.items(), key=lambda kv: -kv[1])[:5]
                    reg = out["regions"].setdefault(country, {})
                    reg[kind] = {
                        "total": round(total, 6),
                        "actors": len(actors),
                        "hhi": round(hhi, 4),
                        "damping": round(self.damping(country), 4) if kind == "energy" else None,
                        "top5": [{"actor": a[:20] + "...", "share": round(v / total, 4)}
                                 for a, v in top],
                    }
            return out

    def get_config(self) -> Dict[str, float]:
        with self._conn() as c:
            return self._cfg(c)


# ----------------------------------------------------------------------------
# C. BENCHMARK GATE — san phan cung bang sustained timing
# ----------------------------------------------------------------------------
class BenchmarkGate:
    def __init__(self, db_path: str):
        self.db_path = db_path
        # dung chung schema voi GeoEconomics (bang benchmark_challenges + geo_config)
        GeoEconomics(db_path)

    def _conn(self) -> sqlite3.Connection:
        c = sqlite3.connect(self.db_path, timeout=10)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA journal_mode=WAL;")
        return c

    def _cfg(self, c) -> Dict[str, float]:
        cfg = dict(DEFAULT_CONFIG)
        cfg.update({r["key"]: r["value"] for r in
                    c.execute("SELECT key,value FROM geo_config").fetchall()})
        return cfg

    def issue(self, address: str) -> Dict[str, Any]:
        """Cap challenge moi (ghi de challenge cu chua xong)."""
        seed = secrets.token_hex(16)
        now = time.time()
        with self._conn() as c:
            cfg = self._cfg(c)
            c.execute(
                "INSERT OR REPLACE INTO benchmark_challenges(address,seed,issued_at,status) "
                "VALUES(?,?,?,?)",
                (address, seed, now, "pending"),
            )
        return {
            "seed": seed,
            "K": int(cfg["bench_K"]),
            "L": int(cfg["bench_L"]),
            "T_max_s": cfg["bench_T_max_s"],
            "note": "Tinh K chains: chain_i = sha256 lap L lan tu sha256(seed:i). "
                    "Nop list K hex digests trong T_max. Laptop ~20-60s.",
        }

    def verify(self, address: str, results: List[str]) -> Dict[str, Any]:
        """Spot-check R chains ngau nhien + kiem tra timing. Raise HTTPException neu fail."""
        now = time.time()
        with self._conn() as c:
            cfg = self._cfg(c)
            row = c.execute(
                "SELECT * FROM benchmark_challenges WHERE address=?", (address,)
            ).fetchone()
        if not row or row["status"] != "pending":
            raise HTTPException(status_code=404,
                                detail="no pending benchmark challenge — register first")
        K, L = int(cfg["bench_K"]), int(cfg["bench_L"])
        elapsed = now - row["issued_at"]
        if elapsed > cfg["bench_expiry_s"]:
            self._set_status(address, "failed")
            raise HTTPException(status_code=410, detail="challenge expired — re-register")
        if len(results) != K:
            raise HTTPException(status_code=400, detail=f"need exactly {K} chain results")
        if elapsed > cfg["bench_T_max_s"]:
            self._set_status(address, "failed")
            raise HTTPException(
                status_code=403,
                detail=f"too slow: {elapsed:.0f}s > T_max {cfg['bench_T_max_s']:.0f}s "
                       f"(sustained-compute floor: laptop/desktop class required)",
            )
        # spot-check R chains ngau nhien (nguyen ly sampling cua chinh mang)
        import random
        for i in random.sample(range(K), int(cfg["bench_R"])):
            expect = bench_chain_end(row["seed"], i, L)
            if results[i].lower() != expect:
                self._set_status(address, "failed")
                raise HTTPException(status_code=403,
                                    detail=f"chain {i} incorrect — benchmark failed")
        self._set_status(address, "passed", elapsed=elapsed, verified_at=now)
        return {"address": address, "status": "passed", "elapsed_s": round(elapsed, 1)}

    def _set_status(self, address: str, status: str,
                    elapsed: Optional[float] = None,
                    verified_at: Optional[float] = None) -> None:
        with self._conn() as c:
            if elapsed is None:
                c.execute("UPDATE benchmark_challenges SET status=? WHERE address=?",
                          (status, address))
            else:
                # Ghi lai phep do, khong chi ket qua do/truot. Xem _init_schema.
                c.execute(
                    "UPDATE benchmark_challenges SET status=?, elapsed_s=?, "
                    "verified_at=? WHERE address=?",
                    (status, float(elapsed), float(verified_at or time.time()), address))

    def fingerprint(self, address: str) -> Optional[Dict[str, Any]]:
        """Phep do phan cung da ghi lai cho address nay, hoac None."""
        with self._conn() as c:
            row = c.execute(
                "SELECT elapsed_s, verified_at, status FROM benchmark_challenges "
                "WHERE address=?", (address,)).fetchone()
        if not row or row["elapsed_s"] is None:
            return None
        return {"elapsed_s": float(row["elapsed_s"]),
                "verified_at": row["verified_at"], "status": row["status"]}

    def status(self, address: str) -> str:
        with self._conn() as c:
            row = c.execute(
                "SELECT status FROM benchmark_challenges WHERE address=?", (address,)
            ).fetchone()
        return row["status"] if row else "none"


# ----------------------------------------------------------------------------
# Router — chi cac endpoint khong can STATE (benchmark submit nam trong bridge
# patch vi can activate worker trong STATE.workers)
# ----------------------------------------------------------------------------
def get_geo_router(geo: GeoEconomics) -> APIRouter:
    r = APIRouter(tags=["geo"])

    @r.get("/concentration")
    def concentration():
        return geo.concentration()

    @r.get("/geo/config")
    def geo_config():
        return geo.get_config()

    return r
