# HEXIS MVP — Setup & Run Guide

> **Không tin chúng tôi?** Đúng thái độ. [VERIFY.md](VERIFY.md) — mọi
> khẳng định kèm lệnh kiểm, không cần xin phép ai. / **Don't trust us?**
> Correct. [VERIFY.md](VERIFY.md): every claim with the command that checks it.


## Check the audit chain yourself

Every action the live network takes is written to a hash-linked chain that is
signed daily with an Ed25519 key held off the server. You do not have to take
our word for any of that. This repository ships the verifier we use, and it
needs no key, no account and no permission — the endpoints it reads are public.

```bash
pip install cryptography
python3 verify_audit_chain.py
```

That is the whole command. It defaults to the live network at
`https://bridge.hexisfoundation.org`; pass `--url` to point it somewhere else.
It prints one line per check and exits `0` on PASS, `1` on FAIL.

```
Chain integrity
  PASS  sequence is contiguous
  PASS  content_hash recomputes for every row
  PASS  event_id recomputes for every row
  PASS  prev_hash linkage holds
  PASS  event count matches the server's own claim
Seal
  PASS  chain carries at least one seal
  PASS  published key is a PUBLIC key
  PASS  public key is Ed25519 and parses
  PASS  every seal signature verifies
  PASS  seal records agree with the rows they seal

RESULT: PASSED — all 10 checks passed
```

Three things worth knowing before you trust the output:

- **It ignores the server's own verdict.** There is a `/audit/chain/verify`
  endpoint. This script does not call it, because a server grading its own
  homework proves nothing. Every hash is recomputed here from the published
  spec, and the script deliberately does not import our audit module — sharing
  code with the thing under test lets a bug hide itself.
- **What it cannot establish.** That the events existed *before* the first
  seal ran. A signature starts a clock; it cannot wind one back. Only an
  anchor published outside our control would settle that, and there is none
  yet.
- **Read it before running it.** It is ~350 lines of standard library plus
  `cryptography`, MIT licensed. Rewriting it from scratch against the spec
  in the endpoint response is a better check than running ours.

This verifier existed and passed since 2026-08-07, but until 2026-08-18 it
lived only in a private repository — which meant nobody outside could run it.
That is recorded as a correction, not presented as a feature.

---

## Check that the chain is older than we say

The verifier above proves the chain is internally consistent and that we signed
it. It cannot prove the events existed *before* we signed them — a signature
starts a clock, it cannot wind one back. Both of those layers are ours.

From 2026-08-20 the sealed heads are also committed to Bitcoin, using
[OpenTimestamps](https://opentimestamps.org). A Bitcoin block timestamp is not
ours and not the calendar servers'; moving it means rewriting Bitcoin. It is the
first of the three layers that does not depend on trusting us.

The proofs are in [`ots/`](ots/), with the exact commands, what they prove, what
they do not, and what we can still do to you despite them, in
[`ots/HOW_TO_VERIFY.md`](ots/HOW_TO_VERIFY.md).

```bash
pip install opentimestamps-client==0.7.2
curl -sO https://hexisfoundation.org/ots/<name>.txt
curl -sO https://hexisfoundation.org/ots/<name>.txt.ots
ots verify <name>.txt.ots
```

Read the message, not the exit code: `ots verify` returns 1 both for a proof
that is still waiting for its first block and for one that is broken. A newly
stamped anchor is pending for a few hours and that is normal.

## What is still wrong

[**OPEN.md**](OPEN.md) lists the defects that are open right now. Each one
carries a **closing condition** rather than a date — what has to be true before
it is closed, checkable by you, including against us. Three of them gate a
specific action: the Trust API's authorisation gap closes *before* any key is
ever issued to a third party, and the rate limiter gets load-tested *before*
first public traffic.

## What this project got wrong

[**CORRECTIONS.md**](CORRECTIONS.md) is the running record of claims this
project made confidently and got wrong, and of live defects found after the
fact. It was kept privately from 2026-08-05 and published on 2026-08-20.

It is not a changelog and it is not a highlights reel. It contains, among
others: an endpoint that minted currency from nothing, unauthenticated, in
production for at least ten days; SSH accepting root passwords for months
while a config file said it did not; twelve audit event types silently
discarded, including a fraud that was caught and is not in the record;
the first record ever published naming three witnesses that do not exist;
and defects that are still open today, marked as open.

The reason for publishing it is the one in the paragraph above this section. A
record of our own errors that only we can read is worth what the unpublished
verifier was worth. Four operational details are generalised for publication
— the hour of a backup, a directory path, an account inventory, one IP address
— and each is marked in place where it stood, so you can see the size of what
is withheld. Nothing is rewritten and no finding is softened.

---

## Files in this package

**Read this before reading any code here.** On 2026-08-30 the running system
was compared file by file against this repository, and seven files with the
same name held different bytes — `hexis_bridge_v0.6.2.py` was published at
43,933 bytes while the file that actually runs is 230,682. Anyone who read the
published copy to check a claim about the bridge was reading a different
program with the same name. That is recorded in `CORRECTIONS.md`.

Most of the running modules are now here. **These are not, and are marked
reference-only until each is reviewed line by line:**

```
hexis_bridge_v0.6.2.py    REFERENCE ONLY — older/smaller than the file that runs
hexis_api_v0.6.1.py       REFERENCE ONLY — older/smaller than the file that runs
hexis_ledger.py           REFERENCE ONLY — older than the file that runs
hexis_audit.py            REFERENCE ONLY — older than the file that runs
hexis_api.py              REFERENCE ONLY — an early version, kept as history
hexis_api_v0.5.py         history
hexis newflow bridge.py   history — superseded by hexis_newflow_bridge.py
hexis_mining_v0.2.py      history — superseded by hexis_mining.py
hexis_classifier.py       history
hexis_data_collector.py   history
hexis_pipeline.py         history
hexis_genesis.py          history
```

They were held back by a secrets scan run before publication, not by choice:
each contains something — an environment-variable name, a key file path, a
server address — that a person has to look at before it is published, and
"probably fine" is not the standard this repository is arguing for. They stay
until that review happens. Do not treat them as the running system.

**These are the running modules, synced 2026-08-30:**

```
hexis_newflow_bridge.py   NEWFLOW bridge
newflow_core.py           Core ledger and address encoding
scs_engine.py             Supply and halving constants
hexis_mining.py           Mining algorithm and tokenomics constants
hexis_geo.py              Geographic context multiplier
hexis_identity.py         Identity registry — public key is the primary key
hexis_ledger_entries.py   Double-entry ledger for escrow and balances
hexis_reconcile.py        Reconciliation between the two records
hexis_sampling.py         PoSP commit-reveal audit sampling
hexis_severity.py         Damage tiers
hexis_cid.py              Content addressing
whitepaper_figures.py     Boot validator: refuses to start when the whitepaper
                          and the code disagree about a NUMBER. Numbers only —
                          a passing boot says nothing about any mechanism.
verify_audit_chain.py     Third-party verifier for the live audit chain
CORRECTIONS.md            What this project claimed and got wrong, newest first
OPEN.md                   Defects still open, each with its closing condition
ots/                      Bitcoin anchors for sealed chain heads, + HOW_TO_VERIFY
```

The step-by-step guide below predates all of this and describes the original
v0.1 pipeline. It is kept because deleting it would be tidying history, but it
is not a description of what runs today.

---

## Step 1 — Test the core algorithm (no setup needed)

```bash
python hexis_mining_v0.1.py
```

This runs with zero dependencies. Confirms the math works.

---

## Step 2 — Get your API keys (free)

### News API (for witness collection)
1. Go to https://newsapi.org/register
2. Create a free account
3. Copy your API key
4. Put it in your environment — **not in a file**:

```bash
export NEWS_API_KEY="your_key_here"
```

Free tier: 100 requests/day, articles from past 30 days.
Paid tier ($449/mo): full archive access.

### Pinata (for IPFS storage)
1. Go to https://app.pinata.cloud
2. Create a free account
3. Go to API Keys → Generate New Key → select "pinFileToIPFS" + "pinJSONToIPFS"
4. Copy the JWT token
5. Put it in your environment — **not in a file**:

```bash
export PINATA_JWT="your_jwt_here"
```

Free tier: 1 GB storage, unlimited pins.

**Do not edit `PINATA_JWT` in `hexis_ledger.py` or `hexis_pipeline.py`.** Steps
5 and 6 used to say to do exactly that, and that was wrong: this is a public
repository, and a key pasted into a tracked file is published by the next
`git push` and stays in the history after any commit that removes it. The
placeholder in those files is there to be left alone.

For a long-running service, put it in the service environment rather than a
shell — a systemd drop-in at mode 600, or an `EnvironmentFile` that is not
world-readable. The same rule applies to every other key on this page.

### GDELT (no key needed)
Free, public, no registration. Just works.

---

## Step 3 — Test each module

```bash
# Test data collection (GDELT works without key, News API needs key)
python hexis_data_collector.py

# Test IPFS ledger (works offline, IPFS needs key for cloud storage)
python hexis_ledger.py

# Test adversarial classifier (no dependencies for Approach A)
python hexis_classifier.py
```

---

## Step 4 — Run the full pipeline

Rename the mining file first (Python cannot import files with dots in names):

```bash
cp hexis_mining_v0.1.py hexis_mining.py
```

Then edit `hexis_pipeline.py`:
- Change `from hexis_mining_v0_1 import` to `from hexis_mining import`
- Set `dry_run = False` to enable IPFS storage

Export the keys rather than editing them into the file — see Step 2:

```bash
export NEWS_API_KEY="..." PINATA_JWT="..."
```

Then run:

```bash
python hexis_pipeline.py
```

---

## Step 5 — Optional: NLP classifier for unknown sources

```bash
pip install transformers torch
```

First run downloads ~1.5 GB model (facebook/bart-large-mnli).
In `hexis_pipeline.py`, set `use_nlp_classifier = True`.

---

## What you provide manually (always)

These four values require human judgment — they are intentionally not automated:

| Variable | Description | Example |
|---|---|---|
| `asset_could_have_taken` | Max value actor could have taken/kept | $200 (wallet), $50M (political capital) |
| `asset_actually_returned` | Value actually returned/honored | Same as above if fully honest |
| `prob_betrayal_detected` | Probability betrayal would be caught | 0.05 (anonymous), 0.95 (public figure) |
| `gain_if_betrayed` | How much betrayal would have gained | Market value of betrayal |

This is the human proof-of-work. No automation here is intentional.

---

## Architecture notes

**Why not blockchain for storage?**
IPFS uses content-addressing — the CID is derived from the content.
Same content always = same CID. Cannot be altered without changing the CID.
This is sufficient for immutability without needing BTC/ETH dependency.

*Status, 2026-08-17.* That paragraph described an intention for most of this
project's life. One record is now actually on IPFS and checkable by anyone:

```
bafkreiaii4hvi3oiolthzme7wvevsou7xti2vinzxawhj2xiyeay2acjd4
```

```sh
curl -sSL "https://ipfs.io/ipfs/bafkreiaii4hvi3oiolthzme7wvevsou7xti2vinzxawhj2xiyeay2acjd4" -o record.json
python3 - <<'EOF'
import base64, hashlib
b = open("record.json","rb").read()
print("b" + base64.b32encode(bytes([1,0x55,0x12,0x20]) + hashlib.sha256(b).digest()).decode().lower().rstrip("="))
EOF
```

`ipfs.io` is not ours and is not Pinata's, and the CID recomputes from the bytes
it serves — so verifying this needs no trust in whoever published it. Two things
this does not claim: the 36 records minted before 2026-08-16 are **not** on IPFS
and never will be, because their content was never retained; and a fresh pin
takes on the order of ten minutes to become retrievable from a third-party
gateway, so a single 504 means "wait", not "missing".

**Why GDELT and not Twitter/X API?**
GDELT covers 65+ languages, 250+ countries, updated every 15 minutes.
Twitter API is expensive ($100/month for basic access).
For mention counts over time, GDELT is more reliable and free.

**Why AllSides for source lean?**
AllSides is the most widely cited, methodologically transparent
political lean rating service. Ratings are updated annually.
The database in hexis_classifier.py covers 200+ major outlets.

---

## Extending the source database

Add any outlet to `SOURCE_LEAN_DATABASE` in `hexis_classifier.py`:

```python
SOURCE_LEAN_DATABASE = {
    ...
    "your outlet name": 0,   # -2=far left, -1=left, 0=center, +1=right, +2=far right
    ...
}
```

Add any actor to `ACTOR_LEAN_DATABASE`:

```python
ACTOR_LEAN_DATABASE = {
    ...
    "your_actor_id": 1,   # their political lean
    ...
}
```

---

## Version history

v0.1 — April 2026 — Initial release
    Core mining algorithm
    Data collection (News API + GDELT)
    IPFS ledger (Pinata + local daemon)
    Adversarial classifier (database + NLP fallback)
    Full pipeline

---

*No authors. No foundation. No pre-mine.*
*The protocol belongs to the behavior it records.*
