# HEXIS MVP — Setup & Run Guide

## Files in this package

```
hexis_mining_v0.1.py      Core algorithm — runs standalone, no dependencies
hexis_data_collector.py   Module 1 — News API + GDELT data collection
hexis_ledger.py           Module 2 — IPFS decentralized storage
hexis_classifier.py       Module 3 — Adversarial/neutral/allied classification
hexis_pipeline.py         Full pipeline — ties all modules together
```

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
