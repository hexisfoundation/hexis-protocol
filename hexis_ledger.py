"""
HEXIS LEDGER v0.1
==================
Module 2 of 3 for the Hexis MVP pipeline.

READ THIS FIRST (updated 2026-08-17). What is true of the running system:

  - **One record is on IPFS, verified from outside.** Event
    `25b0a06d5aed10b4990e5af84724827bb47afa4b488de0f3437e7821122bddc2`, minted
    on production on 2026-08-17, CID
    `bafkreiaii4hvi3oiolthzme7wvevsou7xti2vinzxawhj2xiyeay2acjd4`. It resolves
    from `ipfs.io` — a gateway that is neither ours nor Pinata's — and the CID
    recomputes exactly from the bytes that gateway served. Reproduce it with
    `verify_mint_pin_production.py --check-cid <cid>`.

    That is the whole of what "the CID is the source of truth" rests on: one
    record, one gateway, one day. It is a description now rather than an
    intention, and it is a thin one.

  - **It took eleven minutes.** `gateway.pinata.cloud` served the object in
    8 seconds while `ipfs.io` and `dweb.link` answered 504 for the first ten.
    A pin that has just returned a CID is not yet retrievable by anyone else,
    so a check that runs once proves nothing in either direction.

  - **The 36 older records will never be pinned.** Each carries a `local:` CID,
    which addresses nothing. Their canonical content was never written down —
    the index kept five fields — so they can be reconstructed but not recovered,
    and it was decided on 2026-08-16 that they will not be. `unpinned_legacy` in
    `/status` stays at 36 on purpose. See `PinQueue` and CORRECTIONS.md.

  - Since 2026-08-16 the bridge builds the canonical `LedgerRecord` for every
    mint, writes its `record_hash` into the index, and hands the record to
    `PinService`. **The record content is retained on disk whether or not
    pinning is configured** (`PinQueue`) — that retention is what the 36 lacked.
    A pin may never block or fail a mint; see `PinService` for why.

  - Pinning depends on `PINATA_JWT` in the service environment on the VPS. It is
    read from the environment and is never written into this repo, which is
    public. With no JWT the queue accumulates and mints are unaffected.

  - **A record's CID is not readable by anyone outside.** No public endpoint
    returns one; it lives in `bridge_hexis_index.json` on the host. So a third
    party can check a record's bytes against its CID only if we hand them the
    CID — which is most of the trust that content addressing was supposed to
    remove. Recorded as OPEN in CORRECTIONS.md.

Stores every mined hexis proof permanently and retrievably
on IPFS — the only decentralized storage that is:
    - Permanent (content-addressed, not server-addressed)
    - Free to read for anyone
    - Impossible to alter (changing content changes the CID)
    - Independent of any single organization

Why IPFS and not a blockchain:
    Hexis does not need consensus on order of transactions.
    It needs immutable content addressing.
    IPFS is simpler, cheaper, and sufficient for this purpose.
    A blockchain would be overkill and would introduce dependencies
    on BTC/ETH that contradict the Hexis philosophy.

Setup (one time):
    1. Create a free Pinata account: https://pinata.cloud
       Free tier: 1 GB storage, unlimited pins.
    2. Get your JWT token from: https://app.pinata.cloud/keys
    3. Put it in the service environment on the VPS, never in a file in this
       repo — see DEPLOY.md, "Turning pinning on". Everything in this repo is
       pushed to a public GitHub remote, and a JWT committed once is a JWT
       leaked forever, whatever the next commit removes.

Alternatively: run a local IPFS daemon.
    - Install: https://docs.ipfs.tech/install/command-line/
    - Run: ipfs daemon
    - Set USE_LOCAL_IPFS = True below

Run this file standalone to test:
    python hexis_ledger.py

Then import HexisLedger into your main pipeline:
    from hexis_ledger import HexisLedger
"""

import json
import hashlib
import os
import tempfile
import threading
import time
import urllib.request
import urllib.parse

# The one place bytes and addresses are decided. See its docstring for why the
# provider is not allowed to decide either.
import hexis_cid
from datetime import datetime, timezone
from typing import Callable, Optional


# ============================================================
# CONFIGURATION
#
# From the environment, not from this file. The repo is public; a literal here
# would be published by the same command that deploys it. The placeholder that
# used to sit on this line is kept below only so a JWT accidentally set to it
# is refused rather than sent.
# ============================================================

_PLACEHOLDER_JWT = "YOUR_PINATA_JWT_HERE"

PINATA_JWT     = os.environ.get("PINATA_JWT", "").strip()
USE_LOCAL_IPFS = os.environ.get("USE_LOCAL_IPFS", "").strip().lower() in (
                 "1", "true", "yes", "on")
LOCAL_IPFS_URL = os.environ.get("LOCAL_IPFS_URL", "http://127.0.0.1:5001")


def pinning_enabled(pinata_jwt: str = None, use_local: bool = None) -> bool:
    """Whether a pin has anywhere to go. Cheap, and safe to call per record."""
    jwt   = PINATA_JWT if pinata_jwt is None else pinata_jwt
    local = USE_LOCAL_IPFS if use_local is None else use_local
    return bool(local or (jwt and jwt != _PLACEHOLDER_JWT))


def _atomic_write_json(path: str, obj) -> None:
    """
    Write `obj` to `path` so a reader or a crash sees the whole old file or the
    whole new one, never a half-written one.

    Temp file in the same directory (so `os.replace` stays on one filesystem
    and is therefore atomic), fsync the bytes before the rename, fsync the
    directory after it because the rename itself is metadata.

    Used by both the index and the pin queue: both are sole records of
    something, and neither may be truncated in place.
    """
    path      = os.path.abspath(path)
    directory = os.path.dirname(path)
    fd, tmp = tempfile.mkstemp(
        dir=directory, prefix=os.path.basename(path) + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(obj, f, indent=2)
            f.flush()
            os.fsync(f.fileno())        # the bytes, before the rename
        os.replace(tmp, path)           # atomic: whole old file, or whole new one
    except BaseException:
        # Leave the live file exactly as it was, and take the scrap with us.
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    # Best effort: some filesystems refuse O_RDONLY fsync on a directory, and a
    # failure here costs durability, not correctness.
    try:
        dfd = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(dfd)
        finally:
            os.close(dfd)
    except OSError:
        pass


# ============================================================
# LEDGER RECORD STRUCTURE
# ============================================================

class LedgerRecord:
    """
    What gets stored on IPFS for each mined hexis.

    Content is deterministic — same inputs always produce same JSON,
    same JSON always produces same IPFS CID.
    This means the CID itself is the proof of integrity.
    """

    @staticmethod
    def build(mining_result: dict, behavior_event_dict: dict,
              actor_pubkey: Optional[str] = None) -> dict:
        """
        Builds the canonical ledger record from mining output.

        Args:
            mining_result:       Output of HexisMiner.calculate_hexis_value()
            behavior_event_dict: The original BehaviorEvent as a dict
                                 (use dataclasses.asdict(event))
            actor_pubkey:        Ed25519 public key (hex) of the actor, if
                                 known. Omitted from the record when absent —
                                 see LocalIndex.add() for why absent beats null.

        Returns:
            A dict ready to be pinned to IPFS.

        Older records carry no actor_pubkey. They still verify: record_hash is
        computed over whatever fields the record actually has, not over a fixed
        list, so adding a field changes the hash of new records only.
        """
        record = {
            # Identity
            "hexis_version": "0.1",
            "event_id":      mining_result["event_id"],
            "actor_id":      mining_result["actor_id"],
            "mined_at":      mining_result["mined_at"],

            # Core scores
            "scores": {
                "S":         mining_result["S"],
                "BO":        mining_result["BO"],
                "W":         mining_result["W"],
                "TDR":       mining_result["TDR"],
                "hexis_raw": mining_result["hexis_raw"],
            },

            # The proof hash from the miner
            "proof_hash": mining_result["proof_hash"],

            # Interpretation.
            #
            # `HexisMiner.mine()` has always returned this as "grade"; this
            # line read "interpretation" and would have raised KeyError on
            # every real mining result. It never did, because nothing ever
            # called `build()` on one — the bridge wrote its own index entry
            # and `hexis_pipeline.py` was a manual CLI with no JWT. The first
            # test that put a real mint through this path found it in one run
            # (test_hexis_pinning.py [8]).
            #
            # Both names are accepted rather than one replaced: an old caller
            # passing "interpretation" is still right, and a record built from
            # either says the same thing.
            "grade": mining_result.get("interpretation")
                     or mining_result.get("grade"),

            # Full event data for independent verification
            # Anyone can re-run the algorithm with this data and get the same proof_hash
            "event": {
                "description":           behavior_event_dict.get("description"),
                "timestamp":             behavior_event_dict.get("timestamp"),
                "asset_could_have_taken": behavior_event_dict.get("asset_could_have_taken"),
                "asset_actually_returned": behavior_event_dict.get("asset_actually_returned"),
                "prob_betrayal_detected": behavior_event_dict.get("prob_betrayal_detected"),
                "gain_if_betrayed":       behavior_event_dict.get("gain_if_betrayed"),
                "witness_count":          len(behavior_event_dict.get("witness_sources", [])),
                "witness_sources":        behavior_event_dict.get("witness_sources", []),
                "mention_counts":         behavior_event_dict.get("mention_counts", {}),
            },

            # Self-referential integrity check
            # SHA256 of the entire record (excluding this field) allows anyone
            # to verify the record has not been tampered with after IPFS pinning
            "record_hash": None,  # filled in below
        }

        if actor_pubkey:
            record["actor_pubkey"] = actor_pubkey

        # Compute record hash (excluding the record_hash field itself)
        # One serialiser, shared with the pin path (2026-08-23). This used to
        # build its own JSON string here while the pinning code built another
        # one, so `record_hash` and the pinned bytes were hashes of different
        # text. Both now go through hexis_cid.canonical_bytes, which is the
        # only way two hashes of "the same record" can be made to agree.
        record_copy = {k: v for k, v in record.items() if k != "record_hash"}
        record["record_hash"] = hashlib.sha256(
            hexis_cid.canonical_bytes(record_copy)).hexdigest()

        return record


# ============================================================
# IPFS INTERFACE
# ============================================================

class IPFSClient:
    """
    Thin wrapper for IPFS operations.
    Supports both Pinata (cloud) and local IPFS daemon.
    """

    def __init__(self, use_local: bool = None, pinata_jwt: str = None):
        # Defaults come from the environment, read once at import. Passing
        # either explicitly is for tests; production passes neither, so there
        # is no call site that could hold a JWT literal.
        self.use_local  = USE_LOCAL_IPFS if use_local is None else use_local
        self.pinata_jwt = PINATA_JWT if pinata_jwt is None else pinata_jwt
        # [(provider, ok, error)] from the last pin(). Lets a caller tell
        # "this record has an address" from "somebody is serving it".
        self.pin_results: list = []

    def pin(self, data: dict, name: str = "hexis-record") -> Optional[str]:
        """
        Pin a record and return the CID *we* computed, or None.

        Rewritten 2026-08-23. The old version handed a Python dict to Pinata's
        `pinJSONToIPFS` and stored whatever CID came back, which gave the
        provider two decisions that were never theirs to make: what bytes to
        store, and what to call them. Both went wrong in the one record ever
        pinned — Pinata re-encoded the JSON, so the bytes on IPFS are not the
        bytes we hashed, and that record's own `record_hash` no longer verifies
        against its own content.

        Now: `hexis_cid.canonical_bytes` decides the bytes, `hexis_cid.cid_v1_raw`
        decides the address, and every provider is handed those exact bytes and
        checked against that address. A provider whose answer disagrees is
        treated as having failed, not as having renamed our data.

        The CID is returned even when every provider fails. That is deliberate:
        the address of a record is a property of the record, not of whether
        anybody happens to be hosting it. `pin_results` on this client carries
        who actually confirmed, so a caller can tell "addressable" from
        "retrievable" — two different claims that were previously one field.
        """
        blob = hexis_cid.canonical_bytes(data)
        cid = hexis_cid.cid_v1_raw(blob)
        self.pin_results = self._pin_everywhere(blob, cid, name)
        confirmed = [p for p, ok, _ in self.pin_results if ok]
        if not confirmed:
            why = "; ".join(f"{p}: {err}" for p, ok, err in self.pin_results if not ok)
            print(f"[IPFSClient] {cid} pinned nowhere — {why or 'no provider configured'}")
            return None
        print(f"[IPFSClient] {cid} confirmed by {', '.join(confirmed)}")
        return cid

    def _pin_everywhere(self, blob: bytes, cid: str, name: str) -> list:
        """
        Every configured provider gets the same bytes. Returns
        [(provider, ok, error)].

        One provider is a single point of failure, and a single point of
        failure is the specific thing a CID is supposed to remove — if the
        record only survives while we keep paying one company, the operator
        has not been taken out of the loop, only renamed. Current IPFS guidance
        is to pin across two providers plus your own node, and this fans out to
        whatever is configured rather than stopping at the first success, so
        that adding the second provider is a config change and not a code
        change.
        """
        results = []
        if self.pinata_jwt and self.pinata_jwt != _PLACEHOLDER_JWT:
            results.append(self._pin_pinata(blob, cid, name))
        if self.use_local:
            results.append(self._pin_local(blob, cid, name))
        return results

    def _check(self, provider: str, returned: Optional[str], cid: str, blob: bytes):
        """
        A provider's answer is only accepted if it names our bytes.

        Two ways to be wrong and both matter: a different CID means it stored
        something else, and a CID that does not verify against the blob means
        it is describing content we did not send. Either way the pin failed,
        and recording the provider's CID would put an address in the chain that
        we cannot rederive from the record — the exact dependency being removed.
        """
        if not returned:
            return (provider, False, "no CID returned")
        if returned != cid:
            return (provider, False,
                    f"returned {returned} but these bytes address {cid} — "
                    f"the provider stored something other than what was sent")
        if not hexis_cid.verify_cid(returned, blob):
            return (provider, False, f"{returned} does not verify against the bytes")
        return (provider, True, "")

    def _pin_pinata(self, blob: bytes, cid: str, name: str):
        """
        `pinFileToIPFS` with the exact bytes — never `pinJSONToIPFS`.

        `pinJSONToIPFS` takes an object and re-encodes it server-side, which is
        how the bytes and the hash came apart. Sending a file means the bytes
        crossing the wire are the bytes we hashed, and the CID Pinata computes
        is then arithmetic on the same input rather than an opinion about it.
        """
        boundary = "----HexisBoundary" + hashlib.sha256(blob).hexdigest()[:16]
        body = (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="file"; filename="{name}.json"\r\n'
            f"Content-Type: application/json\r\n\r\n"
        ).encode() + blob + (
            f"\r\n--{boundary}\r\n"
            f'Content-Disposition: form-data; name="pinataOptions"\r\n\r\n'
            f'{{"cidVersion":1}}\r\n'
            f"--{boundary}--\r\n"
        ).encode()
        req = urllib.request.Request(
            url     = "https://api.pinata.cloud/pinning/pinFileToIPFS",
            data    = body,
            headers = {
                "Content-Type":  f"multipart/form-data; boundary={boundary}",
                "Authorization": f"Bearer {self.pinata_jwt}",
                # Our own name, not urllib's default. Cloudflare in front of
                # our own bridge already 403s `Python-urllib/X.Y` (DEPLOY.md),
                # which is a good reminder that the default User-Agent is a
                # thing intermediaries act on.
                "User-Agent":    "hexis-bridge/0.6.2 (+https://hexisfoundation.org)",
            },
            method  = "POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=20) as response:
                result = json.loads(response.read().decode())
            return self._check("pinata", result.get("IpfsHash"), cid, blob)
        except Exception as e:
            return ("pinata", False, str(e))

    def _pin_local(self, blob: bytes, cid: str, name: str):
        """
        A node we run ourselves. Same bytes, same check.

        `raw-leaves=true` and `cid-version=1` are not optional here: without
        them go-ipfs wraps small files in a dag-pb node and returns a `bafybei…`
        CID, which addresses the same content under a different name and would
        fail the check below for a reason that is nobody's fault. `hexis_cid`
        refuses dag-pb deliberately — its CID depends on chunking and layout,
        so it is not reproducible from the bytes alone.
        """
        boundary = "----HexisBoundary" + hashlib.sha256(blob).hexdigest()[:16]
        body = (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="file"; filename="{name}.json"\r\n'
            f"Content-Type: application/json\r\n\r\n"
        ).encode() + blob + f"\r\n--{boundary}--\r\n".encode()
        req = urllib.request.Request(
            url     = (f"{LOCAL_IPFS_URL}/api/v0/add"
                       f"?pin=true&cid-version=1&raw-leaves=true&hash=sha2-256"),
            data    = body,
            headers = {"Content-Type": f"multipart/form-data; boundary={boundary}"},
            method  = "POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as response:
                result = json.loads(response.read().decode())
            return self._check("local", result.get("Hash"), cid, blob)
        except Exception as e:
            return ("local", False, f"{e} — is `ipfs daemon` running?")


# ============================================================
# LOCAL INDEX
#
# Written as a fallback for when IPFS is not configured. IPFS has never been
# configured on the running system, so in production this is not the fallback
# — it is the record. See the class docstring.
# ============================================================

class LocalIndex:
    """
    JSON file index of all mined hexis records.

    The docstring here used to say:

        This is NOT the decentralized ledger — it is a local cache
        that maps event_id -> IPFS CID for quick lookup.
        Even if this index is lost, all records remain on IPFS permanently.
        The CID is the source of truth, not this file.

    That is not true of the one caller that runs in production. The bridge
    (`bridge_hexis_index.json`) never pins anything: it calls `add()` directly
    with `cid = f"local:{proof_hash[:16]}"`, a string that names nothing
    retrievable, and `PINATA_JWT` is still the placeholder. There is no copy on
    IPFS to fall back to. **This file is the only durable record of minted
    HEXIS that exists**, and the sentence promising otherwise was the reason it
    was being written the careless way for so long — a cache may be truncated
    in place, a sole record may not.

    So, since 2026-08-15:

      - `_save()` writes a temp file in the same directory, fsyncs it, and
        `os.replace()`s it over the target. The replace is atomic, so a reader
        or a crash sees either the whole old file or the whole new one, never
        a half-written one. The directory is fsynced afterwards, because the
        rename itself needs flushing to survive power loss.
      - `_load()` refuses to start on a file it cannot parse, instead of
        treating it as absent. `{"records": [], "total_minted": 0}` for a
        damaged file would report every mined record as never mined, and the
        first `add()` after that would overwrite the damaged file with the
        empty one — turning a recoverable problem into a permanent one.

    A missing file is still an empty index: that is a first run.

    Thread safety (2026-08-16): every mutation holds `self._lock`. Until the
    pin service existed, only the request thread ever wrote here and a lock
    would have been decoration. Now a background thread rewrites entries as
    pins land, and `add()` is read-modify-write on one list — two writers
    without a lock lose a minted record, not just a pin status.
    """

    def __init__(self, filepath: str = "hexis_index.json"):
        self.filepath = filepath
        self._lock    = threading.RLock()
        self._load()

    def _load(self):
        try:
            with open(self.filepath, "r") as f:
                self.index = json.load(f)
        except FileNotFoundError:
            # First run. Distinct from a damaged file, deliberately.
            self.index = {"records": [], "total_minted": 0}
        except json.JSONDecodeError as e:
            path = os.path.abspath(self.filepath)
            raise RuntimeError(
                f"HEXIS index will not parse: {path}\n"
                f"  {e}\n"
                "This file is the only durable record of minted HEXIS — it is "
                "not rebuilt from anywhere, so it is not being replaced with an "
                "empty one.\n"
                "Restore it from a backup and start again:\n"
                "  ls -la /opt/hexis_newflow/backups/*/bridge_hexis_index.json\n"
                "The nightly backup verifies this file parses before keeping a "
                "run, so the newest backup present is a good copy."
            ) from e

    def _save(self):
        """
        Atomic replace. Never truncates the live file.

        The old two-line version opened the target with "w" — which truncates
        first and writes second, so every single `add()` had a window where the
        file on disk was empty or half a JSON document. That is the sole record
        of minted HEXIS, and `_load()` did not survive reading one.
        """
        _atomic_write_json(self.filepath, self.index)

    def add(self, event_id: str, cid: str, hexis_raw: float, actor_id: str,
            actor_pubkey: Optional[str] = None,
            record_hash: Optional[str] = None,
            pin_status: Optional[str] = None,
            record: Optional[dict] = None):
        """
        Append one mined-hexis entry.

        actor_pubkey is the Ed25519 public key (hex) the actor signs with.
        It is recorded alongside actor_id because an address is only a
        *commitment* to a key — sha256(pubkey), one-way — so a record holding
        the address alone cannot be checked against a signature by anyone who
        does not also hold this server's worker table.

        The field is omitted entirely, rather than written as null, when no
        key is known. That keeps two situations distinguishable forever:

            field absent  -> written before the identity layer existed
            field present -> written by a registered, key-holding actor

        A null would have collapsed both into one unreadable state.

        `record_hash` and `pin_status` follow the same rule, and are absent on
        every record written before 2026-08-16 for the same honest reason: no
        canonical record was built then, so there is nothing to hash and
        nothing was ever queued for a pin.
        """
        entry = {
            "event_id":   event_id,
            "actor_id":   actor_id,
            "cid":        cid,
            "hexis_raw":  hexis_raw,
            "indexed_at": datetime.now(timezone.utc).isoformat(),
        }
        if actor_pubkey:
            entry["actor_pubkey"] = actor_pubkey
        if record_hash:
            entry["record_hash"] = record_hash
        if pin_status:
            entry["pin_status"] = pin_status
        # The full canonical record, kept forever (2026-08-23). Until today the
        # only copy lived in the pin QUEUE, which a record leaves the moment a
        # CID comes back — so pinning a record deleted our local copy of it,
        # and the only bytes left were whatever the provider stored. For the
        # one record that ever completed that journey, the provider had
        # re-encoded them. A public read endpoint cannot be built on a store
        # that forgets its contents on success.
        if record:
            entry["record"] = record
        # NO handle here, deliberately. A handle is display metadata and lives
        # in the actor_handles table; putting one in a record would make the
        # record depend on a mutable label. See README, rule 6.
        with self._lock:
            self.index["records"].append(entry)
            self.index["total_minted"] += 1
            self._save()
        print(f"[LocalIndex] Indexed event {event_id} -> CID {cid}")

    def set_pinned(self, event_id: str, cid: str) -> bool:
        """
        Record that this event's canonical record is now on IPFS.

        The `local:` string it was written with is kept as `local_cid`, not
        overwritten. It is not a CID and never addressed anything, but it is
        what this record was identified by for however long it went unpinned,
        and erasing it would erase that. Returns False if the event is unknown.
        """
        with self._lock:
            for r in self.index["records"]:
                if r["event_id"] != event_id:
                    continue
                old = r.get("cid", "")
                if old.startswith("local:") and "local_cid" not in r:
                    r["local_cid"] = old
                r["cid"]        = cid
                r["pin_status"] = "pinned"
                r["pinned_at"]  = datetime.now(timezone.utc).isoformat()
                self._save()
                return True
        return False

    def pin_summary(self) -> dict:
        """Counts by pin status. Records written before pinning existed carry
        no `pin_status`, and are counted as `unpinned_legacy` rather than
        folded in with today's pending ones — they are a different problem."""
        out = {"pinned": 0, "pending": 0, "unpinned_legacy": 0}
        with self._lock:
            for r in self.index["records"]:
                st = r.get("pin_status")
                if st == "pinned":
                    out["pinned"] += 1
                elif st:
                    out["pending"] += 1
                else:
                    out["unpinned_legacy"] += 1
        return out

    def get_record(self, event_id: str) -> Optional[dict]:
        """One entry by event_id, or None. Read-only."""
        with self._lock:
            for r in self.index["records"]:
                if r["event_id"] == event_id:
                    return dict(r)
        return None

    def restore_content(self, event_id: str, record: dict,
                        requeue: Optional["PinQueue"] = None,
                        name: str = "hexis-record") -> str:
        """
        Put a canonical record's content back into its own index entry.

        Exists for exactly one situation, found 2026-08-23: a record that was
        pinned before the index kept content has no local copy, because the
        pin queue was the only holder and success removed it. Restoring is
        gated on the stored record_hash — the content must hash to what the
        index has said it was since the day it was minted, or nothing is
        written. Fail closed: a restore that cannot prove it is restoring the
        same record would be an edit wearing a restore's name.

        If the entry's stored CID is not the canonical CID of these bytes
        (the provider re-encoded them), the provider's CID is preserved as
        `cid_provider` and the record is re-queued so the canonical bytes get
        pinned under their own address.
        """
        import hexis_cid as _hc
        blob = _hc.canonical_bytes({k: v for k, v in record.items()
                                    if k != "record_hash"})
        h = hashlib.sha256(blob).hexdigest()
        with self._lock:
            for r in self.index["records"]:
                if r["event_id"] != event_id:
                    continue
                stored = r.get("record_hash")
                if not stored:
                    return "refused: entry has no record_hash to check against"
                if h != stored:
                    return (f"refused: content hashes to {h[:16]}…, the index "
                            f"has said {stored[:16]}… since it was minted")
                if r.get("record"):
                    return "already has content"
                r["record"] = record
                canonical = _hc.cid_v1_raw(_hc.canonical_bytes(record))
                if r.get("cid") and r["cid"] != canonical:
                    r["cid_provider"] = r["cid"]
                    r["cid"] = canonical
                    r["pin_status"] = "pending"
                    if requeue is not None:
                        requeue.add(event_id, record, name)
                self._save()
                return f"restored; canonical cid {canonical}"
        return "refused: no such event_id"

    def get_by_actor(self, actor_id: str) -> list:
        return [r for r in self.index["records"] if r["actor_id"] == actor_id]

    def get_actor_total_hexis(self, actor_id: str) -> float:
        records = self.get_by_actor(actor_id)
        return sum(r["hexis_raw"] for r in records)

    def summary(self) -> dict:
        return {
            "total_records_minted": self.index["total_minted"],
            "unique_actors":        len(set(r["actor_id"] for r in self.index["records"])),
            "top_actors":           self._top_actors(5),
        }

    def _top_actors(self, n: int) -> list:
        from collections import defaultdict
        totals = defaultdict(float)
        for r in self.index["records"]:
            totals[r["actor_id"]] += r["hexis_raw"]
        return sorted(totals.items(), key=lambda x: x[1], reverse=True)[:n]


# ============================================================
# PIN QUEUE
# ============================================================

class PinQueue:
    """
    The canonical records waiting to be pinned, on disk, written atomically.

    This exists because of what the 36 records minted before 2026-08-16 cost.
    Their canonical `LedgerRecord` was never written down anywhere: the index
    kept five fields, the audit chain kept the mining result for 31 of them and
    nothing at all for the 5 from May, and neither keeps the BehaviorEvent the
    record is built from. So they cannot be pinned as they were minted — only
    as they are reconstructed, which is a different claim. Decided 2026-08-16:
    they will not be. They stay `unpinned_legacy`, and that count staying at 36
    is the honest answer, not a backlog.

    Every record enters this queue before any pin is attempted, and leaves it
    only when a CID comes back. If pinning is off, the queue is simply where
    the content lives, and the day a JWT is configured every record minted
    since this file shipped can be pinned exactly as it was minted. If pinning
    is on and Pinata is down, the same thing is true across the outage.

    A record here is duplicated content — it is also derivable from the index
    plus the audit chain going forward — so losing this file loses retries, not
    the record. It is still written atomically, because a half-written queue
    that will not parse would block every later pin.
    """

    def __init__(self, filepath: str = "bridge_hexis_pins.json"):
        self.filepath = filepath
        self._lock    = threading.RLock()
        try:
            with open(self.filepath, "r") as f:
                self.pending = json.load(f)
        except FileNotFoundError:
            self.pending = []
        except json.JSONDecodeError as e:
            # Unlike the index, this one is recoverable by hand and is not the
            # sole record of anything — but it is not silently discarded
            # either, because that would silently drop pins.
            raise RuntimeError(
                f"HEXIS pin queue will not parse: {os.path.abspath(self.filepath)}\n"
                f"  {e}\n"
                "This holds records queued for IPFS but not yet pinned. It is "
                "not the only copy of a mint — the index is — but discarding "
                "it drops those pins, so it is not being discarded here.\n"
                "Inspect it, or move it aside to start an empty queue."
            ) from e

    def __len__(self) -> int:
        with self._lock:
            return len(self.pending)

    def add(self, event_id: str, record: dict, name: str) -> None:
        with self._lock:
            if any(p["event_id"] == event_id for p in self.pending):
                return                      # already queued; a retry, not a second pin
            self.pending.append({
                "event_id":  event_id,
                "name":      name,
                "record":    record,
                "queued_at": datetime.now(timezone.utc).isoformat(),
                "attempts":  0,
                "last_error": None,
            })
            _atomic_write_json(self.filepath, self.pending)

    def take_all(self) -> list:
        """A snapshot to work from, so the lock is not held across the network."""
        with self._lock:
            return list(self.pending)

    def done(self, event_id: str) -> None:
        with self._lock:
            before = len(self.pending)
            self.pending = [p for p in self.pending if p["event_id"] != event_id]
            if len(self.pending) != before:
                _atomic_write_json(self.filepath, self.pending)

    def failed(self, event_id: str, error: str) -> None:
        with self._lock:
            for p in self.pending:
                if p["event_id"] == event_id:
                    p["attempts"]   = p.get("attempts", 0) + 1
                    p["last_error"] = error[:300]
                    p["last_attempt_at"] = datetime.now(timezone.utc).isoformat()
                    _atomic_write_json(self.filepath, self.pending)
                    return


# ============================================================
# PIN SERVICE
# ============================================================

class PinService:
    """
    Pins queued records to IPFS on a background thread.

    One rule above the others: **a pin may never block or fail a mint.** A mint
    is a statement about behaviour that already happened, made by this service
    against its own database; a pin is an HTTP request to a company. If the two
    are wired together, Pinata being slow makes job settlement slow, and Pinata
    being down makes honest work unrecordable. So `submit()` does two local
    writes and returns, and everything that can fail happens on this thread.

    Retries back off per record: 60s doubling to an hour, from the time of the
    last attempt. Nothing is ever dropped for having failed too often — a
    record whose pin has failed 200 times is a record that still is not on
    IPFS, and forgetting it would make the index say so less loudly, not make
    it less true.
    """

    RETRY_BASE_S = 60
    RETRY_MAX_S  = 3600

    def __init__(self, queue: PinQueue, ipfs: "IPFSClient",
                 on_pinned: Callable[[str, str], None],
                 log: Optional[Callable[[str], None]] = None):
        self.queue     = queue
        self.ipfs      = ipfs
        self.on_pinned = on_pinned
        self.log       = log or (lambda m: print(f"[PinService] {m}"))
        self._wake     = threading.Event()
        self._stop     = threading.Event()
        self._thread   = None
        self.last_error = None
        self.pinned_this_boot = 0

    # -- called from the request thread -----------------------------------
    def submit(self, event_id: str, record: dict, name: str) -> None:
        """Queue a record and wake the worker. Never raises into the caller."""
        try:
            self.queue.add(event_id, record, name)
            self._wake.set()
        except Exception as e:
            # A mint that succeeded is not undone by a queue that failed to
            # write. Say so and carry on; the index already holds the record.
            self.last_error = f"queue write failed: {e}"
            self.log(self.last_error)

    # -- lifecycle ---------------------------------------------------------
    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(
            target=self._loop, name="hexis-pin", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)
            self._thread = None

    def enabled(self) -> bool:
        """Asked of the client this service actually holds, not of the module
        globals — a test that builds a client with its own JWT is still a
        service with somewhere to pin to."""
        return pinning_enabled(self.ipfs.pinata_jwt, self.ipfs.use_local)

    def status(self) -> dict:
        return {
            "enabled":          self.enabled(),
            "queued":           len(self.queue),
            "pinned_this_boot": self.pinned_this_boot,
            "last_error":       self.last_error,
        }

    # -- the worker --------------------------------------------------------
    def _loop(self) -> None:
        while not self._stop.is_set():
            # Woken by a submit, or every 5 minutes to retry what is waiting.
            self._wake.wait(timeout=300)
            self._wake.clear()
            if self._stop.is_set():
                return
            if not self.enabled():
                continue            # queue up; nothing to send them to yet
            try:
                self._drain()
            except Exception as e:
                # The thread outliving one bad record matters more than the
                # record: if this thread dies, pinning stops silently for the
                # rest of the process's life.
                self.last_error = f"pin loop: {e}"
                self.log(self.last_error)

    def _drain(self) -> None:
        now = time.time()
        for item in self.queue.take_all():
            if self._stop.is_set():
                return
            if not self._due(item, now):
                continue
            event_id = item["event_id"]
            try:
                cid = self.ipfs.pin(item["record"], name=item.get("name", "hexis-record"))
            except Exception as e:                      # never trusted to not raise
                cid = None
                err = str(e)
            else:
                err = "pin returned no CID"
            if not cid:
                self.queue.failed(event_id, err)
                self.last_error = f"{event_id[:16]}: {err}"
                continue
            # Index first, queue second. If the process dies between them the
            # record is pinned and the index says so; the queue keeps a stale
            # entry, and the next attempt re-pins identical content to the same
            # CID. The other order would lose the CID of a successful pin.
            try:
                self.on_pinned(event_id, cid)
            except Exception as e:
                self.last_error = f"{event_id[:16]}: pinned {cid} but index write failed: {e}"
                self.log(self.last_error)
                continue
            self.queue.done(event_id)
            self.pinned_this_boot += 1
            self.log(f"pinned {event_id[:16]} -> {cid}")

    def _due(self, item: dict, now: float) -> bool:
        attempts = item.get("attempts", 0)
        if attempts == 0:
            return True
        last = item.get("last_attempt_at")
        if not last:
            return True
        try:
            last_ts = datetime.fromisoformat(last).timestamp()
        except ValueError:
            return True
        backoff = min(self.RETRY_BASE_S * (2 ** (attempts - 1)), self.RETRY_MAX_S)
        return (now - last_ts) >= backoff


# ============================================================
# MAIN LEDGER INTERFACE
# ============================================================

class HexisLedger:
    """
    Main interface for storing and retrieving hexis records.

    Usage:
        ledger = HexisLedger()

        # Store a mined hexis
        cid = ledger.store(mining_result, behavior_event_dict)

        # Retrieve and verify
        record = ledger.retrieve(cid)

        # Get all hexis for an actor
        actor_records = ledger.get_actor_records("potus_47")
    """

    def __init__(
        self,
        pinata_jwt:    str  = PINATA_JWT,
        use_local_ipfs: bool = USE_LOCAL_IPFS,
        index_file:    str  = "hexis_index.json",
    ):
        self.ipfs  = IPFSClient(use_local=use_local_ipfs, pinata_jwt=pinata_jwt)
        self.index = LocalIndex(index_file)

    def store(self, mining_result: dict, behavior_event_dict: dict) -> Optional[str]:
        """
        Stores a mined hexis proof on IPFS and indexes it locally.

        Args:
            mining_result:       From HexisMiner.calculate_hexis_value()
            behavior_event_dict: From dataclasses.asdict(behavior_event)

        Returns:
            IPFS CID string, or None if storage failed.
        """
        if not mining_result.get("eligible"):
            print("[HexisLedger] Cannot store — event not eligible for minting.")
            return None

        # Build canonical record
        record = LedgerRecord.build(mining_result, behavior_event_dict)

        # Pin to IPFS
        record_name = f"hexis-{mining_result['event_id']}"
        cid = self.ipfs.pin(record, name=record_name)

        if cid:
            # Index locally for quick lookup
            self.index.add(
                event_id  = mining_result["event_id"],
                cid       = cid,
                hexis_raw = mining_result["hexis_raw"],
                actor_id  = mining_result["actor_id"],
            )
            print(f"[HexisLedger] Minted and stored.")
            print(f"  Event ID:  {mining_result['event_id']}")
            print(f"  HEXIS:     {mining_result['hexis_raw']}")
            print(f"  IPFS CID:  {cid}")
            print(f"  Verify at: https://ipfs.io/ipfs/{cid}")
        else:
            print("[HexisLedger] IPFS storage failed. Record not persisted.")
            print("  Configure PINATA_JWT or start local IPFS daemon.")

        return cid

    def retrieve(self, cid: str) -> Optional[dict]:
        """
        Retrieves and verifies a hexis record by its IPFS CID.
        Verifies the record_hash to confirm integrity.
        """
        record = self.ipfs.retrieve(cid)
        if not record:
            return None

        # Verify record integrity
        stored_hash = record.pop("record_hash", None)
        canonical   = json.dumps(record, sort_keys=True, ensure_ascii=True)
        computed    = hashlib.sha256(canonical.encode()).hexdigest()

        if stored_hash == computed:
            record["record_hash"] = stored_hash
            print(f"[HexisLedger] Integrity verified. Record is authentic.")
        else:
            print(f"[HexisLedger] WARNING: Integrity check FAILED. Record may be tampered.")
            record["record_hash"] = stored_hash
            record["integrity_warning"] = True

        return record

    def get_actor_records(self, actor_id: str) -> list:
        return self.index.get_by_actor(actor_id)

    def get_actor_total_hexis(self, actor_id: str) -> float:
        return self.index.get_actor_total_hexis(actor_id)

    def ledger_summary(self) -> dict:
        return self.index.summary()


# ============================================================
# STANDALONE TEST
# ============================================================

if __name__ == "__main__":

    print("=" * 60)
    print("HEXIS LEDGER — Setup Test")
    print("=" * 60)

    # Simulate a mining result
    fake_mining_result = {
        "eligible":       True,
        "event_id":       "abc123def456",
        "actor_id":       "potus_47",
        "S":              1.0,
        "BO":             0.63,
        "W":              0.58,
        "TDR":            0.06,
        "hexis_raw":      0.02210885,
        "proof_hash":     "3ce8436f71ee736291bebc56af03edf660" + "9e752c",
        "mined_at":       datetime.now(timezone.utc).isoformat(),
        "interpretation": "Low — genuine behavior",
    }

    fake_event_dict = {
        "description":            "Test declaration",
        "timestamp":              1744502400.0,
        "asset_could_have_taken":  50_000_000.0,
        "asset_actually_returned": 50_000_000.0,
        "prob_betrayal_detected":  0.95,
        "gain_if_betrayed":        10_000_000.0,
        "witness_sources":         [{"type": "adversarial", "name": "CNN"}],
        "mention_counts":          {"30d": 500000, "1y": 50000, "5y": 10000},
    }

    # Test record building (always works)
    print("\n[TEST 1] Building ledger record")
    record = LedgerRecord.build(fake_mining_result, fake_event_dict)
    print(f"  Record hash:     {record['record_hash'][:32]}...")
    print(f"  Scores:          {record['scores']}")
    print(f"  Witness count:   {record['event']['witness_count']}")
    print("  Record structure: OK")

    # Test local index (always works)
    print("\n[TEST 2] Local index")
    index = LocalIndex("hexis_index_test.json")
    index.add("abc123def456", "QmTestCID123", 0.022, "potus_47")
    total = index.get_actor_total_hexis("potus_47")
    print(f"  Actor hexis total: {total}")
    print(f"  Index: OK")

    # Test IPFS (requires Pinata JWT or local daemon)
    print("\n[TEST 3] IPFS storage")
    if not pinning_enabled():
        print("  SKIPPED — export PINATA_JWT=... or USE_LOCAL_IPFS=1 and re-run")
        print("  Get free Pinata JWT at: https://app.pinata.cloud/keys")
    else:
        ledger = HexisLedger()
        cid = ledger.store(fake_mining_result, fake_event_dict)
        if cid:
            print(f"  CID: {cid}")
            print(f"  Verify at: https://ipfs.io/ipfs/{cid}")

    print("\n" + "=" * 60)
    print("Ledger ready. Proceed to hexis_classifier.py (Module 3).")
    print("=" * 60)
