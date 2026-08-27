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

**Closed on 2026-08-23: items 1, 2, 3, 5, 6 and 9.** They are kept below with what
was actually done, because a list that deletes its closed entries cannot show
it closed them rather than dropped them. Item 4 is corrected but NOT closed,
and says so.

**2026-08-28.** The three that remain — 4, 7 and 8 — are all blocked on the
same thing, and it is not code. Item 4 needs corroboration from a party that
is not us. Item 7 needs key holders who are genuinely independent. Item 8
needs enough real audits to measure. Every one of them closes the moment a
second operator exists, and none of them closes before that, no matter how
much is written here. That is the honest state of this list: the remaining
work is not engineering.

Separately, the drift that produced several of the closed items now has a
mechanical guard. `whitepaper_figures.py` holds nineteen numbers printed in
the published paper against the constants that produce them, and eighteen
source tags on claims about the world, and the bridge refuses to boot if any
disagree. It checks **numbers, not mechanisms** — the wallet cap passes it
while the claim around the wallet cap was false — so a green boot line is not
a verified whitepaper, and the code says so in as many words.

---

## Closed

### 1. `/stake/lock` debits two parties on one signature — CLOSED 2026-08-23

**Closed by P1 bilateral stake.** `stake_terms` now carries the agreement.
The consumer proposes at `POST /stake/terms`; the worker accepts at
`POST /stake/terms/{job_id}/accept`, naming the `terms_hash` it agrees to.
Neither moves any ECU. `lock()` refuses with **423 Locked** unless accepted
terms exist whose hash equals the numbers being locked — so a lock with no
terms is frozen, a lock the worker has not signed is frozen, and an amount
changed after acceptance is frozen. Nothing is debited on any of those paths;
verified by balance before and after.

The worker's signature is made by the worker, at the moment of consent, over a
URL containing the job_id, with a recorded nonce. That is what rules out all
four cheap fixes named below rather than merely avoiding them. The signer is
read from `stake_terms`, never from the request, so no caller can nominate
whose key is checked.

The original entry follows.

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

### 2. Trust API: authentication is not authorisation — CLOSED 2026-08-23

**Closed.** `api_keys.actor_scope` records which actor_ids a key may credit —
a list, or `*`. `key_may_credit()` is asked at the write site before anything
else runs, and returns 403 otherwise. It fails closed on a NULL scope: a key
with no scope recorded is refused, not waved through. No migration widened
existing keys, and none needed to — there were zero keys in the live database,
which is also why the closing condition was still comfortably ahead of us.
Verified on production: a key scoped to one actor credits that actor (200) and
is refused for another (403).

The original entry follows.

A key proves who called. It does not establish that the caller may credit the
`actor_id` they named, and no such binding exists. **Any keyholder can mint
trust for any actor.**

**Closes before: any key is ever issued to a third party.** Today every key was
issued on the host by us, so the set of people who can exploit this is the set
of people who already have shell there. That stops being true the first time a
key goes to somebody else, and the binding has to exist before that happens,
not in response to it.

### 3. Trust API rate limiter: unverified behind nginx — CLOSED 2026-08-23

**Closed by measurement, and the answer is that it works.** The load test the
condition asked for was run: 40 concurrent requests return exactly 10 × 200 and
30 × 429, so the 10/sec limit is real and enforced.

Buckets are keyed on the true visitor IP, not on a collapsed one. Proved with
two distinct real source addresses from one machine: exhausting the IPv6 bucket
leaves IPv4 answering 200, and the reverse holds. A 15-request burst from
loopback on the host does not limit an external caller either. Spoofing
`X-Forwarded-For` or `X-Real-IP` changes nothing — 40 concurrent requests with
40 different claimed addresses still gave 10/30.

Why it works, since the service code alone does not show it:
`/etc/nginx/conf.d/cloudflare-realip.conf` carries the full Cloudflare range
list with `real_ip_header CF-Connecting-IP` and `real_ip_recursive on`, so
`$remote_addr` is already the visitor before any `proxy_set_header` runs.

Recorded because it was nearly reported the other way: reading only the vhost
files showed `X-Real-IP $remote_addr` and no `set_real_ip_from`, which looks
exactly like a Cloudflare-edge collapse. The global config was one directory
away. **Grepping part of a configuration and concluding from it is the same
error as reading `$?` off the end of a pipe.**

The original entry follows.

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

---

## Still open

### 4. The witness set is three hardcoded strings — CORRECTED, NOT CLOSED

**2026-08-23.** The two names that referred to nothing are gone. "On-chain TEE
Proof" named a TEE that does not exist; "Consumer Confirmation" named a
confirmation never asked for. Every record also carried the sentence "TEE proof
verified by validator." All three witnesses now name something real and
checkable, and the description says plainly that this host asserts delivery and
also mints.

**Types and weights are unchanged**, so W stays 0.14085 and yield is
bit-identical — a deliberate decision to stop asserting what is false without
silently repricing every future mint on a running testnet. Which means the
closing condition below is *not* met: W has not fallen, and no corroboration
has been built. The fabrication is gone; the defect is not.

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

### 5. A record's CID is not readable by anyone outside — CLOSED 2026-08-23

**Closed, at a condition rewritten before it was met — openly.** The original
condition, "a public read that returns a record's CID", could have been
satisfied by the most dangerous possible act: committing permanent addresses
to content that was still wrong. The condition actually met is stronger and
honest about what a CID is for:

*A CID proves nothing the chain does not already prove better. The chain
commits `proof_hash` for every mint, signed and anchored into Bitcoin; that is
the integrity claim. What a CID buys is the one thing nothing else provides —
somebody other than us being able to serve the record when we are gone. The
protocol keeps running without its operator, or the CID is decoration.*

What exists now:

- `GET /hexis/records` — every record, its address, and whether content is
  held. The 36 pre-2026-08-16 records say plainly that their content was never
  built and cannot be served as minted — a permanent loss stated as one, not a
  backlog.
- `GET /hexis/record/{event_id}` — the record, its CID **computed from the
  bytes at answer time, never read from a stored field**, and three ways to
  verify: bytes against CID, record_hash against content, proof_hash against
  the Bitcoin-anchored chain.
- `GET /hexis/record/{event_id}/raw` — the exact canonical bytes. sha256 of
  the body equals the digest inside the CID: checkable with curl and shasum,
  no software of ours involved. Verified end to end from outside on
  2026-08-23, all three checks passing.

Two defects found and fixed while building it, both in CORRECTIONS.md: pinning
handed the provider both the bytes and the name (the one pinned record's own
`record_hash` did not verify against what IPFS served); and a record's
canonical content lived only in the pin queue, which success deletes — so
pinning a record destroyed our local copy of it. Content now lives in the
index forever, and the CID is computed locally and checked against every
provider's answer.

Still true and still stated: only our paid provider serves these bytes today.
Addressable is not retrievable, and survivability needs a second pinner or a
Filecoin deal — money, not code. See DEPLOY.md, "The CID layer".

The original entry follows.

The pinning work ends on the sentence "the CID is the source of truth", and no
public read returns a CID. It lives in `bridge_hexis_index.json` on the host,
and the only way to get one is a shell there. So a third party can verify a
record against its CID **only if we hand them the CID** — exactly the trust the
content addressing was supposed to remove.

**Closes at: a public read that returns a record's CID.** Which means first
deciding what a record's public shape is, which has not been decided.

### 6. Chain balances are not durable — CLOSED 2026-08-23

**Closed at the condition as written**, which asked for the wallets and said
so deliberately: *"persistence has to start there and not with the balances."*
The validator and faucet keys live in `protocol_wallets` and are restored on
boot, so the issuing authority of this ledger no longer changes identity every
restart. Proved by restarting the live service twice and reading the addresses
back unchanged; the restored key still signs.

It refuses to boot if a stored key does not derive its stored address, and if
the wallet cannot be persisted at all — falling back to a per-boot keypair
would reintroduce the defect while looking like a successful start.

**The balances are still re-minted on every start**, now to the same addresses.
That is the difference between a ledger that can be replayed and one that
cannot, which is what the condition was about, but it is not the same as
durable balances and the genesis log line says which is which on every boot.

The original entry follows.

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

**2026-08-28, measured:** `/sampling/stats` reports `total_audits: 3`, of which
one is still pending, so two have resolved. The endpoint also prints
`fraud_rate: 0.5` off those two, which is exactly the kind of number this item
exists to refuse: a rate computed from a denominator of two is not a
measurement of anything, and calibrating σ from it would be choosing a value
while calling it derived. Still open, and the count is now published rather
than remembered.

### 9. Anti-concentration triggers are absolute numbers — CLOSED 2026-08-23

**Both halves closed.**

*Relative triggers.* `damping()` was `min(1, Q/V)` against `baseline_energy_j`,
an absolute quota. It is now `min(1, cap/share)` against the region's share of
network energy, so the trigger means the same thing at any network size and
needs no recalibration as traffic arrives — which was the actual defect, not
the particular number. Verified scale-invariant: an identically shaped network
run at 1e9× the energy returns the identical damping factor, where the absolute
version would have given two different answers. Damping requires at least two
active regions, because concentration is a comparison and a lone region would
otherwise be damped forever for being the only participant. The mint cap's
absolute free allowance of 20 events became a participant count: a region with
too few distinct actors has nobody to be concentrated against.

*Silence that says so.* A dormant mechanism is now named at boot, at WARN, with
its day count and the sentence that it is a measurement rather than a
reassurance. `/status` had carried the same numbers for a month, but reporting
to an endpoint nobody polls is not the same as saying it. Live on the first
boot after deploy: `DORMANT: geo_damping (30d), mint_share_cap (30d)`.

Calibration against organic traffic is still not possible and is still not
claimed. The point of a relative trigger is that it does not need to be.

The original entry follows.

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
