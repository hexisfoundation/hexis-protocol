"""
hexis_identity.py — RFC 9421 HTTP Message Signatures for HEXIS writes.

Added 2026-08-11 as the identity layer. Before it, an actor was a wallet
address and nothing more: registration verified nothing, no endpoint
authenticated anything, and the one signature check that existed guarded a
single endpoint.

WHAT THIS MODULE IS NOT
    It is not a cryptography implementation and must never become one. Every
    signing and verifying operation is performed by PyCA `cryptography` via
    http-message-signatures (pinned at 2.0.1). This module supplies policy:
    which components must be signed, how long a signature lives, what counts
    as a replay, and which actor a signature is allowed to speak for.

WHY POLICY LIVES HERE AND NOT IN THE LIBRARY
    RFC 9421 deliberately leaves these open, and the defaults are not safe for
    us. Three in particular:

    1. RFC 9421 NEVER SIGNS THE BODY. It signs headers and derived components
       only. A body is protected only if a Content-Digest header covers it AND
       the receiver recomputes that digest from the bytes it actually got. The
       library's own README says digest validation "remains the caller's
       responsibility". Skip it and an attacker rewrites the body at will while
       the signature still verifies perfectly. See _check_content_digest.

    2. THE LIBRARY'S DEFAULT max_age IS ONE DAY. That is a 24-hour replay
       window. We bypass the library's age check entirely and apply our own
       (see _check_freshness) so the policy is visible in code we read.

    3. NOTHING ENFORCES nonce. The library returns it; remembering it is ours.
       See _record_signature, where the remembering and the rejecting are a
       single atomic INSERT.

VENDOR INDEPENDENCE
    Ed25519 keys from any issuer work here. There is no dependency on
    Cloudflare or on Web Bot Auth infrastructure — we implement the same
    open standard those happen to use.
"""

import base64
import hashlib
import re
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import HTTPException
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from http_message_signatures import (
    HTTPMessageVerifier,
    HTTPSignatureKeyResolver,
    algorithms,
)
from http_message_signatures.structures import CaseInsensitiveDict

from newflow_core import address_encode


# ===========================================================
# POLICY CONSTANTS
# ===========================================================

SIG_ALGORITHM = algorithms.ED25519

# Pinned, and checked explicitly on every request. Letting the request choose
# its own algorithm is the classic algorithm-confusion hole: an attacker
# switches `alg` to a symmetric MAC and signs with a value they can guess.
SIG_ALG_NAME = "ed25519"

# Scopes a signature to this service. A signature minted for some other RFC
# 9421 service cannot be replayed against HEXIS.
SIG_TAG = "hexis"

# What a signature must cover to be accepted here.
#
# @target-uri is deliberately ABSENT. It includes the scheme, and behind a TLS
# terminating proxy the server sees http:// while the client signed https://,
# so every signature would fail. The obvious repair is to trust
# X-Forwarded-Proto — but that is a client-settable header, so the security
# property would then rest on nginx being configured to overwrite rather than
# pass it through, one config edit away from silently breaking.
#
# @authority + @path + @query says the same thing about WHERE the request went
# without depending on deployment topology at all. Losing scheme coverage is
# the deliberate price.
SIG_REQUIRED_COMPONENTS = ("@method", "@authority", "@path", "@query",
                           "content-digest")

SIG_MAX_AGE_S    = 300   # a signature older than this is refused
SIG_CLOCK_SKEW_S = 60    # tolerance for a client clock running fast

# How long a replay must be REMEMBERED. Never set below SIG_MAX_AGE_S +
# SIG_CLOCK_SKEW_S: forgetting a nonce while its signature is still inside its
# validity window is precisely the hole the nonce closes.
SIG_NONCE_TTL_S = SIG_MAX_AGE_S + SIG_CLOCK_SKEW_S   # 360

# How long the PROOF must survive. A different question, asked much later:
# was this write actually signed, and by which key? Pruning at the nonce TTL
# would leave replay protection intact and destroy the evidence — six minutes
# later the table could no longer say anything about the past.
#
# Deleting rows past 90 days cannot reopen a replay hole, because 90 days is
# enormously longer than 360 seconds. Growth is bounded and calculable: about
# 300 bytes per write, so writes/day x 300 bytes x 90 days. Lower it only as a
# deliberate decision to keep less evidence, never as a tidy-up.
SIG_RETENTION_S = 90 * 24 * 3600   # 90 days

# Only sha-256 is accepted. RFC 9530 allows more; a verifier that accepts
# whatever digest algorithm the request names lets the request pick a weak one.
_DIGEST_RE = re.compile(r"sha-256=:([A-Za-z0-9+/=]+):")


# ===========================================================
# ERRORS
#
# Every rejection is a 401 with a machine-readable `error` string. Distinct
# codes matter operationally: "your clock is wrong" and "your signature is
# forged" need very different responses from whoever is on the other end, and
# a single generic 401 for both leaves an honest operator with no way to tell.
# ===========================================================

def _reject(error: str, **extra):
    detail = {"error": error}
    detail.update(extra)
    raise HTTPException(status_code=401, detail=detail)


def _reject_stale(created: Optional[int], reason: str):
    """
    Clock problems are made DIAGNOSABLE rather than accommodated.

    A machine without NTP drifts by minutes or hours, so widening the skew
    tolerance would not rescue it anyway — it would only enlarge the replay
    window for everyone. Instead the response carries this server's clock, so
    an operator with a broken clock can see the difference immediately instead
    of staring at a generic 401.
    """
    now = int(time.time())
    _reject(
        "signature_expired_or_clock_skew",
        reason=reason,
        signature_created_unix=created,
        server_time_unix=now,
        server_time=datetime.now(timezone.utc).isoformat(),
        clock_difference_s=(created - now) if created is not None else None,
        max_age_s=SIG_MAX_AGE_S,
        clock_skew_tolerance_s=SIG_CLOCK_SKEW_S,
    )


# ===========================================================
# KEYS AND ADDRESSES
# ===========================================================

def load_pubkey(pubkey_hex: str) -> Ed25519PublicKey:
    """Parse a 32-byte Ed25519 public key given as 64 hex characters."""
    try:
        return Ed25519PublicKey.from_public_bytes(bytes.fromhex(pubkey_hex))
    except Exception:
        raise HTTPException(status_code=400,
                            detail="invalid ed25519 public key (expect 64 hex chars)")


def address_for_pubkey(pubkey_hex: str) -> str:
    """
    The address an Ed25519 public key commits to.

    An address is sha256(pubkey) wrapped in base58 with a checksum — one-way,
    so the key cannot be recovered from it. Deriving the address here instead
    of letting a caller assert one is what binds an identity to a key that its
    holder has actually proved possession of.
    """
    return address_encode(bytes.fromhex(pubkey_hex))


class _SingleKeyResolver(HTTPSignatureKeyResolver):
    """
    Resolves to exactly one key, whatever key_id is asked for.

    The key_id -> key lookup happens in the caller, which then hands us the
    key it found. Binding key_id to an actor is checked separately and
    explicitly (see the keyid check in verify_signed_write) rather than being
    an invisible side effect of resolution.
    """

    def __init__(self, public_key: Ed25519PublicKey):
        self._public_key = public_key

    def resolve_public_key(self, key_id: str):
        return self._public_key

    def resolve_private_key(self, key_id: str):
        raise NotImplementedError(
            "this process never signs; it only verifies")


# A hostname, and nothing that could smuggle a path, query or fragment into
# one. Ours, on purpose: see signing_url below for why we cannot borrow the
# web framework's.
_HOST_RE = re.compile(
    r"^([a-z0-9.-]+|\[[a-f0-9]*:[a-f0-9.:]+\])(?::[0-9]+)?$", re.IGNORECASE)


def signing_url(request) -> str:
    """
    Rebuild the URL that @authority, @path and @query are derived from.

    NOT str(request.url), and this is a security decision, not a style one.

    The verifier resolves those three components by running urlsplit() over
    whatever URL string it is handed (resolvers.py). Starlette 1.0.0 — the
    version running on the VPS — builds request.url by interpolating the Host
    header raw:

        url = f"{scheme}://{host_header}{path}"

    A Host header of `example.com/worker/register?` therefore yields a URL
    whose urlsplit() path is /worker/register while FastAPI routes the request
    to whatever the real path was. An actor could sign for one endpoint and
    have another one execute — destroying exactly the cross-endpoint binding
    that covering @path is supposed to provide.

    Starlette 1.4.1 fixed this by validating the Host header. The VPS does not
    have that fix, and pinning transitive dependencies is not something this
    project does, so relying on the framework version would make a security
    property depend on which starlette pip happened to resolve.

    So: path and query come from the ASGI scope, which is what the router
    actually matched and cannot be influenced by a header. The authority comes
    from the Host header validated against _HOST_RE here. Same behaviour on
    every starlette version.

    The scheme is a placeholder. @scheme is deliberately not covered (see
    SIG_REQUIRED_COMPONENTS), so this string being https:// while the client
    signed http:// changes nothing.
    """
    host = request.headers.get("host", "")
    if not _HOST_RE.fullmatch(host):
        _reject("invalid_host_header",
                detail_hint="Host must be a bare hostname with optional port")

    path = request.scope.get("path", "")
    query = request.scope.get("query_string", b"").decode("latin-1")
    return f"https://{host}{path}" + (f"?{query}" if query else "")


class _SignedMessage:
    """
    Adapter presenting a FastAPI request the way the verifier expects.

    headers MUST be a CaseInsensitiveDict. The library looks up
    "Signature-Input" with that exact capitalisation against message.headers
    directly (signatures.py:177), while Starlette hands out lowercase keys —
    a plain dict would make every signature look absent.
    """

    def __init__(self, method: str, url: str, headers):
        self.method = method
        self.url = url
        self.headers = CaseInsensitiveDict(headers)


# ===========================================================
# THE CHECKS
# ===========================================================

def content_digest_header(body: bytes) -> str:
    """The Content-Digest header value a client must send for this body."""
    return "sha-256=:" + base64.b64encode(hashlib.sha256(body).digest()).decode() + ":"


def _check_content_digest(headers: CaseInsensitiveDict, body: bytes):
    """
    Recompute the digest from the bytes actually received and compare.

    This is the step that makes body protection real. Without it the signature
    proves only that SOME body's digest was signed, and an attacker is free to
    substitute a different body entirely.
    """
    header = headers.get("content-digest")
    if not header:
        _reject("content_digest_missing")

    m = _DIGEST_RE.search(header)
    if not m:
        _reject("content_digest_unparseable",
                detail_hint="expected sha-256=:<base64>:")

    try:
        claimed = base64.b64decode(m.group(1))
    except Exception:
        _reject("content_digest_unparseable")

    actual = hashlib.sha256(body).digest()
    if not _constant_time_eq(claimed, actual):
        _reject("body_digest_mismatch",
                detail_hint="the body does not match the signed Content-Digest")


def _constant_time_eq(a: bytes, b: bytes) -> bool:
    import hmac
    return hmac.compare_digest(a, b)


def _check_components(covered):
    """
    Every component in SIG_REQUIRED_COMPONENTS must actually be covered.

    A signature that omits content-digest verifies perfectly and protects
    nothing about the body; one that omits @path can be lifted from one
    endpoint and replayed against another. Both are valid RFC 9421
    signatures — refusing them is policy, and policy has to be enforced.
    """
    # covered_components keys arrive quoted: '"@method"', '"content-digest"'
    present = {k.strip('"') for k in covered}
    missing = [c for c in SIG_REQUIRED_COMPONENTS if c not in present]
    if missing:
        _reject("signature_components_incomplete",
                missing=missing,
                covered=sorted(present),
                required=list(SIG_REQUIRED_COMPONENTS))


_CREATED_RE = re.compile(r"created=(\d+)")
_KEYID_RE = re.compile(r'keyid="([^"]+)"')


def claimed_keyid(headers) -> Optional[str]:
    """
    The keyid a request claims, read before anything is verified.

    For the handful of endpoints with no actor in the path or body — a
    maintenance sweep, say — there is nothing to look a key up by except the
    request's own claim. That is safe only because of what happens next: the
    claimed keyid is passed straight back in as expect_keyid, so the signature
    must verify under the key that keyid resolves to. Naming someone else's
    keyid gets you their key and a signature that fails against it.

    It decides WHICH key to check, never WHETHER the check passed.
    """
    m = _KEYID_RE.search(CaseInsensitiveDict(headers).get("signature-input") or "")
    return m.group(1) if m else None


def _reclassify_if_clock_problem(headers: CaseInsensitiveDict):
    """
    Turn an opaque verification failure into a clock diagnosis where it is one.

    Reads `created` straight out of the Signature-Input header text, which at
    this point is UNVERIFIED. That is safe because of what this function is
    allowed to do: it can only replace one rejection with a more specific
    rejection. It never accepts anything, never returns a value anyone acts
    on, and the request has already failed by the time it is called.

    The worst an attacker achieves by putting a false `created` here is a
    different error string and this server's clock — which any HTTP Date
    header discloses anyway.
    """
    raw = headers.get("signature-input") or ""
    m = _CREATED_RE.search(raw)
    if not m:
        return
    created = int(m.group(1))
    now = int(time.time())
    if created > now + SIG_CLOCK_SKEW_S:
        _reject_stale(created, "signature created in the future")
    if created < now - SIG_MAX_AGE_S:
        _reject_stale(created, "signature older than the validity window")


def _check_freshness(params: dict):
    """
    Apply our own validity window; never rely on the library's default.

    The library's max_age defaults to one day. We pass a window so large that
    its check can never fire, then decide here, where the numbers are readable
    and a future reader can see what they are.
    """
    created = params.get("created")
    if created is None:
        _reject("signature_created_missing")

    now = int(time.time())
    if created > now + SIG_CLOCK_SKEW_S:
        _reject_stale(created, "signature created in the future")
    if created < now - SIG_MAX_AGE_S:
        _reject_stale(created, "signature older than the validity window")

    expires = params.get("expires")
    if expires is not None and expires < now:
        _reject_stale(created, "signature expires parameter is in the past")


def _record_signature(db_path: str, *, nonce: str, keyid: str, method: str,
                      path: str, created: int, signature: str):
    """
    Record the signature, and reject a replay, in one atomic statement.

    nonce is the PRIMARY KEY, so a repeat fails the INSERT. Doing this as a
    SELECT followed by an INSERT would let two concurrent replays both pass
    the lookup before either wrote — the race is avoided by not having two
    steps.

    The row is also the durable evidence that this write was signed, and by
    which key. See SIG_RETENTION_S.
    """
    conn = sqlite3.connect(db_path, timeout=30)
    try:
        conn.execute(
            "INSERT INTO write_signatures "
            "(nonce, keyid, method, path, created_at, received_at, signature) "
            "VALUES (?,?,?,?,?,?,?)",
            (nonce, keyid, method, path, created, int(time.time()), signature),
        )
        conn.commit()
    except sqlite3.IntegrityError:
        _reject("signature_replayed",
                detail_hint="this nonce has been used before")
    finally:
        conn.close()


# ===========================================================
# PUBLIC ENTRY POINT
# ===========================================================

def verify_signed_write(*, method: str, url: str, headers, body: bytes,
                        pubkey_hex: str, expect_keyid: str,
                        db_path: str, path: str) -> dict:
    """
    Verify one signed write request, or raise HTTPException.

    The caller supplies the public key, having looked it up by whatever means
    is right for that endpoint: from the workers table for an established
    actor, or from the request body itself when an actor is registering and
    the server does not know them yet.

    expect_keyid is the actor this request is allowed to act for. A valid
    signature by actor A on a request that modifies actor B is a valid
    signature and an unauthorised write; this is where the two are separated.

    Returns {keyid, nonce, created} on success.
    """
    headers = CaseInsensitiveDict(headers)

    if "signature" not in headers or "signature-input" not in headers:
        _reject("signature_missing",
                detail_hint="RFC 9421 Signature and Signature-Input headers required")

    verifier = HTTPMessageVerifier(
        signature_algorithm=SIG_ALGORITHM,
        key_resolver=_SingleKeyResolver(load_pubkey(pubkey_hex)),
    )

    try:
        # max_age is set absurdly high on purpose: the library's age check is
        # disabled so that _check_freshness below is the only thing deciding.
        results = verifier.verify(_SignedMessage(method, url, headers),
                                  max_age=timedelta(days=36500))
    except Exception:
        # The library refuses a `created` in the future on its own, and raises
        # the same error it uses for a forged signature. Left alone, the most
        # common real clock fault — a machine running fast — would come back
        # as "signature_invalid", sending an operator hunting for a key
        # problem that does not exist. Recover the distinction.
        _reclassify_if_clock_problem(headers)
        # Deliberately no exception detail in the response. What failed and
        # why is useful to an attacker probing for which check they tripped.
        _reject("signature_invalid")

    if len(results) != 1:
        _reject("signature_ambiguous",
                detail_hint=f"expected exactly one signature, got {len(results)}")

    result = results[0]
    params = dict(result.parameters)

    alg = params.get("alg")
    if alg is not None and alg != SIG_ALG_NAME:
        _reject("signature_algorithm_rejected", got=alg, expected=SIG_ALG_NAME)

    if params.get("tag") != SIG_TAG:
        _reject("signature_tag_mismatch", expected=SIG_TAG, got=params.get("tag"))

    keyid = params.get("keyid")
    if keyid != expect_keyid:
        # The actor-binding check. Without it, any registered actor could sign
        # a well-formed request acting on any other actor.
        _reject("signature_keyid_mismatch",
                detail_hint="the signing key does not belong to the actor this "
                            "request acts for",
                signed_by=keyid, acting_for=expect_keyid)

    _check_components(result.covered_components)
    _check_content_digest(headers, body)
    _check_freshness(params)

    nonce = params.get("nonce")
    if not nonce:
        _reject("signature_nonce_missing")

    _record_signature(db_path, nonce=nonce, keyid=keyid, method=method,
                      path=path, created=params["created"],
                      signature=headers.get("signature", ""))

    return {"keyid": keyid, "nonce": nonce, "created": params["created"]}


# ===========================================================
# RETENTION
# ===========================================================

def prune_write_signatures(db_path: str,
                           retain_seconds: int = SIG_RETENTION_S) -> int:
    """
    Delete signature rows past the retention window. Returns rows removed.

    Refuses a retention below the nonce TTL. That would forget a nonce while
    its signature is still valid, reopening the replay hole this table exists
    to close — so it raises rather than quietly doing it.
    """
    if retain_seconds < SIG_NONCE_TTL_S:
        raise ValueError(
            f"retain_seconds={retain_seconds} is below SIG_NONCE_TTL_S="
            f"{SIG_NONCE_TTL_S}; pruning that early reopens replay"
        )
    cutoff = int(time.time()) - retain_seconds
    conn = sqlite3.connect(db_path, timeout=30)
    try:
        cur = conn.execute(
            "DELETE FROM write_signatures WHERE received_at < ?", (cutoff,))
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()
