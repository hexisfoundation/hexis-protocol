# Bitcoin anchors — how to check one

This directory holds proofs that the HEXIS audit chain existed before a given
Bitcoin block. You do not need us, an account, or permission to check them.

## What is being claimed

Each pair of files makes one claim:

    seal-000317-10cfb298d0575701.txt        a chain head, as 64 hex characters
    seal-000317-10cfb298d0575701.txt.ots    an OpenTimestamps proof of that file

> **The bytes in the `.txt` existed before the Bitcoin block named in the proof
> was mined.**

That is the whole claim. Two things follow from it and one does not.

It **does** cover the entire chain up to that point. The audit chain is
hash-linked — every `event_id` is computed over the previous hash — so a head
commits to every event before it. Anchoring one head anchors the prefix.

It **does not** say the events are true. A timestamp is a lower bound on age.
It proves nothing about whether a recorded action happened as described.

It **does not** cover anything added afterwards. Later events need later
anchors.

## Why this layer exists at all

The chain already has two properties. Both are ours:

1. **Hash linkage.** You cannot alter a row without altering every row after
   it. Anyone can recompute this — that is what `verify_audit_chain.py` does.
2. **A daily Ed25519 seal**, signed with a key that is not on the server.

Neither answers the question the verifier has always said it cannot answer:
*did these events exist before the first seal ran?* A signature starts a clock;
it cannot wind one back. Both layers are produced by the same operator, so both
could in principle be produced again, in any order, by whoever holds the key.

A Bitcoin block timestamp is not ours. It is not the calendar servers'. Moving
it means rewriting Bitcoin. **This is the first layer of the three that does not
depend on trusting us**, which is the only reason it is worth having.

## Install the verifier

    pip install opentimestamps-client==0.7.2

It is not ours. It is the reference client from the OpenTimestamps project, and
checking our anchors with a tool we wrote would defeat the point.

## Check a proof

Fetch both files — the proof alone is not verifiable, because it attests to the
`.txt` and needs it present:

    curl -sO https://hexisfoundation.org/ots/seal-000317-10cfb298d0575701.txt
    curl -sO https://hexisfoundation.org/ots/seal-000317-10cfb298d0575701.txt.ots

    ots verify seal-000317-10cfb298d0575701.txt.ots

A confirmed proof prints a block height and a date:

    Success! Bitcoin block <height> attests existence as of <date>

## Read the exit code carefully

`ots verify` exits **0** only when the proof is in a block. It exits **1** both
for a proof that is *still pending* and for a proof that is *broken*, and those
mean opposite things:

| Output | Exit | Meaning |
|---|---|---|
| `Bitcoin block N attests existence as of …` | 0 | anchored |
| `Pending confirmation in Bitcoin blockchain` | 1 | too young; no block yet |
| anything else | 1 | the proof did not check out |

So `ots verify ... || echo BROKEN` will call a perfectly good anchor broken for
the first few hours of its life. Read the message, not the code. `ots info
<proof>` shows the same thing in more detail: a `BitcoinBlockHeaderAttestation(
<height>)` means anchored, a `PendingAttestation('<calendar>')` means waiting.

Pending is the normal state after stamping. OpenTimestamps batches submissions
and commits them on a cadence of hours; the proof upgrades itself when a block
lands, and a stamp that has not been upgraded yet is not evidence of anything
being wrong.

## Then connect it to the chain

The proof establishes the age of a hex string. To make that mean something,
check the string is a real chain head:

    cat seal-000317-10cfb298d0575701.txt
    # -> 10cfb298d0575701f7a463d31a11a0a7e6ca73e76e1fad183f057333e045907c

    curl -s https://bridge.hexisfoundation.org/audit/audit_chain
    # the ots_anchor events; each names the head it anchored and this proof file

    python3 verify_audit_chain.py
    # recomputes every hash in the chain from the published spec

The three together say: this head is in the chain, the chain recomputes, and
the head is older than a Bitcoin block we do not control.

## Compute a digest, on whatever you are running

The algorithm is SHA-256 of the bytes. The command differs by platform, and the
command is not the fact:

    macOS, the BSDs:      shasum -a 256 <file>
    Linux, coreutils:     sha256sum <file>

If you would rather not download the `.txt`, `ots verify -d <digest>` takes the
hex digest directly.

## What we can still do to you, stated plainly

Anchoring stops backdating. It does not stop everything.

- **We can decline to anchor.** A gap in this directory means no anchor was
  made, and the events in that gap have only the two weaker layers behind them.
  Check the gaps; they are visible in the filenames.
- **We can stop.** Nothing here obliges the next anchor to happen.
- **We can anchor a chain and later publish a different one.** You would catch
  it — the published head would not match the anchored one — but only if you
  check, which is the reason all of this is published rather than described.

None of these lets us make an event look older than it is. That one is closed,
and it is the only one this layer was ever meant to close.

## If a proof does not verify

Say so publicly. A failing anchor is a finding, and this project keeps a record
of its own defects at
[CORRECTIONS.md](https://hexisfoundation.org/CORRECTIONS.md) precisely so that
findings have somewhere to land.
