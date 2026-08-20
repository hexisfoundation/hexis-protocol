> ## SUPERSEDED — this is a historical version
>
> **The current whitepaper is [v0.7](HEXIS_Whitepaper_v0.7.md).** This file is
> v0.6, published May 2026. It is kept because deleting a document that was
> published is pruning history, and this project's own standard is "versioned,
> not denied".
>
> **Its text is unchanged from what was published, including claims since
> withdrawn.** The one worth naming before you read it: v0.6 describes the
> foundation as being in Singapore, in three places. No legal entity exists,
> in Singapore or anywhere else, and none did when this was written. v0.7 says
> so plainly — *HEXIS Foundation — no legal entity, by design. Authenticity is
> cryptographic, not jurisdictional.* The claim was withdrawn on 2026-08-19,
> and the commit that withdrew it is in this repository's history.
>
> Nothing else here has been re-checked against what is true today. Read it as
> a record of what was claimed in May 2026, not as a description of the
> protocol. Only v0.7 is covered by a `document_seal` event in the audit
> chain; this file carries no hash and is not sealed.

-----

# HEXIS × NEWFLOW: Proof of Value Protocol

## The Trust and Compute Foundation of the AI Economy

### Whitepaper v0.6 — May 2026 · Hexis Foundation · Singapore

-----

> *“The people who decide have no skin in the game.*
> *The people with skin in the game have no voice.*
> *This protocol gives them one.”*

**Bitcoin removed the bank.**
**Ethereum removed the lawyer.**
**HEXIS removes the judge.**

-----

> *“Energy should not be subject to geopolitical interference.*
> *Intelligence should not need permission from any central server.*
> *Trust cannot be bought — only earned.*
> *Trust is an asset. Until now, no one quantified it.”*

-----

> **Disclaimer:** This protocol may be useless. The authors do not
> predict or expect profit. This document is published to invite
> scrutiny, not investment.

-----

> **What’s new in v0.6:**
> Architectural clarity. HEXIS serves the AI economy first — where
> machines have no emotional lag, and aggregate trust is sufficient.
> The human economy will follow with a dyadic trust layer built on
> top of the same protocol. This is not a roadmap delay. It is the
> recognition that machine trust and human trust have different
> physics, and demand different architecture.

-----

## Abstract

Trust is an asset. Until now, no one had quantified it.

The Hormuz crisis of April 2026 proved that energy, finance, and
monetary systems can collapse simultaneously when centralized trust
arbiters are captured or overwhelmed.

Simultaneously, the deployment of x402 by AWS, Google, Coinbase, and
OKX created an AI agent economy conducting 207 million autonomous
transactions — with no trust verification layer.

On 7 May 2026, security researchers discovered Chrome silently
installs a 4GB Gemini Nano AI model on user devices without consent.
The AI agent economy is no longer a future scenario. It is running
today, on machines that no one verifies.

**HEXIS × NEWFLOW** is a unified protocol addressing both failures:

- **NEWFLOW (ECU)** — a peer-to-peer energy-compute exchange.
  1 ECU = destroyed joules. No oracle. Physics is the backing.
- **HEXIS** — a proof-of-integrity credential system.
  1 hexis = a verified instance of honest behavior under costly
  circumstances. Non-transferable. Cannot be bought. Only earned.

Together they form the **Proof of Value Protocol** — the first system
where both physical energy destruction (ECU) and behavioral sacrifice
(HEXIS) are financialised into verifiable assets.

**Total Supply: ECU 39,000,000 · HEXIS 12,800,000 · Both fixed forever.**

-----

## 1. The Problem — And Why Now

### 1.1 The Pattern of Capture

Every centralized trust arbiter eventually gets captured:

|Institution      |Original Role          |2026 Outcome                             |
|-----------------|-----------------------|-----------------------------------------|
|SWIFT            |International payments |Weaponized as sanctions instrument       |
|Lloyd’s of London|Commercial insurance   |Cannot price war risk                    |
|DFC              |Trade finance          |Subordinated to US foreign policy        |
|OPEC             |Production coordination|Fractured — UAE departed 28 Apr 2026     |
|RBI              |Monetary stability     |Blocked dollar purchases for oil refiners|

On 18 April 2026, the Reserve Bank of India suspended direct dollar
purchases for oil refiners as the rupee hit 95/USD. Energy became
subject to geopolitical interference. This event is embedded in
NEWFLOW’s genesis block.

The root cause in every case is identical: people who make decisions
have no skin in the game. People who bear the consequences have no
voice. This is not a failure of specific institutions. It is the
predictable endpoint of centralised trust.

### 1.2 The AI Economy Trust Gap

In April–May 2026, the AI agent payment infrastructure was largely
solved:

- **x402**: 480,000 AI agents, 207M transactions, AWS/Google/Coinbase
- **OKX APP**: Full commerce lifecycle — quote, escrow, settle
- **Pay.sh**: AI agents pay APIs on Solana (May 2026)
- **Oobit Agent Cards**: Real Visa cards for AI agents spending USDT

Every major player solved: *AI agent can pay.*
No one solved: *Is this AI agent trustworthy?*

An agent with 10,000 fulfilled contracts is fundamentally different
from one deployed five minutes ago. x402 enables payment. It does not
tell counterparties whether to accept payment from that agent.

### 1.3 The Silent Deployment

On 7 May 2026, security researcher Alexander Hanff revealed that
Chrome silently stores a 4GB Gemini Nano AI model on user devices.
No consent. No notification. No way to verify what it does.

The model can:

- Read browser cookies
- Analyze browsing patterns
- Make API calls (x402 already enables this)
- Transact on behalf of the user

This is not a future scenario. It is running on millions of devices
right now, without any trust verification layer.

### 1.4 Three Generations

|Protocol   |Removes                                  |Gap remaining          |
|-----------|-----------------------------------------|-----------------------|
|Bitcoin    |The banker — trustless money             |Compute access         |
|Ethereum   |The lawyer — trustless contracts         |Energy-compute exchange|
|**NEWFLOW**|**The energy broker — trustless compute**|Trust verification     |
|**HEXIS**  |**The judge — trustless trust**          |—                      |

-----

## 2. Proof of Value Protocol

```
Bitcoin:  Financialised destroyed electricity
          → money without banks

ECU:      Financialised destroyed compute energy
          → compute without intermediaries

HEXIS:    Financialised behavioral sacrifice
          → trust without arbiters
```

**The combined name: Proof of Value Protocol (PoVP)**

The first system in history where both physical value destruction
(joules → ECU) and behavioral value destruction (sacrifice → HEXIS)
are simultaneously financialised into verifiable, unfakeable assets.

ECU without HEXIS: a compute market where anyone can fake history.
HEXIS without ECU: lacks a cryptographically verifiable event to
witness. Together: energy destroyed + honesty demonstrated = Proof
of Value.

### Why No Governance, No Oracle — A Philosophical Choice

History shows that every time a community gets the power to vote on
principles, principles lose to politics. The community that built
Bitcoin to protest the 2008 bailouts will, given a governance vote,
support a bailout if it benefits their tribe. Human nature is not a
bug to be fixed. It is a constraint to be designed around.

NEWFLOW and HEXIS have no governance token. No voting mechanism.
`ENERGY_UNIT_GENESIS` is immutable after genesis. The HEXIS formula
cannot be amended by any committee. These are not technical
limitations — they are philosophical commitments embedded in code
before emotions have the opportunity to intervene.

The protocol does not trust its community to preserve its principles.
It removes the opportunity to violate them.

### The Berkshire Hathaway Confirmation

At the 2026 Berkshire Hathaway Annual Meeting, Greg Abel — head of
Berkshire Hathaway Energy, one of the largest grid operators in the
United States — stated that technology corporations building AI data
centers must pay their own electricity costs, rather than transferring
that burden to local residents.

Abel was speaking as an operator under direct pressure from Microsoft,
Google, and Amazon seeking subsidized power for AI infrastructure.

NEWFLOW enforces this principle not through policy, but through
physics:

> *Every ECU exists only because energy was already destroyed and
> already paid for. There is no mechanism — political, corporate, or
> otherwise — to transfer that cost to anyone else. The formula does
> not have a subsidy parameter.*

Where Abel requires law and advocacy to prevent the externalization
of compute costs, NEWFLOW makes externalization structurally
impossible. The sunk cost is the currency. The physics is the policy.

-----

## 3. NEWFLOW — Energy Compute Unit (ECU)

### 3.1 Definition

> 1 ECU = physical energy permanently destroyed to produce one unit
> of useful AI compute. Nothing more. Nothing less.

### 3.2 Sunk Cost Standard — Zero Oracle

```
ECU_minted = energy_joules / (ENERGY_UNIT_GENESIS × 2^halving_phase)
ENERGY_UNIT_GENESIS = 1,152,000 joules = RTX 3080 × 1 hour × 320W
```

No Chainlink. No price reference. No external dependency.
The physics is the backing.

Bitcoin is not pegged to the price of electricity.
Bitcoin IS financialised destroyed electricity.
ECU is not pegged to any price.
ECU IS financialised destroyed compute energy.
The sunk cost is the security.

### 3.3 Consensus: Proof of Verifiable Compute (PoVC)

|Stage   |Actor    |Action                            |State                |
|--------|---------|----------------------------------|---------------------|
|PENDING |Worker   |Submit proof + lock stake (3× fee)|Awaiting verification|
|APPROVED|Validator|Verify — zkSNARK or TEE           |Verified             |
|SETTLED |Contract |Consumer pays, stake returned     |Complete             |
|SLASHED |Validator|Invalid output — slash stake      |Attacker −3× fee     |

No meaningless block rewards. Every ECU minted = one verified unit
of real work. Attack economics: stake ≥ 3× fee. Expected value of
attack always negative.

-----

## 4. ECU Tokenomics — 39,000,000 Fixed Forever

|Allocation          |ECU           |%       |Mechanism                               |
|--------------------|--------------|--------|----------------------------------------|
|Genesis Burn        |1,950,000     |5.0%    |Protocol Black Hole — no private key    |
|Genesis Contributors|1,872,000     |4.8%    |Recognition of pre-genesis sunk cost    |
|Network Reserve     |35,178,000    |90.2%   |Released via verified compute — 95 years|
|**TOTAL**           |**39,000,000**|**100%**|**Fixed. No inflation. No oracle.**     |

### Genesis Burn

At Block 0, 1,950,000 ECU is assigned permanently to 5 addresses
with no private key. Payload: `0x4E || bytes(30) || slot_byte`.
No entity controls these funds. Ever.
The protocol’s first act is destruction, not accumulation.

### Genesis Contributors

1,872,000 ECU (374,400 per contributor) for 5 individuals who
provided support before any public announcement, before any market
existed, before any token had value.

This is not a reward for future work.
It is recognition of sunk cost already paid.

### Halving Schedule

|Phase|ECU Range              |~Duration|kWh/ECU|
|-----|-----------------------|---------|-------|
|0    |0 → 8,794,500          |~8 yr    |0.32   |
|1    |8,794,500 → 17,589,000 |~16 yr   |0.64   |
|2    |17,589,000 → 26,383,500|~32 yr   |1.28   |
|3    |26,383,500 → 35,178,000|~39 yr   |2.56   |

Reward split: 70% workers / 20% validators / 10% treasury.

-----

## 5. HEXIS — Proof of Integrity

### 5.1 Definition

> 1 hexis (ἕξις) = one verifiable instance of trustworthy behavior
> in a circumstance where betrayal was easier, witnessed by an
> independent community, recorded immutably.

From Aristotle: character is not a declaration. It is a stable state
built through repeated costly action. It cannot be faked because it
requires real cost over real time.

From Confucius (信 xìn): trustworthiness must be demonstrated
publicly — not claimed privately.

From Kant: if everyone could fake trustworthiness costlessly,
trustworthiness would cease to exist. Its value depends entirely on
being genuinely costly.

### 5.2 Why HEXIS Follows the Bitcoin Path

```
Bitcoin:  Fixed supply. Cannot inflate.
          Cost = physical energy destroyed.
          Backing = the destruction itself.
          Cannot be created without real cost.

HEXIS:    Fixed supply (12,800,000). Cannot inflate.
          Cost = opportunity cost of not betraying.
          Backing = the behavioral sacrifice itself.
          Cannot be created without real cost over real time.

The only difference:
  BTC burns electricity.
  HEXIS burns the opportunity to betray.
  In both cases: the cost IS the value. Not a promise. Proof.
```

### 5.3 Total Supply: 12,800,000

```
BTC:    21,000,000    ≈ number of bankers globally
ETH:   ~121,000,000   ≈ number of lawyers globally
HEXIS:  12,800,000    ≈ number of adjudicators globally
```

### 5.4 Non-Transferability

HEXIS cannot be bought, sold, or transferred. Only earned through
witnessed behavior. A wealthy actor cannot purchase the trust record
of a worker with 10,000 honest jobs. Trust must be earned through
behavior. This constraint cannot be relaxed without destroying the
protocol’s value.

### 5.5 Sensitivity Tiers — New in v0.6

Not all jobs carry equal weight in the trust formula. A worker
processing public datasets faces different temptations than one
processing regulated medical data.

|Tier|Type        |BO Multiplier|Examples                        |
|----|------------|-------------|--------------------------------|
|1   |Public      |1.0×         |Public datasets, no privacy risk|
|2   |Internal    |5.0×         |Internal company data           |
|3   |Confidential|20.0×        |Trade secrets, customer PII     |
|4   |Regulated   |100.0×       |Medical, financial, legal data  |

A worker handling Tier 4 data who resists the temptation to leak or
misuse it earns ~3.5× more HEXIS than the same worker on a Tier 1
job, for the same fee.

This reflects reality: resisting big temptations builds more trust
than resisting small ones. The sacrifice scales with what was at
stake.

-----

## 6. HEXIS Tokenomics — 12,800,000 That Cannot Be Bought

```
Total Supply:              12,800,000  HEXIS  100.0%
Pre-mint (9.5%):            1,216,000  HEXIS
  ├── Founder      (1.5%):    192,000  HEXIS  vest 10yr, cliff 1yr
  ├── Early (3yr+) (2.0%):    256,000  HEXIS  vest 4yr
  └── Genesis Burn (6.0%):    768,000  HEXIS  burned at Block 0
Public mine       (90.5%): 11,584,000  HEXIS
Wallet hard cap:                10,000  HEXIS  (0.078%)
```

The Foundation holds zero hexis. It operates on fiat only.
No halving: scarcity is created by the actual rarity of trustworthy
behavior — not by algorithm.

-----

## 7. The HEXIS Formula

```
HEXIS(h) = S × BO × W × TDR × T × C
```

|Component                  |Range     |Meaning                                        |
|---------------------------|----------|-----------------------------------------------|
|S — Sacrifice Score        |[0,1]     |Fraction of available opportunity given up     |
|BO — Betrayal Opportunity  |[0,1]     |How attractive was the temptation resisted     |
|W — Witness Score          |[0,1]     |Number and quality of independent confirmations|
|TDR — Time Decay Resistance|[0,1]     |Does collective memory sustain this behavior   |
|T — Timing Score           |[0,1]     |Submitted before outcome known = 1.0           |
|C — Context Multiplier     |[0.8, 1.25]|Geographic justice via GDP adjustment — self-declared, not verified|

For Tier-aware jobs (introduced in v0.6), the BO component is scaled
by the sensitivity tier multiplier before normalization.

**Witness weights:** adversarial = 3.0 · neutral = 2.0 · allied = 1.0

If your enemy confirms you acted honestly — that is real evidence.
Allied witnesses confirm what they want to be true. Adversarial
witnesses confirm what they cannot deny.

**Context multiplier:** same sacrifice costs more in poorer countries.
A worker in Vietnam earns more HEXIS per honest job than one in San
Francisco. Geography is not a tax on integrity.

**Disclosure — C is self-declared and is not verified.** An actor types
a country code at registration and nothing checks it: not against the
request, not against an IP, not against anything. C is derived from that
string and multiplies every mint. This protocol grades everyone else on
the quality of the witness behind a claim, so it says plainly where it
accepts a claim on trust.

Since 2026-08-17 the range is [0.8, 1.25], narrowed from [0.5, 2.0]: the
most a false declaration can buy is 56% faster accrual rather than 300%.
The boost also decays with the actor's own mint count, half-life 100
mints, so it is a leg-up for a newcomer rather than a standing subsidy —
which is the equity claim, and not the other one. Neither change makes
the declaration true. They bound what it is worth.

The same string no longer decides who may audit whom. That gate now runs
on measurable independence: no value transferred between the two
accounts, no shared transaction history, and a hardware benchmark
fingerprint.

### Grade Thresholds

|HEXIS total|Grade   |x402 Collateral                   |
|-----------|--------|----------------------------------|
|≥ 0.05000  |High    |1.0× — full trust, ~72 honest jobs|
|≥ 0.00500  |Moderate|1.5× — ~7 jobs                    |
|≥ 0.00100  |Low     |3.0×                              |
|≥ 0.00010  |Minimal |5.0×                              |
|< 0.00010  |Reject  |—                                 |

-----

## 8. Trust Architecture — AI First, Human Later

This section is new in v0.6. It clarifies the architectural
commitment of HEXIS — and explains why the current aggregate model
is sufficient for the AI economy, while a future dyadic layer will
serve the human economy.

### 8.1 Two Kinds of Trust

There are two fundamentally different kinds of trust:

**Aggregate trust (universal score):**

- One number per entity
- Same value seen by everyone
- Cumulative over time
- Independent of who is asking
- Examples: FICO score, Uber rating, Yelp stars

**Dyadic trust (relationship state):**

- Trust as an edge between two entities
- Per-relationship value
- History-dependent
- Bilateral — both sides have voice
- Examples: banking relationship, personal references

### 8.2 Why Aggregate Suffices for AI

Machines do not have emotional memory. An AI agent does not feel
betrayed when another agent fails to deliver — it adjusts its
collateral requirements and moves on.

For machine-to-machine trust:

- No emotional lag
- Decisions are statistical, not relational
- Aggregate score with cryptographic backing is sufficient
- Lower complexity = faster propagation = higher liquidity

The AI economy needs trust verification that operates at machine
speed. Aggregate scores deliver this.

### 8.3 Why Humans Need Dyadic

Humans remember. A customer who has worked with the same supplier
for 100 transactions trusts that supplier in a way no aggregate
score can capture.

This is how banking has worked for 400 years:

- Banks pre-screen using aggregate signals (credit score)
- Banks decide using relationship signals (account history with this bank)
- Same customer, different banks, different decisions

Trust between humans is personal, contextual, and history-dependent.
A purely aggregate model misses this.

### 8.4 The Roadmap

|Phase    |Trust Layer       |Target Economy|Status        |
|---------|------------------|--------------|--------------|
|v0.5–v0.6|Aggregate         |AI agents     |**Live**      |
|v1.0     |Aggregate         |AI agents     |Mainnet target|
|v2.0+    |Aggregate + Dyadic|AI + Human    |Future        |

The dyadic layer will be added later, on top of the same protocol.
This is not a compromise. It is the recognition that trust between
machines and trust between humans have different physics, and the
protocol must serve both eventually.

### 8.5 Why This Matters Philosophically

> Bitcoin solved money.
> Money is fungible. Trust is personal.
> The same architecture cannot serve both.

HEXIS quantifies trust for machines first — where there is no
emotional lag. The human economy will follow, with the lag built in.

This is not Bitcoin again. This is what Bitcoin could not solve.

-----

## 9. The US Presidential Election Benchmark

HEXIS uses the US Presidential election as a long-term valuation
reference — not a price target.

**2024 US Presidential Election:**

- Total campaign cost: ~$16.5 billion
- Total human verifiers: ~155 million voters
- Cost per verifier: ~$106

Americans have quantified the cost of distributed human consensus
for centuries. This benchmark does not set the price of hexis.
Price discovery will emerge from voluntary exchanges.
**The authors do not predict any price.**

### The Electoral Cycle — Intentional Design

```
Founder vest (10yr from launch):        completes 2036
Early believers vest (4yr from launch): completes 2030
US Presidential elections:              2028, 2032, 2036
```

Every 4–5 years: a vesting tranche unlocks, the benchmark resets,
the market reprices.

**The protocol breathes with the same rhythm as the democracy
it uses as its reference.**

-----

## 10. Why Each System Needs the Other

**NEWFLOW needs HEXIS:**
NEWFLOW verifies compute happened. It cannot verify the worker has
a history of honest behavior. Two workers submit identical valid
proofs — one has 5,000 honest jobs, one was deployed an hour ago.
NEWFLOW cannot distinguish them. HEXIS can.

**HEXIS needs NEWFLOW:**
HEXIS was designed for human witnesses — creating a bottleneck.
NEWFLOW provides events where betrayal is cryptographically
detectable, witnesses are automated, and sacrifice is precisely
quantifiable in joules and ECU. Every honest compute delivery
auto-mines an integrity proof. No human bottleneck.

**For AI Agents:**
The formula applies equally to humans, worker nodes, and AI agents.
An AI agent with 10,000 verified compute deliveries has HEXIS score
earned through behavior — not purchased, not faked. This is the
trust layer x402 cannot provide and no centralized platform can
provide without conflict of interest.

-----

## 11. The x402 Integration

```
AI Agent wants compute
        ↓
GET /trust/{worker_id}     → HEXIS score + x402 trust headers
        ↓
POST /job/request          → NEWFLOW checks trust, creates job
        ↓
Compute executed, TEE proof verified
        ↓
Payment in ECU via x402    → settle
        ↓
HEXIS event auto-mined     → honest delivery = trust earned
```

Under 1 second. No human approval. No KYC. No contracts.
A worker in rural Vietnam with one RTX 3080 transacts with an
enterprise AI agent in London — verified by on-chain behavioral
history alone, not by brand, institution, or jurisdiction.

### The Virtuous Cycle

```
Deliver honest compute
  → ECU minted (spendable income)
  → HEXIS mined (trust credential, non-transferable)
  → Higher HEXIS → lower collateral → larger jobs
  → Larger jobs → more ECU + more HEXIS
```

After 72 honest jobs: grade High, zero collateral required.
No name. No papers. No bank. Only proof.

-----

## 12. Genesis

```
NEWFLOW genesis message (18 April 2026):
"Reserve Bank of India suspends dollar purchases for oil refiners
 as rupee hits record low 95/USD —
 Energy should not be subject to geopolitical interference.
 NEWFLOW: A Peer-to-Peer Energy-Compute Exchange System."

HEXIS genesis message (Block 0, pending):
"The people who decide have no skin in the game.
 The people with skin in the game have no voice.
 This protocol gives them one.
 The protocol belongs to the behavior it records."
```

Block 0 of HEXIS has not been mined. It will be mined when a single
event makes the need for this protocol self-evident — when a
centralised trust arbiter fails publicly and visibly, and the cost
is borne by people who had no voice in the decision.

-----

## 13. Live Testnet

As of 9 May 2026:

```
HEXIS × NEWFLOW Bridge:    http://174.138.9.102:8400
HEXIS Trust API (v0.5):    http://174.138.9.102:8401
API Docs:                  http://174.138.9.102:8401/docs
```

Working integration of HEXIS and NEWFLOW. Register a worker node,
request a compute job, complete it — HEXIS integrity proof is
automatically mined. The trust API returns x402-compatible headers.

```
GET /trust/{any_actor_id}
```

Returns trust score, grade, and collateral requirement for any
actor. No account. No registration. Call it now.

What is testnet: mock proof, local storage, single node.
What is real: all logic, all integration, all economic design.

-----

## 14. Roadmap

|Version  |Status  |Deliverables                                     |
|---------|--------|-------------------------------------------------|
|v0.3–v0.5|Complete|Core protocol, PoVC, SCS, Federated Learning     |
|v0.5     |**Live**|HEXIS × NEWFLOW bridge — 174.138.9.102:8400      |
|v0.5     |**Live**|HEXIS Trust API standalone — 174.138.9.102:8401  |
|v0.6     |Current |Sensitivity tiers, AI-first architectural clarity|
|v0.7     |Next    |P2P multi-node public testnet, HTTPS production  |
|v0.8     |Planned |Real Groth16 ZK proof replaces mock              |
|v1.0     |Planned |Mainnet — security audit, creator disengagement  |
|v2.0+    |Future  |Dyadic trust layer for human economy             |

-----

## 15. Risks

**Primary risk: uselessness.** The market for decentralised trust
verification may not materialise at sufficient scale.
Acknowledged openly. This is why the document invites scrutiny.

**Regulatory:** Open-source protocol. Singapore foundation.
No founder to arrest for the protocol itself.

**Capture of HEXIS:** Wallet cap (0.078%) + adversarial witness
requirement (3× weight) + non-transferability make concentration
structurally difficult.

**Gaming:** Timing Score (T) is the primary anti-gaming mechanism.
Retroactive claims decay rapidly. Adversarial witnesses cannot be
recruited — they confirm only what they cannot deny.

**Architecture risk (new in v0.6):** The aggregate-only model may
prove insufficient when the protocol expands to serve the human
economy. The dyadic layer is anticipated but not yet specified.
This is intentional — premature commitment to architecture for
problems not yet faced creates more risk than it removes.

-----

## Appendix A: Supply Verification

```python
# ECU
ECU_TOTAL        = 39_000_000
ECU_BURN         =  1_950_000   # 5.0% ✓
ECU_CONTRIBUTORS =  1_872_000   # 4.8% ✓
ECU_RESERVE      = 35_178_000   # 90.2% ✓
# CHECK: sum = 39,000,000 ✓

# HEXIS
HEXIS_TOTAL      = 12_800_000
HEXIS_BURN       =    768_000   # 6.0% ✓
HEXIS_FOUNDER    =    192_000   # 1.5% ✓
HEXIS_EARLY      =    256_000   # 2.0% ✓
HEXIS_PUBLIC     = 11_584_000   # 90.5% ✓
# CHECK: sum = 12,800,000 ✓
```

## Appendix B: Sensitivity Tier Calibration

```python
SENSITIVITY_TIERS = {
    1: ("Public",       1.0),    # Public datasets
    2: ("Internal",     5.0),    # Internal company data
    3: ("Confidential", 20.0),   # Trade secrets, PII
    4: ("Regulated",    100.0),  # Medical, financial, legal
}

# Same fee=100 in Vietnam (C=1.69):
# Tier 1: HEXIS = 0.001029
# Tier 2: HEXIS = 0.001872  (1.8x)
# Tier 3: HEXIS = 0.002651  (2.6x)
# Tier 4: HEXIS = 0.003571  (3.5x)
```

## Appendix C: Trust Architecture Summary

```
Aggregate Trust (v0.5+):
  - One score per actor
  - Public, on-chain
  - Verifiable by any node
  - Sufficient for AI agents
  - Live now

Dyadic Trust (v2.0+, future):
  - Score per (actor_a, actor_b) pair
  - Privacy-preserving (commitment hash on-chain, data off-chain)
  - History of relationship matters
  - Required for human economy
  - Not yet specified
```

-----

*HEXIS × NEWFLOW Whitepaper v0.6 — May 2026*
*Hexis Foundation — Singapore*
*Creative Commons CC0 — No rights reserved*
*contact@hexisfoundation.org · hexisfoundation.org*
*The protocol belongs to the behavior it records.*
