# Verify us

This page is for an engineer who does not trust us, which is the correct
starting position. Every claim below comes with the command that checks it.
None of the commands need an account, a key, or permission from us, and the
strongest ones need no software of ours at all.

What is claimed: an append-only behavioral ledger, hash-linked, sealed with an
Ed25519 key that has never been on the server, and anchored into Bitcoin —
the one layer whose clock we cannot move. What is *not* claimed is just as
load-bearing, and is at the bottom.

Commands are written for macOS/BSD (`shasum -a 256`); on Linux use
`sha256sum`. The algorithm is always "sha256 of the bytes" — the binary name
is the only thing that changes.

---

## 1. The chain verifies, and your machine is the judge

```sh
git clone https://github.com/hexisfoundation/hexis-protocol
cd hexis-protocol
pip install cryptography
HEXIS_BRIDGE_URL=https://bridge.hexisfoundation.org python3 verify_audit_chain.py
```

The script states its stance in its second line of output: *"Treating the
server as untrusted. Its own chain/verify verdict is ignored."* It refetches
every event, recomputes every `content_hash` and `event_id` from content,
walks the `prev_hash` linkage, and verifies every seal signature against the
published key. Ten checks; the result is computed on your machine.

The public key it checks against is served at
`https://bridge.hexisfoundation.org/audit/pubkey`. Its fingerprint — sha256 of
the 32 raw public-key bytes, lowercase hex — is
`a59cad19d852ba6c045732fb9101407033808984df44cf4d4c58deeaed6c0086`.

## 2. The chain is older than we could fake

A signature starts a clock; it cannot wind one back. For that there is
Bitcoin. Each day's chain head is stamped through OpenTimestamps and the
proofs are published:

```sh
pip install opentimestamps-client
curl -sO https://hexisfoundation.org/ots/seal-000317-10cfb298d0575701.txt
curl -sO https://hexisfoundation.org/ots/seal-000317-10cfb298d0575701.txt.ots
ots --no-bitcoin verify seal-000317-10cfb298d0575701.txt.ots
```

`--no-bitcoin` because you probably run no bitcoind; the command prints the
Bitcoin block height and merkle root to check by hand on any explorer. Read
the output, not the exit code — `ots verify` exits 1 for a pending proof, a
broken proof, and a missing node alike. `ots info <file>.ots` exits 0, needs
no network, and a `BitcoinBlockHeaderAttestation(N)` line in it is the
confirmation. Full walkthrough, including verifying the raw 80-byte block
header yourself: [ots/HOW_TO_VERIFY.md](ots/HOW_TO_VERIFY.md).

That proof puts chain sequence 317 before Bitcoin block 963342 (mined
2026-08-20). Because the chain is hash-linked, the anchor covers every event
before it too.

## 3. A published document matches its seal

```sh
curl -s https://hexisfoundation.org/CORRECTIONS.md | shasum -a 256
curl -s https://bridge.hexisfoundation.org/audit/CORRECTIONS.md
```

The newest `document_seal` event in the second response names the sha256 of
the bytes the first one just gave you. Same check works for
`HEXIS_Whitepaper_v0.7.md` and `OPEN.md`. A seal proves what was published,
never when it was written — the Bitcoin anchor is what dates it.

## 4. A record verifies three independent ways

```sh
EID=25b0a06d5aed10b4990e5af84724827bb47afa4b488de0f3437e7821122bddc2
curl -s https://bridge.hexisfoundation.org/hexis/record/$EID/raw -o rec.json
shasum -a 256 rec.json
```

- **Against its address**: the sha256 you just computed is the digest inside
  the record's CID (`bafkreie73a6u…` — CIDv1/raw/sha2-256, four constant
  bytes and a hash; `hexis_cid.py` in this repo rederives it, or do it by
  hand from the base32).
- **Against itself**: sha256 of the record minus its `record_hash` field,
  JSON with sorted keys, equals `record_hash`.
- **Against the chain**: the record's `proof_hash` appears in the
  `hexis_mint` event for this `event_id` at
  `https://bridge.hexisfoundation.org/audit/<actor_id>` — and the chain is
  what step 2 anchored into Bitcoin.

## 5. The audit lottery cannot be aimed — including by us

Whether a job gets audited is `sha256(epoch_secret + job_id) < σ`. The
secret's hash enters the chain **before any job of its epoch exists**; the
secret itself is published when the epoch ends:

```sh
curl -s https://bridge.hexisfoundation.org/sampling/epochs
curl -s https://bridge.hexisfoundation.org/audit/posp_sampling
```

For any revealed epoch: check `sha256(secret)` equals the committed hash,
then recompute the selection for every `job_complete` in the window and
compare with the recorded `sampling_open` events. A mismatch in either
direction is our misconduct, provable by you.

## 6. The succession record shows its own mistakes

```sh
curl -s https://bridge.hexisfoundation.org/audit/foundation_key_succession
```

Five rows. Two designations that should have been refused (a fail-open bug,
2026-08-21), two voids retracting them with reasons, and one live designation
whose payload begins its `grants` field with the word `NOTHING`. The junk
rows still hash, still verify, and still stand — a system that could quietly
remove its own bad rows could not demonstrate it had not removed others.

## 7. What we could still do to you, stated plainly

- **Single node, our key.** Until someone else verifies under a key we do not
  hold, we are the residual judge. This is the highest-priority gap and the
  reason this page exists.
- **We hold the epoch secret during its epoch.** We could leak it to a
  favoured worker. The commitment removes retroactive aiming, not operator
  collusion.
- **One paid pinner.** The record's CID is provider-independent, but today
  only infrastructure we pay for serves the bytes.
- **No revocation.** If the seal key is stolen rather than lost, nothing
  published can say otherwise yet.

The full lists: [OPEN.md](https://hexisfoundation.org/OPEN.md) — every open
defect with the condition that closes it — and
[CORRECTIONS.md](https://hexisfoundation.org/CORRECTIONS.md) — everything we
got wrong, including the parts that are permanent.

## 8. What we are looking for

One engineer, independent of us, to do either or both:

- **Run a second node**: fetch the chain on your own schedule, verify it
  under your own key, publish your own attestation. The moment that exists,
  "the foundation is the residual judge" stops being true — and you would be
  the one who ended it.
- **Push one real job through it**: register a worker, complete a job, watch
  the mint, the sampling lottery, and the anchor happen to *your* record.

You would be the first party whose view of this system we cannot control.
Everything above is what we can show you before you decide to trust nothing
we say.

contact@hexisfoundation.org

---

*As of 2026-08-23: 444 chain events, 20 seal signatures, 9 Bitcoin-confirmed
anchors — each number measured by the commands above, which is also how you
should treat every number on this page: stale until you rerun them.*
