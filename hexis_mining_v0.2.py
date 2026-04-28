"""
HEXIS MINING ALGORITHM v0.2
============================
Proof of Integrity — Human trustworthiness as a mineable asset

TOKEN DISTRIBUTION (verified):
    Total supply:    12,800,000  HEXIS  100.0%
    Pre-mint:         1,216,000  HEXIS    9.5%
      Founder:          192,000  HEXIS    1.5%  vest 10yr, cliff 1yr
      Early (3yr+):     256,000  HEXIS    2.0%  vest 4yr
      Genesis burn:     768,000  HEXIS    6.0%  burned at Block 0
    Public mine:     11,584,000  HEXIS   90.5%
    Wallet cap:          10,000  HEXIS    0.078%
    Foundation:       Singapore  (MAS) — holds zero hexis

Core formula:
    HEXIS(h) = S × BO × W × TDR × T × C

    S   Sacrifice Score          [0, 1]   what was given up
    BO  Betrayal Opportunity     [0, 1]   temptation resisted
    W   Witness Score            [0, 1]   independent confirmation
    TDR Time Decay Resistance    [0, 1]   collective memory persistence
    T   Timing Score             [0, 1]   claim before outcome known
    C   Context Multiplier     [0.5, 2]   geographic justice correction

Security:
    - All float inputs validated (NaN/Inf rejected)
    - prob_betrayal_detected clamped to [0, 1]
    - S, W, TDR, BO, T clamped to valid ranges
    - Unknown country codes fall back to world median
    - Wallet hard cap enforced at LEDGER level (not here)
"""

import math
import hashlib
import json
import time
from datetime import datetime, timezone
from dataclasses import dataclass, field

# ============================================================
# PROTOCOL CONSTANTS
# ============================================================
TOTAL_SUPPLY       = 12_800_000
PRE_MINT_TOTAL     =  1_216_000   # 9.5%
PRE_MINT_FOUNDER   =    192_000   # 1.5% — vest 10yr, cliff 1yr
PRE_MINT_EARLY     =    256_000   # 2.0% — vest 4yr
GENESIS_BURN       =    768_000   # 6.0% — burned at Block 0 → 0x000...dead
PUBLIC_MINE        = 11_584_000   # 90.5%
WALLET_HARD_CAP    =     10_000   # max per wallet — enforced at ledger level

# Verify totals at import time
assert PRE_MINT_FOUNDER + PRE_MINT_EARLY + GENESIS_BURN == PRE_MINT_TOTAL
assert PRE_MINT_TOTAL + PUBLIC_MINE == TOTAL_SUPPLY

MIN_WITNESSES   = 3
MIN_SACRIFICE   = 0.1
MIN_HEXIS       = 0.00001

# ============================================================
# GDP PER CAPITA — Context Multiplier (World Bank 2024, USD)
# ============================================================
GDP_PER_CAPITA = {
    # High income
    "US": 80_000, "CH": 90_000, "NO": 100_000, "AU": 65_000,
    "GB": 48_000, "DE": 52_000, "JP": 34_000,  "KR": 33_000,
    "SG": 88_000, "FR": 44_000, "CA": 55_000,  "NL": 58_000,
    "SE": 56_000, "DK": 68_000, "FI": 53_000,  "NZ": 48_000,
    # Upper middle
    "CN": 13_000, "BR":  9_000, "MX": 11_000,  "TH":  7_000,
    "MY": 12_000, "ZA":  6_000, "RU": 14_000,  "TR": 10_000,
    "AR":  9_000, "CL": 15_000, "CO":  7_000,
    # Lower middle
    "VN":  4_200, "IN":  2_500, "PH":  3_700,  "ID":  4_900,
    "EG":  3_800, "NG":  2_200, "KE":  2_100,  "PK":  1_600,
    "MM":  1_200, "BD":  2_800, "KH":  1_800,  "LK":  3_800,
    # Low income
    "ET":  1_100, "TZ":  1_200, "UG":    900,
    "MZ":    500, "MW":    600, "ML":    900,
    "DEFAULT": 12_000,  # world median
}
REFERENCE_GDP = 12_000  # world median → C = 1.0


# ============================================================
# INPUT VALIDATOR
# ============================================================
def _safe_float(val, name: str, lo: float = None, hi: float = None) -> float:
    """Validate float input. Raises ValueError for NaN or Inf. Clamps to [lo, hi]."""
    if not isinstance(val, (int, float)):
        raise ValueError(f"{name} must be numeric, got {type(val).__name__}")
    f = float(val)
    if math.isnan(f):
        raise ValueError(f"{name} cannot be NaN")
    if math.isinf(f):
        raise ValueError(f"{name} cannot be Inf")
    if lo is not None:
        f = max(lo, f)
    if hi is not None:
        f = min(hi, f)
    return f


# ============================================================
# BEHAVIOR EVENT
# ============================================================
@dataclass
class BehaviorEvent:
    """
    A specific behavior submitted for mining.

    Human-supplied (require judgment — cannot be automated):
        asset_could_have_taken, asset_actually_returned
        prob_betrayal_detected, gain_if_betrayed

    Auto-collected (see hexis_data_collector.py):
        witness_sources, mention_counts
        result_timestamp, submit_timestamp
    """
    event_id:        str
    actor_id:        str
    timestamp:       float    # Unix timestamp when behavior occurred
    description:     str
    actor_country:   str      # ISO 2-letter code e.g. "US", "VN", "NG"

    # S — Sacrifice
    asset_could_have_taken:  float  # Max value available to take
    asset_actually_returned: float  # Value returned/honored (= could_have for burns)

    # BO — Betrayal Opportunity
    prob_betrayal_detected: float   # [0.0, 1.0]
    gain_if_betrayed:       float   # USD equivalent

    # W — Witnesses
    witness_sources: list   # [{"type": "adversarial"|"neutral"|"allied"|"anonymous", "name": "..."}, ...]

    # TDR — Time Decay
    mention_counts: dict = field(default_factory=dict)  # {"30d": N, "1y": N, "5y": N}

    # T — Timing
    result_timestamp: float = 0.0   # When outcome was confirmed (0 = unknown)
    submit_timestamp: float = 0.0   # When submitted to Hexis (0 = now)


# ============================================================
# HEXIS MINER
# ============================================================
class HexisMiner:

    @staticmethod
    def calc_S(e: BehaviorEvent) -> float:
        """
        Sacrifice Score = (could_have - actually_taken) / could_have
        Range: [0, 1]
        For burns: asset_actually_returned = asset_could_have_taken → S = 1.0
        """
        could = _safe_float(e.asset_could_have_taken, "asset_could_have_taken", lo=0.0)
        returned = _safe_float(e.asset_actually_returned, "asset_actually_returned", lo=0.0)
        if could == 0:
            return 0.0
        taken = could - returned
        return max(0.0, min(1.0, (could - taken) / could))

    @staticmethod
    def calc_BO(e: BehaviorEvent) -> float:
        """
        Betrayal Opportunity = log(gain × (1 - prob_detected) + 1) / log(1B + 1)
        Range: [0, 1]
        prob_betrayal_detected clamped to [0, 1].
        """
        prob = _safe_float(e.prob_betrayal_detected, "prob_betrayal_detected", lo=0.0, hi=1.0)
        gain = _safe_float(e.gain_if_betrayed, "gain_if_betrayed", lo=0.0)
        raw = gain * (1.0 - prob)
        if raw <= 0:
            return 0.0
        return max(0.0, min(1.0, math.log(raw + 1) / math.log(1_000_000_000 + 1)))

    @staticmethod
    def calc_W(e: BehaviorEvent) -> float:
        """
        Witness Score = log(weighted_count + 1) / log(1M + 1)
        Range: [0, 1]
        Weights: adversarial=3.0, neutral=2.0, allied=1.0, anonymous=0.3
        Malformed entries handled gracefully.
        """
        if not e.witness_sources:
            return 0.0
        weights = {"adversarial": 3.0, "neutral": 2.0, "allied": 1.0, "anonymous": 0.3}
        weighted = 0.0
        for s in e.witness_sources:
            if isinstance(s, dict):
                wtype = s.get("type", "neutral")
                weighted += weights.get(wtype, 2.0)
            else:
                weighted += 0.3  # non-dict → anonymous
        if weighted <= 0:
            return 0.0
        return min(1.0, math.log(weighted + 1) / math.log(1_000_000 + 1))

    @staticmethod
    def calc_TDR(e: BehaviorEvent) -> float:
        """
        Time Decay Resistance = avg(retention_1y, retention_5y)
        Range: [0, 1]
        retention = mentions_Xy / mentions_30d
        """
        m = e.mention_counts
        if not m or m.get("30d", 0) == 0:
            return 0.1
        m30 = max(1, int(m.get("30d", 1)))
        m1y = int(m.get("1y", 0))
        m5y = int(m.get("5y", 0))
        if m1y == 0:
            return 0.1
        r1y = min(1.0, m1y / m30)
        if m5y == 0:
            return r1y * 0.5
        r5y = min(1.0, m5y / m30)
        return max(0.0, min(1.0, (r1y + r5y) / 2))

    @staticmethod
    def calc_T(e: BehaviorEvent) -> float:
        """
        Timing Score = pre_result_bonus × window_score
        Range: [0, 1]

        PRIMARY ANTI-GAMING MECHANISM.
        Submit BEFORE outcome known → T = 1.0
        Submit AFTER outcome known  → T decays 1%/hour, floor 0.1

        window_score (hours between claim and outcome):
            ≤24h: 1.0 | ≤72h: 0.9 | ≤7d: 0.7 | ≤30d: 0.5 | ≤1y: 0.3 | >1y: 0.1
        """
        sub_ts = e.submit_timestamp if e.submit_timestamp > 0 else time.time()
        res_ts = e.result_timestamp
        if res_ts == 0:
            return 0.5  # outcome not yet confirmed

        hours_to_result = (res_ts - e.timestamp) / 3600
        if hours_to_result < 0:
            return 0.05  # claim after result — suspicious

        # Pre-result bonus
        if sub_ts <= res_ts:
            pre = 1.0
        else:
            hours_late = (sub_ts - res_ts) / 3600
            pre = max(0.1, 1.0 - (hours_late / 720))  # -1%/hr, floor 0.1

        # Window score
        if   hours_to_result <= 24:    win = 1.0
        elif hours_to_result <= 72:    win = 0.9
        elif hours_to_result <= 168:   win = 0.7
        elif hours_to_result <= 720:   win = 0.5
        elif hours_to_result <= 8760:  win = 0.3
        else:                          win = 0.1

        return pre * win

    @staticmethod
    def calc_C(e: BehaviorEvent) -> float:
        """
        Context Multiplier = clamp(REFERENCE_GDP / COUNTRY_GDP, 0.5, 2.0)
        Range: [0.5, 2.0]

        Rawlsian justice: same sacrifice costs more in poorer countries.
        High income (US, SG): C ≈ 0.5 | World median: C = 1.0 | Low income (NG, ET): C = 2.0
        Unknown country → DEFAULT (world median).
        """
        code = str(e.actor_country or "").upper().strip()
        gdp = GDP_PER_CAPITA.get(code, GDP_PER_CAPITA["DEFAULT"])
        if gdp <= 0:
            return 1.0
        return max(0.5, min(2.0, REFERENCE_GDP / gdp))

    def mine(self, e: BehaviorEvent) -> dict:
        """
        HEXIS(h) = S × BO × W × TDR × T × C

        Returns dict with eligible=True and all scores if mintable,
        or eligible=False with reason if not.

        proof_hash: deterministic SHA256 of all inputs.
        Same behavior → same hash. Cannot be reverse-engineered.

        NOTE: Wallet hard cap (10,000 hexis) is enforced at the
        LEDGER level (hexis_ledger.py), not here.
        """
        # Eligibility checks
        if len(e.witness_sources) < MIN_WITNESSES:
            return {
                "eligible": False,
                "reason": f"Need ≥{MIN_WITNESSES} witnesses. Got {len(e.witness_sources)}."
            }

        try:
            S = self.calc_S(e)
        except ValueError as ex:
            return {"eligible": False, "reason": f"Invalid sacrifice input: {ex}"}

        if S < MIN_SACRIFICE:
            return {
                "eligible": False,
                "reason": f"Sacrifice score {S:.4f} below minimum {MIN_SACRIFICE}."
            }

        try:
            BO  = self.calc_BO(e)
            W   = self.calc_W(e)
            TDR = self.calc_TDR(e)
            T   = self.calc_T(e)
            C   = self.calc_C(e)
        except ValueError as ex:
            return {"eligible": False, "reason": f"Invalid input: {ex}"}

        hexis = S * BO * W * TDR * T * C

        if hexis < MIN_HEXIS:
            return {
                "eligible": False,
                "reason": f"HEXIS {hexis:.8f} below mint threshold {MIN_HEXIS}."
            }

        # Deterministic proof hash
        proof_data = {
            "event_id":    e.event_id,
            "actor_id":    e.actor_id,
            "country":     e.actor_country,
            "timestamp":   e.timestamp,
            "result_ts":   e.result_timestamp,
            "submit_ts":   e.submit_timestamp or time.time(),
            "S":   round(S,   8),
            "BO":  round(BO,  8),
            "W":   round(W,   8),
            "TDR": round(TDR, 8),
            "T":   round(T,   8),
            "C":   round(C,   8),
            "hexis": round(hexis, 12),
        }
        proof_hash = hashlib.sha256(
            json.dumps(proof_data, sort_keys=True).encode()
        ).hexdigest()

        grade = (
            "Exceptional" if hexis > 0.5 else
            "High"        if hexis > 0.05 else
            "Moderate"    if hexis > 0.005 else
            "Low"         if hexis > 0.001 else
            "Minimal"
        )

        return {
            "eligible":   True,
            "event_id":   e.event_id,
            "actor_id":   e.actor_id,
            "country":    e.actor_country,
            "S":   round(S,   4),
            "BO":  round(BO,  4),
            "W":   round(W,   4),
            "TDR": round(TDR, 4),
            "T":   round(T,   4),
            "C":   round(C,   4),
            "hexis_raw":  round(hexis, 8),
            "proof_hash": proof_hash,
            "grade":      grade,
            "mined_at":   datetime.now(timezone.utc).isoformat(),
        }


# ============================================================
# QUICK TEST
# ============================================================
def _p(r: dict):
    if not r.get("eligible"):
        print(f"  NOT ELIGIBLE: {r['reason']}")
        return
    print(f"  S={r['S']}  BO={r['BO']}  W={r['W']}  "
          f"TDR={r['TDR']}  T={r['T']}  C={r['C']}")
    print(f"  HEXIS={r['hexis_raw']:.8f}  [{r['grade']}]")
    print(f"  Hash={r['proof_hash'][:48]}...")


if __name__ == "__main__":
    m = HexisMiner()
    now = time.time()

    print("=" * 70)
    print("HEXIS MINING ENGINE v0.2")
    print(f"Total supply: {TOTAL_SUPPLY:,} | Pre-mint: {PRE_MINT_TOTAL:,} (9.5%)")
    print(f"  Founder: {PRE_MINT_FOUNDER:,} (1.5%)  "
          f"Early: {PRE_MINT_EARLY:,} (2.0%)  "
          f"Burn: {GENESIS_BURN:,} (6.0%)")
    print(f"Public mine: {PUBLIC_MINE:,} (90.5%)")
    print("=" * 70)

    # 1 — Local honest act, Chicago USA
    print("\n[1] Marcus Webb — Chicago USA — returns $200 wallet")
    r1 = m.mine(BehaviorEvent(
        event_id    = hashlib.sha256(b"marcus_chicago_2026").hexdigest()[:16],
        actor_id    = "marcus_webb",
        timestamp   = now - 86400 * 30,
        description = "Found $200 wallet on subway. No cameras. Returned intact.",
        actor_country = "US",
        asset_could_have_taken  = 200.0,
        asset_actually_returned = 200.0,
        prob_betrayal_detected  = 0.05,
        gain_if_betrayed        = 200.0,
        witness_sources = [
            {"type": "neutral", "name": "nextdoor_admin"},
            {"type": "neutral", "name": "witness_A"},
            {"type": "neutral", "name": "witness_B"},
        ],
        mention_counts   = {"30d": 15, "1y": 1, "5y": 0},
        result_timestamp = now - 86400 * 29,
        submit_timestamp = now - 86400 * 29,
    ))
    _p(r1)

    # 2 — Same act, Lagos Nigeria (Context Multiplier effect)
    print("\n[2] Adebayo — Lagos Nigeria — same $200 wallet returned")
    r2 = m.mine(BehaviorEvent(
        event_id    = hashlib.sha256(b"adebayo_lagos_2026").hexdigest()[:16],
        actor_id    = "adebayo_001",
        timestamp   = now - 86400 * 30,
        description = "Found $200 equivalent wallet. No cameras. Returned intact.",
        actor_country = "NG",
        asset_could_have_taken  = 200.0,
        asset_actually_returned = 200.0,
        prob_betrayal_detected  = 0.03,
        gain_if_betrayed        = 200.0,
        witness_sources = [
            {"type": "neutral", "name": "community_leader"},
            {"type": "neutral", "name": "mosque_witness"},
            {"type": "neutral", "name": "market_witness"},
        ],
        mention_counts   = {"30d": 12, "1y": 1, "5y": 0},
        result_timestamp = now - 86400 * 29,
        submit_timestamp = now - 86400 * 29,
    ))
    _p(r2)

    # 3 — Presidential declaration, Hormuz
    print("\n[3] POTUS — Hormuz declaration — verified in 68 hours")
    ct = now - 86400 * 14
    rt = ct + 3600 * 68
    r3 = m.mine(BehaviorEvent(
        event_id    = hashlib.sha256(b"potus_hormuz_2026").hexdigest()[:16],
        actor_id    = "potus_47",
        timestamp   = ct,
        description = "Declared Hormuz opens in 72hrs. Verified in 68hrs by Kpler + CENTCOM.",
        actor_country = "US",
        asset_could_have_taken  = 50_000_000.0,
        asset_actually_returned = 50_000_000.0,
        prob_betrayal_detected  = 0.95,
        gain_if_betrayed        = 10_000_000.0,
        witness_sources = ([
            {"type": "adversarial", "name": "CNN"},
            {"type": "adversarial", "name": "WashPost"},
            {"type": "adversarial", "name": "NYT"},
            {"type": "neutral",     "name": "Reuters"},
            {"type": "neutral",     "name": "AP"},
            {"type": "neutral",     "name": "BBC"},
            {"type": "neutral",     "name": "Kpler"},
            {"type": "neutral",     "name": "CENTCOM"},
            {"type": "allied",      "name": "FoxNews"},
        ] * 150),
        mention_counts   = {"30d": 500_000, "1y": 50_000, "5y": 10_000},
        result_timestamp = rt,
        submit_timestamp = ct + 3600,
    ))
    _p(r3)

    # 4 — Genesis burn
    print("\n[4] Genesis burn — founder burns 768,000 hexis publicly at Block 0")
    r4 = m.mine(BehaviorEvent(
        event_id    = hashlib.sha256(b"hexis_genesis_burn_2026").hexdigest()[:16],
        actor_id    = "hexis_founder",
        timestamp   = now,
        description = (
            "Founder burns 768,000 hexis (6% of total supply) at genesis. "
            "'This project may be useless. We do not expect profit.' "
            "Witnessed publicly on IPFS."
        ),
        actor_country = "SG",
        asset_could_have_taken  = 768_000.0,
        asset_actually_returned = 768_000.0,  # burned = fully given up
        prob_betrayal_detected  = 0.30,
        gain_if_betrayed        = 768_000.0,
        witness_sources = ([
            {"type": "adversarial", "name": "crypto_skeptics"},
            {"type": "adversarial", "name": "independent_reviewers"},
            {"type": "neutral",     "name": "ipfs_permanent_record"},
            {"type": "neutral",     "name": "whitepaper_publication"},
            {"type": "neutral",     "name": "github_commit"},
        ] * 10),
        mention_counts   = {"30d": 500, "1y": 0, "5y": 0},
        result_timestamp = now + 1,
        submit_timestamp = now,
    ))
    _p(r4)

    # Summary
    h1 = r1.get("hexis_raw", 0)
    h2 = r2.get("hexis_raw", 0)
    h3 = r3.get("hexis_raw", 0)
    h4 = r4.get("hexis_raw", 0)

    print(f"\n{'=' * 70}")
    print("RESULTS SUMMARY")
    print(f"{'=' * 70}")
    print(f"  [1] Chicago  US   {h1:.6f}  C={r1.get('C')}  [{r1.get('grade')}]")
    print(f"  [2] Lagos    NG   {h2:.6f}  C={r2.get('C')}  [{r2.get('grade')}]  ({h2/h1:.1f}x Chicago)")
    print(f"  [3] POTUS    US   {h3:.6f}  C={r3.get('C')}  [{r3.get('grade')}]  ({h3/h1:.0f}x Chicago)")
    print(f"  [4] Burn     SG   {h4:.6f}  C={r4.get('C')}  [{r4.get('grade')}]")

    print(f"\n  Geographic justice: Lagos={h2/h1:.1f}x Chicago")
    print(f"  (Same act — $200 costs proportionally more in Nigeria)")

    print(f"\n{'=' * 70}")
    print("SUPPLY VERIFICATION")
    print(f"{'=' * 70}")
    print(f"  Total:         {TOTAL_SUPPLY:>12,}  100.0%")
    print(f"  Pre-mint:      {PRE_MINT_TOTAL:>12,}    9.5%")
    print(f"    Founder:     {PRE_MINT_FOUNDER:>12,}    1.5%  vest 10yr cliff 1yr")
    print(f"    Early (3yr): {PRE_MINT_EARLY:>12,}    2.0%  vest 4yr")
    print(f"    Genesis burn:{GENESIS_BURN:>12,}    6.0%  burned at Block 0")
    print(f"  Public mine:   {PUBLIC_MINE:>12,}   90.5%")
    print(f"  Wallet cap:    {WALLET_HARD_CAP:>12,}   0.078%  (ledger-enforced)")
    print(f"  Foundation:    holds zero hexis")
    print(f"  Verifiers:     paid in stable coin")
    print(f"{'=' * 70}")
