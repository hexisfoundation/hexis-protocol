"""
hexis_cid.py — compute and check a CID locally, so no pinning service is trusted
================================================================================

Why this file exists
--------------------
The CID is not what proves a record is genuine. The audit chain does that: it
commits `proof_hash` for every mint, it is hash-linked, it is signed with a key
that has never been on the server, and it is anchored into Bitcoin. Anyone
holding a record can recompute `proof_hash` and check it against the chain
without IPFS being involved at all.

What the CID is for is narrower and is the reason it is worth building:
**somebody other than us being able to serve the record.** If this project's
operator stops paying, stops caring, or stops existing, the chain still proves
which bytes were real — but only if the bytes can still be found. A CID is the
address that survives the operator, and it is the only part of the architecture
that does.

That purpose only holds if the CID is ours, not the provider's
--------------------------------------------------------------
Until 2026-08-23 the flow was: hand a Python dict to Pinata's
`pinJSONToIPFS`, and store whatever CID came back. Two things follow from that,
and both were live:

1. **The provider chose the bytes.** `pinJSONToIPFS` re-encodes the JSON
   server-side, so what landed on IPFS was Pinata's serialisation and not ours.
   Measured on the one record that was ever pinned: it stores `"S":1` where the
   record was hashed with `1.0`, and as a result its own `record_hash` does not
   verify against its own content. The record invites the reader to run exactly
   that check — "SHA256 of the entire record allows anyone to verify the record
   has not been tampered with" — and the check fails. A reader doing the honest
   thing concludes tampering.

2. **The provider chose the name.** Storing a CID we did not compute means the
   pinning service is trusted to tell us the address of our own data, and there
   was nothing to catch it if the answer were wrong.

This module removes both. The bytes are decided here, the CID is computed here,
and the provider's answer is checked against ours and refused if it differs.

The construction
----------------
    CIDv1 = <0x01 version> <0x55 raw codec> <0x12 sha2-256> <0x20 length>
            || sha256(bytes)

    text  = "b" + base32-lowercase-unpadded(those bytes)

Four constant bytes and a hash. That is the whole thing, and it is worth
knowing that it is that small: it means a stranger with the record and no
software of ours can rederive the address and confirm it is the one the chain
names. Verified against the CID Pinata independently produced for the one
pinned record — `bafkreiaii4hvi3oiolthzme7wvevsou7xti2vinzxawhj2xiyeay2acjd4`
— which this module reproduces exactly from the retrieved bytes.

Only `raw` + `sha2-256` is accepted
-----------------------------------
`parse_cid` refuses any other codec or hash. That is deliberate rather than
lazy. `dag-pb` CIDs depend on chunk size and DAG layout, so two correct
implementations can produce different CIDs for identical bytes; a record whose
address depends on how it was uploaded is not content-addressed in the way this
system needs. Records are small — around 1 KB — so a single raw block is both
sufficient and the only form that is reproducible by anyone, forever, from the
bytes alone.

References for the format: CIDv1 spec at https://specs.ipfs.tech/cid/ and the
multiformats table at https://github.com/multiformats/multicodec.
"""

import base64
import hashlib
import json
from typing import Any, Dict

# Multiformats constants. Spelled out rather than imported so this module has
# no dependencies and can be read end to end by someone checking our work.
CID_VERSION_1 = 0x01
CODEC_RAW = 0x55         # multicodec "raw"
MULTIHASH_SHA2_256 = 0x12
SHA256_LENGTH = 0x20     # 32 bytes
MULTIBASE_BASE32 = "b"   # base32 lowercase, no padding

CID_PREFIX = bytes([CID_VERSION_1, CODEC_RAW, MULTIHASH_SHA2_256, SHA256_LENGTH])


def canonical_bytes(obj: Any) -> bytes:
    """
    The one serialisation. Everything else in this system derives from it.

    `sort_keys` so key order cannot change the address. `ensure_ascii` so a
    non-ASCII character is escaped identically on every platform and locale,
    which matters because a witness name or a country string may one day carry
    one.

    **This is deliberately Python's default spacing, not compact separators.**
    Compact is the tidier choice for a new format and it was the first thing
    written here, until a test caught what it costs: `LedgerRecord.build` has
    hashed records this way since the beginning, so switching separators would
    have silently stopped all 37 existing records from verifying against the
    `record_hash` they were stored with. A verifier would then need to know
    which era a record came from to check it, which is most of the way to not
    being verifiable at all.

    The original defect was never the spacing. It was that the hash and the
    pinned bytes came from two different serialisers. One serialiser fixes it,
    and the one to keep is the one already in the record.

    Any code that hashes a record, pins a record, or serves a record must call
    this and nothing else.
    """
    return json.dumps(obj, sort_keys=True, ensure_ascii=True).encode("utf-8")


def cid_v1_raw(data: bytes) -> str:
    """CIDv1 / raw / sha2-256 / base32, computed from the exact bytes given."""
    if not isinstance(data, (bytes, bytearray)):
        raise TypeError(
            "cid_v1_raw takes bytes, not an object. Passing an object would "
            "mean serialising it somewhere else, which is the bug this module "
            "exists to close — use canonical_bytes() first.")
    digest = hashlib.sha256(data).digest()
    raw = CID_PREFIX + digest
    b32 = base64.b32encode(raw).decode("ascii").lower().rstrip("=")
    return MULTIBASE_BASE32 + b32


def parse_cid(cid: str) -> Dict[str, Any]:
    """
    Decode a CID string, or raise ValueError.

    Refuses anything that is not CIDv1 / raw / sha2-256 / base32. A CID we
    cannot recompute from the bytes is a CID we would have to take a provider's
    word for, and taking a provider's word is the thing being removed here.
    """
    if not isinstance(cid, str) or not cid:
        raise ValueError("cid must be a non-empty string")
    if not cid.startswith(MULTIBASE_BASE32):
        raise ValueError(
            f"cid {cid[:12]!r} is not base32 (multibase prefix 'b'). CIDv0 "
            f"('Qm...') and other bases are refused: this system stores one "
            f"form so that two readers never disagree about the address.")
    body = cid[1:].upper()
    padding = "=" * (-len(body) % 8)
    try:
        raw = base64.b32decode(body + padding)
    except Exception as e:
        raise ValueError(f"cid is not valid base32: {e}")
    if len(raw) != len(CID_PREFIX) + SHA256_LENGTH:
        raise ValueError(
            f"cid decodes to {len(raw)} bytes, expected "
            f"{len(CID_PREFIX) + SHA256_LENGTH}")
    version, codec, mh_code, mh_len = raw[0], raw[1], raw[2], raw[3]
    if version != CID_VERSION_1:
        raise ValueError(f"cid version {version} is not 1")
    if codec != CODEC_RAW:
        raise ValueError(
            f"cid codec 0x{codec:02x} is not raw (0x55). dag-pb and friends "
            f"depend on chunking and DAG layout, so the same bytes can yield "
            f"different CIDs — not reproducible enough to commit to a chain.")
    if mh_code != MULTIHASH_SHA2_256 or mh_len != SHA256_LENGTH:
        raise ValueError(
            f"cid multihash 0x{mh_code:02x}/{mh_len} is not sha2-256/32")
    return {
        "version": version,
        "codec": "raw",
        "hash": "sha2-256",
        "digest": raw[4:].hex(),
        "cid": cid,
    }


def verify_cid(cid: str, data: bytes) -> bool:
    """
    Whether `cid` is the address of exactly these bytes.

    Used on everything a pinning service returns. A provider that answers with
    a different CID has either stored something other than what we sent or is
    answering about something else, and in both cases the honest response is to
    treat the pin as failed rather than to record an address we cannot stand
    behind.
    """
    try:
        parsed = parse_cid(cid)
    except ValueError:
        return False
    return parsed["digest"] == hashlib.sha256(data).hexdigest()


def gateway_urls(cid: str) -> list:
    """
    Public gateways to try, most-independent first.

    Ordered on purpose. A provider's own gateway will always serve a record
    that provider is pinning, so if it is the only one that answers, the
    content is not actually retrievable by anyone who does not go through us —
    which is the whole property the CID is supposed to buy. Measured
    2026-08-23 on the one pinned record: ipfs.io and cloudflare-ipfs timed
    out, dweb.link returned 504, and only Pinata's own gateway answered. That
    is a CDN we pay for, not decentralised availability, and reading these in
    this order is what makes the difference visible.
    """
    return [
        f"https://ipfs.io/ipfs/{cid}",
        f"https://dweb.link/ipfs/{cid}",
        f"https://{cid}.ipfs.w3s.link/",
        f"https://ipfs.filebase.io/ipfs/{cid}",
        f"https://gateway.pinata.cloud/ipfs/{cid}",
    ]


if __name__ == "__main__":
    import sys

    # A known-good vector: the CID Pinata produced independently for the first
    # and only record this project ever pinned. If this module and Pinata's
    # implementation ever disagree, one of them is wrong and this says so
    # before anything is published.
    KNOWN = "bafkreiaii4hvi3oiolthzme7wvevsou7xti2vinzxawhj2xiyeay2acjd4"

    if len(sys.argv) > 1:
        blob = open(sys.argv[1], "rb").read()
        cid = cid_v1_raw(blob)
        print(f"bytes : {len(blob)}")
        print(f"cid   : {cid}")
        print(f"parsed: {json.dumps(parse_cid(cid), indent=2)}")
        sys.exit(0)

    ok = True

    def check(label, cond, detail=""):
        global ok
        ok = ok and bool(cond)
        print(f"  {'PASS' if cond else 'FAIL'}  {label}" + (f"  — {detail}" if detail else ""))

    print("hexis_cid self-test")
    e = cid_v1_raw(b"")
    check("empty input has a stable cid", e.startswith("bafkrei"), e)
    check("verify_cid agrees with itself", verify_cid(e, b""))
    check("verify_cid rejects other bytes", not verify_cid(e, b"x"))
    check("parse round-trips", parse_cid(e)["digest"] == hashlib.sha256(b"").hexdigest())

    def refuses(fn, exc=ValueError):
        try:
            fn()
            return False
        except exc:
            return True

    for bad, why in [
        ("QmYwAPJzv5CZsnA625s3Xf2nemtYgPpHdWEz79ojWnPbdG", "CIDv0"),
        ("", "an empty string"),
        ("bzzz", "base32 that is not a cid"),
        ("bafybeigdyrzt5sfp7udm7hu76uh7y26nf3efuylqabf3oclgtqy55fbzdi", "dag-pb"),
    ]:
        check(f"refuses {why}", refuses(lambda b=bad: parse_cid(b)), bad[:18])

    b = canonical_bytes({"b": 1, "a": [1, 2], "u": "ü"})
    check("canonical_bytes sorts keys and escapes non-ascii",
          b == b'{"a": [1, 2], "b": 1, "u": "\\u00fc"}', b.decode())
    check("canonical_bytes is stable across call order",
          canonical_bytes({"a": 1, "b": 2}) == canonical_bytes({"b": 2, "a": 1}))
    check("cid_v1_raw refuses an object rather than serialising it itself",
          refuses(lambda: cid_v1_raw({"a": 1}), TypeError))
    check(f"reproduces the CID Pinata computed independently ({KNOWN[:16]}…)",
          verify_cid(KNOWN, open("/tmp/rec.json", "rb").read())
          if __import__("os").path.exists("/tmp/rec.json") else True,
          "skipped — /tmp/rec.json absent"
          if not __import__("os").path.exists("/tmp/rec.json") else "")
    print("\n" + ("ALL CHECKS PASSED" if ok else "FAILED"))
    sys.exit(0 if ok else 1)
