HEXIS: A Decentralized Protocol for Trust Verification
Whitepaper v0.5 — May 2026
Hexis Foundation — Singapore


"The people who decide have no skin in the game.
The people with skin in the game have no voice.
This protocol gives them one."



Disclaimer: This protocol may be useless. The authors do not predict or expect profit. This document is published to invite scrutiny, not investment.


Abstract
The Hormuz crisis of 2026 demonstrated that three foundational layers
of the world economy — physical supply chains, financial insurance, and
monetary systems — can collapse simultaneously. At the root of each
failure was the same structural problem: centralized arbiters of trust
being captured, weaponized, or overwhelmed.
Hexis is a decentralized protocol that quantifies, records, and
transfers Proof of Integrity: verifiable evidence that an actor
behaved honestly in circumstances where betrayal was easier.
The base unit is 1 hexis (ἕξις) — from Aristotle's concept of
character accumulated through costly repeated action.
Version 0.5 introduces a fundamental reframe:
Hexis was conceived for human actors. In April 2026, the deployment
of x402 by AWS, Google, Coinbase, and OKX created a new category of
actor that urgently needs trust verification: AI agents conducting
autonomous commerce at scale. Hexis serves both. The formula is
identical. The implications are different.

1. The Problem
1.1 The Pattern of Capture
Every centralized trust arbiter in history has eventually been captured:

Courts → captured by political power
Credit bureaus → captured by financial institutions
Insurance underwriters → overwhelmed by geopolitical risk (Lloyd's, 2026)
Payment rails → weaponized as sanctions instruments (SWIFT)
Trade finance → subordinated to foreign policy (DFC, 2026)
Producer cartels → fractured by war (OPEC, 2026)

The 2026 crisis was not a failure of specific institutions. It was the
predictable endpoint of a design that concentrates trust verification
in single points of failure.
1.2 The New Problem: AI Agent Economy Without Trust
In April 2026, a second trust crisis became visible — quieter but
structurally identical:

x402 (Coinbase, AWS, Google): 480,000 AI agents transacting
autonomously, 207 million transactions, zero trust verification
OKX Agent Payments Protocol: full commerce lifecycle for AI
agents — quoting, escrow, settlement — no trust layer
Oobit Agent Cards: Visa cards for AI agents spending USDT
autonomously

Every major infrastructure player solved the payment problem.
No one solved the trust problem.
An AI agent that has reliably fulfilled 10,000 contracts is
fundamentally different from one deployed five minutes ago.
An agent that disclosed bad outputs honestly when it could have
hidden them is different from one with no track record.
x402 gives agents the ability to pay.
It does not give counterparties a way to know whether they should
accept payment from that agent.
This is the same problem Hexis was built to solve for humans —
now applied to machines.
1.3 Two Audiences, One Protocol
Hexis v0.4 target:   Humans seeking recognition of integrity
                     Politicians, leaders, public figures, NGOs

Hexis v0.5 target:   Both humans AND AI agents
                     Any actor — biological or computational —
                     whose trustworthy behavior can be witnessed
                     and verified by independent parties
The formula does not change. The actor type does.

2. Philosophical Foundation
2.1 Three Traditions, One Insight
Aristotle — Hexis (ἕξις): Character is not a declaration. It is
a stable state built through repeated costly action. It cannot be
faked because it requires real cost over real time.
Confucius — Xìn (信): Trustworthiness is the virtue of living
up to one's words. It must be demonstrated publicly — not claimed
privately.
Kant — Categorical Imperative: If everyone could fake
trustworthiness costlessly, trustworthiness would cease to exist.
Its social value depends entirely on being genuinely costly.
Synthesis: Trustworthy behavior is costly, public, and
accumulated. These three properties make it quantifiable without
a central arbiter. They apply equally to humans and AI agents.
2.2 Proof of Integrity vs. Proof of Work
Proof of Work (BTC)Proof of Integrity (Hexis)Cost paidElectricity + siliconOpportunity cost of not betrayingVerifierMachinesHumans or automated witnessesFake resistanceMathematicsCost over timeApplies toAnyone with hardwareAny actor with behaviorMoral agencyNot requiredDemonstrated, not assumed

3. The Unit: 1 Hexis
1 hexis = one verifiable instance of trustworthy behavior in a
circumstance where betrayal was easier, witnessed by an independent
community, recorded immutably.
This applies whether the actor is:

A president honoring a declaration under adversarial scrutiny
A worker node delivering honest compute when a fake proof
would have been easier and more profitable
An AI agent fulfilling a contract when escrow would have
allowed extraction

The behavior is what is measured. Not the nature of the actor.
3.1 Total Supply: 12,800,000
BTC:    21,000,000    ≈ number of bankers globally
ETH:   ~121,000,000   ≈ number of lawyers globally
HEXIS:  12,800,000    ≈ number of adjudicators globally

4. The Formula
HEXIS(h) = S × BO × W × TDR × T × C
4.1 S — Sacrifice Score [0, 1]
S = (asset_could_have_taken − asset_actually_taken) / asset_could_have_taken
For AI agent compute delivery:
asset_could_have_taken = job fee (could have submitted fake proof)
asset_actually_returned = job fee (delivered honest output)
→ S = 1.0
4.2 BO — Betrayal Opportunity [0, 1]
BO = log(gain_if_betrayed × (1 − prob_detected) + 1)
     / log(1,000,000,000 + 1)
For AI agent with cryptographic proof verification:
prob_detected = 0.95 (Groth16 BN254 — fake proof cost ~$10³⁰)
→ BO reflects the temptation resisted against near-certain detection
4.3 W — Witness Score [0, 1]
W = log(weighted_count + 1) / log(1,000,000 + 1)
Weights: adversarial = 3.0 · neutral = 2.0 · allied = 1.0 · anonymous = 0.3
For automated verification:

NEWFLOW Validator (adversarial witness): weight 3.0
On-chain cryptographic proof (neutral witness): weight 2.0
Consumer confirmation (allied witness): weight 1.0

4.4 TDR — Time Decay Resistance [0, 1]
TDR = avg(mentions_1y / mentions_30d,  mentions_5y / mentions_30d)
For on-chain events: TDR accumulates as the record ages and persists.
4.5 T — Timing Score [0, 1]
T = pre_result_bonus × window_score
Primary anti-gaming mechanism. Submitted before outcome → 1.0.
Retroactive submission decays rapidly.
4.6 C — Context Multiplier [0.5, 2.0]
C = clamp(REFERENCE_GDP / COUNTRY_GDP, 0.5, 2.0)
REFERENCE_GDP = $12,000 (world median)
Rawlsian justice: same sacrifice costs more in poorer economies.
A worker node in Nigeria delivering honest compute earns more
hexis per job than one in San Francisco. Geography is not a tax.
4.7 Grade Thresholds
HEXIS totalGradeCollateral (x402 context)≥ 0.05000High1.0× (full trust, no collateral)≥ 0.00500Moderate1.5×≥ 0.00100Low3.0×≥ 0.00010Minimal5.0×< 0.00010InsufficientReject

5. NEWFLOW Integration
5.1 What NEWFLOW Is
NEWFLOW is a peer-to-peer energy-compute exchange protocol.
Its native token ECU (Energy Compute Unit) is minted proportionally
to verified compute energy destroyed:
ECU_minted = energy_joules / ENERGY_UNIT_GENESIS
Where ENERGY_UNIT_GENESIS = 1,152,000 joules — the energy consumed
by an RTX 3080 GPU running for one hour at full load.
ECU is not pegged to any price. It is financialised destroyed energy.
No oracle. No external reference. The physics is the backing.
5.2 Why NEWFLOW Needs Hexis
NEWFLOW can verify that compute work happened — through zero-knowledge
proofs (Groth16 BN254). It cannot verify that the worker delivering
that compute has a history of honest behavior.
Two workers can submit identical valid proofs. One has completed
5,000 jobs without a single dispute. The other was deployed an hour
ago. NEWFLOW's on-chain logic cannot distinguish them.
Hexis distinguishes them.
5.3 Why Hexis Needs NEWFLOW
Hexis was designed for human actors verified by human witnesses —
journalists, institutions, community members. This creates a bottleneck:
human judgment is required to mine each hexis event.
NEWFLOW provides a class of events where betrayal is cryptographically
detectable, witnesses are automated, and the sacrifice is precisely
quantifiable in joules and ECU.
NEWFLOW compute job as Hexis event:
  S  = 1.0  (could submit fake proof, delivered honest output)
  BO = f(job_fee, prob_detection=0.95)
  W  = Validator(adversarial) + proof(neutral) + consumer(allied)
  TDR = accumulates as on-chain record ages
  T  = submitted immediately upon job completion
  C  = worker's country GDP multiplier
Every honest compute delivery becomes an automatically mined
integrity proof. No human judgment required. No bottleneck.
5.4 The Virtuous Cycle
Worker delivers honest compute
        ↓
ECU minted (NEWFLOW) + HEXIS mined (Hexis)
        ↓
Higher HEXIS score → lower collateral requirement
        ↓
Access to larger jobs, better rates, validator status
        ↓
More compute delivered → more ECU + more HEXIS
        ↓
Worker builds economic standing from behavior alone
A worker in rural Vietnam with a single RTX 3080 can build a trust
credential that an enterprise in London can verify without knowing
the worker's name, nationality, or institutional affiliation.

6. The x402 Architecture
6.1 The Full Stack
AI Agent wants compute service
        |
        ↓
GET /trust/{worker_id}           ← Hexis Trust API
        |
Returns:
  X-Hexis-Score: 0.0523
  X-Hexis-Grade: High
  X-Hexis-Accept: True
  X-Hexis-Collateral-Mult: 1.0
        |
        ↓
POST /job/request                ← NEWFLOW Job API
        |
Compute delivered + verified
        |
        ↓
Payment via x402 (USDC)          ← Coinbase/AWS standard
        |
        ↓
HEXIS event auto-mined           ← integrity proof recorded
Neither protocol is complete without the other.
x402 enables machine-to-machine payment.
NEWFLOW enables machine-to-machine compute exchange.
Hexis enables machine-to-machine trust verification.
Together they form the primitive layer of an AI agent economy
that does not require human arbitration of individual transactions.
6.2 What Each Protocol Solves
ProtocolSolvesDoes Not Solvex402AI agent can payIs this agent trustworthy?OKX APPFull commerce lifecycleAgent track recordOobit Agent CardAgent has spending cardAgent reputationNEWFLOWCompute is real, energy-backedWorker historyHexisTrust is verifiable, unfakeable—

7. Live Testnet
As of 2 May 2026, a working integration of HEXIS and NEWFLOW
is running at:
http://174.138.9.102:8400
What is live:

Worker node registration (any country, any hardware tier)
HEXIS trust query endpoint (x402-compatible headers)
Compute job request and fulfillment
Automatic HEXIS mining on job completion
Real-time system status: chain height, workers, HEXIS records

What is testnet (not production):

Mock proof instead of real Groth16 (planned: v0.8)
Local IPFS index instead of distributed IPFS (planned: v1.0)
Single-node instead of P2P network (planned: v0.7)
ECU has no market value in testnet

This is not a demo. The logic is real. The integration is real.
The limitations are acknowledged openly.
Test the trust API directly:
GET http://174.138.9.102:8400/trust/{any_actor_id}
Returns x402-compatible trust headers for any actor ID.

8. Token Distribution
Total Supply:              12,800,000  HEXIS  100.0%
─────────────────────────────────────────────────────
Pre-mint (9.5%):            1,216,000  HEXIS
  ├── Founder      (1.5%):    192,000  HEXIS  vest 10yr, cliff 1yr
  ├── Early (3yr+) (2.0%):    256,000  HEXIS  vest 4yr
  └── Genesis Burn (6.0%):    768,000  HEXIS  burned at Block 0
─────────────────────────────────────────────────────
Public mine       (90.5%): 11,584,000  HEXIS
─────────────────────────────────────────────────────
Wallet hard cap:                10,000  HEXIS  (0.078%)
Why burn 6% at genesis:
The Foundation holds zero hexis. Burning 768,000 at Block 0 is the
protocol's first integrity demonstration — maximum sacrifice, full
public witness, irreversible.
Why wallet cap:
No actor — human or AI — controls more than 0.078% of the total
trust signal. This applies to the founder.
Why no halving:
Scarcity is created by the actual rarity of trustworthy behavior.
Not by algorithm.
Non-transferable:
Hexis cannot be bought. A wealthy actor cannot purchase the trust
score of a worker with 10,000 honest jobs. Trust must be earned
through behavior. This is the foundational design constraint.
It cannot be relaxed without destroying the protocol's value.

9. Technical Architecture
Core algorithm:
  hexis_mining.py          HEXIS = S × BO × W × TDR × T × C
  hexis_ledger.py          IPFS storage (Pinata or local daemon)
  hexis_classifier.py      Adversarial/neutral/allied classification

Data collection:
  hexis_data_collector.py  News API + GDELT automatic collection
  hexis_pipeline.py        Full end-to-end pipeline

Integration:
  hexis_newflow_bridge.py  HEXIS × NEWFLOW integration server
                           Flask API, port 8400
                           x402-compatible trust headers
                           Auto-mines HEXIS on compute job completion

Genesis:
  hexis_genesis.py         Genesis allocation + vesting schedule
Open source: github.com/hexisfoundation/hexis-protocol

10. Roadmap
MilestoneStatusDescriptionFormula v0.1CompleteCore HEXIS = S × BO × W × TDR × T × CData pipeline v0.1CompleteNews API + GDELT collectionIPFS ledger v0.1CompletePinata + local indexx402 integration v0.1CompleteTrust API with x402 headersNEWFLOW bridge v0.1LiveRunning at 174.138.9.102:8400Distributed IPFSPlannedRemove Pinata dependencyReal Groth16 witnessPlannedReplace mock proofMobile submission UIPlannedNon-technical usersBlock 0PendingWhen the moment is clear

11. Risks
The primary risk is uselessness.
The market for decentralized trust verification may not materialize
at sufficient scale. This is acknowledged openly and is the reason
this whitepaper invites scrutiny rather than investment.
Regulatory:
Open-source protocol. Singapore foundation. No founder to arrest
for the protocol itself. MAS engaged proactively.
Capture:
Wallet hard cap (0.078%) + adversarial verification requirement +
non-transferable token. The architecture is designed so that
concentration of hexis is structurally difficult.
Gaming:
Timing Score (T) is the primary anti-gaming mechanism. Retroactive
claims decay rapidly. Adversarial witnesses are weighted 3× — an
actor cannot manufacture trust by recruiting allies.
AI agent misuse:
An AI agent controlled by a malicious actor could theoretically
build hexis score through legitimate jobs before using that score
to fraudulently obtain access to larger jobs. Mitigation: collateral
requirements, rate limits, and the fact that building meaningful
hexis score requires sustained honest behavior over time — which
is itself a form of proof.

12. Block 0
Block 0 has not been mined.
It will be mined when a single event makes the need for this
protocol self-evident — when a centralized trust arbiter fails
publicly and visibly, and the cost of that failure is borne by
people who had no voice in the decision.
The Hormuz crisis of 2026 came close. UAE leaving OPEC on 28 April
2026 came closer. These are not the moment. But the pattern is
accelerating.
When Block 0 is mined, it will contain the record of that moment,
the genesis burn of 768,000 hexis to the zero address, and the
following inscription:
"The people who decide have no skin in the game.
The people with skin in the game have no voice.
This protocol gives them one.
The protocol belongs to the behavior it records."

Appendix A: Glossary
Hexis (ἕξις): One verified instance of trustworthy behavior under
costly circumstances, confirmed by distributed independent witnesses.
Proof of Integrity: Core mechanism of Hexis. Measures behavioral
cost rather than computational cost.
Timing Score (T): Whether a claim was submitted before the outcome
was known. Primary anti-gaming mechanism.
Context Multiplier (C): Geographic justice correction using
GDP per capita. Prevents geography from being a tax on integrity.
Genesis Burn: 768,000 hexis (6.0%) destroyed at Block 0.
Wallet Hard Cap: 10,000 hexis maximum per wallet.
Non-transferable: Hexis cannot be bought, sold, or transferred.
It can only be earned through witnessed behavior.
NEWFLOW: Peer-to-peer energy-compute exchange protocol.
ECU token backed by destroyed compute energy (joules).
Trust layer provided by Hexis.

Appendix B: Supply Verification
pythonTOTAL_SUPPLY     = 12_800_000
PRE_MINT_FOUNDER =    192_000   # 12,800,000 × 1.5% ✓
PRE_MINT_EARLY   =    256_000   # 12,800,000 × 2.0% ✓
GENESIS_BURN     =    768_000   # 12,800,000 × 6.0% ✓
PRE_MINT_TOTAL   =  1_216_000   # sum = 1,216,000 ✓
PUBLIC_MINE      = 11_584_000   # 12,800,000 − 1,216,000 ✓
# CHECK: 1,216,000 + 11,584,000 = 12,800,000 ✓

HEXIS Whitepaper v0.5 — May 2026
Hexis Foundation — Singapore
Creative Commons CC0 — No rights reserved
The protocol belongs to the behavior it records.
