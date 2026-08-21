# Open defects

What is wrong with this system right now, and what specifically has to be true
before each one is closed.

Every entry carries a **closing condition**, not a date. A date is a guess, and
a list of missed guesses reads as neglect. A closing condition is checkable by
anyone, including against us: if the condition happens and the item is still
open, that is a broken promise you can point at rather than a schedule that
slipped.

Nothing here is theoretical. Each item is a defect found in this system, most
of them written up at length in
[CORRECTIONS.md](https://hexisfoundation.org/CORRECTIONS.md); this file is the
short form with the exit criteria attached.

---

## Blocking a specific action

These three have closing conditions that gate something. The point of writing
them this way is that the gate is checkable from outside.

### 1. `/stake/lock` debits two parties on one signature

`lock()` takes `consumer_amount + job_value_ecu` from the consumer and
`worker_amount` from the worker. Whoever signs, one signature moves the other
party's money. The consumer is the correct signer of the two — it initiates,
and its debit is larger — but there is nowhere in the flow where the worker has
agreed: `/stake/lock` happens *before* `/job/request`, so no job row exists yet
to carry consent.

**Closes at: P1 bilateral stake.** A step where both parties see the same terms
— amount, counterparty, expiry — and each signs them, with the worker's
signature produced at the moment the worker consents and unusable for any other
job.

Four cheap fixes are ruled out in advance, in `CORRECTIONS.md`, because each
looks like progress and violates the sentence above: a signature the consumer
collects and forwards; a ceiling pre-authorised at registration; the server
signing for an online worker; and inferring consent from job acceptance.

**Not mitigated by being hard to reach.** It is currently unreachable for a
different reason — escrow has no public funding path — and that is not a
control, it is an accident that will end.

### 2. Trust API: authentication is not authorisation

A key proves who called. It does not establish that the caller may credit the
`actor_id` they named, and no such binding exists. **Any keyholder can mint
trust for any actor.**

**Closes before: any key is ever issued to a third party.** Today every key was
issued on the host by us, so the set of people who can exploit this is the set
of people who already have shell there. That stops being true the first time a
key goes to somebody else, and the binding has to exist before that happens,
not in response to it.

### 3. Trust API rate limiter: unverified behind nginx

`rate_limit_middleware` keys its per-IP buckets on `request.client.host` with
no real-IP handling. The bridge hit exactly this and needed a patch — every
user collapsing into one `127.0.0.1` bucket, self-DoS, and an attacker sharing
a bucket with real users. Uvicorn *may* rewrite the client address from
`X-Forwarded-For` before the middleware sees it. **This was never established.**

The probe that was run proved nothing: 12 sequential HTTPS GETs, too slow to
exceed a 10/sec window, all 200s.

**Closes at: a load test before first public traffic.** Concurrent requests,
reading the actual bucket keys under load. Reasoning about uvicorn defaults is
not a result, and "probably fine" is the class of answer this project keeps
paying for.

---

## Structural, and honest about it

### 4. The witness set is three hardcoded strings

Every HEXIS record ever minted names the same three witnesses — `NEWFLOW
Validator`, `On-chain TEE Proof`, `Consumer Confirmation`. **None of them is a
thing that happened, and there is no TEE.** The consumer confirms nothing; it
signs `/job/request` and is never asked again. The "validator" is the same
process doing the minting.

This matters more than a wrong string because `W` in the §5 formula is *witness
diversity*, and three hardcoded sources of three different types is the maximum
that field can express. So W is a constant wearing a mechanism's name.

**Closes at: either the witnesses that correspond to nothing are dropped and W
falls accordingly, or the corroboration the record claims is actually built.**
Both are real answers. Publishing the sentence unchanged is the one option that
is not, and it is the one in force.

Existing records are not re-graded — that was decided in STEP4_PROPOSAL §8.4 —
so this closes forward only.

### 5. A record's CID is not readable by anyone outside

The pinning work ends on the sentence "the CID is the source of truth", and no
public read returns a CID. It lives in `bridge_hexis_index.json` on the host,
and the only way to get one is a shell there. So a third party can verify a
record against its CID **only if we hand them the CID** — exactly the trust the
content addressing was supposed to remove.

**Closes at: a public read that returns a record's CID.** Which means first
deciding what a record's public shape is, which has not been decided.

### 6. Chain balances are not durable

`BridgeState.__init__` builds `ChainState`, `Ledger` and the mint engine fresh
and calls `_init_genesis()`. There is no load path. Every restart produces a
new validator wallet, a new faucet wallet, and balances reset to genesis.
`/status` marks the affected fields `ephemeral`, so nothing lies about it, and
the escrow ledger — which is the money that matters — *is* durable and
reconciled.

**Closes at: the validator and faucet wallets survive a restart.** A ledger
whose issuing authority is regenerated every boot cannot be replayed, so
persistence has to start there and not with the balances.

### 7. No revocation path for the seal key

A successor key can be designated (`successor_designation`), which handles the
holder dying or losing the key. It does nothing about theft. **If the seal key
is stolen rather than lost, the thief seals validly and nothing published can
say otherwise** — the successor has no way to say "that one is no longer us"
that the thief cannot say back.

**Closes at: a threshold scheme with genuinely independent holders.** Not
solvable at one person, and no interim measure is honest here: every one that
fits a single holder either lets an attacker trigger it or requires the holder
to be alive.

Accepted, deliberately, at the current scale. Recorded so that it is accepted
rather than overlooked.

### 8. `sampling_config.sigma = 0.1` on production

A sampling parameter running at its development value against real traffic.

**Closes at: a value derived from measured audit outcomes rather than chosen.**
Which needs enough audits to measure — there have been two.

### 9. Anti-concentration triggers are absolute numbers on an unmeasured network

`baseline_energy_j` is roughly 55 standard jobs per day per region: far above
organic traffic now, and *below* it for any network worth attacking, at which
point damping would be permanently on. **There is no traffic level at which
that number is right for long.** Neither damping nor the mint cap has ever
bound anyone — zero `geo_damping_scale` values and zero `mint_capped` events in
the whole chain.

**Closes at: both triggers become relative** (the module already computes an
HHI that nothing reads), **and a mechanism that has not bound in N days says
so.** Silence from a safety mechanism currently reads as protection when it
should read as an unanswered question.

Proposed in STEP4_PROPOSAL §9, parked until there is organic traffic to
calibrate against — which is itself the honest reason and not an excuse, since
calibrating against no traffic produces another absolute number.

---

## What this list is not

It is not everything that could be better. It is the set of things where a
reader could otherwise reasonably assume a property that does not hold.

Items that are **decided rather than open** are not here — the 36 records that
were never pinned and never will be, the test actors in the chain, the chain
events lost before the audit allowlist was fixed. Those are settled, they are
in `CORRECTIONS.md`, and reopening them would be pretending.

If something on this list closes, the entry moves to `CORRECTIONS.md` with what
was actually done. If a closing condition arrives and the item has not closed,
that is the failure the condition exists to make visible.
