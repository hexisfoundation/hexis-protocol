# CORRECTIONS

Things this repo's own documents got wrong, and live defects found after the
fact. Newest first. Every entry carries the date it was recorded.

The point of keeping this is narrow: a claim that was written down confidently
and turned out to be false is worth more as a record than as a quiet edit.

### About this file being public

It was written for ourselves and published on 2026-08-20. Publishing it was
read through once with a single question: does any entry hand an attacker a
live operational detail — a path, a key procedure, a backup layout, an account
inventory.

The rule applied, and the reason for each half of it:

- **Nothing is rewritten.** No finding softened, no entry removed, no severity
  reworded. A file that edits its own history to look better is the thing this
  file exists to object to.
- **Four live details are generalised**, and each is marked in place with
  a bracketed `generalised` marker and a description of what stood there. A generalisation you
  cannot see is an edit; one you can see is a declared omission, and the reader
  can tell exactly how much they are not being shown. The four are: the hour the
  nightly backup runs, the path of the service's backup directory, the host's
  account and SSH-key inventory, and one residential IP address that is ours.
  There are four. If you count a fifth, it is a mistake and worth reporting.

What was deliberately **not** generalised, so the choice is on the record
rather than inferred: the open defects. `/stake/lock` debits two parties on one
signature; any Trust API keyholder can still mint trust for any `actor_id`; the
Trust API's rate limiter has no real-IP handling and that was never verified
under load. Those are live and unfixed. Withholding them would make this file a
selective disclosure, which is worth less than none — the entries say what is
wrong and say that it is still wrong.

### STANDING NOTE — do not ever publish `hexis-core` as-is

Written 2026-08-20, for whoever reads it years from now, while the reason is
still in someone's head rather than only in a diff.

**`hexisfoundation/hexis-core` is the private repository. Its git history
contains personal information.** A residential IPv6 address and the name of the
ISP behind it sat in this file until the generalisation pass above; the
generalisation changed the working tree and did not change history, because
nothing can. There are others of the same kind.

So if a decision is ever taken to open `hexis-core`:

> **Do not make the existing repository public.** Create a new public
> repository and copy the current contents into it as a first commit. The old
> repository stays private, as the archive.

The reasoning is the one this whole file runs on, pointed at itself. **History
is append-only — and that is precisely why it must not be exported wholesale.**
The property that makes the audit chain worth having is the property that makes
a git history dangerous to publish: nothing in it can be taken back. Every
argument this project makes for keeping records also argues for being
deliberate about who they are handed to, and those are not in tension. A record
you cannot edit is worth keeping; it is not therefore worth publishing.

Two ways this goes wrong that are worth naming, because both look like doing
it properly:

- **"Rewrite the history first."** `filter-repo`, force-push, done. That
  destroys the archive to produce the copy, and it is the one move this project
  has refused everywhere else — `mint_block_0.sh` was left in the public
  history for exactly this reason. Copy forward; do not rewrite backward.
- **"It is only a few files, just make it public."** The files are not the
  problem. The history is, and a repository's history is not visible in its
  file listing. Whoever proposes it will be looking at a clean working tree.

The same note is in `DEPLOY.md`, where the person doing the work will be
standing.

---

## 2026-08-23 — What the CID is for, two defects under it, and a wrong claim of mine about pinning being off

Asked plainly — *what is the CID for?* — the honest answer contradicted the
documents. Not integrity: the audit chain commits `proof_hash` for every mint,
signed and Bitcoin-anchored, and that check runs without IPFS existing. The
CID's one real job is **the record surviving its operator** — somebody else
serving the bytes when we are gone. Decided on that basis: build it, publicly
(`/hexis/records`, `/hexis/record/{id}`, `/hexis/record/{id}/raw`), and state
plainly that with one paid provider the survival claim is a design property,
not an observed one.

Building it surfaced two defects and one error of mine.

### The provider chose our bytes and our name

Pinning used Pinata's `pinJSONToIPFS`, which takes an *object* and re-encodes
it server-side. So the bytes on IPFS were Pinata's serialisation, not the ones
we hashed — measured on the one record ever pinned: IPFS held `"S":1` where the
record was hashed with `1.0`, and the record's own `record_hash` failed against
its own published content. The record explicitly invites the reader to run that
exact check. Anyone who did concluded tampering.

There were **three serialisers** for one record: `LedgerRecord.build` for the
hash, Pinata's for the pinned bytes, a third in `_pin_local`. Now there is one
(`hexis_cid.canonical_bytes`), the CID is computed locally before upload
(CIDv1/raw/sha2-256 — four constant bytes and a hash, rederivable by a stranger
with no software of ours), bytes go up via `pinFileToIPFS`, and a provider
whose answer differs from our CID is treated as failed, not as having renamed
our data. Verified live: Pinata now confirms the canonical CID, and sha256 of
the bytes it serves equals the digest inside it.

One near-miss inside the fix, caught by `test_identity_3a.py [7]`: the new
canonical serialiser was first written with compact separators, which would
have silently broken verification of all 37 existing records against the
record_hash they were stored with. The defect was never the spacing; it was the
plurality. Kept the historical form.

### Success deleted the record

A record's canonical content lived only in the pin queue, and a record leaves
the queue when a CID comes back. **Pinning a record destroyed our local copy of
it.** For the one record that completed the journey, the only surviving bytes
were the provider's re-encoding; the canonical bytes had to be recovered by
inverting Pinata's int/float rewrite until the stored `record_hash` matched.
The index now keeps full content forever, and a boot-time restore path re-adds
content to an entry — gated on the record_hash the index has carried since the
mint, refusing anything that hashes differently, renaming consumed files
`.done`/`.refused` so a vanished file cannot be mistaken for a restored one.

### And my own error, same class as the 350/351 incident

On 2026-08-23 I reported to the operator that "pinning is still OFF on
production — nothing has been published." **Pinning was ON.** `PINATA_JWT` is
set in the systemd unit's `Environment=`, which an ssh shell does not inherit;
I had read the "pinning OFF" line from a CLI invocation I ran myself, not from
the service. This is precisely the mechanism behind the two junk chain rows of
2026-08-21 — a unit-set environment variable absent in an interactive shell —
recorded here because making that mistake *while narrating the previous one*
is the strongest evidence yet that the check must be mechanical: the nightly
host-claims block, not a human reading a log line from the wrong process.

---

## 2026-08-23 — Auditing the paper against the code, and the three surfaces against reality

Two audits were run end to end: every testable claim in the sealed whitepaper
against a file and a line, and every published surface against a live request.
104 whitepaper claims were graded; **19 were false as run**. Six defects were
found on the live hosts. All are corrected as of today except the ones that
cannot be, and those are named below rather than left out.

### The one that gave a wrong answer to anyone using the product

`api.hexisfoundation.org` — the service literally named "HEXIS Trust API" —
graded **every actor on the network `Reject`**. All four that have earned HEXIS
on the bridge, three of them Moderate or Low a hostname away:

```
3ceLiWR49…   bridge Moderate   |   API Reject
3dTqYPZkd…   bridge Moderate   |   API Reject
3d2H3mJoy…   bridge Low        |   API Reject
3de1n2kDj…   bridge Minimal    |   API Reject
```

The cause was one missing branch. `compute_grade()` took a score and nothing
else, so a zero that meant *never seen* and a zero that meant *seen and worth
nothing* returned the same verdict at 10.0× collateral. The API's store is fed
only by `POST /integrity/submit` and holds no actors at all, so the wrong branch
was the only one it ever took.

The bridge never had this bug — it has carried a separate `Unverified` grade for
a zero score all along. The two services simply never agreed on what not knowing
means, and nothing compared them. Whitepaper §15 tells an agent to call
`GET /trust/{worker_id}` without saying which host, and the API's own landing
page invited exactly that.

Fixed: `compute_grade(score, known=)`, an `Unverified` grade at 0.0 matching the
bridge, a `known_here` field, and a `store` block in every response naming what
this store contains and where the authoritative record is. Verified over the
network: all four now return `Unverified`, not `Reject`.

**An absent record is not a bad record.** It is the absence of one, and code that
cannot tell them apart will pick the harmful reading, because the harmful reading
is usually the one with a number attached.

### The form nobody had disclosed

The front page collects an email address and a wallet address and posts them to
`formspree.io`, a third-party processor named in no published document. The
whitepaper's vendor paragraph opens *"The protocol's public surfaces sit behind a
single vendor"*, lists Cloudflare, then says *"One more thing sits there, and it
is described narrowly on purpose"* — and describes the `@hexis` name. That reads
as an exhaustive disclosure and was not one, and the vendor it omitted is the one
receiving personal data. There was no privacy policy; `/privacy` and `/terms`
were 404.

An email paired to a wallet is also the exact identity linkage this protocol's
thesis says it removes — §15's *"No name. No papers. No bank."*

Fixed by disclosure at the point of collection: the processor is named, what is
stored is stated, deletion on request is offered, and the wallet field now says
plainly that leaving it empty avoids the pairing.

### A false instruction, signed, three times — twice on the day it was found

The `ots_anchor` payload carried this since the layer was built:

> note that `ots verify` exits 0 for a pending proof too

It exits **1**. The original measurement read `$?` off the end of a pipe, so it
captured the exit status of `tail`. The error was corrected in `DEPLOY.md` and
`HOW_TO_VERIFY.md` on 2026-08-20 — **and not in the code that generates the
payload**, which nobody checked.

So on 2026-08-23, during the audit that identified this exact sentence as a
defect, two more anchors were recorded, and both carry it. Sequences **345, 388
and 389**. Chain rows cannot be edited; all three stand.

Reproduced live during the same session, which is the whole lesson:

```
$ ots --no-bitcoin verify <proof> 2>&1 | tail -4 ; echo $?
0      ← tail's exit code
$ ots --no-bitcoin verify <proof> > /tmp/o.txt 2>&1 ; echo $?
1      ← ots's
```

Fixed forward: the generator now emits the measured behaviour, spells out
`--no-bitcoin`, and directs the reader to `ots info` — which exits 0, needs no
node and no network.

**Fixing the documentation is not fixing the defect.** The prose was right for
three days while the thing that writes into the permanent record stayed wrong,
and the gap was invisible because both were "done".

### Sampling can be evaded for the cost of one retry

The heaviest whitepaper finding. §3.3's safety argument is *"a worker cannot know
in advance which jobs are sampled"*, and PoSP rests on it alone.

A job is audited iff `sha256(job_id + ":posp-sample")[:8] / 0xFFFFFFFF < σ`.
`job_id` is supplied by the caller — `min_length=1, max_length=128`, no format
check — and σ is published at `/sampling/config`. No server entropy enters. So
whoever names a job decides whether it can ever be audited.

Measured: **1.10 attempts on average** to find an id that will never be sampled,
worst case 3 over 1000 trials. Grinding the other way, ~10 attempts to force a
chosen worker's job into audit. Against a caller who does either, σ = 0.1 is not
a 10% audit rate; it is 0%.

Cross-checked against production rather than only against the source: an
independent reimplementation, run over the 30 real `job_complete` events,
predicted one sampled job in the window where σ > 0 — and the chain's single
`sampling_open` event names exactly that job, in the same second. Its id is
`verify-mint-pin-dbcca3181ae2`, which is also the proof that non-uuid ids are
accepted in production.

The module's own docstring names the assumption and does not enforce it:
*"khong doan truoc vi job_id la uuid4 do consumer sinh"*.

Recorded in the whitepaper at §3.3 and open until the selection uses entropy the
caller does not control. Not exploited: every actor on this network was created
by us, and one audit has ever opened.

### Four of the six formula factors have never moved

Across **all 32 HEXIS records ever minted**: `S = 1.0`, `W = 0.1408`,
`TDR = 0.1`, `T = 0.5` — one distinct value each. Only `BO` (3 values) and `C`
(2) vary. The single automated minting path builds every event with identical
inputs for the other four, so in practice `HEXIS = BO × C × 0.007047`.

The sharpest consequence is §19's *"Timing Score (T) is the primary anti-gaming
mechanism."* T has held its "outcome not yet confirmed" default in every record
the protocol has ever produced. It has never discriminated between anything.

Appendix B had been printing the evidence since v0.6 without anyone reading it:
its four figures reproduce to six decimal places only when S=1.0, W=0.14085,
TDR=0.1, T=0.5 and C=1.69 — a C the [0.8, 1.25] clamp made unreachable on
2026-08-17, leaving every figure in that appendix about 35% high.

### The rest, briefly

- **ECU supply.** §4 is built on 39,000,000. The mint engine enforces 950,000,
  and halves every 237,500 rather than at the printed boundaries — a factor of
  37. The bridge's genesis allocation names 39,000,000, so the process boots with
  one figure and mints against another. Zero ECU has ever been minted, so nothing
  has bound; the numbers must be reconciled before anything does.
- **The wallet hard cap does not exist.** §19 lists it first among three defences
  against capture. It is a constant, plus a print statement that calls it
  "ledger-enforced", plus three comments asserting enforcement lives elsewhere.
  It does not. Of the three defences, non-transferability is the only one built —
  and that one is real, by absence: there is no transfer path at all.
- **No stake-to-fee ratio is enforced.** §3.3 says 3×, Appendix D says a 1.0×
  floor, and those already disagree. `POST /stake/lock` takes both amounts from
  the request body and validates only that they are positive. The collateral
  multiplier is computed, returned to callers, and read by nothing.
- **There is no TEE.** §15's flow claimed one, and the bridge writes *"TEE proof
  verified by validator."* into the description of every record it mints, naming
  "On-chain TEE Proof" as a witness worth 2.0 of the 6.0 witness weight. Thirty-
  two chain rows assert a verification that never happened, permanently.
- **`x402_headers` are not headers.** Both services return them as a JSON object
  in the response body. Neither sets an HTTP response header, so a client that
  reads headers — which is what "returns x402-compatible headers" instructs —
  gets nothing.
- **`POST /keys/create`** was advertised on the API landing page as "Provision an
  API key", and in `/openapi.json` with `deprecated: false` and a summary FastAPI
  derived from the function name, "Create Api Key Gone". It has returned 410
  since 2026-08-14. The human-readable docs were right the whole time; the
  machine-readable contract was not.
- **Version numbers.** Both hosts report 0.8.0. §17 named v0.6.2 and v0.6.1 — the
  first thing a reader following §17 would have hit.
- **Three tables** in the published whitepaper had collapsed into unreadable runs
  of text, including the grade table, which is the most operationally
  load-bearing table in the document.
- **Four published HTML pages existed only on the VPS**, untracked, last modified
  three weeks earlier — against the rule that `origin/main` is the source of
  truth and nothing deploys that is not pushed first. Now in the repository.
- **Two anchors were sitting pending while Bitcoin already held them.** The
  calendars had confirmations for heads 347 and 357; our files did not, so the
  published proofs understated the truth — the precise failure `DEPLOY.md` warns
  about. Upgraded (blocks 963462 and 963465), recorded, republished.

### What the audit did not find

Stated because an audit that lists only faults is not a measurement. The chain
was re-verified with an independent implementation rather than by asking the
server: 390 events, `content_hash` and `event_id` recomputed for every row,
linkage unbroken, and 13 of 13 Ed25519 signatures valid against the published
key. The three published documents matched their seals byte for byte. The API's
auth gates refuse correctly. And the `hexis.db` reconciler, which reports `ok`
with zero rows and looked like a check passing vacuously, turned out to be
honest — it reconciles that database against its own events table and never
claimed otherwise. Suspected, measured, cleared.

### The shape of the 19

Six were **drift**: true when written, overtaken by a change that landed in the
code and not in the paper. Mechanical to fix. The lesson is that the paper has no
equivalent of the nightly host-claims block — nothing re-runs its numbers.

Thirteen were **assertion**: never true. A control was designed, named, given a
constant or a docstring, and not built — and in three cases the code *says* it is
enforced somewhere else, which is why they survived. That is the failure this
file exists to catch: a property asserted because it was intended.

Both audits are kept in full, privately, as `WHITEPAPER_AUDIT.md` and
`SURFACE_AUDIT.md`.

---

## 2026-08-21 — A check that could not run, and did not say so: two junk rows written into the chain

Sequences **350 and 351** of the live audit chain are successor designations
that should have been refused. 350 names the seal key already in force as its
own successor. 351 names the sha256 of the empty string. Both are permanent.
Rows do not come back out — that is the property the whole system is built on,
and it does not make exceptions for the operator's mistakes.

They were written by the refusal tests for the feature itself: running each bad
input against production to watch it be rejected, and two of them were not.

### Root cause, measured

```
unit file        HEXIS_SEAL_PUBKEY_PATH=/opt/hexis_newflow/foundation_seal.pub
shell over ssh   UNSET
```

`record_successor_designation()` reads the seal public key to check that the
fingerprint being designated is not the key already in force. The CLI runs over
ssh, where that variable is not set, so the read failed every time. And the
guard was written:

```python
if current is not None and current == fingerprint:   # fail OPEN
```

so a failed read was indistinguishable from a passed check.

**A check that cannot run must refuse, not shrug.** That sentence was written
into `backup_hexis.sh` on 2026-08-20, about `PATH` under cron, and is quoted in
the entry below this one. It was violated the next day, in a new file, by the
same person who wrote it. Knowing a lesson and applying it are different
activities, and the distance between them here is a permanent row in a chain.

The second row is worse in a quieter way. `e3b0c442…b855` is sha256 of nothing,
which is what a broken fingerprint pipeline produces — and the pipeline that
produces it was, an hour earlier, the command about to be written into
`DEPLOY.md` as the ceremony step. macOS ships LibreSSL 3.3.6, which cannot load
an Ed25519 public key: it wrote `unable to load Public Key` to stderr, emitted
zero bytes, and `shasum` hashed them without complaint. **The output is 64
valid hex characters and looks exactly like an answer.** Had that command
survived into the ceremony, the project would have committed, permanently and
under signature, to the designation of a key that does not exist.

### What was done

- **The check fails closed.** No readable seal key means the designation is
  refused, naming the path to set. It also falls back to the conventional
  location beside the module, so the CLI can read it at all.
- **The empty digest is refused by name**, since it is a specific value with a
  specific cause and no legitimate use.
- **`key_fingerprint.py`** replaces the shell pipeline and asserts what the
  pipeline could not: the file parses as Ed25519, the raw key is exactly 32
  bytes, and the digest is not the digest of empty input.
- **A retraction path**, `--void-successor <sequence> <reason>`, and both rows
  are voided — 350 at sequence 353, 351 at 354, each carrying the full reason.

All three original inputs were re-run against production afterwards and all
three now refuse, including the fail-closed path, tested by moving the public
key aside.

### Why a void and not a fix

Nothing was deleted. 350 and 351 still hash, still verify, still sit in the
chain. The void is a later row that says: that one was a mistake, here is why.

That is what an append-only record has instead of an eraser, and it carries
strictly more information than an eraser would — a reader sees the error, the
correction, and the interval between them. A system that quietly removed its
own bad rows would be unable to demonstrate that it had not removed others.

**The retraction path was needed on the day designations shipped**, which is
the argument for it existing at all: a fingerprint is 64 characters typed by a
human under ceremony conditions, and the first two ever written on this chain
were both wrong.

### The uncomfortable part

This happened inside the feature built to be the most careful thing here — the
one whose entire purpose is to make a commitment that cannot be walked back.
The failure was not in the cryptography or the design. It was an environment
variable, a fail-open `if`, and a test run against production on the assumption
that it would refuse.

## 2026-08-20 — The third layer, and the one question the verifier could never answer

Not a correction. It closes a limitation that has been stated in this
repository since `verify_audit_chain.py` was written, restated in its README,
and never fixed — which is close enough to belong here.

### What was missing

The chain had two properties and both were ours.

**Hash linkage** is arithmetic and anyone can recompute it. **The daily seal**
is an Ed25519 signature over the head, made with a key that is not on the
server. Together they prove a great deal about internal consistency and about
the operator having seen a head.

Neither answers this, and the verifier says so in its own output: **that the
events existed before the first seal ran.** A signature starts a clock; it
cannot wind one back. Since both layers are produced by the same person, both
can in principle be produced again, in any order, by whoever holds the key. The
honest description of the old position is that a reader had to trust us about
when, and only about when.

### What was built

Sealed heads are now committed to Bitcoin through OpenTimestamps. A Bitcoin
block timestamp is not ours and is not the calendar servers'; moving it means
rewriting Bitcoin. **This is the first of the three layers that does not depend
on trusting the operator**, and that is the whole of why it was worth the code.

`ots_anchor.py` runs on the laptop, beside `seal_remote.py`, because that is
where the key already is and because the host that holds the chain gains
nothing — no calendar client, no dependency, no new outbound call. Proofs and
instructions are published to `hexisfoundation.org/ots/`; a proof nobody can
fetch proves nothing, which is the 2026-08-18 finding pointed at a new artifact.

The first anchor, end to end, and every number in it is checkable without us:

```
chain head 317   10cfb298d0575701f7a463d31a11a0a7e6ca73e76e1fad183f057333e045907c
stamped          2026-08-20, four calendars accepted it
confirmed        Bitcoin block 963342
proof            https://hexisfoundation.org/ots/seal-000317-10cfb298d0575701.txt.ots
recorded         audit sequence 345, action_type ots_anchor
covers           sequences 0..317, because the chain is hash-linked
```

So every event in this chain up to sequence 317 — which is every entry on this
page, and the whitepaper seals, and the escrow ledger, and the twelve lost
action types — is now provably older than a block nobody here mined.

### The published verification command did not run on a normal machine

Found by walking the stranger path — fetching both files from the public URL on
a clean directory and running what `HOW_TO_VERIFY.md` told a reader to run:

```
$ ots verify seal-000317-10cfb298d0575701.txt.ots
Could not connect to Bitcoin node: Cookie file unusable … and rpcpassword not
specified in the configuration file
```

**`ots verify` needs a local `bitcoind`.** Almost nobody has one. And the
failure is the worst available shape: it exits 1 — the same code as a genuinely
broken proof — and prints a message that reads, to anyone not steeped in this,
like our anchor is bad.

This is the `sha256sum` defect again, three days after it was written up and one
layer higher: **a verification instruction that does not run is not a
verification instruction.** It is worse here than it was there, because there
the reader got "command not found" and knew the fault was theirs. Here they get
a plausible-looking failure about *our* proof.

Fixed. The published route is now `ots --no-bitcoin verify`, which needs no
node and prints the actual check:

```
To verify manually, check that Bitcoin block 963342 has
merkleroot d91266cc91152060207447cb751677d9c2d15fb0a0faa4173914687f70fe2fde
```

That turns the question into one about Bitcoin instead of one about us, which
was the entire point of the layer. The node route is kept and described as the
stronger form rather than the only one.

Recorded rather than quietly corrected for the reason everything here is: the
artifact built specifically to remove the need to trust us shipped, for a day,
with instructions that made it look broken to anyone who tried.

### It was then checked the whole way, by hand

Not left at "the tool says so":

```
ots --no-bitcoin verify   -> block 963342, merkleroot d91266cc…
mempool.space   height 963342 -> hash 00000000000000000000f20dac5682d78cf6235101b4ac564dd1e49687ed5f98
blockstream.info height 963342 -> the same hash, independently
raw 80-byte header, double-sha256 -> that same hash        (so the header is the block's)
bytes 36..68 of that header       -> d91266cc…             (so the block commits to our head)
block timestamp                   -> 2026-08-20 20:04:12 UTC
```

The raw header was parsed rather than a JSON field read, so the block hash
could be recomputed and the explorer's answer checked instead of believed.

**Chain head 317 existed before 2026-08-20 20:04:12 UTC, and the evidence for
that sentence is a Bitcoin block.**

Three design choices carried over from mistakes recorded on this page:

- **It cannot fail a seal.** Every path is wrapped twice and `seal_remote.py`'s
  exit code is untouched by anything it does. `PinService`'s law: a pin may
  never block a mint, so a calendar outage may never make the chain unsignable.
  That would trade a property we have for one we might get.
- **It is driven by the seal, not by its own schedule.** A second thing to
  remember is a second thing to forget — the verifier that sat unpublished for
  eleven days, the Pages alert unread for a day. The seal happens; therefore
  the anchor happens.
- **State is read off the filesystem, never from an index.** A `.txt` with no
  `.ots` *is* the retry queue. `LocalIndex` is the reason: a second record of
  what has been stamped is a record that can disagree with the first.

### The measurement that changed the design, including the one that was wrong

On a pending proof, exit codes captured directly:

```
ots verify     -> rc 1   "Pending confirmation in Bitcoin blockchain"
ots upgrade -n -> rc 1
ots info       -> rc 0   PendingAttestation('<calendar>')
```

`ots verify` returns 1 for a proof that is **merely young** and for one that is
**broken**. A monitor built on the exit code raises a corruption alarm every
time a fresh anchor waits for its first block — a false alarm on a daily
cadence, and this page already carries two entries about what those cost. Only
a `BitcoinBlockHeaderAttestation` in `ots info` counts as confirmed here.

The first attempt at that measurement got it backwards. It reported `rc 0` for
a pending proof and a whole docstring was written on top of that, arguing the
opposite trap. The reason was mundane: `$?` was read at the end of a pipeline
and returned the exit code of `head`, not of `ots`. **The command measured
something real and it was not the thing being asked about** — the same shape as
grepping `whitepaper.html` for a word that only exists in the `.md`, four
entries down this page. Re-measured directly, corrected before it shipped.

### What the chain event does not do

`ots_anchor` rows are pointers, and say so in their own `does_not_attest`
field: **this host did not verify the Bitcoin proof and runs no client that
could.** Verifying it there would mean putting a calendar client on the machine
that holds the chain, which is the arrangement being avoided. The `.ots` file
is the evidence, it is published, and a reader checks it against a blockchain
neither they nor we control. A row claiming more would be the operator vouching
for the operator.

The event carries the hash it anchored, never its own — the `document_seal`
self-reference rule, and here it is automatic: the anchored head always sits at
a lower sequence than the row recording it.

### What anchoring still does not stop, named in the published instructions

Written into `ots/HOW_TO_VERIFY.md` rather than left for a reader to work out,
because a page that lists only what a mechanism achieves is an advertisement:

- **We can decline to anchor.** A gap in the directory is visible in the
  filenames; the events inside it have the two weaker layers only.
- **We can stop.** Nothing obliges the next anchor.
- **We can anchor one chain and publish another.** Catchable — the published
  head would not match the anchored one — but only by someone who checks.

None of those lets us make an event look older than it is. That one is closed,
and it is the only one this layer was ever meant to close.

## 2026-08-20 — A signed payload that depended on coreutils, and the first check that reads the host instead of the prose

### The verification instruction was fixed in the document and left wrong in the chain

The whitepaper's `sha256sum` was corrected earlier today. The same string was
sitting in the `how_to_check` field of every `document_seal` payload, where it
matters more, and the first fix did not touch it:

```
how_to_check: "curl -s https://hexisfoundation.org/... | sha256sum"
```

**A signed payload must never depend on one operating system's binary name.**
The whitepaper can be edited and re-sealed; a chain row cannot be edited at
all, so the wrong instruction would have been permanent in every future seal,
carried under a signature that makes it look authoritative. That is the worse
half of the same defect, and it was found only because the first half was being
written up.

The field is now algorithm-first — the sha256 of the bytes at the URL is the
fact, and a command is one way to compute it — with `shasum -a 256` and
`sha256sum` both named. A separate `check` field states the algorithm alone, so
a reader who cannot run either command still knows what is being claimed.

**Sequences 295 and 313 keep the old wording and are not touched.** They are
signed. Editing them is the one thing this file exists to object to, and the
instruction in them is wrong rather than dangerous: a reader on Linux runs it
successfully, and a reader anywhere else can now find the right spelling in any
later row.

### `CORRECTIONS.md` is now sealed, and could not name its own seal

Added to `DOCUMENT_SEAL_ALLOWLIST` the day it was published. The argument is
short: a corrections file that can be quietly revised is worse than no
corrections file, because it reads as a promise that it has not been. This
document makes that exact argument about other records; being an exception to
it was not defensible for longer than a day.

It seals per edit rather than per release, which is the intended shape for a
document that only ever grows.

One thing that could not be done, and is worth writing down because the first
attempt tried it: **this entry cannot name the sequence its own seal is
recorded at.** Writing the number in changes the bytes, which changes the hash,
which makes the number wrong. It is the same constraint the whitepaper hit —
a document cannot carry its own hash — arriving one level up, and the answer is
the same. The whitepaper's seal (313) is named above because that is a
different document. This file's is not named anywhere inside it. Read it from
the chain:

```sh
curl -s https://bridge.hexisfoundation.org/audit/CORRECTIONS.md
```

### The nightly job now re-runs the commands this file quotes

The stale port-8401 claim below was found by a person reading 1,900 lines by
hand, six days late. That is not a control.

What was **not** built: anything that reads this file's prose and tries to
decide whether it is still true. A checker that has to understand English is a
checker that will be wrong quietly, which is the failure mode already on this
page a dozen times.

What was built is smaller and countable. `backup_hexis.sh` — the only thing
that runs every night without anyone remembering it — gained a `host-claims`
block that re-runs the commands the host-state claims quote, and compares
counts:

| Asserted | Expected |
|---|---|
| ports listening off loopback | exactly 22 and 80 |
| ports listening on loopback | 8400 and 8401 both present |
| `ufw` allow set | exactly 22 and 80, one app profile |
| `hexis-bridge`, `hexis-api` | `active`, `NRestarts=0` |

The convention it enforces, now in `DEPLOY.md`: **a claim in this file about
host state carries the command, its output, and the date.** The nightly block
re-runs the command half. A human still has to fix the sentence.

Four decisions inside it that are load-bearing rather than decorative:

- **Drift does not fail the backup.** Backups and host monitoring are two jobs.
  A backup refused because a port changed is the fastest route to someone
  removing the check. Drift writes to the manifest, prints to stderr so cron
  mails it, and leaves a `HOST_DRIFT` marker for the night nobody reads mail —
  and retention still runs.
- **`PATH` is set explicitly.** `ss`, `ufw` and `systemctl` live in
  `/usr/sbin`; cron runs with `/usr/bin:/bin`. Without that line every command
  is "not found", and **a check that cannot run reads exactly like a check that
  passed.**
- **Silence is drift.** A missing tool, an empty `ss`, a port that has vanished
  — each is reported. A port that disappeared means the service died; it does
  not mean the host got safer.
- **It is tested by being made to fire.** `test_backup_hexis.py [6]` stubs
  `ss`/`ufw`/`systemctl` and drives eight hosts through it, including the exact
  regression: 8401 back on `0.0.0.0`. The mechanisms recorded on this page that
  have never bound anyone are the reason a new one ships with proof it can.

### It was wrong on its first real run, and that is recorded here rather than fixed quietly

The block reported port 53 listening off loopback. It is not. `systemd-resolved`
binds `127.0.0.53` — with a `%lo` scope suffix — and `127.0.0.54`, both
loopback, and the first version of the filter excluded only `127.0.0.1` and
`[::1]`. Measured, the whole table:

```
0.0.0.0:80        nginx
0.0.0.0:22        sshd
127.0.0.1:8400    bridge
127.0.0.1:8401    trust api
127.0.0.54:53     systemd-resolve
127.0.0.53%lo:53  systemd-resolve
```

Nothing was exposed. The check was wrong, and it was wrong in the direction
that destroys checks: **a false alarm that fires every night is how a real
alarm gets ignored.** That sentence is already on this page, about the
`grep -o '"ok":[a-z]*'` bug found in this same script on 2026-08-15. A drift
detector that cries wolf on night one is switched off by night three, and then
the six-day stale claim it exists to prevent happens again with a dead check
standing next to it.

Fixed the same day, before it ran once from cron: all of `127.0.0.0/8` counts
as loopback, scope suffixes included. The host's real listener table is now the
healthy fixture the test is built on, so the case that broke it is the case it
is proved against. Re-run on the host afterwards: `KHỚP`.

Worth noting how it was found. Not by the test — the test passed, because the
fixture was written from what the check expected rather than from what the host
had. It was found by running the thing once against production before trusting
it, which is the habit the whole of this file argues for and the reason the run
happened at all.

### What it does not cover

This asserts the shape of the host, not that the shape is *right*. It would
have said `KHỚP` all through the months when SSH accepted root passwords,
because nothing about that was countable in these four terms.

## 2026-08-20 — Three files that never compiled, a sentence that promised a payment path, and this file made public

### Code that has never compiled, published as the code behind the protocol

`hexis_x402_server.py`, `hexis_api_v0.3.py` and `hexis_apiv0.4.py` sat in the
public repository and none of the three has ever been importable. Two distinct
breakages, which is the part worth recording, because the first assumption was
that it was one:

| File | Fault | Same class as |
|---|---|---|
| `hexis_api_v0.3.py` | 548 curly quotes; line 1 is `# """` so the docstring never opens and the prose below is parsed as code | `mint_block_0.sh` — word processor |
| `hexis_apiv0.4.py` | identical, and only 26 lines different from v0.3 | the same |
| `hexis_x402_server.py` | clean Python with one stray line appended after `run_demo()`: `Content is user-generated and unverified.` | `hexis_api_v0.6.1.py` — GitHub web editor paste |

The x402 file is the interesting one. It carries **zero** curly quotes, so the
word-processor diagnosis that fits the other two is wrong about it, and the
line that breaks it is the same line found at the top of `hexis_api_v0.6.1.py`
and removed on 2026-08-19 — a stray paste from the web editor, in a commit
titled "Update print statement from 'Hello' to 'Goodbye'". Two files in this
repository have now been broken by that one habit. The tell is the commit
message, not the diff.

Removed 2026-08-20 in `hexisfoundation/hexis-protocol@335fffd`, on the reasoning
`mint_block_0.sh` was removed under: **a repository offered to strangers as the
code behind the protocol should not contain code that has never compiled**, and
repairing a file nobody has run is writing new code and calling it a fix. All
three remain in git history.

Checked before removing, because "nothing references it" is the claim that is
usually wrong: `hexis newflow bridge.py` named `hexis_x402_server.py` as the
running Trust API on port 5042, and named 8401 as a demo UI. Both false. The
Trust API is `hexis_api_v0.6.1.py`, FastAPI, on 8401, and both service ports
bind loopback. That is a live claim about a running architecture, so it was
corrected in place rather than deleted along with the file it pointed at. The
whitepaper discusses the x402 *protocol* at length and never names the file.

### The whitepaper promised a payment path that does not exist

Recorded as queued in the entry below and now done, in `33da307`. v0.7's
single-vendor paragraph read "since August 2026 a payment handle (@hexis) does
too" — in the honest-limits section, arguing the Cloudflare dependency is real.
The dependency is real; the handle is a name reserved against a product that
has not launched, with no send, receive or balance path. It now says so.

Batched with it, the second `.md` change held over from the same day: the
verification block piped into `sha256sum`, which stock macOS and the BSDs do
not have. **A verification instruction whose first command answers "command not
found" has not been run** — the same defect as a verifier that existed only in
a private repository, arriving from the other end. Both changes travelled in
one deploy so they cost one `document_seal` between them.

Sealed at **audit sequence 313**, superseding the seal at 295 recorded earlier
the same day. The served document is 48,741 bytes, sha256
`e788dbb036dfcc94904c62f3886ca07f3bcdcfe77814acf5d13f02f5c51e9469`, signed the
same day. Both numbers are checkable without asking us:

```sh
curl -s https://hexisfoundation.org/HEXIS_Whitepaper_v0.7.md | shasum -a 256
curl -s https://bridge.hexisfoundation.org/audit/HEXIS_Whitepaper_v0.7.md
```

### This file is now public

Published to `hexisfoundation/hexis-protocol`. The reasoning is the one from
2026-08-18: a record of our own errors that only we can read is worth what the
unpublished verifier was worth. The v0.6 banner's reference to this file, which
until today pointed at something a reader could not obtain, is now a link.

What the read-through before publishing found is at the head of this file, with
the four generalisations named. It also found a stale claim of its own — an
entry saying port 8401 was still open six days after it was closed — which is
recorded as a correction beside the original rather than as an edit to it.

## 2026-08-20 — The jurisdiction inside the record, and the payment handle that cannot take payment

Three loose ends from the 2026-08-19 entry, closed. Recorded separately because
two of them turned out differently from how they were described when they were
left open.

### The constant, which was the one place it would have mattered

`hexis_genesis.py:52` read `FOUNDATION_JURIS = "Singapore"`. Every other hit
fixed on 2026-08-19 was a caption. This one is not: it lands in
`foundation.jurisdiction` inside the genesis allocation record — the document
whose own text calls it "the source of truth for all pre-mint allocations",
which is meant to be hashed and pinned to IPFS permanently. Of all the places
to assert a jurisdiction that does not exist, that is the worst one.

It was left open on 2026-08-19 on the reasoning that the value might already
sit inside signed records, which are history and not ours to edit. That
reasoning was right and the premise was wrong. Checked before changing
anything: the module is not on the VPS, nothing imports it, no allocation
record has ever been generated from it, and the audit chain's own `genesis`
event carries `{"message","schema_version"}` and nothing else. **No signed row
anywhere contains the old value.** There was no history to preserve — only a
claim in published code.

Now `"none — no legal entity, by design"`. The field is kept rather than
deleted so the record's shape does not change; what it says is now true. The
rule it was being protected under still stands and applies to the test actors
and the 36 unpinned records: an already-signed value is not edited.

### v0.6, kept and labelled

`HEXIS_Whitepaper_v0.6.md` is superseded, linked from nowhere, and served at a
public URL with HTTP 200 — so it reads as current to anyone who reaches it,
and it names Singapore in three places. Deleting it would prune history.
Editing its body would be worse: this document's own standard, in v0.7, is
"versioned, not denied".

So it gains a banner and loses nothing — 22 lines added, none removed, body
byte-identical to what was published. The banner names the withdrawn claim
specifically rather than gesturing at "some claims may be outdated", and says
that only v0.7 is covered by a `document_seal` event.

### The payment handle: reserved, and unable to receive anything

Whitepaper v0.7 line 1149 reads: "since August 2026 a payment handle (@hexis)"
sits behind Cloudflare too, in a passage about single-vendor concentration.
The question asked was whether it can receive funds today, and whether it
therefore needs disabling.

It cannot, and there is nothing to disable. `hexis.cloudflare.pay` resolves and
redirects to `cloudflare.pay/?handle=hexis`, which returns 200 and reports
`TAG_TAKEN` from the site's own `/api/check`. That is the whole of it. The
product is a name reservation for something not yet launched — its own
completion copy reads "**@hexis has been reserved. We'll let you know when
Cloudflare Wallets is ready.**" The application bundle contains no send, no
receive, no balance and no deposit path; the only API it calls is a name
availability check. Independently: no repository here configures a payment
handle, and `/donate`, `/support`, `/pay`, `/sponsor`, `/funding.json` and
`/.well-known/payment` all return 404 on the live site.

The correction is in the whitepaper's wording. "A payment handle … runs on
Cloudflare" describes a working payment surface. What exists is a reserved
name on a waitlist. The distinction matters exactly where that sentence sits —
in the honest-limits section, arguing that a vendor dependency is real. The
dependency is real; the payment handle is not yet a payment handle. Queued for
the next whitepaper deploy rather than fixed today, because editing the `.md`
changes the hash sealed at sequence 295 and the fix should travel with the
other pending `.md` change rather than spending two chain events on one day.

## 2026-08-19 — A jurisdiction, and a regulator, that were never ours to print

The front page carried `Singapore` above the label `Foundation · MAS`, and
repeated `Foundation: Singapore (MAS)` in its footer. The whitepaper's title
page, Regulatory line and footer each named Singapore. `hexis_mining_v0.2.py`
printed `Foundation: Singapore (MAS) — holds zero hexis` in the same banner as
the verified token distribution.

No legal entity exists in Singapore or anywhere else. MAS has never supervised,
licensed, registered or reviewed anything here. Printing the jurisdiction is the
same class of claim as printing the regulator — softer by one notch, and softer
is not the same as true. Sitting inside a table of audited supply figures, it
borrowed their credibility.

Replaced in all five places with what is actually the case:

    HEXIS Foundation — no legal entity, by design.
    Authenticity is cryptographic, not jurisdictional.

Verified over the network after deploy, not in the working tree: `curl` against
`hexisfoundation.org` and `hexisfoundation.org/HEXIS_Whitepaper_v0.7.md` returns
no hit for either word, and the replacement is present at lines 7, 936 and 1169
of the served document.

### The check that said clean while the claim was live

The first verification run was `curl -s .../whitepaper.html | grep -i -E
"MAS|Singapore"`. It returned nothing, and nothing was the wrong answer.
`whitepaper.html` is a 9 KB JavaScript shell that fetches
`HEXIS_Whitepaper_v0.7.md` at render time; the grep never touched a word of the
whitepaper. Reported before it could be relied on, and written into `DEPLOY.md`
so the same command is not run again — **grep the `.md`, never the `.html`**.

Two smaller traps in the same run, both recorded there as well. `-i` on `MAS`
matches *Mastercard* and *MetaMask*, so a raw count of hits overstates by two.
And on 2026-08-17 the Pages **build** succeeded while the **deploy** failed with
an upstream 503, leaving the live site a commit behind for a day: a 200 from a
URL is not evidence the bytes are current.

### Left standing, deliberately

`hexis_genesis.py:52` still reads `FOUNDATION_JURIS = "Singapore"`. That is a
value, not a caption, and it may already have been written into records whose
hashes cannot be edited after the fact. Changing it is a decision about the
chain, not about copy, so it is open rather than quietly fixed.
`HEXIS_Whitepaper_v0.6.md` also still names Singapore in three places. It is
superseded and linked from nowhere, but it is served at a public URL, and
rewriting an archived version conflicts with this project's own rule —
"versioned, not denied". Also open.

## 2026-08-18 — The verifier that proves "don't trust the operator" was only obtainable from the operator

`verify_audit_chain.py` was written on 2026-08-07 (`3be3938`) so that an outsider
could check the audit chain without trusting us. It has passed every time it has
been run, including today against production: `PASSED — all 10 checks passed`
over 256 events and 8 signatures. The results were real.

They were also never independently checkable, and nobody noticed for eleven
days. The file was committed and pushed — to **hexisfoundation/hexis-core, the
private repository**. It has never appeared in any commit of the public
`hexis-protocol`, and it is not on the VPS either. So the only way to obtain the
tool built to make our word unnecessary was to ask us for it.

Two reports state PASS results from it (`BAO_CAO_2026-08-16_so_cai_bridge.md`,
lines 207 and 338). Those results stand — this correction is not about the
verdict being wrong, it is about the verdict having been unverifiable by anyone
but us, while being written down as though it settled the question.

Worth naming precisely, because the near-miss diagnosis is more comfortable than
the real one: this was not a file someone forgot to commit. It was committed
carefully, with a long message explaining why third-party verification matters.
**Doing the disciplined thing in the wrong repository looks exactly like doing
it right, from the inside.** The commit history, the tests, the reports — every
internal signal said this was done.

Fixed 2026-08-18: published to `hexisfoundation/hexis-protocol`, byte-identical
to the private copy (`sha256 a68cd259…`), MIT, with the exact command a stranger
runs in the README. Two small changes went with it — a licence line, and `--url`
now defaults to the live network so the command is `python3
verify_audit_chain.py` with no arguments.

Still true, and stated in the README rather than hidden: the verifier cannot
show the events existed before the first seal ran. That needs an anchor outside
our control, and there is none.

---

## 2026-08-17 — The first record ever published names three witnesses that do not exist

The mint → pin path was proven end to end on production today, and the record
that proved it is now on IPFS at
`bafkreiaii4hvi3oiolthzme7wvevsou7xti2vinzxawhj2xiyeay2acjd4`. Reading it back
from `ipfs.io` is how this was found. It contains:

```
"description": "... TEE proof verified by validator."
"prob_betrayal_detected": 0.95,
"witness_sources": [
  {"type": "adversarial", "name": "NEWFLOW Validator"},
  {"type": "neutral",     "name": "On-chain TEE Proof"},
  {"type": "allied",      "name": "Consumer Confirmation"}
]
```

**None of those three witnesses is a thing that happened, and there is no TEE.**
They are string literals in `mine_hexis_for_job` (hexis_bridge_v0.6.2.py ~1476),
identical in every record ever minted. The consumer confirms nothing — it signs
`/job/request` and is never asked again. The "validator" is the same process
doing the minting. `prob_betrayal_detected = 0.95` is asserted, not measured.

Why this matters more than a wrong string: `W` in the §5 formula is *witness
diversity*, and three hardcoded sources of three different types is the maximum
that field can express. So W is a constant — **the same finding as C, in a
second place**, and this one is worse, because C was at least a function of a
declared input. Here the record asserts corroboration that no part of the system
performed.

Not fixed here, deliberately. Changing the witness set changes an input to the
score, which is a design decision about what the protocol claims to have
observed, and §8.4 of STEP4_PROPOSAL already ruled out re-grading existing
records. Recorded as **OPEN**. The honest options are: drop the witnesses that
correspond to nothing and let W fall, or build the corroboration the record
claims. Publishing the sentence unchanged is the one option that is not honest,
and it is the one in force right now.

---

## 2026-08-17 — Closing a hole left a loop with no way in, and nothing said so

`/stake/credit` was an unauthenticated money printer and closing it on
2026-08-12 was right. What went unrecorded is that it was the **only** way ECU
entered escrow, so closing it made the whole production flow unreachable:
`/job/request` answers 402 until both sides lock stake, locking needs escrow,
and the only issuance left — `_posp_reward` — pays a validator for an audit that
cannot exist until a job has completed. A closed loop with no entry point.

Nothing surfaced this for five days. It was found on 2026-08-17 while trying to
prove the newly deployed pinning worked: `/status` said `"enabled": true` and
`pinned: 0`, and the reason was not in the pinning code at all. A gap that only
appears when someone tries to use the system end to end is the kind this repo
keeps finding, and the lesson is the same each time — **a component test passing
is not the same as the path existing.**

Fixed by `--fund-escrow` on the host (see DEPLOY.md), and by
`verify_mint_pin_production.py`, which walks the whole loop against production
so the next time a link is missing it fails loudly instead of sitting quiet.

### Still open: a record's CID is not readable by anyone outside

The pinning work ends on a sentence — "the CID is the source of truth" — and no
public read returns a CID. It is in `bridge_hexis_index.json` on the host, and
the only way to get it is a shell there. So a third party can verify a record's
bytes against its CID **only if we hand them the CID**, which is exactly the
trust the content addressing was supposed to remove. Not fixed here, because
adding a read is a design decision about what the record's public shape is, and
that has not been taken.

---

## 2026-08-17 — Two mechanisms that were constants wearing a mechanism's name

The Context multiplier was one. Measuring it for STEP 4 found that **every
HEXIS ever minted was minted at C = 2.0** — both countries in the system sit at
the ceiling, so in three months the multiplier never once distinguished between
two actors. Its whole effect on the record was a constant factor of two. And
78% of the GDP table sits at one of the two clamps, so inside the top clamp the
GDP ordering it exists to express is gone: Mozambique (500) and Vietnam (4,200)
got identical C across a 12× difference.

Decided and implemented: C stays self-declared, the range narrows from
`[0.5, 2.0]` to `[0.8, 1.25]`, the boost decays at a half-life of 100 of the
actor's own mints, and the fact that the country is unverified is disclosed on
`/docs`, in the module docstring, and in the whitepaper. The full reasoning and
the cost of each number is in STEP4_PROPOSAL.md §8.

**What the narrowing costs, since it is a trade and not a win:** at `[0.5, 2.0]`
35 of 45 countries sat at a clamp; at `[0.8, 1.25]` it is 40 of 45. Less to
gain from lying, less resolution in the thing C claims to measure.

### The gate nobody was looking at

`strict_cross_country` decided **who may audit whom** by comparing the same
self-declared string. Two identities held by one person, declaring two
countries, passed it completely — and it guards something more dangerous than a
mint rate. Measured over the whole history it had never refused anyone: 2
audits ever, both already cross-country, 0 blocked.

Replaced by measurable independence: no ECU ever moved between the two accounts
(read from `ledger_entries`), no shared transaction history, and a benchmark
fingerprint that is logged but deliberately does not refuse on its own — two
identical laptops produce identical timings, and a statistical gate with no
calibration would have silently stopped all auditing.

That first check is only possible because of the previous piece of work. Before
`ledger_entries` shipped on 2026-08-16, escrow was a running total with no
record of movements, so "has money ever moved between these two?" **had no
answer.**

### A third measurement thrown away every time

`BenchmarkGate.verify()` computed the elapsed time of the sustained-compute
challenge and discarded it. It is the only hardware measurement this system has
ever taken. It is recorded from today, and like the canonical content of the 36
unpinned HEXIS records, it **cannot be backfilled** — every actor registered
before today has none.

### And the two anti-concentration mechanisms have never bitten either

Damping and the mint cap were measured on the real history because the decision
said anti-concentration is their job, not C's. Neither has ever bound anyone:
zero `geo_damping_scale` values and zero `mint_capped` events exist in the whole
chain. Peak regional energy ever observed is 7.2e8 J against a 2.0e10 J
trigger — **3.6% of the quota**, needing 27.8× more to fire — and the largest
number of mints by one actor is 3, against a free allowance of 20 before the 2%
cap is even consulted.

The honest qualification: `geo_activity` covers roughly one day, because the geo
module was wired on 2026-07-24 and mining stopped the same day. These mechanisms
have not failed a test. They have not taken one.

Underneath that is a design fault worth more than the measurement: both triggers
are absolute numbers on a network whose size nobody has measured.
`baseline_energy_j` is ~55 standard jobs per day per region — far above organic
traffic now, and *below* it for any network worth attacking, at which point
damping would be permanently on and the boost it damps would cease to exist.
There is no traffic level at which that number is right for long. Proposed, not
implemented, in STEP4_PROPOSAL.md §9: make both triggers relative (the module
already computes an HHI nothing reads), and make a mechanism that has not bound
in N days say so, because silence from a safety mechanism currently reads as
protection when it should read as an unanswered question.

---

## 2026-08-16 — The public repo's README taught readers to leak their own keys

`hexisfoundation/hexis-protocol` is public. Its README said, in two places, to
open `hexis_ledger.py` and `hexis_pipeline.py` and set `PINATA_JWT` in the
file. Both are tracked files in that repository, so following the instructions
publishes the key on the next push and leaves it in the history after any later
commit that removes it. Nobody appears to have followed them — no JWT-shaped
string exists anywhere in either repo's tracked tree, checked — so this is a
defect in the instructions, not an incident.

Fixed in `hexisfoundation/hexis-protocol@1d48ede`: both now say to export it,
with the reason and a pointer at systemd for anything long-running.

Removed in the same commit: `mint_block_0.sh`, which had been sitting at the
root of the public repo. It was never runnable — `bash -n` fails at line 106
because the entire file carries curly quotes and en-dashes, so
`DB_PATH=”/opt/...”` is not shell and `– Genesis lock table` is not an SQL
comment. It had been through a word processor before it was committed.

Underneath that it was real mint logic — `BEGIN IMMEDIATE`, a `genesis` table,
five burn addresses of 153,600 HEXIS each, an insert into `events` — with three
defects of its own: the operator's typed headline interpolated into SQL
unescaped, a pre-mint backup taken with `cp` on a WAL database, and a log
heredoc whose opening line is commented out while its body would run as shell.

Nothing was fixed or rewritten, here or anywhere private. Block 0 tooling gets
written fresh and reviewed when the catalyst arrives. A byte-exact copy is kept
locally at `attic/mint_block_0.sh`, gitignored, for provenance only —
sha256 `1060e36e40fb97e4e4ff098c820a1403054f4c027cd762e7d70512389398c693`. The
file also remains in the public repo's git history, which is correct: it was
never a secret, and rewriting that history would be a larger claim than the
problem justifies.

---

## 2026-08-16 — Nothing was ever pinned, and the content to pin was never kept

Two failures, and only the first one is about IPFS.

**Nothing has ever been pinned.** All 36 records minted between 2026-05-02 and
2026-07-24 carry a `local:` CID, which addresses nothing. Checked rather than
assumed, on the whole lineage: the May-era `hexis_api.py` has its own in-memory
`HexisLedger` and does not import `hexis_ledger.py` at all, so there was no
IPFS path in it to configure; the only code that ever called
`HexisLedger.store()` is `hexis_pipeline.py`, a manual CLI that is not deployed
and never was, carrying the same `YOUR_PINATA_JWT_HERE` placeholder. The Pinata
account is real and predates the bridge. The wiring never existed.

**The content a pin needs was never written down.** This is the part that
cannot be undone. An index entry holds five fields — `event_id`, `actor_id`,
`cid`, `hexis_raw`, `indexed_at`. A canonical `LedgerRecord` needs nine plus a
nested event of nine more: the description, the witness sources, the mention
counts, what could have been taken and what was returned. The audit chain kept
the *mining result* for the 31 July records and nothing at all for the 5 from
May, and a mining result is the scores, not the event they were computed from.

So the 36 cannot be pinned as they were minted. They can be reconstructed — the
bridge builds each event from a fixed template over job data — but a
reconstruction is not the record, and `proof_hash` cannot be used to prove one
is faithful: it is computed over the scores and the ids, not over the
description or the witness list, so a reconstruction with the wrong text still
matches.

### Decided 2026-08-16: no backfill. `unpinned_legacy` is the terminal state.

Not a deferral — a decision, and this is the entry that records it.

A labelled reconstruction would add durability to nothing. What those records
actually contain already survives in two places that are stronger than a pin:
the sealed audit chain, and the nightly backups. Pinning a reconstruction adds
no fact and creates a risk — that a later reader takes a CID minted in August
for the record of an event in May. A `local:` string cannot be mistaken for
that. A `bafkrei…` can.

They are also testnet records scheduled for wipe before Block 0. Building
permanence machinery for data that is already scheduled to be destroyed is
theatre, and the honest version costs nothing: say the content was never
written down, and leave the gap visible.

**The gap is part of the history.** Between 2026-05-02 and 2026-07-24 this
system minted 36 records and kept five fields of each. That is what happened,
and it is what the index will keep saying.

### What was built

`PinQueue` retains the canonical record on disk at mint time, whether or not
pinning is configured, which is the fix for the second failure and is
independent of the first. `PinService` pins on a background thread, with one
rule above the others: **a pin may never block or fail a mint.** A mint is a
statement about behaviour that already happened, made by this service against
its own database; a pin is an HTTP request to a company. Wired together, Pinata
being slow makes job settlement slow and Pinata being down makes honest work
unrecordable.

The JWT is read from the environment and is not in this repo, which is public.
`test_hexis_pinning.py [6]` greps the tracked tree for a JWT-shaped string.
`DEPLOY.md`, "Turning pinning on", has where it does go.

The index now says which of the three states each record is in — pinned,
pending, or minted before any of this existed — instead of a `local:` string
that looks the same in all three.

### What this found

**`LedgerRecord.build()` would have raised on every real mining result.** It
reads `mining_result["interpretation"]`; `HexisMiner.mine()` has always
returned that field as `"grade"`. `KeyError`, every time, on the one line that
builds the thing a CID addresses. It never fired because nothing ever called it
on a real result — which is the same evidence as everything above, arriving
from a different direction. The first test to put a live mint through this path
found it on the first run.

**A lock the index did not need until now.** `LocalIndex` was only ever written
by the request thread. A background thread rewriting entries as pins land makes
`add()`'s read-modify-write a race, and the thing lost in that race is a minted
record, not a pin status. Under 6 threads × 20 mints it holds: 120 records, 120
pinned, none overwritten.

### Still not true

"The CID is the source of truth" does not become true when a pin succeeds. It
becomes true the day a CID resolves from a gateway that is neither Pinata's nor
ours. The module docstring and the README stay marked as intention until then —
that check is in DEPLOY.md, and it is one `curl`.

---

## 2026-08-16 — The reserve comment named a mechanism the whitepaper rejects

`apply_genesis` skips `NETWORK_RESERVE`, commented "reserve released only via
block rewards". Whitepaper §4 says it is "released via verified compute — 95
years", and §3 says in as many words that there are "no meaningless block
rewards". The comment named the one mechanism the protocol is defined against.

Skipping is right either way — the reserve is not a balance anyone holds at
genesis — so this is a comment fix, not a behaviour fix, and no ECU figure
changes. Both whitepapers in the public repo (v0.6 and v0.7) agree, so there is
no ambiguity to resolve.

Recorded while there: the code's `NETWORK_RESERVE` is 38,850,000 and the
paper's is 35,178,000. The paper splits the other 3,822,000 into Genesis Burn
(1,950,000) and Genesis Contributors (1,872,000); this code implements neither,
so it lumps both into the reserve. The unissued total is right and no supply
figure is wrong. The label on part of it is. Written into the comment at the
allocation rather than filed as a correction to the paper.

---

## 2026-08-16 — The bridge's escrow balance was a total with nothing underneath it

The other half of the reconcile item, and the half that made it an item.

`stake_escrow.balance` was a running total, changed in place, one
`UPDATE ... balance = balance - ?` per movement. Nothing recorded the
movements. `consumer_stake` and `worker_stake` look like they would, and do
not: they hold locks and how each lock ended, so a `credit()`, a `transfer()`,
a PoSP audit fee and a severity repayment all moved escrow and appeared in
neither. **A total that cannot disagree with the rows beneath it cannot report
a breach** — the Harmony sentence, arrived at from the money side this time
instead of the reputation side.

### What was built

`ledger_entries`, append-only, one row per leg, written **in the same
transaction as the balance change it describes**. Not beside it and not
afterwards: there is no interval in which one exists without the other.

Every operation's legs sum to zero, and the sum is checked **before the rows
are written**, so an operation that does not balance raises and rolls back the
money with it. The failure mode is "the money did not move", never "the money
moved and the books are wrong". That is the whole reason the check is at write
time rather than in the reconcile, which can only tell you afterwards.

Money that leaves escrow arrives somewhere nameable — `locked`, another
actor's `escrow`, or `burn`. Money entering comes from somewhere nameable too,
and `issuance` is a deliberately ugly name for it: `credit()` creates ECU from
nothing, and on the live database that account now reads as a number instead
of disappearing into an unexplained increase.

Two boot audits keep the choke points shut. `_debit` and `_credit_in_tx` are
the only functions that write `stake_escrow`, and `audit_escrow_write_sites()`
reads the source at boot to say so; `BridgeState.chain_transfer()` is the only
way chain ECU moves, and `audit_chain_write_sites()` the same. Neither guards
against malice. Both guard against the next person needing a balance adjusted
and reaching for a one-line `UPDATE`.

### This record is tamper-evident, and `hexis.db`'s is not

Every reconcile writes a `ledger_reconcile` event into the audit chain carrying
the ledger head and hash. That chain is hash-linked and sealed daily with an
Ed25519 key that is not on the host. Someone who rewrites `bridge.db` entire —
entries, balances and chain together — still cannot produce the Foundation
signature over the rewritten head, and `verify_audit_chain.py` says so from off
the host.

The `hexis.db` module had to admit in its own docstring that it could not offer
this. Waiting for the bridge half rather than copying the jsonl approach across
is what bought the difference.

### Three things this found before it shipped

**The genesis allocation names 38,850,000 ECU that is never issued.** The dict
in `_init_genesis` allocates 39,000,000 across the validator, the faucet and
`NETWORK_RESERVE` — and `apply_genesis` skips the reserve, commented "released
only via block rewards". No block has ever been produced by this service, and
nothing in it produces one. The ledger caught it on its very first run, because
the legs were built from the intention rather than the result; they are built
from `chain.balances` now, and the unissued figure is recorded in the entry's
`reason` rather than quietly dropped.

**The opening balance had a hole that would have legitimised the thing it was
checking for.** The first version treated "no durable entries yet" as "this
must be the first boot after the ledger arrived". On a system that starts empty
that stays true indefinitely, so the first balance to appear by any route that
bypassed the ledger would have been absorbed as an opening balance and declared
reconciled. It is a row in `ledger_meta` now, written in the same transaction,
and a missing marker with durable history present refuses the boot rather than
opening on top. Found by `test_bridge_ledger.py [9]`.

**Chain entries cannot be keyed to the process.** `ChainState` is rebuilt by
every `BridgeState`, so a process-wide boot id made a second instance's empty
chain look like money the first one had lost. The id belongs to the
`BridgeState`, which is what actually owns the balances.

### What it does not do

`chain:*` entries are durable; the balances they describe are not. Genesis
re-mints at every start, to fresh addresses, and the ledger now accumulates one
genesis per boot. That is not noise — it is the plainest statement available of
what "the chain resets" costs, and it sits next to the `durability` markers
added on 2026-08-14 rather than replacing them. Making chain state itself
durable is still not done, and would need the validator and faucet wallets to
survive a restart first: a ledger whose issuing authority is regenerated every
boot cannot be replayed.

Opening balances are asserted, not derived — 8,270.0 ECU over 8 actors on the
live database, measured on a `.backup` copy before deployment. Everything after
that line is derived; that line takes the previous total's word for it, in an
account named so that anyone reading can see exactly how much was taken on
trust.

---

## 2026-08-15 — The only durable record of minted HEXIS was written by truncating it first

`LocalIndex._save()`, the whole of it, from the first version until today:

```python
def _save(self):
    with open(self.filepath, "w") as f:
        json.dump(self.index, f, indent=2)
```

`"w"` truncates the file, then writes. Between those two things the file on
disk is empty or half a JSON document. Every `add()` — every mint, every wipe —
opened that window on `bridge_hexis_index.json`. Measured in
`test_hexis_index_atomic.py [1]`, one plain reader against one writer:

```
truncate-then-write ->  53 of  829 reads got a file that would not parse
atomic replace      ->   0 of 1209
```

And `_load()` caught only `FileNotFoundError`, so a file caught in that state
by the next start was an unhandled exception at best. The habit one reaches for
next — catch it, start empty — is worse: the first `add()` after it would write
the empty index over the damaged one.

### Why this file and not another

The class docstring said:

> Even if this index is lost, all records remain on IPFS permanently.
> The CID is the source of truth, not this file.

Not true of the only caller that runs. `PINATA_JWT` is still the literal
`"YOUR_PINATA_JWT_HERE"`, `USE_LOCAL_IPFS` is `False`, and the bridge never
calls `HexisLedger.store()` at all — it calls `LocalIndex.add()` directly with
`cid = f"local:{proof_hash[:16]}"`, a string that addresses nothing. Nothing
has ever been pinned. No table holds these records; the Trust API's `hexis.db`
is a different service with different rows.

So the sentence promising a copy elsewhere was, as far as this machine is
concerned, the reason the file was written carelessly for so long. A cache may
be truncated in place. **The sole record may not.** Same shape as the
`"In production: NEWFLOW state persists to disk"` docstring recorded yesterday:
a stated design read as a description of the running system.

The live file is 36 records, 10.7 KB, last written 2026-07-24. Mints are rare,
which is why the window never landed on anything. Rare is not a property of the
code; it is a property of the traffic.

### What changed

- `_save()` writes a temp file in the same directory, `fsync`s it, then
  `os.replace()`s it over the target and `fsync`s the directory. A reader sees
  the whole old file or the whole new one. A failed write leaves the old file
  byte-identical and takes the scrap with it.
- `_load()` **raises** on a file it cannot parse, naming the file and the
  backups. Absent still means first run — that distinction is the point.
- The IPFS paragraphs in this module now say, at the top, that they describe
  the intended design and not the running system.
- `DEPLOY.md` gained the restore procedure, because this refusal fails the unit
  every five seconds under `Restart=always` until someone acts.

The nightly backup already refuses to keep a run whose index will not parse
(2026-08-14), so any backup on disk is a good copy. That check was written
before this bug was understood; it turns out to have been guarding the failure
mode this entry describes.

---

## 2026-08-15 — Two `@reboot` lines would have started both services a second time

Root's crontab held these, alongside the nightly backup:

```
@reboot cd /opt/hexis_newflow && nohup python3 hexis_api_v0.6.1.py > ... &
@reboot sleep 10 && cd /opt/hexis_newflow && ... nohup python3 hexis_bridge_v0.6.2.py > ... &
```

Both units are also `enabled` and symlinked into `multi-user.target.wants`.
On the next boot each service would have come up **twice**: two processes on
one set of SQLite files, two write queues, two boot validators, two schedulers
— and `systemctl status` showing one healthy service, because the cron copy is
not systemd's to report. The `sleep 10` on the second line suggests someone
once hit a race and treated the symptom.

Nothing had to change for this to fire. It needed a reboot, and the host has
not had one since 2026-05-06, so the fault sat there for eleven weeks looking
exactly like a working system. That is the reason it went first, ahead of
defects that are visibly wrong today: **a fault that waits for an event you do
not schedule is discovered by the event.**

Removed 2026-08-15, both lines, crontab backed up to a timestamped file in the
service's backup directory `[generalised — the full path stood here]`, with a comment
left in its place saying why there are none. The nightly backup line stays.
`DEPLOY.md` records that a service which should survive a reboot gets
`systemctl enable`, not a crontab line — the two mechanisms do not know about
each other, so the second is not a fallback, it is a duplicate.

The tell if it ever comes back: systemd appends to
`/opt/hexis_newflow/hexis_api.log` and `server.log`, the `nohup` lines wrote to
`/var/log/hexis_api.log` and `/var/log/hexis_bridge.log`. Anything recent in
the `/var/log` pair means something other than systemd started a service.

---

## 2026-08-15 — 3e: the worker signed for the consumer, and the server held the consumer's key

Workers got keys on 2026-08-11. Consumers did not, and two writes a consumer
initiates carried the consequence, each under a comment admitting it:

| Route | Signed by, until today | Who the operation is for |
|---|---|---|
| `POST /job/request` | `payload["worker_address"]` | the consumer |
| `POST /stake/lock` | `payload["worker_id"]` | the consumer |

The comments called it a placeholder and named 3e as when it would be fixed,
which is better than silence and is not the same as safe. What it meant in
practice: **a worker could open a job naming any consumer, and lock stake that
debits that consumer's escrow.** Not a hypothetical class of bug — the same
class as `/stake/credit`, with a valid signature on top. Authentication that
binds to the wrong actor is not authorisation; it is a receipt made out to
somebody who was not there.

### The server was also the consumer

Worse, and only visible on reading the handler: `consumer_address` was
`Optional`, and when it was absent `/job/request` did this.

```python
consumer_wallet = Wallet()
consumer_addr   = consumer_wallet.address
```

It generated a keypair, kept the private half in `STATE.jobs`, and used it at
completion to sign a payment "from the consumer". That is exactly the
arrangement `/worker/register` was rewritten on 2026-08-11 to end, described
there in the same words: *a keypair the server holds makes the identity the
server's, whatever it is named.* The worker side was fixed and the consumer
side was left, in the same file.

A third one turned up next to it: `worker_wallet = worker_info.get("wallet") or
Wallet()`. Since 3a nulled the stored wallet, that `or` fired on every job and
generated a fresh private key, stored it in `STATE.jobs`, and **nothing ever
read it**. Dead code holding private keys.

### What 3e does

- **`POST /consumer/register`** — same shape as the worker version. The client
  brings the key, the address is derived from it, the signature over the
  request is the proof of possession, duplicate registration is 409. Consumers
  live in a `consumers` table whose `pubkey` is `NOT NULL`, unlike `workers`,
  which allows nulls only because seven legacy actors predate keys.
- **Both bindings move to the consumer.** `/job/request` →
  `payload["consumer_address"]`, `/stake/lock` → `payload["consumer_id"]`.
- **`consumer_address` is mandatory.** A signer the request may omit is not a
  signer. With it absent the guard now refuses at 400 `actor_not_identified`,
  before body validation and long before any code that could invent one.
- **Every server-held private key is gone**: the minted consumer wallet, the
  transfer that used it, and the dead per-job worker wallet.
- **Consumers reload from SQLite at boot**, written at the same time as the
  registry rather than added later — which is what happened to workers
  (`gap #4`) and is recorded two entries down.
- **A new boot validator, `audit_identity_registries()`.** `_resolve_actor_key`
  now consults two registries, so one address in both would mean *lookup order*
  decides which key a signature is checked against. It refuses to boot on that,
  and on one public key registered to two addresses. Unreachable through the
  endpoints, which 409 — but "unreachable through today's endpoints" is a claim
  about endpoints, and the rows are what the check is about.
- `consumer_register` has been in `VALID_ACTIONS` since the allowlist was
  written and emitted by nothing. The boot audit now reports 24 types emitted
  instead of 23.

### The ECU payment was being made twice, and one copy has been removed

`/job/{job_id}/complete` signed a `ChainState` transfer of the fee from
consumer to worker. It could not survive 3e, since it needed a key the server
no longer has — and the right answer was to delete it rather than reimplement
it, because the fee was already paid:

```
StakeManager.lock()      debits the consumer consumer_amount + job_value_ecu
StakeManager.settle_complete()   credits the worker worker_amount + fee
```

Both are rows in `bridge.db` and survive a restart. The `Transfer` moved the
same fee a second time through `ChainState`, which is memory only and resets to
genesis on every start. So the fee was recorded in two ledgers, one durable and
one not, with nothing reconciling them — the standing OPEN item, arrived at
from the other end. `payment_ok` now reports the escrow settlement, which is
the payment; the field was kept rather than removed because it can be made to
tell the truth.

### OPEN — `/stake/lock` debits two parties on one signature

Binding it to the consumer is the correct half and not the whole answer, and
saying so is the point of this entry. `lock()` debits **both** sides:
`consumer_amount + job_value_ecu` from the consumer and `worker_amount` from
the worker. Whoever signs, one signature moves the other party's money.

The consumer is the right signer of the two — it initiates, and its debit is
the larger — but a two-party debit wants two signatures, and there is nowhere
in the flow that the worker has agreed: `/stake/lock` happens *before*
`/job/request`, so no job row exists yet to carry consent. **Not closed. Not
papered over by picking a side quietly.**

**The shape of the fix, decided 2026-08-15, so that it survives until P1.**

> The worker's deduction requires the worker's signature, produced at the
> moment the worker consents.

Everything follows from that sentence, and it is written here because the
cheap patches all violate it while looking like progress:

- Not a second signature the consumer collects and forwards. A signature the
  counterparty holds and replays proves the worker signed *something*, once,
  and says nothing about this job. It has to be bound to this job's terms —
  amount, counterparty, expiry — and be unusable for any other.
- Not the worker pre-authorising a ceiling at registration. That is consent to
  a policy, not to a lock, and it puts the worker's stake back under the
  consumer's control with a signature on top — the `/stake/credit` shape
  again, which is what 3e existed to end.
- Not "the server signs for the worker if the worker is online". A key the
  server can use is the server's key, whatever it is named. Written down three
  times in this file already.
- Not reordering so `/job/request` comes first and infers consent from the
  worker accepting a job. Accepting work is not agreeing to a stake amount,
  and inferred consent is exactly the thing this entry objects to.

This is the P1 **bilateral stake** design and it is not a patch to
`/stake/lock`: it needs a step where both parties see the same terms and each
signs them, which the current flow has no room for. Until that exists,
`/stake/lock` stays as it is — one signature, correctly bound to the consumer,
with the worker's debit unconsented and recorded here as such. An interim
half-measure would make it *look* settled and remove the reason to build the
real thing.

### Also OPEN, and untouched by 3e on instruction

The Trust API's authorisation question — who may submit integrity events for
which `actor_id` — is a different service and a different release. It keeps its
own entry above.

And escrow still has no public funding path: the faucet credits chain ECU,
staking spends escrow, and `/stake/credit` (which created escrow from nothing)
has been 410 since 2026-08-12. A newly registered consumer therefore reaches
`402 insufficient ECU escrow` on its first lock. The register response says so
in its own body rather than leaving it to be discovered.

---

## 2026-08-15 — The nightly backup copied a WAL database with `cp`, and hid it when that failed

`backup_hexis.sh` runs nightly from cron `[generalised — the exact hour stood
here]` and had one line for each database:

```sh
sqlite3 /opt/hexis_newflow/bridge.db ".backup $DEST/bridge.db"   # correct
cp      /opt/hexis_newflow/hexis.db  "$DEST/" 2>/dev/null || true # not
```

`hexis.db` runs in WAL mode, so recent commits live in `hexis.db-wal` and have
not reached the main file. Copying the main file alone gives a database missing
them, or torn if a write is in progress. Measured, in
`test_backup_hexis.py [2]`, against a database held open by a writer exactly as
the running service holds it:

```
50 rows committed, no checkpoint
  cp      ->   0 of 50 rows
  .backup ->  50 of 50 rows
```

Zero. Not "a few missing" — the whole night's work, because the main file was
still the empty one the WAL had not been folded into yet.

**The trailing `2>/dev/null || true` is the half that made it survive.** A
failed copy and a good copy produced the same output, the same exit code and
the same-looking directory. There was nothing to notice.

### It was known, in the other script

`pull_backup_from_vps.sh` carries five lines of commentary explaining precisely
this — why `cp` is wrong under WAL, why `.backup` is right — and does it
correctly for both databases. The nightly job, which is the one that runs
without anybody watching, did not. One of the two places was fixed, and it was
the one a person is already looking at when they run it.

### What the rewrite changes

- **Every database goes through `.backup`**, with `.timeout 10000` so a
  database busy with a write is waited for rather than treated as an error.
- **The backup is verified by opening it and reading it**, not by trusting the
  exit code of the copy: `PRAGMA integrity_check`, plus a row count that must
  be at least the count taken from the source before the snapshot. Both tables
  are append-only, so fewer rows in the copy means it was cut.
- **Nothing is swallowed.** A failure stops the run, prints to stderr so cron
  mails it, and leaves a `FAILED` file in the directory for when nobody reads
  the mail.
- **A failed run prunes nothing.** Retention only executes after everything
  succeeded — on the night the backup breaks, the old ones are the only thing
  left.
- `reconcile_hexis_db.jsonl` is now included, since it exists to survive
  `hexis.db` not surviving.

The point of the whole change is the one thing a backup script cannot afford:
a torn backup is discovered on the day it is needed and not before.

### The test caught a bug in the fix

The first version read the reconcile status with `grep -o '"ok":[a-z]*'`. That
returns nothing if the JSON has a space after the colon, so a perfectly healthy
reconcile would have printed a warning into the manifest **every night**. It
now parses the line properly. A false alarm that fires nightly is not a small
defect; it is how a real alarm gets ignored.

### Two things found while auditing the copy paths, neither fixed

- **`LocalIndex._save()` truncates in place.** `hexis_ledger.py` opens
  `bridge_hexis_index.json` with `"w"` and `json.dump`s straight into it — no
  temp file, no rename, no fsync. So there is a window where the file on disk
  is empty or half-written, and that is the **only durable record of mined
  HEXIS**. A crash inside that window leaves the live file unparseable, and
  `_load()` catches only `FileNotFoundError`, so the bridge would fail to
  start. The backup script can only detect this (it now refuses a backup whose
  index does not parse); fixing it means changing how the file is *written*,
  which is `hexis_ledger.py` and a bridge restart.
- **The crontab starts both services a second time.** Two `@reboot` lines run
  `hexis_api_v0.6.1.py` and `hexis_bridge_v0.6.2.py` under `nohup`, while
  `hexis-api.service` and `hexis-bridge.service` are both `enabled`. On the
  next reboot each service starts twice, and both copies open the same SQLite
  files. The host has not rebooted since 2026-05-06, so this has never been
  exercised against the systemd units — it is left over from how the services
  used to be started. Left in place pending a decision, because deleting
  startup lines on a host is not a change to make in passing.

---

## 2026-08-14 — The bridge's chain state is wiped on every restart, and nothing said so

Found while scoping the bridge half of the reconcile item, by checking whether
`newflow_core.Ledger` could be the source of record. It cannot, and the reason
is worse than the answer: **`BridgeState.__init__` builds `ChainState()`,
`Ledger()` and `SunkCostMintEngine()` fresh and then calls `_init_genesis()`.**
There is no load path. Every restart produces a new validator wallet, a new
faucet wallet, and balances reset to the genesis allocation. Nothing anywhere
records what they held before.

So reconciling `ChainState.balances` against `Ledger` — the plan written into
the item below — would compare two structures that are wiped together and
refilled together. It would prove that one process agreed with itself since its
last start. The item's phrase "one half of it already exists unused" was
correct about the code and wrong about the durability.

### The document that promised it

The class docstring read, in full:

```
In-memory NEWFLOW + HEXIS state for the demo.
In production: NEWFLOW state persists to disk, HEXIS records persist to IPFS.
```

Present tense, and neither measure exists. This belongs in this file for a
reason distinct from the other entries: it is not a description that went stale,
it is **a promise written in the grammar of a description**. "In production, X
persists" reads to anybody skimming as a statement about how the system is
built. Nothing in the sentence marks it as an intention. A reader deciding
whether balances survive a restart has been answered, and answered wrongly.

Rewritten to describe what the code does, with the old sentence kept here.

### What is actually durable, measured on the live service

Two things survive, by two unrelated mechanisms, and one of them survives
because somebody noticed this exact problem once and fixed only the instance in
front of them (`gap #4`, 2026-07-22, worker reload from SQLite).

| Served by `/status` | Value, live | On disk in `bridge.db` |
|---|---|---|
| `newflow.chain_height` | 0 | — chain is not persisted at all |
| `newflow.total_jobs` | **0** | `jobs` — **9 rows** |
| `newflow.jobs_completed` | **0** | — |
| `workers[].jobs` | 1, 21, 0 | read from `workers`, so real |
| `workers[].balance_ecu` | 0, 0, 0 | `stake_escrow` holds 3320 and 350 ECU for two of those same three addresses |
| `scs.ecu_minted_phase0` | 0.0 | — |
| `hexis.total_records` | 36 | `bridge_hexis_index.json`, durable |

**One response says the network has run 0 jobs and, four lines further down,
that a worker in it has completed 21.** Both numbers are correct about their
own source. Nothing in the response says they are measuring different spans of
time. Escrow is the sharper case: a reader sees `balance_ecu: 0` for an address
whose escrow row durably holds 3,320 ECU. Different pot, and not one the
response gives any reason to go looking for.

### Marked, not fixed

`/status` and `/trust/{actor_id}` now carry a `durability` block classifying
every field they serve as `ephemeral`, `durable` or `derived`, with
`ephemeral_since` set to this process's start time. **No figure changed and no
behaviour changed.** `test_durability_markers.py` holds the marker to the
response — every field classified exactly once, nothing served unclassified,
nothing classified that is not served — because a marker that has drifted from
what it describes is the subject of this whole file. It also proves the two
claims rather than restating them: `[3]` builds a second `BridgeState` and shows
the new validator wallet and the emptied ledger, `[4]` writes a worker to SQLite
and reads it back after a boot, next to a chain height of 0.

Making the chain durable is the bridge half of the reconcile item, scheduled
**after 3e**, because 3e changes who the parties to a movement are and writing
today's placeholder binding into a permanent ledger would make it permanent.

---

## 2026-08-14 — hexis.db now reconciles; the bridge half of the same job does not

The standing reconcile item below covers two databases. This closes the
`hexis.db` half of it — the smaller half, and the one where both records
already live in the same file, so the whole job was a `SUM` and a comparison.

The invariant, enforced by `hexis_reconcile.py`:

| Stored | Recomputed from | Compared |
|---|---|---|
| `actors.hexis_score` | `SUM(events.hexis_minted)` per actor | with tolerance |
| `actors.event_count` | `COUNT(events)` per actor | **exactly** |
| `network_stats.total_events` | `COUNT(events)` | **exactly** |
| `network_stats.total_hexis_mined` | `SUM(events.hexis_minted)` | with tolerance |

`event_count` was not on the list of figures asked for. It is here because the
integer checks are the exact ones and the float check is not — see the
tolerance note below. Events whose `actor_id` has no row in `actors` are also
reported: the foreign key is declared but nothing sets `PRAGMA foreign_keys=ON`,
so it is unenforced, and neither the per-actor join nor the totals can see them.

It runs at boot, where a mismatch **refuses to start**, and hourly after that,
where it does not — a service already up is not improved by killing it over a
number that is already wrong, so the running check logs, records, and shows
the failure in the body of `/health` (`"status": "degraded"`), `/status` and
`/metrics`. Status codes stay 200, per this repo's own rule about verifying by
content.

The report names the actor and the delta, and never hides a cap:

```
hexis.db reconcile [boot]: MISMATCH (1) — 1 actor(s), 3 event(s), 0.1ms
  actor  worker_a   hexis_score  stored 2.629400000  recomputed 2.129400000  delta +0.500000000
  (showing 1 of 1)
```

### An `==` here would have been a self-inflicted outage

`hexis_score` is built by repeated addition; the recomputation is one `SUM()`,
and since SQLite 3.44 `sum()` over floats uses compensated (Kahan-Babuška-
Neumaier) summation. The VPS runs 3.45.1. The two are *not* expected to agree
bit for bit, and an exact comparison would refuse to boot the service over the
last bits of a double while calling itself rigour.

Measured rather than assumed — 20,000 events through the service's own write
path, in `test_reconcile_hexis_db.py [9]`:

```
drift 2.799e-09 over 20000 events   (tolerance 2.849e-07, one event ~0.709800)
```

So the drift is real and non-zero, sits two orders of magnitude inside the
tolerance, and three orders below a single event. The trade the tolerance makes
is stated in the code: it grows with the number of events, so at some table
size a lost event fits inside it. The exact integer checks do not degrade, and
that is why they are there. **The float check is for a corrupted value, the
integer checks are for a lost row.**

### The bug this found in itself

The reconcile reads `events`, then `network_stats`. The API's read connections
are autocommit, so those are two different snapshots: a write batch committing
between them makes the totals look wrong when they never were — at boot, a
refusal to start over nothing. All the queries now run inside one deferred read
transaction. `test_reconcile_hexis_db.py [8]` proves it is load-bearing by
running the same interleave without it and getting the false mismatch, and
`[12]` runs 200 real submissions through the real write queue with the timer
firing 62 times: zero false alarms.

### What the record does and does not prove

Every run appends one JSON line to `reconcile_hexis_db.jsonl` and `fsync`s it.
It sits **beside** the database, not inside it, so it survives `hexis.db` being
deleted, restored or rewritten — the situations it exists to speak about — and
`pull_backup_from_vps.sh` now pulls it off the host, so the useful copy is not
on the machine in question. A failing boot writes its record *before* it
raises.

It does **not** prove:

- **Tamper-evidence.** No hash chain, no signature. Whoever can rewrite
  `hexis.db` can edit or truncate this file — same root, same disk. Chaining it
  to itself was considered and rejected: with nothing anchoring it off-host it
  would prove only that whoever edited it could also recompute it, while
  *reading* like the bridge's sealed chain. That is this file's recurring
  subject, so the honest version is a plain log that says it is a plain log.
  Writing into the bridge's Ed25519-sealed chain is the real upgrade and is a
  cross-service write worth deciding on its own.
- **That the history is authentic.** It proves the totals agree with `events`
  *as `events` now stands*. Delete a row, decrement the totals to match, and
  every run afterwards passes. It catches drift, bugs and partial writes. It
  does not catch a consistent rewrite, and `hexis.db` has no audit chain that
  would.

### No `--repair`

`python3 hexis_api_v0.6.1.py --reconcile` prints the report and exits non-zero;
it repairs nothing. A mismatch does not say which side is wrong. Rewriting the
totals to match the rows is right when a total drifted and destroys the
evidence when it was rows that went missing — and this is the one validator on
either service whose failure a rollback cannot fix, because the data disagrees
regardless of which release is running. In DEPLOY.md.

### Three smaller things, found by doing it

- **`network_stats.total_actors` is dead** — initialised by the schema,
  incremented by nothing. It is exempt from the reconcile *with the reason
  written beside it*, because checking it would refuse the boot the moment the
  first actor row exists: an outage caused entirely by a field nobody
  maintains. Its value goes into every record so the deadness stays visible
  rather than becoming invisible by exemption.
- **`backup_hexis.sh` copies `hexis.db` with `cp`.** Under WAL the newest rows
  are in `hexis.db-wal`, so the nightly backup can be missing recent events or
  torn mid-write. `pull_backup_from_vps.sh` documents this at length and uses
  `.backup`; the cron script on the VPS does not. Found because the first
  version of the test copied the file the same way and reconciled an empty
  database. **Not fixed — it is a VPS cron script and a separate change.**
- **`/docs` advertised a quota that does not exist** and an endpoint that has
  returned 410 since the morning. Both corrected in place; the quota line now
  says it was never enforced.

`/status` also now marks which of its figures are stored and which are
computed, which was the specific complaint recorded against it below.

**Status: `hexis.db` half CLOSED. Bridge half — `ChainState.balances` vs
`newflow_core.Ledger`, and escrow, which has no ledger at all — still OPEN.**

---

## 2026-08-14 — The Trust API's only write endpoint had a dependency that refused nobody

`hexis_api_v0.6.1.py` serves `api.hexisfoundation.org` and had never been
reviewed — two weeks of work went into the bridge while this ran beside it on
the same host. Ten routes, two of them writes, and **neither authenticated**.

`POST /integrity/submit` is the one that matters. It declared a dependency:

```python
api_key: Optional[dict] = Depends(check_api_key)
```

which is why it reads as protected. `check_api_key` returned `None` for a
missing, malformed or unknown key instead of raising, and the endpoint used the
result exactly once:

```python
if api_key:
    if api_key.get("tier") == "free":
        pass
```

**This is worse than a missing guard and it is worth being precise about why.**
A route with no dependency is visibly unprotected; anyone auditing writes finds
it. This one had the dependency, the import, the type annotation and the name —
everything a reviewer looks for — and refused nobody. `Optional[dict]` is the
tell: the type says a key may be absent, and the code says that is fine.

### What one anonymous request bought

Every input to the mint is caller-supplied — `actor_id` (any string), `fee`,
`country`, `event_type`, `tier`. Measured, not reasoned:

```
compute_hexis(fee=1000, country="ET", tier=4)  ->  0.819
compute_grade(0.819)                           ->  "High", collateral 1.0
High threshold                                 ->  0.05
```

One unauthenticated POST put **any `actor_id`** at the top trust grade with the
best collateral multiplier. `hexis_score` is unbounded-additive, so there was no
ceiling either.

Two things that bound the severity, and they are why this ranks below the SSH
finding rather than above it:

- **It only inflates.** Score never decreases, so trust could be manufactured
  and never destroyed. No actor could be pushed down.
- **It touches no money.** `hexis.db` is a separate file with four tables
  (`actors`, `events`, `api_keys`, `network_stats`), no `ATTACH`, and no bridge
  module references it — checked, not assumed. Nothing here can reach
  `ChainState`, `stake_escrow` or `audit_log`.

The second point is narrower than it sounds. `/trust/{id}` returns
`x402_headers` carrying `X-Hexis-Accept` and `X-Hexis-Collateral-Mult` — numbers
whose only purpose is for a third party to price collateral on. The isolation is
from *our* money, not from money.

### Never exploited, and here that is provable

```
hexis.db     actors 0 · events 0 · api_keys 0
network_stats  total_actors 0.0 · total_events 0.0 · total_hexis_mined 0.0
local snapshots 06/08, 09/08, 12/08 — byte-identical, 5674e8e7c9f2…
live /status                        — actors 0, events 0, hexis_mined 0.0
```

The database has never taken a write. This is a stronger statement than the one
`/stake/credit` got: a mint there left no audit event, whereas a mint here
leaves an `events` row, an `actors` row **and** increments two monotonic
counters. Hiding it would mean deleting rows and resetting the counters. Not
impossible with direct SQL — which is exactly the hole recorded in the
`/stake/credit` entry — but this is not "no evidence either way".

### Closed 2026-08-14

- `require_api_key` replaces `check_api_key` and raises 401. It cannot return
  `None`; that was the defect, so the type no longer permits it.
- `POST /keys/create` returns **410**. It issued, to any anonymous caller and
  without limit, the credential that now authenticates writes — an endpoint
  that hands out the key to the gate is not a gate. Keys are issued on the host
  with `--create-key`, so the trust boundary is shell access, which the system
  already had. No operator key or admin endpoint was invented.
- A startup audit refuses to boot the service if any write route lacks the
  guard, with an exemption list that requires a reason. Same as the bridge's.
  `test_trust_api.py` [1] proves it fires — including the case that matters
  here: a route carrying a *dependency without the guard marker* is reported,
  so a second decorative `Depends()` cannot pass.
- CORS dropped from `allow_origins=["*"]` with POST to our own origins, GET
  only, no credentials. **This is not what closed the hole** and should not be
  read that way: a form-encoded POST is a simple request, it is sent with no
  preflight regardless of CORS, and the server still processes it. CORS governs
  what a browser will let a page *read*. `require_api_key` is the control.

### Smaller things found in the same read

- **The free-tier quota does not exist.** `calls_today` and `last_used` are in
  the schema and written by no code path, so `GET /usage/{prefix}` returns 0
  forever and the "1000 calls/day" in `/docs` limits nothing. Left unenforced
  rather than half-built, and now commented as such — the `if tier == "free":
  pass` block is precisely how an unenforced quota comes to read as an enforced
  one.
- **`--create-key` printed a label it did not store.** `api_keys` had no
  `label` column. Added, with a guarded `ALTER` for the existing database,
  because `CREATE TABLE IF NOT EXISTS` does nothing to a table that already
  exists — the schema block would have looked correct while every issuance
  failed.
- **`network_stats.total_actors`** is initialised, never updated, never read.

### UNVERIFIED — the rate limiter behind nginx

`rate_limit_middleware` keys its per-IP buckets on `request.client.host` and
does no real-IP handling. The bridge hit exactly this and needed a patch
(`patch_bridge_realip.py`, 24 July: every user collapsing into one
`ip_sec:127.0.0.1` bucket, self-DoS, and an attacker sharing a bucket with real
users). The Trust API has no equivalent.

Uvicorn 0.47 defaults to `proxy_headers=True`, which *may* rewrite the client
address from `X-Forwarded-For` before the middleware sees it — nginx sets that
header. **This was not established.** The probe run against the live service
(12 sequential HTTPS GETs) was too slow to exceed a 10/sec window, so it
returned 200s that prove nothing, and testing it properly means generating
concurrent load, which is not recon.

Recorded as **UNVERIFIED**, deliberately and by name. "Probably fine because
uvicorn defaults to proxy_headers" is a guess wearing the clothes of a finding,
and this file exists because of what those cost. Resolving it means reading the
bucket keys under real traffic, not reasoning about defaults.

### OPEN — authentication is still not authorisation

A key proves who called. It does not establish that the caller may credit the
`actor_id` they named, and no such binding exists: any keyholder can still mint
trust for any actor. That is the same distinction `/stake/credit` turned on. It
is not closed, no binding was invented to pretend otherwise, and it stays here
until there is a real answer.

---

## 2026-08-14 — SSH accepted root passwords for months while a config file said it did not

Found during read-only recon of the Trust API, on a line item that was expected
to be a formality: confirm `PasswordAuthentication no`.

```
sshd -T:  passwordauthentication yes
          permitrootlogin yes
```

Root login by password, open to the whole internet, on the host that holds the
only copy of the audit chain.

### The file that said otherwise

```
/etc/ssh/sshd_config.d/50-cloud-init.conf         PasswordAuthentication yes
/etc/ssh/sshd_config.d/60-cloudimg-settings.conf  PasswordAuthentication no
```

Both files exist, they contradict each other, and the hardened one loses.
`sshd_config` line 12 includes that directory, files are read in lexical order,
and **sshd uses the first value it obtains for a keyword** — so `50-` wins and
`60-` is read and discarded. The image has shipped `60-cloudimg-settings.conf`
since August 2025 saying password auth is off. It has never been off.

This is the same class of defect as the comment claiming chain verification
would expose a dropped audit event: **a file describing a control that is not
in force.** Anyone auditing this box by reading `sshd_config.d/` would have
found a line saying `no` and moved on. The only thing that tells the truth is
`sshd -T`, and nothing was running it.

### The fix, and the way it nearly went wrong

The instruction that produced this fix said to add a drop-in sorting **last**
(`99-hexis-hardening.conf`). That is backwards for a first-match-wins parser,
and it was tested rather than argued — same file, same bytes, only the name
changed, `sshd -T` parse-only so the running daemon was never touched:

```
99-hexis-hardening.conf  ->  passwordauthentication yes     (read, then ignored)
01-hexis-hardening.conf  ->  passwordauthentication no
```

A `99-` file would have been a third document claiming a control that was not
in force — the defect being fixed, applied a second time, in the same
directory, by the fix itself.

`PermitRootLogin` behaves differently and the difference is worth writing down,
because it makes the two look interchangeable: nothing in the drop-in directory
sets it, so its competition is `PermitRootLogin yes` at `sshd_config` line 42 —
*after* the include. Any drop-in beats that. Only `PasswordAuthentication` had
to sort before `50-`. A change tested on `PermitRootLogin` alone would have
appeared to prove that `99-` works.

Live at 2026-08-14, `/etc/ssh/sshd_config.d/01-hexis-hardening.conf`:

```
passwordauthentication no
permitrootlogin without-password        # sshd -T's spelling of prohibit-password
```

Confirmed both ways, on fresh connections, with an authenticated session held
open throughout and closed only afterwards:

```
key login                                    -> lands
-o PubkeyAuthentication=no, password only    -> Permission denied (publickey)
```

The host's account inventory was enumerated before the change and the result
was that this narrowed entry to a single existing key and locked nobody out of
anything else. `[generalised — the count of authorized keys, and which accounts
carry a password hash and a login shell, stood here]`

The file is named `01-` for a reason that a tidy-minded person would undo, so
the reason is written inside the file rather than only here.

### Not fixed

`50-cloud-init.conf` still says `yes` and is still wrong; it is cloud-init's
file and may be rewritten by it. Beating it by sort order is what makes the
hardening survive that. `fail2ban` was already running an sshd jail — a
mitigation that was doing real work for months without anyone knowing it was
the only thing there.

---

## 2026-08-13 — Six *more* audit action types were being discarded, and the fix for the first six would not have found them

### The measurement

Three numbers, read off the live production system on 2026-08-13, before any
of the reasoning below:

| Measured | Source | Value |
|---|---|---|
| Sampling audits performed | `GET /sampling/stats` → `total_audits` | **2** |
| Frauds caught by them | `GET /sampling/stats` → `fraud_caught` | **1** |
| `sampling_*` events in the audit chain | `GET /audit/chain/full` | **0** |

Two verification audits ran. One of them caught a fraud and slashed a
validator. The tamper-evident chain — the record whose entire purpose is that
it cannot be edited after the fact — contains **neither event**.

That is the finding. Everything below is how it happened and what was changed,
but the three numbers are the argument on their own: a hash-linked chain that
verifies perfectly, `valid: true`, over a history that is missing the money
events it exists to hold.

### How

Yesterday's entry, further down this file, records six action types that
`VALID_ACTIONS` rejected and every caller swallowed. The fix was to add the six.
That fix was worth almost nothing, and this is the entry that says so.

Extending the startup validator to derive the list from the source instead of
from memory — the same closed-by-default move as the write-route audit —
immediately found **six more**, none of them in the modules that had been read
by hand:

```
sampling_open              hexis_sampling.py:277
sampling_claim_denied      hexis_sampling.py:343
sampling_assign            hexis_sampling.py:359
sampling_verdict           hexis_sampling.py:453
sampling_stake_refund      hexis_sampling.py:512
sampling_validator_slash   hexis_sampling.py:575
```

The entire PoSP sampling layer. `hexis_sampling.py` has the same
`except Exception: pass` around its audit call as stake and severity, so it had
been dropping every one of them since the module shipped.
`sampling_validator_slash` moves money.

### Why the first fix was the wrong shape

Twelve types were lost, and the difference between the two rounds is only which
files someone thought to open. The defect was never the missing entries; it was
that the allowlist is maintained by hand in one file while the emitters live in
four others, and nothing connects them. Adding six entries left that intact.

`audit_action_types()` (in `hexis_bridge_v0.6.2.py`) now parses every module
loaded from the bridge's own directory, collects the action types the code can
actually emit — from audit call arguments and from the `"action_type": "..."`
dict literals `_audit_derive_action` builds — and refuses to boot if any of them
is missing from `VALID_ACTIONS`. The thirteenth type fails at startup instead of
failing silently forever.

Two things the scan had to be taught, both found by disbelieving its first
answer:

- It reported `genesis` and `daily_seal` as never emitted, while the live chain
  visibly contains both. They are written by calling the private `_insert`
  directly, not through the public wrappers. A scan that trusted the public API
  would have been quietly wrong in exactly the direction that matters.
- Three call sites pass an action type through rather than naming one, so no
  literal can be read there. They are declared in `AUDIT_ACTION_FORWARDERS` by
  enclosing function — not file and line, which rot on every edit above them —
  and each says which literal site it forwards from. Anything unresolvable that
  is **not** declared fails the boot, so the exemption cannot quietly widen.

Five entries in `VALID_ACTIONS` are emitted by nothing (`consumer_register`,
`ecu_transfer`, `job_dispute`, `token_consumption`, `token_topup`). That is
reported at every boot and does not block it: an allowlist entry with no
emitter is harmless, and an allowlist nobody prunes is how the first six went
unnoticed.

### Deployed

Live on the VPS 2026-08-13 17:08:59 UTC. The boot log now carries a third
validator line beside the other two:

```
Write-route signature audit passed.
ECU supply-conservation audit passed.
Audit action-type audit passed. 23 action types emitted, all allowed; 5
  allowed but not emitted anywhere: consumer_register, ecu_transfer,
  job_dispute, token_consumption, token_topup
```

From this restart, sampling events reach the chain. **The ones already lost do
not come back** — including the fraud that was caught. A tamper-evident record
cannot be backfilled, which is the same property that makes it worth having;
the two are not separable, and this is what it costs when the write path was
broken rather than the record.

Chain sealed at sequence 143 the same day, so the corrected history is signed
from here.

---

## 2026-08-12 — Forensics on `/stake/credit`: two calls, both the owner's, books balance

Run before any new code, read-only against the VPS, after the endpoint was
found open. The question was narrow: was it ever called while it was live and
unauthenticated.

**Answer: yes, twice, and both are attributable to our own testing.**

```
<addr-A> - - [01/Aug/2026:16:17:43 +0000] "POST /stake/credit HTTP/1.1" 200 61 "-" "curl/8.7.1"
<addr-A> - - [01/Aug/2026:16:17:43 +0000] "POST /stake/credit HTTP/1.1" 200 83 "-" "curl/8.7.1"
```

`[generalised — a single residential IPv6 address, ours, stood in both lines and
in the two paragraphs below. It is replaced by `<addr-A>` throughout, and every
`<addr-A>` is the same address, which is what the argument turns on.]`

One IPv6 address on a residential ISP, `curl/8.7.1`, inside a session that had been
browsing `/docs`, `/openapi.json`, `/status`, `/metrics` and `/leaderboard`
since 15:47. One second after the two credits, five `stake_lock` pairs land in
the audit chain (sequences 132–141) from `collusion_attacker_1785601062`
against worker `3dTqYPZ…` — five jobs against `PAIR_CAP_COUNT = 5`. It is a
pair-cap test, funded by the two credits.

**Every `/stake/` request in the entire log came from that one address** — 7
locks, 3 aborts, 2 credits, twelve in total, one client. No other IP has ever
touched a stake endpoint.

### The money reconciles exactly

Reconstructed per actor from the stake tables, `slash_log`, and — for five
locks whose rows were later deleted — the audit chain payloads:

```
total implied credit   11,770.00
less recorded slashes     -750.00
less locked-then-deleted -2,750.00
                        ----------
escrow held today        8,270.00   <- matches stake_escrow exactly
```

Every implied credit is a multiple of 250 except validator `3dTqYPZ…` at
4,270. The 20 is two PoSP audit rewards at the configured `audit_fee` of 10,
credited by `_posp_reward` for the two rows in `sampling_audits`. Every
account holding ECU is either one of the three registered workers or a
self-describing test string (`sev_test_*`, `posp_*`, `sig_test_*`,
`SEVERITY_RESERVE`). No unfamiliar address holds a balance.

### What this is not

It is **not** proof the endpoint was never exploited, and it should not be
written down as one. Four gaps, each of which would hide an abuse:

1. **`access_log=False`.** The bridge has never logged a single HTTP request.
   `journalctl -u hexis-bridge` holds 76 lines and stops at the 8 August
   deploy; `server.log` contains startup lines, 9,099 socket errors, and
   Flask access lines from May belonging to the *old* app. The instruction to
   grep the service history found nothing because nothing was ever written.
   The only reason there is any request record at all is nginx, which nobody
   put there for this purpose.
2. **nginx keeps 15 days.** Coverage runs 29 Jul – 12 Aug. `/stake/credit` was
   live from 22 July, when the stake router was wired. **Seven days have no
   surviving request log** and never will.
3. **`credit()` writes no audit event.** It calls `_credit_in_tx` and returns.
   So the reconciliation above cannot be checked against an independent record
   of issuance — "implied credit" is derived from the balance itself and is
   explained by itself. The books balance, but they balance by construction.
   The invariant *every ECU traces to a legitimate issuance event* cannot be
   evaluated on this data, because legitimate issuance was never recorded.
4. **nginx can be bypassed.** Port 8400 is firewalled to Cloudflare ranges but
   listens on `0.0.0.0`, and Cloudflare ranges include anything egressing from
   a Cloudflare Worker. A request straight to `:8400` skips nginx and leaves
   no trace anywhere.

Recorded as: **live and unauthenticated for at least ten days; two calls
found, both ours; no evidence of abuse; not verifiable to the standard of
proof, for the four reasons above.**

#### What was done about the four gaps, 2026-08-13

Three of them were live conditions, not historical ones — they would have
weakened the *next* investigation the same way. The four are left above as
written, because they record what was true when the question was asked.

2. **Closed forward only.** `rotate 14` → `rotate 180` in
   `/etc/logrotate.d/nginx`. Half a year of coverage instead of fifteen days,
   for single-digit megabytes. The seven lost days are still lost — retention
   cannot be applied backwards, which is the entire lesson.
3. **Closed.** `credit()` now writes an `escrow_credit` audit event carrying
   amount, resulting balance and a caller-supplied reason. From today the
   invariant is evaluable; for everything before today it is not, and no
   later change can make it so.
4. **Closed.** `BIND_HOST` is `127.0.0.1`. nginx already proxied to
   `127.0.0.1:8400`, so nothing public changed and the direct path is simply
   gone. The ufw rules admitting Cloudflare ranges to 8400 are now inert and
   were left alone; **port 8401 (`hexis_api`) is still on `0.0.0.0` and is the
   same hole, still open.**

   > **Correction, 2026-08-20.** The last sentence stopped being true on
   > 2026-08-14, the day after it was written, and this file did not say so for
   > six days. The Trust API was bound to `127.0.0.1:8401` that day and the ufw
   > rules for both 8400 and 8401 were deleted rather than left inert;
   > `DEPLOY.md`, "Both services, and the firewall table", has recorded it since.
   > Re-measured on the host today: both services listen on `127.0.0.1` only,
   > `ufw` admits 22 and 80 and nothing else, and both ports refuse a connection
   > from outside.
   >
   > The sentence above is left standing because it is what was true when the
   > question was asked, which is this section's whole convention. But the defect
   > is worth naming rather than quietly appending to: **a document that
   > overstates a hole decays the same way one that understates a control does.**
   > It was found by re-reading this file before publishing it, which is a slow
   > way to find a six-day-old error, and the standing gap is that nothing
   > reconciles an entry marked "still open" against the host it describes.

1 stays open by choice. The application still logs no requests; nginx is the
record, and loopback binding is what makes that record complete for anything
arriving over the network. It is not complete for anything originating on the
host — see DEPLOY.md, "The request log, and what makes it complete".

### Found on the way: money moved by hand, invisibly

The five collusion-test locks had their rows deleted from `consumer_stake`,
`worker_stake` and `pair_activity` afterwards, and `collusion_attacker_…`'s
escrow row with them. That silently destroyed **2,750 ECU** — locked funds
that can now never be released, with no burn recorded anywhere.
`cleanup_severity_testdata.py` does the same smaller thing deliberately:
`UPDATE stake_escrow SET balance=0 WHERE actor_id='SEVERITY_RESERVE'`.

Both were testnet housekeeping and neither is an incident. The pattern is
worth naming anyway: **the conservation check added today would not catch
either of them.** It tests code paths against a throwaway database. Direct SQL
against the live file is outside every control this system has, and it is
currently the only way the books have ever actually been broken.

Only the audit chain survived the deletion — the five locks are still there,
hash-linked, with their amounts, which is the only reason the 2,750 could be
accounted for at all. That is the argument for append-only records, made by
an accident rather than by an attack.

---

## 2026-08-12 — `/stake/credit` minted ECU from nothing, unauthenticated, in production

**Severity: live in production, reachable by anyone on the internet, and
worse than anything else on this page.**

`POST /stake/credit` was served with no authentication of any kind. It calls
`StakeManager.credit()`, which reaches `_credit_in_tx` (`hexis_stake.py`):

```sql
INSERT INTO stake_escrow(actor_id, balance, updated_at) VALUES(?,?,?)
ON CONFLICT(actor_id) DO UPDATE SET balance = balance + excluded.balance
```

Nothing is debited anywhere. The balance is created.

The attack: post `{"actor_id": "<any address>", "amount": 1e9}`. There is no
counterparty, no source account, no cap, and no ceiling on `amount` beyond
`> 0`. Escrow balance is what backs stake locks; stake locks gate
`/job/request` through `require_both_locked`; accepted jobs are what mine
HEXIS. So the endpoint was a direct path from an unauthenticated HTTP request
to unbounded standing in the trust system the whole project exists to
measure. It required no key, no registration, and no prior state.

**How the intermediate state was wrong too.** As of 3c the endpoint required
an RFC 9421 signature bound to `actor_id`. That stops anonymous callers and
makes every call attributable, and it is not a fix: a registered actor signing
for its own `actor_id` is signing to create its own balance. The signature
verifies perfectly and the mint proceeds. Authentication answers *who sent
this*. It says nothing about whether they may do it. Shipping the signature
and calling the endpoint handled would have been the worse outcome of the two,
because the hole would then have been behind a control that looked like it
was doing something.

**Fixed 2026-08-12.** `POST /stake/credit` returns 410 Gone unconditionally
and is listed in `UNGUARDED_WRITE_ROUTES` with that reason — the same
treatment `/stake/release` received in stake v3. `StakeManager.credit()`
survives as a method because the PoSP audit reward path calls it internally
(`_posp_reward`), and because the startup conservation check needs a way to
seed a throwaway database. Nothing reachable over HTTP reaches it.

Deposits from outside the system still have no home. When they get one it has
to debit a real source, not conjure a credit — which is the thing this
endpoint never did.

Forensics on whether it was ever abused: see the entry at the top of this
file. Short version — two calls, both ours, books reconcile, and the evidence
is not strong enough to call it proven.

---

## 2026-08-12 — `/stake/expire` could be triggered by any registered actor

`POST /stake/expire` sweeps every consumer lock past its TTL and moves escrow
on all of them at once. It takes no actor argument, so there is nobody whose
signature could authorise it. The 3c wiring bound it to `ANY_REGISTERED_ACTOR`
— a guard whose entire meaning was *somebody was logged in* — and flagged it
for a decision rather than resolving it.

Same shape as `/stake/release` and `/stake/credit`, so it now has the same
treatment: 410 Gone, internal-only. `expire_stale()` remains a method.

Gating it closed the hole and opened a gap — for a few hours, nothing called
`expire_stale()` at all, so gap #3 was live again: a counterparty vanishes and
the other side's stake stays locked for good.

**Decided and implemented the same day: an in-process scheduled sweep.**
`stake_expiry_sweep()` runs every six hours as an asyncio task started in the
lifespan. No endpoint, no operator key, nothing outside the process can
trigger it, and nothing needs authenticating because no caller exists.

Three details that are load-bearing rather than decorative:

- **It sleeps before the first run.** The unit is `Restart=always` with
  `RestartSec=5`, so a bridge that cannot stay up restarts endlessly. A
  money-moving sweep on the startup path would fire every five seconds for as
  long as that lasted. Nothing here is urgent to the second — the TTL floor is
  an hour.
- **It runs off the event loop** via `asyncio.to_thread`. `expire_stale()` is
  synchronous SQLite over an unbounded number of locks, and on the loop it
  would stall every request for its duration.
- **Every sweep writes one `stake_expiry_sweep` audit event, including the
  ones that expire nothing**, and so do failures. A sweep that only records
  itself when it acts is indistinguishable from a sweep that stopped running,
  and a sweep that stopped running is the exact failure this replaced an
  endpoint to avoid. `expire_stale()` also writes its own `stake_expire` event
  per lock; the sweep event is the proof the sweep happened at all.

Covered by `test_identity_3c.py` [16], which runs the real timer at a
one-second cadence and checks the lock expires, ECU is conserved, and both
kinds of event are written.

The `ANY_REGISTERED_ACTOR` binding has been deleted rather than left unused.
A helper that means "authentication standing in for authorisation" is one that
gets reached for again.

---

## 2026-08-12 — Six audit action types were rejected and silently discarded

> **Superseded 2026-08-13.** There were twelve, not six. The fix recorded
> below — add the six that were found by reading the code — is the one this
> file's newest entry exists to criticise. Read that entry instead.

Found while wiring the sweep above: its `stake_expiry_sweep` event would not
write. `AuditLogger._insert` raises `ValueError` for any `action_type` not in
`VALID_ACTIONS`, and every caller in `hexis_stake.py` and `hexis_severity.py`
reaches it through `except Exception: pass`.

Six types emitted by live code were never in the set:

```
escrow_transfer   stake_abort   stake_expire
severity_classify severity_repay severity_victim_payout
```

Every one had been discarded for as long as it existed. The production chain
confirms it: `bridge.db` holds a `consumer_stake` row with status `aborted`
and **no `stake_abort` event anywhere**. The money moved; the chain does not
know it did.

**The comment on the swallow said the opposite, and that is the part worth
recording.** It read *"audit that bai khong lam hong giao dich kinh te (chain
verify se lo gap)"* — chain verification will reveal the gap. It will not. A
rejected event is never written, `sequence` stays contiguous, and
`verify_audit_chain.py` reports a perfect chain. There is no gap to find. The
comment described a safety net that does not exist and, by describing it,
stopped anyone looking for one.

Fixed: all six added to `VALID_ACTIONS`, along with `stake_expiry_sweep` and
`escrow_credit`; the comment corrected in place to say what actually happens.
The allowlist now carries a note that adding an `_audit(...)` call without
adding its type here writes nothing.

**Also fixed: `credit()` now writes an `escrow_credit` event.** It was the one
money path that recorded nothing at all — which is precisely why the forensic
review above could establish what escrow holds today but had no independent
record of issuance to check it against. An unaudited mint cannot be
reconciled, only believed. `_posp_reward` now passes a reason naming the audit
it is paying for.

---

## 2026-08-12 — Balances are reported from stored variables, not derived from a ledger

Recorded the same day Harmony (ONE) was exploited for roughly 4 billion tokens
— about 26% of supply — minted through empty blocks. The detail worth copying
is not the exploit. It is that Harmony's own `totalSupply` endpoint went on
reporting the pre-inflation figure, and an independent on-chain analyst found
the inflation before their monitoring did. A supply figure that cannot
disagree with the rows beneath it cannot report a breach.

Every balance this bridge reports is read from a stored variable that is
mutated in place. Named, because that was asked for:

| Endpoint | Field | Source |
|---|---|---|
| `GET /stake/balance/{actor_id}` | `balance` | `SELECT balance FROM stake_escrow` |
| `GET /verify/{actor_id}` | `newflow_balance_ecu` | `ChainState.balances[addr]` |
| `GET /status` | `workers[].balance_ecu` | `ChainState.balances[addr]` |
| `POST /worker/register` | `balance_ecu` | `ChainState.balances[addr]` |
| `POST /job/{job_id}/complete` | `worker_balance` | `ChainState.balances[addr]` |

`ChainState.balances` is a plain `dict[str, int]` that every settlement,
transfer and faucet claim writes to directly. `stake_escrow.balance` is a
column updated with `balance = balance + ?`. Neither is derived from
anything; each *is* the record.

`hexis_total` is the exception and shows what the alternative looks like:
`get_hexis_score()` sums `hexis_index` records for the actor. Corrupt the
stored number and there is no stored number to corrupt.

**The gap is reconciliation, and one half of it already exists unused.**
`newflow_core.Ledger` is an append-only list of `LedgerEntry` recording the
same movements `ChainState.balances` is mutated by, with `total_received()`
and `total_sent()` already written. Nothing compares the two. Two independent
records of the same money, and no check that they agree — which is exactly
the position Harmony was in.

Escrow has no such ledger at all. `stake_escrow` is a balance column and
nothing else; the audit chain logs actions, not double-entry movements, so it
cannot be summed into a balance.

**Partially addressed 2026-08-12.** `audit_ecu_conservation()` refuses to boot
the bridge unless every stake operation conserves ECU: it runs `lock`,
`settle_complete`, `abort_unstarted`, `expire_stale`, `dispute_slash` (both
sides) and `transfer` against a throwaway database and compares
`StakeManager.ecu_total()` before and after. Conserving operations must not
move the total; `dispute_slash` must reduce it by exactly the stake it wrote
to `slash_log`. `ecu_total()` is computed from the tables on every call and
never cached, for the reason above.

That enforces the invariant on the *code paths*. It does not verify the *live
database*, and cannot, because a snapshot of balances has nothing to be
reconciled against. Reconciling `ChainState.balances` against `Ledger`, and
giving escrow an entry ledger of its own, is the remaining work.

**Extended 2026-08-14 to cover the second database.** The Trust API keeps three
more figures of the same kind in `hexis.db`, and they were not in scope when
this item was written because nobody had read that service:

| Figure | How it is maintained | Served by |
|---|---|---|
| `actors.hexis_score` | `hexis_score = hexis_score + excluded.hexis_score` | `/trust/{id}`, `/leaderboard` |
| `network_stats.total_hexis_mined` | `value = value + ?` | `/status` as `hexis_mined` |
| `network_stats.total_events` | `value = value + 1` | `/status` as `events` |

None is ever recomputed. The `events` table holds every movement that produced
them and **nothing compares the two** — which is notable because here, unlike
the bridge, both records already live in the same file. Reconciling them is a
`SUM` and a comparison; the reason it does not exist is that nobody wrote it,
not that it is hard.

`/status` also reports `actors` from `COUNT(*)`, which *is* computed. So one
response mixes both conventions with nothing marking which figure is which —
the specific way a reader ends up trusting a stored number because the number
beside it happens to be derived.

These three go on this item rather than being patched where they were found.
The job is one job — a ledger to reconcile balances against — and doing it
piecemeal per figure produces exactly the arrangement that let twelve audit
action types disappear: a fix per instance, and the pattern still in place.

**The `hexis.db` half was closed the same day** — see the 2026-08-14 reconcile
entry at the top of this file. It was the smaller half for exactly the reason
written above: both records already lived in one file, so the work was a `SUM`
and a comparison, and what took the time was the float tolerance and the read
snapshot rather than the idea.

**Status: closed, 2026-08-16.** `hexis.db` reconciled at boot and hourly since
2026-08-14; `bridge.db` since 2026-08-16, against `ledger_entries` — the entry
ledger escrow did not have. `newflow_core.Ledger` turned out not to be the
missing half: it is rebuilt in memory at every start, like `ChainState`
itself, so reconciling the two would have compared two structures inside one
process against each other. The durable table was written instead. What remains
open is narrower and is recorded in that entry: **chain balances are still not
durable**, and cannot be until the validator and faucet wallets are.

---

## 2026-08-12 — Seven write endpoints were missing from the design inventory

The 3c design listed eight write endpoints. The application serves fifteen.
Six of the missing ones — `/stake/lock`, `/stake/release`, `/stake/abort`,
`/stake/expire`, `/stake/credit`, `/job/{job_id}/dispute` — live in
`hexis_stake.py`, which was not searched when the inventory was built. The
seventh is `/audit/seal`, excluded from the inventory as out of scope by
instruction; counting it back in is what makes fifteen rather than the
fourteen first recorded here.

They were found by the startup audit (`audit_write_route_protection`), which
refuses to boot the bridge if a write route carries no signature guard and is
not on an explicit exemption list. The audit was written expecting to catch
future omissions. It caught a present one on first run.

**This is the argument for closed-by-default, and it is worth more than the
fix it produced.** The inventory was not compiled carelessly. It was compiled
by reading the code, and it was still wrong by seven, because it was built
from the files someone thought to open. Per-route discipline fails the same
way every time: it protects what you remembered. Enforcement that runs over
the router protects what is actually there, including the routes nobody
remembered and the ones added six months from now by someone who never read
this file.

The three endpoints above this entry are all instances of the same lesson.
`/stake/credit` was live and unauthenticated in production for as long as it
existed, and it was not found by anyone reasoning about the design. It was
found by a check that enumerated reality and compared it against a list.

---

## 2026-08-12 — Starlette 1.0.0 lets the Host header rewrite the signed path

Found while answering "does anything in the signature path behave differently
between local 1.4.1 and the VPS's 1.0.0". It does.

Starlette 1.0.0 builds `request.url` by interpolating the Host header without
validating it:

```python
url = f"{scheme}://{host_header}{path}"
```

The RFC 9421 verifier derives `@authority`, `@path` and `@query` by running
`urlsplit()` over that string. So a Host header of `example.com/some/path?`
produces a URL whose parsed path is not the path FastAPI routed to — an actor
could sign for one endpoint and have a different one execute, defeating the
cross-endpoint binding that covering `@path` exists to provide.

Starlette 1.4.1 added Host validation. The VPS does not have it, and this
project does not pin transitive dependencies, so relying on the framework
version would make a security property depend on what pip happened to resolve.

Fixed in `hexis_identity.signing_url()`: path and query come from the ASGI
scope, which is what the router actually matched, and the authority comes from
a Host header validated locally. Identical behaviour on every starlette
version.

---

## 2026-08-12 — `bind_pubkey` allowed a permanent account lockout

Live in production for months, and missed by the codebase report of
2026-08-06, which described identity as "registration verifies nothing" and
stopped there.

`POST /worker/{address}/bind_pubkey` accepted any valid Ed25519 public key for
any address. It checked neither that the caller held the matching private key,
nor that the key had any relationship to the address.

The attack: anyone who knew a registered worker's address, before that worker
had bound a key, could bind a key of their choosing — or a random one. A
second bind with a different key returns 409, and there was no unbind. The
real holder was locked out of job completion permanently, with no recovery
path.

Fixed in 3b: the address is now derived from the key
(`address_encode(pubkey)`), the registration request must be signed by that
key, and `bind_pubkey` returns 410 Gone.

**Worth remembering how it was found.** The August 6 report read the code to
summarise it and produced an accurate summary that missed this. It surfaced
only when the code was read again looking for a specific attack. Reading for a
summary and reading for an exploit are different activities and find different
things.

---

## 2026-08-11 — `http-sf` is not a new dependency

Recorded in `requirements.txt` that `http-message-signatures` 2.0.1 pulls in
`http-sf`, and that it would need downloading on the next deploy. Wrong.

`pip show` in the local venv reports `Requires: cryptography` and nothing
else. Version 2.0.1 vendors the Structured Field Values parser inside the
package (`http_sfv/`).

The `http-sf >= 1.3.0` line that produced the error is real, but it is on the
project's `master` branch — development after 2.0.1, not the pinned release.
Reading a dependency off GitHub instead of off the installed artifact is what
caused it.

Consequence, in the better direction: deploying the identity layer requires
exactly one new package, and its only requirement is already satisfied by the
`cryptography` 41.0.7 on the VPS.

---

## 2026-08-11 — README described an address as an encoded public key

The design constraint section said an actor's identity is its Ed25519 public
key, "base58-encoded via `address_encode()`". An address is not an encoding of
the key. It is:

```
base58(0x4E ‖ sha256(pubkey) ‖ checksum)
```

A hash, and one-way: **the public key cannot be recovered from an address.**

The constraint itself was unaffected — the public key is still the primary
identifier. But the wrong description hides the reason the server must store
public keys separately at all, which is the whole basis of the identity layer.

---

## 2026-08-05 — Manual migrations are narrower than reported

Recorded in `MIGRATIONS.md` rather than here, and repeated for completeness:
`BAO_CAO_CODEBASE.md` stated that rebuilding the system requires the manual
migrations. Rebuilding from an empty database does not — `init_db()` creates
the columns. Only the currently running database needs them. This made the
rebuild risk lower than first assessed.
