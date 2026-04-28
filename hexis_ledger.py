"""
HEXIS LEDGER v0.1
==================
Module 2 of 3 for the Hexis MVP pipeline.

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
    3. Set PINATA_JWT below.

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
import urllib.request
import urllib.parse
from datetime import datetime, timezone
from typing import Optional


# ============================================================
# CONFIGURATION
# ============================================================

PINATA_JWT     = "YOUR_PINATA_JWT_HERE"   # From https://app.pinata.cloud/keys
USE_LOCAL_IPFS = False                    # True if running local IPFS daemon
LOCAL_IPFS_URL = "http://127.0.0.1:5001"  # Default local daemon address


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
    def build(mining_result: dict, behavior_event_dict: dict) -> dict:
        """
        Builds the canonical ledger record from mining output.

        Args:
            mining_result:       Output of HexisMiner.calculate_hexis_value()
            behavior_event_dict: The original BehaviorEvent as a dict
                                 (use dataclasses.asdict(event))

        Returns:
            A dict ready to be pinned to IPFS.
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

            # Interpretation
            "grade": mining_result["interpretation"],

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

        # Compute record hash (excluding the record_hash field itself)
        record_copy = {k: v for k, v in record.items() if k != "record_hash"}
        canonical   = json.dumps(record_copy, sort_keys=True, ensure_ascii=True)
        record["record_hash"] = hashlib.sha256(canonical.encode()).hexdigest()

        return record


# ============================================================
# IPFS INTERFACE
# ============================================================

class IPFSClient:
    """
    Thin wrapper for IPFS operations.
    Supports both Pinata (cloud) and local IPFS daemon.
    """

    def __init__(self, use_local: bool = False, pinata_jwt: str = ""):
        self.use_local  = use_local
        self.pinata_jwt = pinata_jwt

    def pin(self, data: dict, name: str = "hexis-record") -> Optional[str]:
        """
        Pins a JSON object to IPFS.
        Returns the IPFS CID (Content Identifier) or None on failure.

        The CID is the permanent address of this record.
        It is derived from the content — same content always = same CID.
        """
        if self.use_local:
            return self._pin_local(data, name)
        else:
            return self._pin_pinata(data, name)

    def retrieve(self, cid: str) -> Optional[dict]:
        """
        Retrieves a JSON record from IPFS by CID.
        Works with any IPFS gateway — no dependency on Pinata.
        """
        # Try public gateways in order
        gateways = [
            f"https://ipfs.io/ipfs/{cid}",
            f"https://cloudflare-ipfs.com/ipfs/{cid}",
            f"https://gateway.pinata.cloud/ipfs/{cid}",
        ]

        for gateway in gateways:
            try:
                with urllib.request.urlopen(gateway, timeout=10) as response:
                    data = json.loads(response.read().decode())
                    print(f"[IPFSClient] Retrieved from {gateway}")
                    return data
            except Exception:
                continue

        print(f"[IPFSClient] Could not retrieve CID: {cid}")
        return None

    def _pin_pinata(self, data: dict, name: str) -> Optional[str]:
        """Pins to Pinata cloud."""
        if not self.pinata_jwt or self.pinata_jwt == "YOUR_PINATA_JWT_HERE":
            print("[IPFSClient] No Pinata JWT configured. Set PINATA_JWT at top of file.")
            return None

        payload = json.dumps({
            "pinataOptions": {"cidVersion": 1},
            "pinataMetadata": {"name": name},
            "pinataContent": data,
        }).encode("utf-8")

        req = urllib.request.Request(
            url     = "https://api.pinata.cloud/pinning/pinJSONToIPFS",
            data    = payload,
            headers = {
                "Content-Type":  "application/json",
                "Authorization": f"Bearer {self.pinata_jwt}",
            },
            method  = "POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=15) as response:
                result = json.loads(response.read().decode())
                cid = result.get("IpfsHash")
                print(f"[IPFSClient] Pinned to Pinata. CID: {cid}")
                return cid
        except Exception as e:
            print(f"[IPFSClient] Pinata error: {e}")
            return None

    def _pin_local(self, data: dict, name: str) -> Optional[str]:
        """Pins to local IPFS daemon (must be running: ipfs daemon)."""
        import io

        json_bytes = json.dumps(data, sort_keys=True).encode("utf-8")

        # Multipart form upload to local IPFS API
        boundary = "----HexisBoundary"
        body = (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="file"; filename="{name}.json"\r\n'
            f"Content-Type: application/json\r\n\r\n"
        ).encode() + json_bytes + f"\r\n--{boundary}--\r\n".encode()

        req = urllib.request.Request(
            url     = f"{LOCAL_IPFS_URL}/api/v0/add?pin=true",
            data    = body,
            headers = {"Content-Type": f"multipart/form-data; boundary={boundary}"},
            method  = "POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=10) as response:
                result = json.loads(response.read().decode())
                cid = result.get("Hash")
                print(f"[IPFSClient] Pinned to local IPFS. CID: {cid}")
                return cid
        except Exception as e:
            print(f"[IPFSClient] Local IPFS error: {e}")
            print(f"  Is the IPFS daemon running? Run: ipfs daemon")
            return None


# ============================================================
# LOCAL INDEX (fallback when IPFS is not configured)
# ============================================================

class LocalIndex:
    """
    Local JSON file index of all mined hexis records.

    This is NOT the decentralized ledger — it is a local cache
    that maps event_id -> IPFS CID for quick lookup.

    Even if this index is lost, all records remain on IPFS permanently.
    The CID is the source of truth, not this file.
    """

    def __init__(self, filepath: str = "hexis_index.json"):
        self.filepath = filepath
        self._load()

    def _load(self):
        try:
            with open(self.filepath, "r") as f:
                self.index = json.load(f)
        except FileNotFoundError:
            self.index = {"records": [], "total_minted": 0}

    def _save(self):
        with open(self.filepath, "w") as f:
            json.dump(self.index, f, indent=2)

    def add(self, event_id: str, cid: str, hexis_raw: float, actor_id: str):
        entry = {
            "event_id":   event_id,
            "actor_id":   actor_id,
            "cid":        cid,
            "hexis_raw":  hexis_raw,
            "indexed_at": datetime.now(timezone.utc).isoformat(),
        }
        self.index["records"].append(entry)
        self.index["total_minted"] += 1
        self._save()
        print(f"[LocalIndex] Indexed event {event_id} -> CID {cid}")

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
    if PINATA_JWT == "YOUR_PINATA_JWT_HERE" and not USE_LOCAL_IPFS:
        print("  SKIPPED — configure PINATA_JWT or set USE_LOCAL_IPFS = True")
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
