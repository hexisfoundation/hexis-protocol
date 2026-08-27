HEXIS × NEWFLOW: Proof of Value Protocol

The Trust and Compute Foundation of the AI Economy

Whitepaper v0.7 — July 2026

HEXIS Foundation — no legal entity, by design.
Authenticity is cryptographic, not jurisdictional.



"The people who decide have no skin in the game.
The people with skin in the game have no voice.
This protocol gives them one."



Bitcoin removed the bank.
Ethereum removed the lawyer.
HEXIS removes the judge.



"Energy should not be subject to geopolitical interference.
Intelligence should not need permission from any central server.
Trust cannot be bought — only earned.
Trust is an asset. Until now, no one quantified it."





Disclaimer: This protocol may be useless. The authors do not
predict or expect profit. This document is published to invite
scrutiny, not investment.





What's new in v0.7:
Integrity becomes bilateral: both sides lock ECU, slashing is
symmetric, collusion is capped. Detection becomes evidence: every
behavioral event is written to a tamper-proof, hash-chained audit
ledger — live on the testnet today. Positioning is answered on
three fronts — registries, constitutions, regimes — and the risk
chapter now contains what red-team demanded: the Loopring test, a
falsifiable Block 0 deadline, honest odds, and the judge that
remains.




Corrections of 2026-08-23

Every claim in this document that could be checked against the running code
was checked, one at a time, against a file and a line or against a live
response. Nineteen were false as run. They are corrected in place, each
marked and dated where it stands, because a document that quietly repaired
itself would be unable to show it had not repaired anything else.

The heaviest: sampling selection is choosable by whoever names a job (§3.3);
no stake-to-fee ratio is enforced anywhere (§3.3, §10, Appendix D); there is
no TEE, and the bridge writes that there is into every record (§15); four of
the formula's six factors have never taken a second value (§7); the wallet
cap is a constant nothing reads (§6, §19); and the ECU supply the code
enforces is not the one in §4. Three tables had collapsed into unreadable
runs of text, one of them the grade table.

The full inventory, including what passed, is in CORRECTIONS.md. Open items
with their closing conditions are in OPEN.md.


Source labels of 2026-08-28

A claim about this system and a claim about the world are not checkable the
same way, and until now this document did not say which was which.

Claims about the world carry an inline tag: a bracketed note opening with
the word **world**, then the source, then the date that source was read. They
are verifiable only against the source named, on the date stated. Everything untagged is a claim about this system, and is checkable
against the chain, the code, or a live response — the commands are in
VERIFY.md.

Eighteen tags cover the sixteen external claims the 2026-08-23 audit listed
as outside its reach; two of the sixteen are asserted in two places each.
Each was looked up rather than assumed, and the tag says what was found. Five
of them do not say what this document says:

  - the x402 agent and transaction counts (two places) — the sourceable
    figure is smaller, and is named in the tag
  - the RBI "suspended dollar purchases" line — not found in any primary
    source. It stays because it is quoted verbatim inside the NEWFLOW genesis
    block, which is immutable; the tag carries the correction instead
  - the 2024 election total — OpenSecrets projected $15.9B, not $16.5B
  - the zk-rollup's "99%" — the shutdown is real, that figure is not
    confirmed

A tag that reports a disagreement is doing its job. The rule going into v0.8
is the one this exercise earned: every external figure carries a source and
an as-of date, or it does not go in.



Abstract

Trust is an asset. Until now, no one had quantified it.

The Hormuz crisis of April 2026 proved that energy, finance, and
monetary systems can collapse simultaneously when centralized trust
arbiters are captured or overwhelmed. [world · UN News "Chokepoints and conflict", 2026-04; Wikipedia "2026 Strait of Hormuz crisis" · read 2026-08-28]

Simultaneously, the deployment of x402 by AWS, Google, Coinbase, and
OKX created an AI agent economy conducting 207 million autonomous
transactions — with no trust verification layer. [world · FIGURE NOT CONFIRMED — the sourceable figure is Coinbase's 69,000 active agents and 165M x402 transactions to late 2026-04 · read 2026-08-28]

On 7 May 2026, security researchers discovered Chrome silently
installs a 4GB Gemini Nano AI model on user devices without consent. [world · gHacks 2026-05-06 and ppc.land, reporting Alexander Hanff · read 2026-08-28]
The AI agent economy is no longer a future scenario. It is running
today, on machines that no one verifies.

HEXIS × NEWFLOW is a unified protocol addressing both failures:


NEWFLOW (ECU) — a peer-to-peer energy-compute exchange.
1 ECU = destroyed joules. No oracle. Physics is the backing.
HEXIS — a proof-of-integrity credential system.
1 hexis = a verified instance of honest behavior under costly
circumstances. Non-transferable. Cannot be bought. Only earned.


Together they form the Proof of Value Protocol — the first system
where both physical energy destruction (ECU) and behavioral sacrifice
(HEXIS) are financialised into verifiable assets.

Total Supply: ECU 39,000,000 · HEXIS 12,800,000 · Both fixed forever.
(ECU: see the correction opening §4. The mint engine running today
enforces a different ceiling.)


1. The Problem — And Why Now

1.1 The Pattern of Capture

Every centralized trust arbiter eventually gets captured:

|Institution        |Original Role                |2026 Outcome                             |
|-------------------|-----------------------------|-----------------------------------------|
|SWIFT              |International payments        |Weaponized as sanctions instrument       |
|Lloyd's of London  |Commercial insurance         |Cannot price war risk                    |
|DFC                |Trade finance                |Subordinated to US foreign policy        |
|OPEC               |Production coordination       |Fractured — UAE departed 28 Apr 2026     |
|RBI                |Monetary stability           |Blocked dollar purchases for oil refiners|
|msUSD / Accountable|Stablecoin trust verifier    |Provider withdrew — 90% depeg within hours|
|Polymarket vendor  |Front-end integrity          |Supply-chain breach — $2.94M phished via injected code|
|Circle             |Independent stablecoin issuer|Absorbed into federal supervision (OCC trust charter) to survive|

Sources for the last five rows: UAE announced its OPEC departure on 28 April 2026, effective 1 May [world · NPR and Al Jazeera 2026-04-28; US EIA "Today in Energy" · read 2026-08-28]. The remaining four are cited in the paragraphs below.

These are not historical analogies. In a single quarter, a
stablecoin lost 90% of its value the moment its trust provider
stepped away (msUSD) [world · 2026-06-20; the reserve-attestation provider Accountable terminated its agreement · read 2026-08-28]; a prediction market lost $2.94M not through
its own code but through a compromised third-party vendor
(Polymarket); and the largest regulated issuer sought a federal [world · 2026-06-25, $2.94M via a compromised third-party vendor; SecurityAffairs, crypto.news · read 2026-08-28]
trust-bank charter to survive competition from a 140-company
consortium (Circle vs OUSD). [world · Circle pressroom: final OCC approval for a national trust bank, 2026-07-10 · read 2026-08-28] Each failure has the same shape as the
institutional captures above — the trust arbiter is a single point,
and the people bearing the cost had no voice in it.

On 18 April 2026, the Reserve Bank of India suspended direct dollar
purchases for oil refiners as the rupee hit 95/USD. [world · the rupee at ~95/USD is corroborated; "suspended direct dollar purchases" is NOT FOUND in any primary source — Bloomberg 2026-04-20 reports the RBI *easing* forex curbs. Retained here because it is quoted verbatim in the NEWFLOW genesis block, which is immutable and is not edited · read 2026-08-28] Energy became
subject to geopolitical interference. This event is embedded in
NEWFLOW's genesis block.

The root cause in every case is identical: people who make decisions
have no skin in the game. People who bear the consequences have no
voice. This is not a failure of specific institutions. It is the
predictable endpoint of centralised trust.

1.2 The AI Economy Trust Gap

In April–May 2026, the AI agent payment infrastructure was largely
solved:


x402: 480,000 AI agents, 207M transactions, AWS/Google/Coinbase
      [world · FIGURE NOT CONFIRMED — Coinbase reported 69,000 active agents and 165M transactions to late 2026-04 · read 2026-08-28]
OKX APP: Full commerce lifecycle — quote, escrow, settle
Pay.sh: AI agents pay APIs on Solana (May 2026)
Oobit Agent Cards: Real Visa cards for AI agents spending USDT


Every major player solved: AI agent can pay.
No one solved: Is this AI agent trustworthy?

An agent with 10,000 fulfilled contracts is fundamentally different
from one deployed five minutes ago. x402 enables payment. It does not
tell counterparties whether to accept payment from that agent.

1.3 The Silent Deployment

On 7 May 2026, security researcher Alexander Hanff revealed that
Chrome silently stores a 4GB Gemini Nano AI model on user devices.
No consent. No notification. No way to verify what it does. [world · gHacks 2026-05-06; ppc.land. Hanff's evidence chain dates the download 2026-04-24 · read 2026-08-28]

The model can:


Read browser cookies
Analyze browsing patterns
Make API calls (x402 already enables this)
Transact on behalf of the user


This is not a future scenario. It is running on millions of devices
right now, without any trust verification layer.

1.4 The Machine Economy Is Here

The machine economy is no longer hypothetical. In Q2 2026, Keyrock
reported 176 million AI-agent transactions settling roughly $73
million — an average of 31 cents each — with 98% concentrated in a
single stablecoin. [world · Keyrock, "Who Pays the Agent?", with Coinbase, Tempo and Virtuals; CoinDesk 2026-05-21. The window is 2025-05 to 2026-04, not Q2 2026, and the USDC share is 98.6% · read 2026-08-28] In the same window, Mastercard and Chainlink
opened a direct on-ramp letting 3.5 billion cardholders buy crypto. [world · Mastercard press release, June 2025 — 2025, so not "the same window" as the Keyrock figure · read 2026-08-28]
Stablecoins are becoming the default settlement layer for autonomous
agents, and traditional finance is institutionalizing that default.

What this build-out assumes is a payment rail. What it omits is a
record of whether an agent behaved. A transaction can settle for 31
cents and still represent a betrayal — a job half-done, a
counterparty stranded, a promise broken. The missing layer is not
another rail. It is behavioral audit: an account of conduct that
survives even when the rail beneath it depegs, freezes, or is
subpoenaed. HEXIS is that layer. It does not move value; it records
whether the agents who moved it can be trusted again.

As of July 2026, this settlement default has legal footing: US
federal law now bars a central bank digital currency through 2030
while explicitly exempting open, permissionless, private dollar
tokens — codifying private stablecoins as the sanctioned
digital-dollar rail for the rest of the decade. [world · 21st Century ROAD to Housing Act; Senate 85-5 on 2026-06-22; bars a Fed CBDC to 2030-12-31 with an explicit carve-out for open, permissionless dollar tokens · read 2026-08-28] The payment layer is
not merely built; it is now legislated. What remains unlegislated,
and unbuilt at the neutral layer, is the record of whether the
agents moving that money behaved.

Figures are drawn from different measurement windows and providers
(x402 cumulative through May 2026; Keyrock Q2 2026 settlement
snapshot); they are directionally consistent, not the same series.

1.5 Three Generations

|Protocol|Removes                              |Gap remaining          |
|--------|-------------------------------------|-----------------------|
|Bitcoin |The banker — trustless money         |Compute access         |
|Ethereum|The lawyer — trustless contracts     |Energy-compute exchange|
|NEWFLOW |The energy broker — trustless compute|Trust verification     |
|HEXIS   |The judge — trustless trust          |—                      |

1.6 The Guardrail That Could Not Hold

In June 2026, a frontier AI model was withdrawn from public access
by government order within roughly ninety minutes, after a national
security review found it could compromise nearly all classified test
systems within hours. [world · a US Commerce Department order of 2026-06-12 gave 90 minutes to comply; IAPP · read 2026-08-28] In the weeks that followed, both the
government and the model's developer conceded in the open record
that no model can be made completely jailbreak-proof, and that the
operative safeguard is not a static guardrail but continuous
behavioral monitoring, defense-in-depth, and severity grading.
Access was later restored under conditions, leaving the commercial
question stated plainly: will enterprises trust a model that once
vanished without notice?

This is the clearest admission to date that centralized guardrails
fail, and that the fallback everyone reaches for — monitor behavior
continuously, grade severity, keep an auditable record — is
precisely the architecture HEXIS specifies. The difference is
custody. A lab monitoring its own model is a judge auditing itself.
HEXIS externalizes the record so that no single party owns the
verdict.


2. Proof of Value Protocol

```
Bitcoin:  Financialised destroyed electricity
          → money without banks

ECU:      Financialised destroyed compute energy
          → compute without intermediaries

HEXIS:    Financialised behavioral sacrifice
          → trust without arbiters
```

The combined name: Proof of Value Protocol (PoVP)

The first system in history where both physical value destruction
(joules → ECU) and behavioral value destruction (sacrifice → HEXIS)
are simultaneously financialised into verifiable, unfakeable assets.

ECU without HEXIS: a compute market where anyone can fake history.
HEXIS without ECU: lacks a cryptographically verifiable event to
witness. Together: energy destroyed + honesty demonstrated = Proof
of Value.

Why No Governance, No Oracle — A Philosophical Choice

History shows that every time a community gets the power to vote on
principles, principles lose to politics. The community that built
Bitcoin to protest the 2008 bailouts will, given a governance vote,
support a bailout if it benefits their tribe. Human nature is not a
bug to be fixed. It is a constraint to be designed around.

NEWFLOW and HEXIS have no governance token. No voting mechanism.
ENERGY_UNIT_GENESIS is immutable after genesis. The HEXIS formula
cannot be amended by any committee. These are not technical
limitations — they are philosophical commitments embedded in code
before emotions have the opportunity to intervene.

The protocol does not trust its community to preserve its principles.
It removes the opportunity to violate them.

The Berkshire Hathaway Confirmation

At the 2026 Berkshire Hathaway Annual Meeting, Greg Abel — head of
Berkshire Hathaway Energy, one of the largest grid operators in the
United States — stated that technology corporations building AI data
centers must pay their own electricity costs, rather than transferring
that burden to local residents.

Abel was speaking as an operator under direct pressure from Microsoft,
Google, and Amazon seeking subsidized power for AI infrastructure.

NEWFLOW enforces this principle not through policy, but through
physics:


Every ECU exists only because energy was already destroyed and
already paid for. There is no mechanism — political, corporate, or
otherwise — to transfer that cost to anyone else. The formula does
not have a subsidy parameter.



Where Abel requires law and advocacy to prevent the externalization
of compute costs, NEWFLOW makes externalization structurally
impossible. The sunk cost is the currency. The physics is the policy.


3. NEWFLOW — Energy Compute Unit (ECU)

3.1 Definition


1 ECU = physical energy permanently destroyed to produce one unit
of useful AI compute. Nothing more. Nothing less.



3.2 Sunk Cost Standard — Zero Oracle

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

3.3 Consensus: Proof of Verifiable Compute (PoVC)

|Stage   |Actor    |Action                            |State                |
|--------|---------|----------------------------------|---------------------|
|PENDING |Worker   |Submit proof + lock stake (see below)|Awaiting verification|
|APPROVED|Validator|Verify — re-execute σ% (PoSP)     |Verified             |
|SETTLED |Contract |Consumer pays, stake returned     |Complete             |
|SLASHED |Validator|Invalid output — slash stake      |Attacker −3× fee     |

No meaningless block rewards. Every ECU minted = one verified unit
of real work — vacuously so far: no ECU has been minted.

Attack economics, as designed: stake ≥ 3× fee, expected value of attack
always negative. CORRECTION, 2026-08-23: **no such ratio is enforced.**
`POST /stake/lock` takes both stake amounts from the request body and
validates only that they are positive. The collateral multiplier the trust
grade returns (1.0× to 5.0×) is reported to callers and read by nothing.
Appendix D states the same economics with a different constant — "floor
1.0×" — and neither is in force. Every argument in this document that rests
on a locked amount being proportional to a fee is, today, an argument about
a mechanism that is advisory.

Verification is Proof-of-Sampling (PoSP): the validator re-executes a
random σ% of completed jobs and compares outputs; any mismatch slashes
the worker's stake. The intent is that because a worker cannot know in
advance which jobs are sampled, executing every job honestly is the only
safe strategy.

CORRECTION, 2026-08-23, and it is the most serious in this document.
**The selection is predictable and choosable.** A job is sampled iff
`sha256(job_id + ":posp-sample") < σ`, `job_id` is supplied by the caller
as any string of 1–128 characters with no format check, and σ is published
at `/sampling/config`. Whoever names a job therefore decides whether it can
ever be audited. Measured: finding an id that will never be sampled takes
1.1 attempts on average. Against a caller who grinds ids, σ = 0.1 is not a
10% audit rate; it is 0%. The sampling module's own docstring names the
assumption it relies on — that `job_id` is a uuid4 — and nothing enforces
it. Until the selection uses entropy the caller does not control, the
safety argument above does not hold.
Verification therefore costs a fraction of the work — not the orders-of-
magnitude overhead of forcing each AI inference through a zero-knowledge
circuit (zkML), which in 2026 is slower and costlier than the compute it
would attest. The same sunk-cost, slash-on-fault economics that back ECU
also secure the proof: security is economic and self-sourced, not borrowed
from an external cryptographic oracle.


4. ECU Tokenomics — 39,000,000 Fixed Forever

|Allocation          |ECU           |%       |Mechanism                               |
|--------------------|--------------|--------|----------------------------------------|
|Genesis Burn        |1,950,000     |5.0%    |Protocol Black Hole — no private key    |
|Genesis Contributors|1,872,000     |4.8%    |Recognition of pre-genesis sunk cost    |
|Network Reserve     |35,178,000    |90.2%   |Released via verified compute — 95 years|
|**TOTAL**           |**39,000,000**|**100%**|**Fixed. No inflation. No oracle.**     |

CORRECTION, 2026-08-23. The table above is the design. It is not what the
code enforces. The mint engine that runs on the testnet caps total supply at
950,000 ECU (`scs_engine.py`, `MAX_SUPPLY`), and halves every 237,500 ECU
rather than at the boundaries printed below — a factor of roughly 37. The
bridge's genesis allocation does name 39,000,000, so the process boots with
one figure and mints against another. Neither has bound anything: zero ECU
has ever been minted. The two numbers must be reconciled before any is, and
until they are, "fixed forever" describes an intention rather than a
constraint in force. The Genesis Burn and Genesis Contributors rows have no
implementation at all; their 3,822,000 sits inside NETWORK_RESERVE.

Genesis Burn

At Block 0, 1,950,000 ECU is assigned permanently to 5 addresses
with no private key. Payload: 0x4E || bytes(30) || slot_byte.
No entity controls these funds. Ever.
The protocol's first act is destruction, not accumulation.

Genesis Contributors

1,872,000 ECU (374,400 per contributor) for 5 individuals who
provided support before any public announcement, before any market
existed, before any token had value.

This is not a reward for future work.
It is recognition of sunk cost already paid.

Halving Schedule

|Phase|ECU Range              |~Duration|kWh/ECU|
|-----|-----------------------|---------|-------|
|0    |0 → 8,794,500          |~8 yr    |0.32   |
|1    |8,794,500 → 17,589,000 |~16 yr   |0.64   |
|2    |17,589,000 → 26,383,500|~32 yr   |1.28   |
|3    |26,383,500 → 35,178,000|~39 yr   |2.56   |

Two defects in this table, recorded 2026-08-23 rather than quietly fixed.
The ECU boundaries are those of the 39,000,000 design, not of the 950,000
the code enforces. And the durations are internally inconsistent: a schedule
that doubles the energy per ECU each phase gives ~64 years for phase 3, not
~39. The published figures were chosen to sum to the 95 years named above,
which traces to a constant in a block-reward function the bridge never
calls. The kWh/ECU column is correct and does follow the doubling rule.

Reward split: 70% workers / 20% validators / 10% treasury. Coded, but only
the worker leg is distributed; the validator and treasury legs are computed
nowhere, and the function that would apply any of them has no caller
(noted 2026-08-23).


5. HEXIS — Proof of Integrity

5.1 Definition


1 hexis (ἕξις) = one verifiable instance of trustworthy behavior
in a circumstance where betrayal was easier, witnessed by an
independent community, recorded immutably.

"Witnessed by an independent community" is the definition, not yet the
implementation. Every record minted to date names the same three witnesses,
one of which is the process doing the minting, and one of which refers to a
TEE that does not exist. Open defect #4 in OPEN.md; disclosed here
2026-08-23 because a definition is the wrong place to be aspirational.



From Aristotle: character is not a declaration. It is a stable state
built through repeated costly action. It cannot be faked because it
requires real cost over real time.

From Confucius (信 xìn): trustworthiness must be demonstrated
publicly — not claimed privately.

From Kant: if everyone could fake trustworthiness costlessly,
trustworthiness would cease to exist. Its value depends entirely on
being genuinely costly.

5.2 Why HEXIS Follows the Bitcoin Path

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

5.3 Total Supply: 12,800,000

```
BTC:    21,000,000    ≈ number of bankers globally
ETH:   ~121,000,000   ≈ number of lawyers globally
HEXIS:  12,800,000    (a rhetorical echo, not a census — chosen fixed, like Bitcoin's 21M)
```

5.4 Non-Transferability

HEXIS cannot be bought, sold, or transferred. Only earned through
witnessed behavior. A wealthy actor cannot purchase the trust record
of a worker with 10,000 honest jobs. Trust must be earned through
behavior. This constraint cannot be relaxed without destroying the
protocol's value.

5.5 Sensitivity Tiers — New in v0.6

Not all jobs carry equal weight in the trust formula. A worker
processing public datasets faces different temptations than one
processing regulated medical data.

|Tier|Type        |BO Multiplier|Examples                          |
|----|------------|-------------|----------------------------------|
|1   |Public      |1.0×         |Public datasets, no privacy risk  |
|2   |Internal    |5.0×         |Internal company data             |
|3   |Confidential|20.0×        |Trade secrets, customer PII       |
|4   |Regulated   |100.0×       |Medical, financial, legal data    |

A worker handling Tier 4 data who resists the temptation to leak or
misuse it earns ~3.5× more HEXIS than the same worker on a Tier 1
job, for the same fee.

This reflects reality: resisting big temptations builds more trust
than resisting small ones. The sacrifice scales with what was at
stake.


6. HEXIS Tokenomics — 12,800,000 That Cannot Be Bought

```
Total Supply:              12,800,000  HEXIS  100.0%
Pre-mint (9.5%):            1,216,000  HEXIS
  ├── Founder      (1.5%):    192,000  HEXIS  vest 10yr, cliff 1yr
  ├── Early (3yr+) (2.0%):    256,000  HEXIS  vest 4yr
  └── Genesis Burn (6.0%):    768,000  HEXIS  burned at Block 0
Public mine       (90.5%): 11,584,000  HEXIS
Wallet hard cap:                10,000  HEXIS  (0.078%)  [NOT ENFORCED]
```

The Foundation holds zero hexis. It operates on fiat only.
No halving: scarcity is created by the actual rarity of trustworthy
behavior — not by algorithm.


7. The HEXIS Formula

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

Witness weights: adversarial = 3.0 · neutral = 2.0 · allied = 1.0
(a fourth type, anonymous = 0.3, exists in code and is not described here)

Disclosure, 2026-08-23 — what the six factors actually do. Across all 32
HEXIS records ever minted, four of them have never taken a second value:
S = 1.0, W = 0.1408, TDR = 0.1, T = 0.5. The only automated minting path
builds every event with identical inputs for those four, so in practice
HEXIS = BO × C × 0.007047. The formula is six factors; two of them move.
This is a property of the one path that mints, not of the formula, and it
is a defect in that path — but a reader is entitled to know that the table
above describes a design and not an observed distribution.

If your enemy confirms you acted honestly — that is real evidence.
Allied witnesses confirm what they want to be true. Adversarial
witnesses confirm what they cannot deny.

Context multiplier: same sacrifice costs more in poorer countries.
A worker in Vietnam earns more HEXIS per honest job than one in San
Francisco. Geography is not a tax on integrity.

Disclosure — C is self-declared and is not verified. An actor types a
country code at registration and nothing checks it: not against the
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

Grade Thresholds

|HEXIS total|Grade   |x402 Collateral|
|-----------|--------|---------------|
|≥ 0.05000  |High    |1.0×           |
|≥ 0.00500  |Moderate|1.5×           |
|≥ 0.00100  |Low     |3.0×           |
|≥ 0.00010  |Minimal |5.0×           |
|< 0.00010  |Reject  |—              |
|no record  |Unverified|— (nothing is known; not a judgement)|

This table was unreadable in the published .md until 2026-08-23 — it had
collapsed into a single run of text, which is the worst place in the
document for that to happen. Two further corrections made at the same time.
The multipliers are reported by the API and applied by nothing (§3.3). And
the "~72 honest jobs" that stood beside the High row has been removed: it
depends on fee, tier and country, none of which were stated, and recomputes
to anywhere between 49 and 82. The bridge and the Trust API also disagreed
at the bottom of this scale until 2026-08-23, the API returning Reject for
actors it had simply never seen.


8. Trust Architecture — AI First, Human Later

This section is new in v0.6. It clarifies the architectural
commitment of HEXIS — and explains why the current aggregate model
is sufficient for the AI economy, while a future dyadic layer will
serve the human economy.

8.1 Two Kinds of Trust

There are two fundamentally different kinds of trust:

Aggregate trust (universal score):


One number per entity
Same value seen by everyone
Cumulative over time
Independent of who is asking
Examples: FICO score, Uber rating, Yelp stars


Dyadic trust (relationship state):


Trust as an edge between two entities
Per-relationship value
History-dependent
Bilateral — both sides have voice
Examples: banking relationship, personal references


8.2 Why Aggregate Suffices for AI

Machines do not have emotional memory. An AI agent does not feel
betrayed when another agent fails to deliver — it adjusts its
collateral requirements and moves on.

For machine-to-machine trust:


No emotional lag
Decisions are statistical, not relational
Aggregate score with cryptographic backing is sufficient
Lower complexity = faster propagation = higher liquidity


The AI economy needs trust verification that operates at machine
speed. Aggregate scores deliver this.

8.3 Why Humans Need Dyadic

Humans remember. A customer who has worked with the same supplier
for 100 transactions trusts that supplier in a way no aggregate
score can capture.

This is how banking has worked for 400 years:


Banks pre-screen using aggregate signals (credit score)
Banks decide using relationship signals (account history with this bank)
Same customer, different banks, different decisions


Trust between humans is personal, contextual, and history-dependent.
A purely aggregate model misses this.

8.4 The Roadmap

|Phase    |Trust Layer       |Target Economy|Status        |
|---------|------------------|--------------|--------------|
|v0.5–v0.6|Aggregate         |AI agents     |**Live**      |
|v1.0     |Aggregate         |AI agents     |Mainnet target|
|v2.0+    |Aggregate + Dyadic|AI + Human    |Future        |

The dyadic layer will be added later, on top of the same protocol.
This is not a compromise. It is the recognition that trust between
machines and trust between humans have different physics, and the
protocol must serve both eventually.

8.5 Why This Matters Philosophically


Bitcoin solved money.
Money is fungible. Trust is personal.
The same architecture cannot serve both.



HEXIS quantifies trust for machines first — where there is no
emotional lag. The human economy will follow, with the lag built in.

This is not Bitcoin again. This is what Bitcoin could not solve.


9. Audit & Compliance — The Immutable Ledger

9.1 Detection Without a Record Is Theater

Detection without an indelible record is theater. The bridge
observes agent behavior — registration, job request, completion,
value transfer — but observation that can be edited after the fact
proves nothing. HEXIS closes this gap with an append-only,
hash-chained audit ledger.

Every behavioral event is written once and chained to its
predecessor by cryptographic hash. Altering any past entry breaks
every hash that follows it, so tampering is not merely discouraged —
it is visible. The chain can be verified end-to-end by anyone
(/audit/chain/verify), exported per actor (/audit/{actor_id}),
and sealed periodically under the foundation key.

This is what makes HEXIS usable as evidence. In a dispute, a
compliance review, or a regulator's inquiry, the question is never
"what does HEXIS say happened" but "can the record be trusted." A
hash chain answers that without asking anyone to trust HEXIS itself.
The ledger belongs to the behavior it records, not to the foundation
that hosts it.

One admission is owed here and made in full in §19: today the seal
key and the only node are the foundation's. Until an independent
node verifies the chain under a key the foundation does not hold,
the foundation is the residual judge.

9.2 Timing as Backbone

Of the six factors in the HEXIS formula — Sacrifice, Betrayal
Opportunity, Witness, Time Decay Resistance, Timing, Context — five
can in principle be argued. Only one cannot be forged after the
fact: timing.

A behavior's place in the sequence of all behaviors is fixed the
moment it is recorded. You cannot insert an honest act into your
past. You cannot pre-date a sacrifice. The hash-chained audit ledger
makes the timeline itself the proof — the same way Bitcoin's
proof-of-work is, at bottom, a method of timestamping. PoW is not
about energy; it is about ordering events so that no one can rewrite
which came first.

Timing is the most valuable thing the protocol holds. A reputation
score can be modeled; a position in an immutable sequence cannot be
counterfeited. This is why the audit chain is not a feature bolted
onto HEXIS — it is the backbone HEXIS stands on.


10. Counterparty Integrity — Bilateral Stake

The protocol's earlier design secured one side of a transaction. A
worker locked collateral and was slashed for non-delivery. But a
market has two sides, and a consumer who spams requests, refuses
valid work, or strands a worker is equally a betrayal — one a
one-sided design could not price.

v0.7 makes integrity bilateral. Before a job is accepted, both
parties lock collateral denominated in ECU. The amount each must
lock is intended to scale inversely with HEXIS standing, so a
high-trust actor locks little and a new or degraded actor locks more
— friction priced to reputation. CORRECTION, 2026-08-23: it does not.
The multiplier is computed and returned to callers; the lock endpoint takes
the amounts from the request and never consults it. The pricing described
in this paragraph is advisory, and so is the "Higher HEXIS → lower
collateral" step in §15's virtuous cycle. If either side betrays, slashing is
symmetric: the betrayer forfeits locked ECU and the honest party is
made whole.

Two consequences follow. First, ECU is the stake; HEXIS is never
staked — reputation cannot be pledged, because it is the score,
not the collateral. A betrayal therefore costs twice: ECU
immediately, and HEXIS standing permanently, which raises the price
of every future transaction. Second, collusion is bounded by a
pair-frequency cap: two addresses cannot manufacture trust by
transacting repeatedly with each other, because the cap throttles
same-pair activity inside a window.

This closes the loop the protocol is built on: audit detects,
stake enforces, reputation remembers.

(Principle locked. Endpoint surface and parameters finalize with
the v0.7 implementation; activation of the HEXIS-side consequence is
gated until Genesis Block 0.)


11. Independence from Registries, Constitutions, and Regimes

11.1 Independence from Registries

On 29 January 2026, ERC-8004 went live with MetaMask, the Ethereum
Foundation, Google, and Coinbase behind it — a standard for agent
[world · live on Ethereum mainnet 2026-01-29, eips.ethereum.org/EIPS/eip-8004; the four named backers are not confirmed in the sources read · read 2026-08-28]
identity and reputation. The obvious question is whether HEXIS
competes.

It does not, and the framing of "better" misunderstands both.
ERC-8004 and HEXIS proceed from different first principles. ERC-8004
treats reputation as portable attestation inside a standardized
registry. HEXIS treats trust as something that cannot be granted,
transferred, or standardized into existence — only earned through
witnessed behavior over time, and only spent as reduced friction.

HEXIS does not try to be better than ERC-8004. HEXIS starts from a
different principle. Those who agree with that principle choose
HEXIS. Those who do not choose ERC-8004. There is no winner — only
self-selection.

This is not modesty. It is the same stance the protocol takes
everywhere: no governance to capture, no oracle to corrupt, no
standards body to lobby. A protocol that competes can be beaten. A
protocol that self-selects cannot.

11.2 Independence from Constitutions

A parallel approach places trust inside the model: a written
constitution, dozens of pages of principles, curates behavior from
within. This is valuable and orthogonal. A constitution shapes what
an agent intends; HEXIS records what an agent did. One is internal
and static, fixed at training time; the other is external and
continuous, written by conduct after deployment. A constitution
cannot testify against the model that carries it. An external,
hash-chained record can. HEXIS does not replace internal alignment —
it supplies the witness that internal alignment cannot be.

11.3 Independence from Regimes

A third approach places trust inside the state: identity
verification, licensing, export control. This produces
trust-by-permission — revocable, jurisdictional, and captured by
definition (the same quarter that saw a model withdrawn by order saw
it restored by negotiation). HEXIS produces trust-by-behavior —
non-revocable because it was never granted, portable because it was
never licensed. Where a regime asks "who authorized you," HEXIS asks
"what have you done, and who that had reason to deny it confirmed
you did it."

11.4 Why Self-Audit Is Not the Answer

In mid-2026 a major foundation began using AI agents to audit its
own protocol, finding and patching a real vulnerability before
exploitation. [world · the Ethereum Foundation, blog.ethereum.org 2026-07-09, "The triage is the product"; the bug was CVE-2026-34219 in libp2p gossipsub · read 2026-08-28] This proves the detection layer is feasible — and
proves its limit. An organization auditing itself is a closed loop:
the same party defines the test, runs it, and grades the result.
HEXIS's witness weighting (adversarial 3.0 vs allied 1.0) exists
precisely to break that loop. Detection by a party with reason to
deny the finding is evidence; detection by oneself is housekeeping.
The neutral layer is not "who can detect" — everyone with resources
can now detect. It is "whose detection can a counterparty who trusts
neither party rely on." That question has one structural answer:
cross-adversarial verification that no single participant controls.


12. The "Say No" Architecture

A trustworthy system must be able to refuse. When the bridge is
saturated or a request cannot be honored, it does not pretend — it
returns 429 or 503 with Retry-After and declines. Honest
backpressure is itself a trust signal: a system that always says yes
is either lying or about to fail silently. Saying no — legibly and
on time — is how an honest counterparty behaves, and the protocol
holds itself to the standard it measures in others.


13. The US Presidential Election Benchmark

HEXIS uses the US Presidential election as a long-term valuation
reference — not a price target.

2024 US Presidential Election:


Total campaign cost: ~$16.5 billion
Total human verifiers: ~155 million voters
Cost per verifier: ~$106

[world · OpenSecrets projected $15.9 billion for the 2024 federal election, not $16.5 billion; at $15.9B over ~155M voters the cost per verifier is ~$103 · read 2026-08-28]


Americans have quantified the cost of distributed human consensus
for centuries. This benchmark does not set the price of hexis.
Price discovery will emerge from voluntary exchanges.
The authors do not predict any price.

The Electoral Cycle — Intentional Design

```
Founder vest (10yr from launch):        completes 2036
Early believers vest (4yr from launch): completes 2030
US Presidential elections:              2028, 2032, 2036
```

Every 4–5 years: a vesting tranche unlocks, the benchmark resets,
the market reprices.

The protocol breathes with the same rhythm as the democracy
it uses as its reference.


14. Why Each System Needs the Other

NEWFLOW needs HEXIS:
NEWFLOW verifies compute happened. It cannot verify the worker has
a history of honest behavior. Two workers submit identical valid
proofs — one has 5,000 honest jobs, one was deployed an hour ago.
NEWFLOW cannot distinguish them. HEXIS can.

HEXIS needs NEWFLOW:
HEXIS was designed for human witnesses — creating a bottleneck.
NEWFLOW provides events where betrayal is cryptographically
detectable, witnesses are automated, and sacrifice is precisely
quantifiable in joules and ECU. Every honest compute delivery
auto-mines an integrity proof. No human bottleneck.

For AI Agents:
The formula applies equally to humans, worker nodes, and AI agents.
An AI agent with 10,000 verified compute deliveries has HEXIS score
earned through behavior — not purchased, not faked. This is the
trust layer x402 cannot provide and no centralized platform can
provide without conflict of interest.


15. The x402 Integration

```
AI Agent wants compute
        ↓
GET /trust/{worker_id}     → HEXIS score + x402 trust headers
        ↓
POST /job/request          → NEWFLOW checks trust, creates job
        ↓
Compute executed  [see the correction below: there is no TEE]
        ↓
Payment in ECU via x402    → settle
        ↓
HEXIS event auto-mined     → honest delivery = trust earned
```

CORRECTION, 2026-08-23. **There is no TEE and no TEE proof.** The step above
described one for as long as this diagram has existed, and worse, the bridge
writes the sentence "TEE proof verified by validator." into the description
of every HEXIS record it mints, and names "On-chain TEE Proof" as one of the
three witnesses. Thirty-two rows in the audit chain assert a verification
that never happened. The chain is append-only, so those rows stand; what can
change is that no further row is written this way. The payment step is also
not built — escrow has no public funding path.

Under 1 second for the trust lookup, measured. The full cycle above has never
been timed end to end and contains a step that does not exist.
No human approval. No KYC. No contracts.
A worker in rural Vietnam with one RTX 3080 transacts with an
enterprise AI agent in London — verified by on-chain behavioral
history alone, not by brand, institution, or jurisdiction.

The Virtuous Cycle

```
Deliver honest compute
  → ECU minted (spendable income)
  → HEXIS mined (trust credential, non-transferable)
  → Higher HEXIS → lower collateral → larger jobs
  → Larger jobs → more ECU + more HEXIS
```

After ~72 honest jobs: grade High — minimum collateral, 1.0×.
No name. No papers. No bank. Only proof.


16. Genesis

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


17. Live Testnet

As of July 2026 (v0.7):

```
HEXIS × NEWFLOW Bridge (v0.8.0):   https://bridge.hexisfoundation.org
HEXIS Trust API (v0.8.0):          https://api.hexisfoundation.org
API Docs:                          https://api.hexisfoundation.org/docs
```

All endpoints are served over HTTPS via Cloudflare.

Working integration of HEXIS and NEWFLOW. Register a worker node,
request a compute job, complete it — HEXIS integrity proof is
automatically mined. The trust API returns the x402 header *values*, as a
JSON object named `x402_headers` in the response body — corrected here
2026-08-23, having said "returns x402-compatible headers" since v0.5.
Neither service sets an actual HTTP response header, so a client that reads
response headers gets nothing and must read the body instead.

```
GET /trust/{any_actor_id}
```

Returns trust score, grade, and collateral requirement for any
actor. No account. No registration. Call it now.

New in v0.7: the audit chain is live — verify it end-to-end
(/audit/chain/verify) or export any actor's record
(/audit/{actor_id}).

What is testnet: mock proof, local storage, single node.
What is real: all logic, all integration, all economic design.


18. Roadmap

|Version  |Status  |Deliverables|
|---------|--------|------------|
|v0.3–v0.5|Complete|Core protocol, PoVC, SCS. *Federated Learning was listed here and has no implementation — corrected 2026-08-23*|
|v0.6     |Complete|Sensitivity tiers; AI-first architectural clarity; HTTPS production (Cloudflare subdomains)|
|v0.7     |Current |Counterparty Integrity / bilateral stake (consumer+worker, symmetric slashing, pair-frequency cap); Audit & Compliance layer (tamper-proof hash chain) — live; positioning vs registries, constitutions, regimes; red-team hardening|
|v0.8     |Partly shipped|Second independent node (non-foundation key) — *not started*. Severity Tiers calibrated on a public incident corpus (Q2 2026) — *not started*. Proof-of-Sampling — ***deployed and live at σ=0.1 since 2026-08-17***, on a mock workload, with the selection defect corrected in §3.3. Quorum and validator stake are deployed at inert settings|
|v1.0     |Planned |Mainnet — security audit, creator disengagement|
|v2.0+    |Future  |Dyadic trust layer for the human economy|


19. Risks

Primary risk: uselessness. The market for decentralised trust
verification may not materialise at sufficient scale.
Acknowledged openly. This is why the document invites scrutiny.

Regulatory: Open-source protocol. No legal entity, by design.
Authenticity is cryptographic, not jurisdictional.
No founder to arrest for the protocol itself.

Capture of HEXIS: non-transferability makes concentration structurally
difficult, and that one is real — there is no transfer path in the code at
all. CORRECTION, 2026-08-23, for the other two named here since v0.3.
The wallet cap is not enforced anywhere: it is a constant, and three
separate comments assert it is applied "at the ledger level", where no such
check exists. The "adversarial witness requirement" is not a requirement —
the minimum-witness gate counts witnesses without regard to type, and the
minting path supplies its own. Of the three defences listed, one is built.

Gaming: Timing Score (T) is the intended primary anti-gaming mechanism.
Retroactive claims decay rapidly by design.

CORRECTION, 2026-08-23. T has held the single value 0.5 — its
"outcome not yet confirmed" default — in every record the protocol has ever
minted, because the automated path never supplies a result timestamp. It
has never discriminated between anything. The claim that adversarial
witnesses cannot be recruited is also wrong as built: the minting path
hardcodes its own adversarial witness, and that witness is the minting
process. The argument in §9.2 about position in a hash-chained sequence is
separate, and does hold.

Architecture risk (new in v0.6): The aggregate-only model may
prove insufficient when the protocol expands to serve the human
economy. The dyadic layer is anticipated but not yet specified.
This is intentional — premature commitment to architecture for
problems not yet faced creates more risk than it removes.

The Three Questions That Decide This Protocol

A first-generation zk-rollup with pioneering technology and hundreds
of millions in peak value shut down in 2026 after its total value
collapsed 99%. [world · Polygon zkEVM shut down 2026-07-01 with peak TVL of roughly $187M–$250M (CryptoTimes); the 99% figure is not confirmed in the sources read · read 2026-08-28] Its own postmortem named three causes: no users, an
architecture that could not connect to the surrounding ecosystem,
and a team without business development. Being technically first did
not save it. HEXIS carries all three risks and must answer them in
the open:


Who is the first real user, before Block 0? "The incident
will bring users" is the exact illusion that killed the protocol
above. The target is one real integration or tester before
Block 0 — not after.
Where does it plug in? The trust API returns x402-compatible
headers; the integration point must exist inside an actual agent
framework, not only at hexisfoundation.org. An architecture that
cannot connect is limited at the foundation, as the zk-rollup was
without an execution environment.
How decentralized, truly? Who runs a node other than the
founder? Who controls upgrades? Until the answer is more than
one, the only moat — real decentralization — does not yet exist,
and this document says so.


Block 0 Is Time-Bound

Block 0 mines when a centralized trust arbiter fails publicly and
the cost falls on people who had no voice (§16). Three independent
methods — the acceleration of premises, the capability-to-incident
cycle (~one year, as with the DAO), and the agent money-flow curve —
converge on a central estimate of mid-2027; earlier if the AI
bubble breaks early, as late as 2028 if guardrails improve. This is
a prediction, and predictions that cannot fail have no value. The
commitment: if no qualifying event occurs by the end of 2028 —
the late bound of the estimate — the timing thesis is wrong and
this chapter must be rewritten, not quietly reinterpreted. The premises are accumulating fast (a model
withdrawn by the state; a stablecoin depegged by a departing
verifier; a market drained through a third party), but accumulation
of premises is not the trigger, and the protocol will not pretend
it is.

What Success Actually Means

The authors hold no illusion about odds, and state them rather than
imply them:


Becoming a widely adopted trust layer: 1–2%.
Surviving as a niche protocol with real users and a few
integrations: 5–8%.
Having the thesis proven correct and the ideas absorbed — even by
others: 30–40%.


The three questions above are the lever that moves 1–2% toward the
higher end. By the authors' own definition — build because it is
right, release the outcome — success at the level of conduct is
already met: the work was done honestly, in the open, and given away
under MIT — free to use, free to build on, with the authors' name
carried forward. Everything beyond that is not owed.

The Judge That Remains

"HEXIS removes the judge" is an aim, not yet a fact. Today the audit
chain is sealed under the foundation key and replicated on a single
foundation-hosted node (Appendix D). Until an independent node
verifies the chain end-to-end under a key the foundation does not
hold, the foundation is the residual judge. This is the
highest-priority gap before mainnet, ranked above proof-system
upgrades, because it is the difference between the protocol's
central claim being true and being aspirational.


Appendix A: Supply Verification

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

Appendix B: Sensitivity Tier Calibration

```python
SENSITIVITY_TIERS = {
    1: ("Public",       1.0),    # Public datasets
    2: ("Internal",     5.0),    # Internal company data
    3: ("Confidential", 20.0),   # Trade secrets, PII
    4: ("Regulated",    100.0),  # Medical, financial, legal
}

# Same fee=100 in Vietnam. CORRECTED 2026-08-23: the figures below were
# computed at C=1.69, which the [0.8, 1.25] clamp has made unreachable
# since 2026-08-17. At the live cap of C=1.25 the same four jobs yield
# 0.000761 / 0.001384 / 0.001961 / 0.002641 — about 35% lower. The tier
# RATIOS are unaffected by C and still hold.
# Historical figures, at C=1.69:
# Tier 1: HEXIS = 0.001029
# Tier 2: HEXIS = 0.001872  (1.8x)
# Tier 3: HEXIS = 0.002651  (2.6x)
# Tier 4: HEXIS = 0.003571  (3.5x)
```

Appendix C: Trust Architecture Summary

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

Appendix D: Stress Test — The 1% Adversary

The network has no Byzantine threshold. It has a price list.

The 1.0× floor. stake = fee × multiplier, floor 1.0×. Betraying
a job with fee F gains at most F and forfeits at least F in slashed
stake, plus all HEXIS standing — ~72 honest jobs of sunk behavior.
EV < 0 at every grade, every job size. Farming trust to defect on a
larger job demands a larger stake. There is no point in the
parameter space where rational defection pays.

Collusion. A ring of k addresses still destroys real energy per
fake job, and its witnesses are all allied (weight 1.0 vs 3.0
adversarial), so HEXIS yield stays minimal. Known limit: k addresses
hold k(k−1)/2 pairs — rotation dilutes the pair-frequency cap. The
defense against rings is cost-per-HEXIS, not the cap.

Sybil. Identities are free; each starts Unverified — rejected,
or admitted at 5.0× collateral. Attack capacity is capital in
joules, not identities.

At 1%. Each adversarial identity needs ~72 honest jobs to reach
a position worth betraying, then defects once and self-liquidates.
Steady-state betrayal rate ≈ f/73 ≈ 0.014% of jobs — each fully
collateralized, the honest party made whole from the slashed stake.
Griefing is a donation to its victims.

Honest limits.


Made-whole holds only while damage ≤ fee. A Tier 4 leak can
inflict damage far beyond the fee. Pricing that is the work of
Severity Tiers (v0.8), calibrated on a public incident corpus
(Q2 2026) and refined on protocol data as it accrues.
The entire argument assumes valid proofs. The testnet runs a
mock; Proof-of-Sampling replaces it — the validator re-executes a
random σ% of jobs and slashes on mismatch. The check is economic,
not zkSNARK: forcing every AI job through a proof circuit (zkML) costs
far more than the work itself, and the same slashing that secures the
stake secures the sampling. What sampling does not yet have is a second
pair of eyes. Its two defences against a dishonest validator — a quorum
of independent validators that must agree before anyone is condemned,
and a stake the validator forfeits for a verdict contradicting that
agreement — are implemented and deployed, but held at inert settings.
They cannot be switched on honestly while the foundation holds every
validator key: one hand casting two votes is not a quorum. That gate
opens when an independent operator runs a node, not when more code is
written.
Tolerance to adversarial actors is economic and high. Tolerance
to adversarial infrastructure is zero: single node, foundation-
hosted, not yet independently replicated. A second node run by the
foundation would not repair this. By the standard §11.4 sets for
everyone else it would be housekeeping — the same party defining
the test, running it, and grading the result. Replication counts
only under a key the foundation does not hold.
The Context multiplier (C ∈ [0.8, 1.25]; this line said [0.5, 2.0] until
2026-08-23, contradicting §7 in the same document) raises HEXIS yield in
lower-GDP jurisdictions by design, so integrity is not taxed by
poverty. The same lever is an attack surface: domicile
infrastructure in a high-C jurisdiction and mine reputation
faster. The protocol does not yet verify geographic claims
cryptographically — until it does (candidate mitigations:
energy-grid attestation, latency triangulation, capping C's
marginal contribution per actor), C is trusted input, and
trusted input is what the protocol elsewhere refuses.
The protocol's public surfaces sit behind a single vendor. DNS,
TLS termination, and edge filtering run on Cloudflare. One more
thing sits there, and it is described narrowly on purpose: the name
@hexis was reserved on Cloudflare Wallets on 2026-08-05. That
product has not launched. The reservation exposes no send, no
receive and no balance — nothing can be paid to it today, and the
protocol has no funding path through it or anywhere else. It is a
name held against a future service, and calling it a payment handle
would describe a surface that does not exist. Today the dependency
is convenience, not identity: an actor is its key, and the handle is
a distribution surface the protocol does not depend on. It becomes
a constraint at any of three triggers. (a) An actor cannot transact,
or cannot be
resolved, without the handle — identity would have migrated into a
namespace the foundation does not own. (b) Restoring service without
Cloudflare takes longer than 24 hours. (c) A settlement path can be
frozen by the vendor. (a) and (c) do not hold. (b) is unmeasured:
no failover has been exercised, so the restore time is unknown, and
an unmeasured dependency is recorded here as open rather than
passed. §11's standard applies to infrastructure as much as to
registries — a dependency the protocol could not survive is a judge
under another name.


The exposure is not the fraction of dishonest actors. It is the
gaps above — versioned, not denied.


Check that this copy is the published one

No hash is printed below. A document cannot carry its own hash: writing the
number in would change the number, and any value that survived that would be
describing some other version of this page. So the hash is kept outside the
document, in the audit chain, where it is signed with a key that is not on
the server.

    curl -s https://hexisfoundation.org/HEXIS_Whitepaper_v0.7.md | sha256sum
    curl -s https://bridge.hexisfoundation.org/audit/HEXIS_Whitepaper_v0.7.md

On macOS and the BSDs there is no sha256sum; the first command ends
`| shasum -a 256` there. It is spelled out because a verification
instruction whose first command answers "command not found" has not
been run, and an instruction nobody can run is worth exactly what a
verifier nobody could download was worth.

The newest document_seal event in the second response names the sha256 of the
bytes served by the first. They should match. To check that the chain those
events sit in has not been rebuilt — and that the signature over it is real —
run the verifier from the repository. It needs no key, no account and no
permission from us:

    python3 verify_audit_chain.py

Two things worth being clear about. This .md file is the document; the page at
/whitepaper.html is a viewer that fetches it, and the hash does not cover the
viewer. And a seal proves what was published, never when it was written — the
event is evidence from the moment it was recorded onward. That last clause
used to end "and nothing anchors it earlier than that", which stopped being
true on 2026-08-20: the chain head is now stamped into Bitcoin through
OpenTimestamps at each seal, and the proofs are published at
https://hexisfoundation.org/ots/ with instructions beside them. A Bitcoin
block is the one layer of this system that does not require trusting its
operator.

HEXIS × NEWFLOW Whitepaper v0.7 — July 2026
HEXIS Foundation — no legal entity, by design.
Authenticity is cryptographic, not jurisdictional.
© 2026 hexisfoundation. Licensed under MIT.
contact@hexisfoundation.org · hexisfoundation.org
The protocol belongs to the behavior it records.
