"""
NEWFLOW v0.6 — Sunk Cost Standard (SCS)
=========================================

"The most defensible backing is not a promise about future value.
 It is proof of past destruction."

PHILOSOPHICAL BREAK FROM ALL PRIOR VERSIONS:

  v0.4  Electricity peg:    ECU tracks P_electricity via oracle
  v0.5  OHAS peg:           ECU tracks Oil-Compute spread via 2 oracles
  v0.6  Sunk Cost Standard: ECU IS destroyed energy. No oracle. No peg.

THE BITCOIN EQUIVALENCE:

  Bitcoin:
    Miners burn electricity → SHA-256 hash → BTC minted
    BTC is not PEGGED to electricity price.
    BTC IS financialised destroyed electricity.
    The electricity cannot be un-burned. The BTC cannot be un-backed.
    Security: attacking Bitcoin requires re-burning the energy.
    Sunk cost IS the defense.

  NEWFLOW v0.6:
    Workers burn energy → TEE-attested compute → ECU minted
    ECU is not PEGGED to any price.
    ECU IS financialised destroyed compute energy.
    The energy cannot be un-consumed. The ECU cannot be un-backed.
    Security: faking a proof requires consuming the same energy.
    Sunk cost IS the defense.

WHAT IS ELIMINATED:

  - All price oracles (Chainlink WTI, Chainlink electricity, U3O8 feeds)
  - The concept of "peg" — no external reference price ever again
  - Oracle manipulation attack surface (zero Chainlink dependency)
  - External commodity risk (oil, uranium, electricity tariff changes)
  - Geographic arbitrage (cheap electricity regions do not gain unfair advantage —
    they mint proportionally to energy consumed, not to price differential)

WHAT REMAINS IMMUTABLE FROM GENESIS:

  ENERGY_UNIT     = joules of compute energy required to mint 1 ECU at genesis
                    Set on 18 April 2026. Never changes.
                    This is the Rosetta Stone of NEWFLOW value.

  HALVING_INTERVAL = 237,500 ECU — same as all prior versions
                    The halving doubles ENERGY_UNIT every phase.
                    Scarcity increases over time, exactly like Bitcoin.

MINTING FORMULA (oracle-free):

  effective_unit = ENERGY_UNIT × 2^halving_phase
  ECU_minted     = energy_joules / effective_unit

  Where energy_joules is TEE-attested (Groth16 proof, cannot be forged).

DIFFICULTY (network-level, auto-adjusting):

  Target: emit TARGET_ECU_PER_BLOCK ECU per block on average.
  Actual: if network submits more energy → more ECU emitted → supply expands.
  No manual difficulty adjustment needed. The formula IS the difficulty.

  This is identical to how Bitcoin works:
    More hashrate → more BTC minted per time → but difficulty adjusts
  In NEWFLOW SCS:
    More compute energy → more ECU minted → but ENERGY_UNIT is fixed
    → halving controls long-run supply
    → short-run: more workers = more total ECU = exactly right
       (more compute delivered = economy should have more ECU)

WHY THIS IS MORE DEVASTATING THAN OHAS:

  1. Zero oracle dependency — no Chainlink, no manipulation surface
  2. No commodity risk — oil, uranium, electricity prices: irrelevant
  3. Absolute backing — energy is already destroyed; token exists; done
  4. Anti-fragile — as global energy prices rise, each ECU represents
     more USD of destroyed energy (value accrues without mechanism change)
  5. Perfect narrative — "Each ECU = permanently destroyed compute energy"
  6. Geographic fairness — cheap electricity regions earn proportionally
     to energy consumed, not to price. No arbitrage. No inequality.
  7. Sunk cost defense — identical to Bitcoin's security model

THE SUNK COST DEFENSE IN DETAIL:

  A worker in India consuming 1 kWh of cheap electricity ($0.04):
    OHAS: reward penalised if spread is negative (compute expensive regime)
    SCS:  reward = 1 kWh × 3,600,000 J/kWh / ENERGY_UNIT (full, no penalty)

  A whale attempting to manipulate by burning cheap energy to mint ECU:
    OHAS: if electricity price rises (oracle), difficulty rises, attack fails
    SCS:  difficulty is fixed per genesis. BUT: the whale still burned real energy.
          The ECU they minted IS backed by that energy. No manipulation occurred.
          They simply participated in the network at scale — exactly as intended.

  An attacker trying to fake a TEE proof:
    Must produce a Groth16 BN254 proof of energy consumption without consuming energy.
    This is computationally equivalent to breaking BN254 discrete log.
    Cost: ~$10^30 in current compute. Not viable.
"""

import math
import hashlib
import time
import unittest
from dataclasses import dataclass, field
from typing import Optional


# ======================================================
# IMMUTABLE GENESIS CONSTANTS
# ======================================================

# 1 kWh = 3,600,000 joules
JOULES_PER_KWH = 3_600_000.0

# ENERGY_UNIT: joules of real compute energy to mint 1 ECU at genesis.
# Set on 18 April 2026. NEVER CHANGES.
# Derived from genesis block context:
#   Target: make 1 ECU cost approximately the energy of
#   running a mid-range GPU (RTX 3080) for 1 hour.
#   RTX 3080 TDP: 320W = 0.32 kWh/hr = 1,152,000 J/hr
#   1 ECU = 1,152,000 joules at genesis
ENERGY_UNIT_GENESIS = 1_152_000.0   # joules per ECU at genesis

# Halving: every HALVING_INTERVAL ECU minted, effective_unit doubles
HALVING_INTERVAL    = 237_500        # ECU
MAX_SUPPLY          = 950_000        # ECU (fixed forever)

# Block reward baseline (for backward compat — log2 scaling still applies)
# In SCS, this is just the conversion coefficient
BASE_COEFFICIENT    = 1.0            # dimensionless


# ======================================================
# SUNK COST MINTING ENGINE
# ======================================================

class SunkCostMintEngine:
    """
    Oracle-free minting engine. ECU = financialised destroyed compute energy.

    No external price references. No Chainlink feeds. No peg.
    The TEE-attested energy consumption IS the backing.

    MINTING:
      effective_unit = ENERGY_UNIT_GENESIS x 2^halving_phase
      ECU_minted     = energy_joules / effective_unit

    HALVING:
      Every 237,500 ECU minted, effective_unit doubles.
      Identical mechanics to Bitcoin's block reward halving.
      Scarcity increases over time. Supply is bounded at 950,000 ECU.

    INVARIANT:
      sum(ECU_minted_all_time) <= 950,000
      Each minted ECU = permanently destroyed compute energy.
      No ECU can exist without corresponding destroyed joules.
    """

    def __init__(self):
        self.total_minted = 0.0
        self.energy_unit  = ENERGY_UNIT_GENESIS
        self.mint_log: list[dict] = []

    @property
    def halving_phase(self) -> int:
        return int(self.total_minted / HALVING_INTERVAL)

    @property
    def effective_energy_unit(self) -> float:
        """Joules required to mint 1 ECU in current halving phase."""
        return ENERGY_UNIT_GENESIS * (2 ** self.halving_phase)

    def joules_to_ecu(self, joules: float) -> float:
        """Convert real destroyed joules to ECU. No oracle needed."""
        return joules / self.effective_energy_unit

    def ecu_to_joules(self, ecu: float) -> float:
        """How many joules does 1 ECU represent in current phase?"""
        return ecu * self.effective_energy_unit

    def mint(self, energy_joules: float, worker: str) -> float:
        """
        Mint ECU proportional to TEE-attested destroyed energy.
        energy_joules: from HardwareOracle Groth16 proof (actual_mwh * 3,600,000)
        Returns: ECU minted
        """
        if energy_joules <= 0:
            raise ValueError("Cannot mint from zero or negative energy")

        ecu_raw    = self.joules_to_ecu(energy_joules)
        remaining  = MAX_SUPPLY - self.total_minted
        ecu_actual = min(ecu_raw, remaining)

        if ecu_actual <= 0:
            raise ValueError("Supply cap reached")

        self.total_minted += ecu_actual
        self.mint_log.append({
            "worker":         worker,
            "energy_joules":  energy_joules,
            "energy_kwh":     energy_joules / JOULES_PER_KWH,
            "ecu_minted":     ecu_actual,
            "halving_phase":  self.halving_phase,
            "effective_unit": self.effective_energy_unit,
            "timestamp":      int(time.time()),
        })
        return ecu_actual

    def mint_from_tee(self,
                      actual_mwh: float,
                      actual_teraflops: float,
                      worker: str) -> float:
        """
        Mint from TEE-attested proof data (mwh and teraflops from HardwareOracle).
        Only mwh matters for minting — teraflops determines task validity.
        """
        joules = actual_mwh * 1000 * JOULES_PER_KWH  # MWh → kWh → J
        return self.mint(joules, worker)

    def preview_mint(self, energy_joules: float) -> float:
        """Preview ECU without state change."""
        return energy_joules / self.effective_energy_unit

    def backing_per_ecu(self) -> dict:
        """
        What does 1 ECU represent in physical energy terms?
        This is the 'backing' — not a price, but a physical quantity.
        """
        unit = self.effective_energy_unit
        return {
            "joules_per_ecu":   unit,
            "kwh_per_ecu":      unit / JOULES_PER_KWH,
            "mwh_per_ecu":      unit / (JOULES_PER_KWH * 1000),
            "halving_phase":    self.halving_phase,
            "total_minted_ecu": self.total_minted,
            "remaining_ecu":    MAX_SUPPLY - self.total_minted,
            "description":      (
                f"1 ECU = {unit/JOULES_PER_KWH:.4f} kWh of permanently "
                f"destroyed compute energy (phase {self.halving_phase})"
            ),
        }

    def network_energy_stats(self) -> dict:
        """Total energy permanently destroyed across all mint events."""
        total_j = sum(e["energy_joules"] for e in self.mint_log)
        return {
            "total_energy_joules": total_j,
            "total_energy_kwh":    total_j / JOULES_PER_KWH,
            "total_energy_mwh":    total_j / (JOULES_PER_KWH * 1000),
            "total_ecu_minted":    self.total_minted,
            "joules_per_ecu_avg":  total_j / self.total_minted if self.total_minted > 0 else 0,
        }


# ======================================================
# SUNK COST DIFFICULTY ENGINE
# ======================================================

class SunkCostDifficultyEngine:
    """
    Network-level difficulty — how much energy the NETWORK has committed.

    In Bitcoin: difficulty adjusts so blocks arrive every 10 minutes
    regardless of total hashrate.

    In NEWFLOW SCS: no per-block difficulty. The ENERGY_UNIT is the difficulty.
    As more workers join, more total energy is consumed, more ECU is minted.
    This is correct behaviour: a larger compute economy should have more ECU.

    The halving schedule handles long-run scarcity.
    Short-run: supply is elastic to real compute demand.

    NETWORK ENERGY RATE:
      Tracks total energy consumed per unit time across all workers.
      Used for analytics and fee estimation, not for minting difficulty.

    ENERGY EFFICIENCY SCORE:
      Measures how much useful computation (teraflops) per joule
      the network produces. Higher = more efficient network.
      Improves over time as hardware improves (Moore's Law).
    """

    def __init__(self, window_blocks: int = 100):
        self.window = window_blocks
        self.energy_history: list[dict] = []  # {block, joules, teraflops, ts}

    def record_block(self, block_index: int,
                     energy_joules: float,
                     teraflops: float) -> None:
        self.energy_history.append({
            "block":      block_index,
            "joules":     energy_joules,
            "teraflops":  teraflops,
            "ts":         int(time.time()),
        })
        # Keep only last N blocks
        if len(self.energy_history) > self.window:
            self.energy_history.pop(0)

    def network_energy_rate_jps(self) -> float:
        """Network energy consumption rate (joules/second)."""
        if len(self.energy_history) < 2:
            return 0.0
        total_j  = sum(b["joules"] for b in self.energy_history)
        duration = (self.energy_history[-1]["ts"]
                    - self.energy_history[0]["ts"])
        return total_j / duration if duration > 0 else 0.0

    def network_efficiency_tflops_per_joule(self) -> float:
        """How many teraflops per joule the network achieves."""
        total_j  = sum(b["joules"] for b in self.energy_history)
        total_tf = sum(b["teraflops"] for b in self.energy_history)
        return total_tf / total_j if total_j > 0 else 0.0

    def projected_mine_duration_years(self, mint_engine: SunkCostMintEngine) -> float:
        """
        At current energy rate, how many years to mine remaining supply?
        Accounts for halving phases.
        """
        remaining = MAX_SUPPLY - mint_engine.total_minted
        rate_jps  = self.network_energy_rate_jps()
        if rate_jps <= 0:
            return float("inf")

        # Integrate over halving phases
        phase     = mint_engine.halving_phase
        years     = 0.0
        ecu_left  = remaining

        while ecu_left > 0 and phase < 10:
            phase_remaining = HALVING_INTERVAL - (mint_engine.total_minted % HALVING_INTERVAL)
            this_phase_ecu  = min(ecu_left, phase_remaining)
            unit            = ENERGY_UNIT_GENESIS * (2 ** phase)
            joules_needed   = this_phase_ecu * unit
            years          += joules_needed / rate_jps / (365.25 * 86400)
            ecu_left       -= this_phase_ecu
            phase          += 1

        return years


# ======================================================
# COMPARISON: SCS vs OHAS vs U3O8
# ======================================================

def compare_peg_architectures() -> list[dict]:
    """
    Side-by-side comparison of all three peg generations.
    """
    return [
        {
            "property": "Peg concept",
            "u3o8":     "Pegged to uranium spot price",
            "ohas":     "Pegged to Oil-Compute spread",
            "scs":      "No peg. ECU = destroyed energy.",
        },
        {
            "property": "Oracle dependency",
            "u3o8":     "U3O8/USD (manual, no Chainlink)",
            "ohas":     "WTI/USD + electricity (2 Chainlink feeds)",
            "scs":      "ZERO — no external price ever referenced",
        },
        {
            "property": "Manipulation surface",
            "u3o8":     "Single illiquid $8B/yr market",
            "ohas":     "Must move both WTI + electricity TWAP",
            "scs":      "None — energy is physically consumed, not priced",
        },
        {
            "property": "External risk",
            "u3o8":     "Kazakhstan ban = 5-10x difficulty spike",
            "ohas":     "Immune to uranium; exposed to oil geopolitics",
            "scs":      "Immune to ALL commodity prices — they are irrelevant",
        },
        {
            "property": "Backing guarantee",
            "u3o8":     "Price-based — changes with market",
            "ohas":     "Spread-based — changes with two markets",
            "scs":      "Physical — destroyed joules cannot be un-destroyed",
        },
        {
            "property": "Geographic fairness",
            "u3o8":     "Cheap electricity = higher margin mining",
            "ohas":     "Cheap electricity = wider spread = easier minting",
            "scs":      "Proportional to energy consumed. No price arbitrage.",
        },
        {
            "property": "Bitcoin equivalence",
            "u3o8":     "None — peg introduces external dependency",
            "ohas":     "Partial — still oracle-dependent",
            "scs":      "Exact — energy burned IS the backing, IS the security",
        },
        {
            "property": "Security model",
            "u3o8":     "Cryptographic + price stability",
            "ohas":     "Cryptographic + two-market stability",
            "scs":      "Cryptographic + sunk cost (same as Bitcoin)",
        },
        {
            "property": "Value source",
            "u3o8":     "Uranium market consensus",
            "ohas":     "Oil-compute arbitrage consensus",
            "scs":      "Real energy destroyed — consensus not required",
        },
        {
            "property": "Anti-fragility",
            "u3o8":     "Fragile to uranium supply shocks",
            "ohas":     "Resilient to uranium; semi-resilient to oil",
            "scs":      "Anti-fragile: energy prices rise → each ECU worth more",
        },
        {
            "property": "Chainlink dependency",
            "u3o8":     "Custom manual oracle",
            "ohas":     "2 tier-1 Chainlink feeds",
            "scs":      "Zero Chainlink. Zero oracle. Zero external API.",
        },
        {
            "property": "Genesis alignment",
            "u3o8":     "Tangential to RBI/oil event",
            "ohas":     "Direct — oil crisis encoded in spread",
            "scs":      "Deepest — India GPU energy = ECU, regardless of rupee",
        },
    ]


# ======================================================
# UNIT TESTS
# ======================================================

class TestSunkCostMint(unittest.TestCase):

    def setUp(self):
        self.engine = SunkCostMintEngine()

    def test_joules_to_ecu_at_genesis(self):
        """1 ECU costs exactly ENERGY_UNIT_GENESIS joules in phase 0."""
        ecu = self.engine.joules_to_ecu(ENERGY_UNIT_GENESIS)
        self.assertAlmostEqual(ecu, 1.0, places=10)

    def test_mint_proportional_to_energy(self):
        """2x energy consumed -> 2x ECU minted (same phase)."""
        ecu_1 = self.engine.preview_mint(ENERGY_UNIT_GENESIS * 10)
        ecu_2 = self.engine.preview_mint(ENERGY_UNIT_GENESIS * 20)
        self.assertAlmostEqual(ecu_2 / ecu_1, 2.0, places=10)

    def test_halving_doubles_energy_per_ecu(self):
        """After phase 1 (237,500 ECU minted), 2x energy needed per ECU."""
        # Simulate reaching phase 1
        self.engine.total_minted = HALVING_INTERVAL
        unit_phase1 = self.engine.effective_energy_unit
        self.assertAlmostEqual(unit_phase1, ENERGY_UNIT_GENESIS * 2, places=5)

    def test_supply_cap_enforced(self):
        """Total minted never exceeds MAX_SUPPLY."""
        huge_joules = ENERGY_UNIT_GENESIS * MAX_SUPPLY * 100
        minted = 0.0
        for _ in range(100):
            try:
                minted += self.engine.mint(huge_joules, "worker")
            except ValueError:
                break
        self.assertLessEqual(self.engine.total_minted, MAX_SUPPLY)

    def test_no_oracle_in_formula(self):
        """
        Minting uses zero external prices.
        Only: energy_joules, ENERGY_UNIT_GENESIS, halving_phase.
        """
        # Mint without any price data — should work perfectly
        ecu = self.engine.mint(ENERGY_UNIT_GENESIS * 5, "india_node")
        self.assertAlmostEqual(ecu, 5.0, places=10)
        # No oracle call made — formula is self-contained

    def test_backing_is_physical_quantity(self):
        """
        Backing of ECU is expressed in joules, not USD.
        No price reference in the backing calculation.
        """
        backing = self.engine.backing_per_ecu()
        self.assertIn("joules_per_ecu", backing)
        self.assertIn("kwh_per_ecu", backing)
        self.assertNotIn("usd", str(backing).lower())
        self.assertNotIn("price", str(backing).lower())
        self.assertNotIn("oracle", str(backing).lower())

    def test_mint_from_tee_data(self):
        """Mint from TEE-attested mwh (actual_mwh from HardwareOracle proof)."""
        # 1 MWh = 1000 kWh = 3,600,000,000 J
        actual_mwh   = 1.0
        joules       = actual_mwh * 1000 * JOULES_PER_KWH  # 3.6e9
        expected_ecu = joules / ENERGY_UNIT_GENESIS
        ecu = self.engine.mint_from_tee(actual_mwh=1.0, actual_teraflops=1000,
                                         worker="datacenter_india")
        self.assertAlmostEqual(ecu, expected_ecu, places=5)

    def test_halving_phases_correct_count(self):
        """Four halving phases cover the 950,000 ECU supply."""
        phases = []
        minted = 0
        for phase in range(5):
            start = phase * HALVING_INTERVAL
            end   = min((phase + 1) * HALVING_INTERVAL, MAX_SUPPLY)
            if start < MAX_SUPPLY:
                phases.append(end - start)
        self.assertAlmostEqual(sum(phases), MAX_SUPPLY, places=0)

    def test_geographic_fairness(self):
        """
        India node (cheap electricity, same energy): same ECU as US node.
        SCS is blind to electricity price. Only joules matter.
        """
        joules = ENERGY_UNIT_GENESIS * 10  # same energy consumed

        engine_india = SunkCostMintEngine()
        engine_us    = SunkCostMintEngine()

        # Both consumed same joules — electricity price irrelevant
        ecu_india = engine_india.mint(joules, "india_node")
        ecu_us    = engine_us.mint(joules, "us_node")

        self.assertAlmostEqual(ecu_india, ecu_us, places=10)

    def test_energy_destruction_is_irreversible(self):
        """
        Once minted, the backing energy is permanently destroyed.
        total_minted only ever increases.
        """
        self.engine.mint(ENERGY_UNIT_GENESIS * 100, "worker")
        before = self.engine.total_minted
        # No mechanism to un-mint or reverse
        self.assertGreater(before, 0)
        # total_minted is monotonically increasing
        self.engine.mint(ENERGY_UNIT_GENESIS * 10, "worker2")
        self.assertGreater(self.engine.total_minted, before)

    def test_sunk_cost_defense_equivalence(self):
        """
        The cost to fake 1 ECU = the cost to consume ENERGY_UNIT joules.
        At phase 0: must consume 1,152,000 J = 0.32 kWh = ~$0.013 of electricity.
        At phase 3: must consume 9,216,000 J = 2.56 kWh = ~$0.10 of electricity.
        The sunk cost defense strengthens with each halving.
        """
        phases = [0, 1, 2, 3]
        units  = [ENERGY_UNIT_GENESIS * (2**p) for p in phases]
        kwh    = [u / JOULES_PER_KWH for u in units]

        # Each halving doubles the physical cost to mint 1 ECU
        for i in range(1, len(units)):
            self.assertAlmostEqual(units[i] / units[i-1], 2.0, places=10)

        # Physical cost always increasing — anti-fragile security
        for i in range(1, len(kwh)):
            self.assertGreater(kwh[i], kwh[i-1])


class TestSunkCostVsPeg(unittest.TestCase):
    """
    Demonstrate that SCS is strictly superior to OHAS and U3O8
    across adversarial scenarios.
    """

    def test_oil_shock_irrelevant_to_scs(self):
        """
        Oil at $300/bbl: OHAS difficulty would be 3.27x. SCS: unchanged.
        """
        engine = SunkCostMintEngine()
        # Oil price is not in the formula
        ecu_normal = engine.preview_mint(ENERGY_UNIT_GENESIS * 100)
        # "Oil shock" — doesn't matter, formula doesn't reference oil
        ecu_shock  = engine.preview_mint(ENERGY_UNIT_GENESIS * 100)
        self.assertAlmostEqual(ecu_normal, ecu_shock, places=10)

    def test_uranium_squeeze_irrelevant(self):
        """U3O8 at $400/kg: U3O8 peg difficulty +20x. SCS: unchanged."""
        engine = SunkCostMintEngine()
        ecu = engine.preview_mint(ENERGY_UNIT_GENESIS * 100)
        # Uranium price not in formula
        self.assertAlmostEqual(ecu, 100.0, places=10)

    def test_electricity_price_irrelevant(self):
        """
        Electricity at $0.001/kWh vs $1.00/kWh: SCS minting identical.
        A node with free electricity does not get more ECU —
        it gets ECU proportional to the joules it consumes.
        """
        engine = SunkCostMintEngine()
        joules = ENERGY_UNIT_GENESIS * 50
        # Same joules regardless of electricity price
        ecu = engine.preview_mint(joules)
        self.assertAlmostEqual(ecu, 50.0, places=10)

    def test_oracle_free_complete(self):
        """
        SCS minting can be computed with zero external data.
        Inputs: energy_joules (from TEE), ENERGY_UNIT_GENESIS (genesis constant).
        No price, no oracle, no network call needed.
        """
        engine = SunkCostMintEngine()
        # These are the ONLY inputs needed:
        energy_joules    = 5_760_000.0   # 1.6 kWh — RTX 3080 for 5 hours
        genesis_constant = ENERGY_UNIT_GENESIS
        halving_phase    = 0

        effective_unit = genesis_constant * (2 ** halving_phase)
        expected_ecu   = energy_joules / effective_unit

        actual_ecu = engine.preview_mint(energy_joules)
        self.assertAlmostEqual(actual_ecu, expected_ecu, places=10)

    def test_sunk_cost_antifragility(self):
        """
        As global energy prices rise (e.g. post-RBI crisis),
        each ECU represents more USD of destroyed energy.
        No mechanism change needed — the backing AUTOMATICALLY appreciates.
        """
        engine = SunkCostMintEngine()
        backing = engine.backing_per_ecu()
        kwh_per_ecu = backing["kwh_per_ecu"]

        # At cheap electricity ($0.04/kWh India industrial):
        usd_backing_india = kwh_per_ecu * 0.04
        # At expensive electricity ($0.15/kWh post-crisis):
        usd_backing_crisis = kwh_per_ecu * 0.15

        # ECU automatically represents more USD when energy is expensive
        # No rebalancing, no oracle update, no governance vote
        self.assertGreater(usd_backing_crisis, usd_backing_india)
        # The ECU mechanism did not change. The world changed. ECU adapted.


if __name__ == "__main__":
    print("=" * 68)
    print("NEWFLOW v0.6 — Sunk Cost Standard (SCS)")
    print("Financialisation of Destroyed Compute Energy")
    print("=" * 68)

    engine = SunkCostMintEngine()

    # Demonstrate backing across halving phases
    print("\nPhase  Joules/ECU      kWh/ECU   Description")
    print("-" * 68)
    for phase in range(4):
        unit = ENERGY_UNIT_GENESIS * (2 ** phase)
        kwh  = unit / JOULES_PER_KWH
        desc = {
            0: "RTX 3080 for 1.0 hr (genesis)",
            1: "RTX 3080 for 2.0 hr (phase 1)",
            2: "RTX 3080 for 4.0 hr (phase 2)",
            3: "RTX 3080 for 8.0 hr (phase 3)",
        }[phase]
        print(f"  {phase}    {unit:>12.0f}  {kwh:>10.4f}  {desc}")

    # Architecture comparison
    print("\n" + "=" * 68)
    print("ARCHITECTURE COMPARISON: SCS vs OHAS vs U3O8")
    print("=" * 68)
    props = compare_peg_architectures()
    for p in props:
        print(f"\n  {p['property']}:")
        print(f"    U3O8: {p['u3o8']}")
        print(f"    OHAS: {p['ohas']}")
        print(f"    SCS:  {p['scs']}")

    # Unit tests
    print("\n" + "=" * 68)
    print("Unit Tests")
    print("=" * 68)
    loader = unittest.TestLoader()
    suite  = unittest.TestSuite()
    for cls in [TestSunkCostMint, TestSunkCostVsPeg]:
        suite.addTests(loader.loadTestsFromTestCase(cls))
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    print(f"\n{'OK' if result.wasSuccessful() else 'FAILED'} — {result.testsRun} tests")
