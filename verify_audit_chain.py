#!/usr/bin/env python3
"""
verify_audit_chain.py — verify a HEXIS audit chain as a sceptical outsider.

MIT licensed, same as the rest of hexisfoundation/hexis-protocol. Copy it,
read it, change it, run it against us. It needs no key, no account and no
permission from anyone: every endpoint it touches is public.

This script assumes the server is lying and tries to catch it. It talks only
to public HTTP endpoints and trusts nothing the server asserts about itself:
the /audit/chain/verify verdict is deliberately ignored, because a server
grading its own homework proves nothing.

It deliberately does NOT import hexis_audit. Every hash is recomputed from
the published specification. Sharing code with the thing under test would
mean a bug in that code hides itself from the check.

    Dependencies: Python standard library, plus `cryptography` for Ed25519.

Usage:
    python3 verify_audit_chain.py                        # the live network
    python3 verify_audit_chain.py --url http://127.0.0.1:8400
    python3 verify_audit_chain.py --url http://... --expect-fail   # tamper test

Exit codes:
    0  every check passed (or failed as expected under --expect-fail)
    1  verification failed
    2  could not run the checks at all (network, missing dependency)

What is checked
    1. the chain is contiguous — no missing sequence numbers
    2. content_hash recomputed independently for every row
    3. event_id recomputed independently for every row
    4. prev_hash of each row equals event_id of the row before it
    5. seal signatures verify against the published Foundation public key
    6. each daily_seal record points at a row that really carries that signature

What CANNOT be checked from here
    That the events existed before the first seal ran. A seal starts the
    clock; it cannot wind it back. Only an anchor published outside the
    operator's control can establish that.
"""

import argparse
import hashlib
import json
import sys
import urllib.error
import urllib.request

GENESIS_PREV_HASH = "GENESIS"
HTTP_TIMEOUT = 30
DEFAULT_URL = "https://bridge.hexisfoundation.org"

# Identify the tool by name. urllib's default User-Agent is "Python-urllib/X.Y",
# which sits on Cloudflare's known-bot list and is answered with 403 — so a
# verifier using library defaults is turned away before it reads a single row.
# Saying who we are is also simply better manners than sending a library name.
USER_AGENT = "hexis-audit-verifier/1.0"

try:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    from cryptography.hazmat.primitives import serialization
    from cryptography.exceptions import InvalidSignature
    CRYPTO_AVAILABLE = True
except ImportError:
    CRYPTO_AVAILABLE = False


# --------------------------------------------------------------------------
# reporting
# --------------------------------------------------------------------------

class Report:
    def __init__(self):
        self.failures = []
        self.checks = 0

    def ok(self, label, detail=""):
        self.checks += 1
        print(f"  PASS  {label}" + (f"  ({detail})" if detail else ""))

    def fail(self, label, detail=""):
        self.checks += 1
        self.failures.append(f"{label}: {detail}" if detail else label)
        print(f"  FAIL  {label}" + (f"  ({detail})" if detail else ""))

    def note(self, text):
        print(f"        {text}")


# --------------------------------------------------------------------------
# hash recomputation — reimplemented from the spec, not imported
# --------------------------------------------------------------------------

def content_hash(action_type, actor_id, counterparty_id, payload_obj, ts_unix):
    canonical = json.dumps(
        {
            "action_type": action_type,
            "actor_id": actor_id,
            "counterparty_id": counterparty_id or "",
            "payload": payload_obj,
            "timestamp_unix": ts_unix,
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def event_id(prev_hash, ch, sequence, ts_unix):
    material = f"{prev_hash}|{ch}|{sequence}|{ts_unix}"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------
# fetching
# --------------------------------------------------------------------------

def http_get(url):
    req = urllib.request.Request(
        url, headers={"Accept": "*/*", "User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
        return resp.read()


def fetch_full_chain(base_url, page_size):
    """Page through /audit/chain/full until the server says there is no more."""
    events, from_seq, pages, meta = [], 0, 0, None
    while True:
        url = (f"{base_url}/audit/chain/full"
               f"?from_sequence={from_seq}&limit={page_size}")
        page = json.loads(http_get(url))
        pages += 1
        if meta is None:
            meta = {k: v for k, v in page.items() if k != "events"}
        events.extend(page["events"])
        if not page.get("has_more"):
            break
        nxt = page.get("next_from_sequence")
        if nxt is None or nxt <= from_seq:
            raise RuntimeError("pagination did not advance; aborting")
        from_seq = nxt
    return events, meta, pages


# --------------------------------------------------------------------------
# checks
# --------------------------------------------------------------------------

def check_chain(events, rep):
    if not events:
        rep.fail("chain is non-empty", "server returned zero events")
        return

    # 1. contiguity
    seqs = [e["sequence"] for e in events]
    expected = list(range(seqs[0], seqs[0] + len(seqs)))
    if seqs == expected:
        rep.ok("sequence is contiguous", f"{seqs[0]}..{seqs[-1]}, {len(seqs)} events")
    else:
        missing = sorted(set(expected) - set(seqs))
        rep.fail("sequence is contiguous", f"gaps at {missing[:10]}")

    # 2-4. hashes and linkage
    bad_content, bad_event, bad_link = [], [], []
    expected_prev = GENESIS_PREV_HASH if seqs[0] == 0 else None

    for e in events:
        try:
            payload_obj = json.loads(e["payload"])
        except (TypeError, ValueError):
            bad_content.append((e["sequence"], "payload is not valid JSON"))
            continue

        ch = content_hash(e["action_type"], e["actor_id"],
                          e["counterparty_id"], payload_obj, e["timestamp_unix"])
        if ch != e["content_hash"]:
            bad_content.append((e["sequence"], "recomputed content_hash differs"))

        eid = event_id(e["prev_hash"], e["content_hash"],
                       e["sequence"], e["timestamp_unix"])
        if eid != e["event_id"]:
            bad_event.append((e["sequence"], "recomputed event_id differs"))

        if expected_prev is not None and e["prev_hash"] != expected_prev:
            bad_link.append((e["sequence"], "prev_hash does not match previous event_id"))
        expected_prev = e["event_id"]

    for label, bad in (("content_hash recomputes for every row", bad_content),
                       ("event_id recomputes for every row", bad_event),
                       ("prev_hash linkage holds", bad_link)):
        if bad:
            first = bad[0]
            rep.fail(label,
                     f"{len(bad)} bad row(s); first at sequence {first[0]}: {first[1]}")
        else:
            rep.ok(label, f"{len(events)} rows")


def check_seals(base_url, events, rep):
    signed = [e for e in events if e.get("signature")]
    seal_rows = [e for e in events if e["action_type"] == "daily_seal"]

    if not signed:
        rep.fail("chain carries at least one seal",
                 "no signed rows — the chain is unattested")
        return

    rep.ok("chain carries at least one seal", f"{len(signed)} signed row(s)")

    # 5. the published public key must verify every signature
    try:
        pem = http_get(f"{base_url}/audit/pubkey")
    except urllib.error.HTTPError as exc:
        rep.fail("Foundation public key is published",
                 f"HTTP {exc.code} from /audit/pubkey")
        return

    if b"PRIVATE" in pem.upper():
        rep.fail("published key is a PUBLIC key",
                 "endpoint served a PRIVATE key — critical")
        return
    rep.ok("published key is a PUBLIC key")

    try:
        pub = serialization.load_pem_public_key(pem)
    except Exception as exc:
        rep.fail("public key parses as PEM", type(exc).__name__)
        return
    if not isinstance(pub, Ed25519PublicKey):
        rep.fail("public key is Ed25519", type(pub).__name__)
        return
    rep.ok("public key is Ed25519 and parses")

    bad_sig = []
    for e in signed:
        try:
            pub.verify(bytes.fromhex(e["signature"]),
                       e["event_id"].encode("utf-8"))
        except InvalidSignature:
            bad_sig.append((e["sequence"], "signature does not verify"))
        except ValueError as exc:
            bad_sig.append((e["sequence"], f"malformed signature: {exc}"))

    if bad_sig:
        rep.fail("every seal signature verifies",
                 f"{len(bad_sig)} bad; first at sequence {bad_sig[0][0]}")
    else:
        rep.ok("every seal signature verifies", f"{len(signed)} signature(s)")

    # 6. each daily_seal record must point at a row that really carries it
    by_id = {e["event_id"]: e for e in events}
    mismatched = []
    for s in seal_rows:
        try:
            p = json.loads(s["payload"])
        except (TypeError, ValueError):
            mismatched.append((s["sequence"], "seal payload is not valid JSON"))
            continue
        target = by_id.get(p.get("sealed_event_id"))
        if target is None:
            mismatched.append((s["sequence"], "sealed_event_id not present in chain"))
        elif target.get("signature") != p.get("signature"):
            mismatched.append((s["sequence"], "recorded signature differs from the row"))

    if seal_rows:
        if mismatched:
            rep.fail("seal records agree with the rows they seal",
                     f"{len(mismatched)} mismatch; first at sequence {mismatched[0][0]}")
        else:
            rep.ok("seal records agree with the rows they seal",
                   f"{len(seal_rows)} seal record(s)")


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(
        description="Independently verify a HEXIS audit chain over HTTP.")
    # Defaulted, not required: a verifier that needs an argument before it will
    # say anything is one more step between a sceptic and an answer.
    ap.add_argument("--url", default=DEFAULT_URL,
                    help=f"base URL of the bridge (default: {DEFAULT_URL})")
    ap.add_argument("--page-size", type=int, default=1000)
    ap.add_argument("--expect-fail", action="store_true",
                    help="invert the exit code; for tamper tests")
    args = ap.parse_args()

    base = args.url.rstrip("/")

    if not CRYPTO_AVAILABLE:
        print("cannot run: the `cryptography` package is required for Ed25519",
              file=sys.stderr)
        return 2

    print(f"Verifying {base}")
    print("Treating the server as untrusted. Its own chain/verify verdict is ignored.\n")

    rep = Report()

    try:
        events, meta, pages = fetch_full_chain(base, args.page_size)
    except urllib.error.URLError as exc:
        print(f"cannot run: could not reach {base} ({exc})", file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"cannot run: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2

    print(f"Fetched {len(events)} events over {pages} page(s). "
          f"Server claims {meta.get('total_events')} total.\n")

    print("Chain integrity")
    check_chain(events, rep)

    # The server's own claim about how many events exist, cross-checked
    # against what it actually handed over.
    if meta.get("total_events") is not None:
        if meta["total_events"] == len(events):
            rep.ok("event count matches the server's own claim", str(len(events)))
        else:
            rep.fail("event count matches the server's own claim",
                     f"claimed {meta['total_events']}, received {len(events)}")

    print("\nSeal")
    check_seals(base, events, rep)

    print()
    failed = bool(rep.failures)
    if failed:
        print(f"RESULT: FAILED — {len(rep.failures)} of {rep.checks} checks failed")
        for f in rep.failures:
            print(f"  - {f}")
    else:
        print(f"RESULT: PASSED — all {rep.checks} checks passed")

    if args.expect_fail:
        if failed:
            print("\n--expect-fail was set and verification failed, as intended.")
            return 0
        print("\n--expect-fail was set but verification PASSED. "
              "Tampering went undetected — this is a serious problem.",
              file=sys.stderr)
        return 1

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
