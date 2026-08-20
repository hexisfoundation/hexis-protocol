"""
HEXIS GENESIS ALLOCATION v0.1
==============================
Manages pre-mint records, vesting schedule, and mining registration.

This module handles three things:

1. GENESIS ALLOCATION RECORD
   The authoritative document that proves who owns what pre-mint hexis.
   Created once at Block 0, signed by founder, pinned to IPFS permanently.

2. VESTING UNLOCK EVENTS
   Annual records that "release" vested hexis to recipients.
   Each unlock is a new IPFS record referencing the genesis allocation.

3. MINING REGISTRATION
   How anyone can register to mine hexis when the system is ready.
   Whitepaper readers who want to participate register their wallet address
   and the types of events they want to verify.

TECHNICAL NOTE ON PRE-MINT:
   Pre-mint hexis does not "exist" anywhere until the protocol has a
   production ledger (smart contract or equivalent). Before that, the
   genesis allocation document IS the proof of entitlement.
   When the ledger launches, genesis allocations are migrated to it
   using the IPFS CID as the source of truth.

   This is identical to how Ethereum's genesis block handled
   pre-sale ETH allocations in 2015.
"""

import hashlib
import json
import time
from datetime import datetime, timezone, date
from dataclasses import dataclass, field
from typing import Optional


# ============================================================
# PROTOCOL CONSTANTS — match hexis_mining_v0.2.py
# ============================================================

TOTAL_SUPPLY     = 12_800_000
GENESIS_BURN     =    768_000   # 6.0% burned at Block 0 → 0x000...dead
FOUNDER_ALLOC    =    192_000   # 1.5% vest 10yr cliff 1yr
EARLY_ALLOC      =    256_000   # 2.0% vest 4yr
PUBLIC_SUPPLY    = 11_584_000   # 90.5% mined from behavior

BURN_ADDRESS     = "0x0000000000000000000000000000000000000000"
FOUNDATION_NAME  = "HEXIS Foundation"
# Read "Singapore" until 2026-08-19. No entity exists there or anywhere else,
# and this value is not a caption — it lands in the `foundation.jurisdiction`
# field of the allocation record below, the document that calls itself the
# source of truth for every pre-mint allocation and is meant to be pinned to
# IPFS permanently. A false claim inside that record would have been the most
# load-bearing one in the repository.
#
# The field is kept rather than deleted so the record's shape does not change;
# what it says is now true. Nothing already written needs revisiting: this
# module is not on the VPS, nothing imports it, no allocation record has ever
# been generated from it, and the audit chain's own `genesis` event carries
# only {"message","schema_version"} — no jurisdiction reached any signed row.
FOUNDATION_JURIS = "none — no legal entity, by design"


# ============================================================
# VESTING SCHEDULE CALCULATOR
# ============================================================

class VestingSchedule:
    """
    Calculates vesting for a given allocation.

    Two schedules:
        Founder: 10-year linear vest, 1-year cliff
            - Year 0-1: 0 hexis unlocked (cliff period)
            - Year 1:   19,200 hexis unlock (10% in one event)
            - Year 2-10: 19,200/year
            - Total: 192,000 hexis over 10 years

        Early:  4-year linear vest, no cliff
            - Year 1: 64,000 hexis
            - Year 2: 64,000 hexis
            - Year 3: 64,000 hexis
            - Year 4: 64,000 hexis
            - Total: 256,000 hexis over 4 years
    """

    @staticmethod
    def founder(genesis_date: str) -> list:
        """
        Returns list of unlock events for founder allocation.
        genesis_date: "YYYY-MM-DD"
        """
        genesis = date.fromisoformat(genesis_date)
        annual = FOUNDER_ALLOC // 10   # 19,200/year
        events = []

        for year in range(1, 11):  # years 1-10 (cliff at year 0)
            unlock_date = date(
                genesis.year + year,
                genesis.month,
                genesis.day
            )
            events.append({
                "year":         year,
                "unlock_date":  unlock_date.isoformat(),
                "amount":       annual,
                "cumulative":   annual * year,
                "pct_total":    round(annual * year / FOUNDER_ALLOC * 100, 1),
                "note":         "cliff" if year == 1 else f"year {year}",
            })

        return events

    @staticmethod
    def early(genesis_date: str) -> list:
        """
        Returns list of unlock events for early believers allocation.
        genesis_date: "YYYY-MM-DD"
        """
        genesis = date.fromisoformat(genesis_date)
        annual = EARLY_ALLOC // 4   # 64,000/year
        events = []

        for year in range(1, 5):  # years 1-4
            unlock_date = date(
                genesis.year + year,
                genesis.month,
                genesis.day
            )
            events.append({
                "year":        year,
                "unlock_date": unlock_date.isoformat(),
                "amount":      annual,
                "cumulative":  annual * year,
                "pct_total":   round(annual * year / EARLY_ALLOC * 100, 1),
            })

        return events

    @staticmethod
    def unlocked_by(
        schedule: list,
        as_of_date: str,
    ) -> int:
        """
        Returns total hexis unlocked as of a given date.
        as_of_date: "YYYY-MM-DD"
        """
        as_of = date.fromisoformat(as_of_date)
        unlocked = 0
        for event in schedule:
            if date.fromisoformat(event["unlock_date"]) <= as_of:
                unlocked += event["amount"]
        return unlocked


# ============================================================
# GENESIS ALLOCATION RECORD
# ============================================================

@dataclass
class Recipient:
    """A pre-mint recipient."""
    name:           str     # Legal name or pseudonym
    wallet_address: str     # ETH-compatible address (0x...)
    allocation:     int     # Total hexis allocated
    vest_type:      str     # "founder" or "early"
    notes:          str     # e.g. "3+ years in project, early believer"


@dataclass
class GenesisAllocation:
    """
    The authoritative pre-mint allocation record.
    Created once. Signed by founder. Pinned to IPFS.
    Cannot be altered — any change changes the IPFS CID.
    """
    genesis_date:    str    # "YYYY-MM-DD" — Block 0 date
    protocol_name:   str    # "Hexis"
    total_supply:    int    # 12,800,000
    genesis_burn:    int    # 768,000 → BURN_ADDRESS
    recipients:      list   # List of Recipient dicts
    founder_message: str    # The genesis statement
    ipfs_cid:        str    # Filled in after pinning to IPFS
    signature:       str    # Founder's cryptographic signature (future)


def build_genesis_record(
    genesis_date: str,
    recipients: list,   # List of Recipient objects
    founder_message: str,
) -> dict:
    """
    Builds the genesis allocation record as a canonical JSON dict.
    This dict is what gets pinned to IPFS.

    Args:
        genesis_date:     "YYYY-MM-DD" of Block 0
        recipients:       List of Recipient objects
        founder_message:  The statement embedded in Block 0

    Returns:
        dict ready to be signed and pinned to IPFS
    """
    vesting = VestingSchedule()

    recipient_records = []
    for r in recipients:
        if r.vest_type == "founder":
            schedule = vesting.founder(genesis_date)
        elif r.vest_type == "early":
            schedule = vesting.early(genesis_date)
        else:
            schedule = []

        recipient_records.append({
            "name":            r.name,
            "wallet_address":  r.wallet_address,
            "allocation":      r.allocation,
            "vest_type":       r.vest_type,
            "notes":           r.notes,
            "vesting_schedule": schedule,
            "first_unlock":    schedule[0]["unlock_date"] if schedule else None,
            "final_unlock":    schedule[-1]["unlock_date"] if schedule else None,
        })

    # Burn record
    burn_record = {
        "recipient":       BURN_ADDRESS,
        "description":     "Genesis burn — 6.0% of total supply",
        "amount":          GENESIS_BURN,
        "vest_type":       "burn",
        "irrevocable":     True,
        "purpose":         (
            "Burned at genesis to demonstrate that the Foundation holds zero hexis "
            "and that the protocol's first act is sacrifice, not accumulation."
        ),
    }

    record = {
        "protocol":           "Hexis",
        "version":            "0.1",
        "genesis_date":       genesis_date,
        "created_at":         datetime.now(timezone.utc).isoformat(),

        "supply": {
            "total":          TOTAL_SUPPLY,
            "genesis_burn":   GENESIS_BURN,
            "pre_mint":       FOUNDER_ALLOC + EARLY_ALLOC,
            "public_mine":    PUBLIC_SUPPLY,
        },

        "genesis_burn":       burn_record,
        "recipients":         recipient_records,

        "foundation": {
            "name":           FOUNDATION_NAME,
            "jurisdiction":   FOUNDATION_JURIS,
            "hexis_held":     0,
            "note":           "Foundation holds zero hexis. Operates on fiat only.",
        },

        "founder_message":    founder_message,

        "verification": {
            "note": (
                "This record is the source of truth for all pre-mint allocations. "
                "To verify any allocation: re-hash this document with SHA256. "
                "The hash must match the proof_hash field. "
                "The IPFS CID of this document cannot be altered retroactively."
            ),
        },

        "proof_hash":  None,   # Filled in below
        "ipfs_cid":    None,   # Filled in after IPFS pin
        "signature":   None,   # Filled in by founder's private key (future)
    }

    # Self-referential hash (excluding proof_hash, ipfs_cid, signature)
    hashable = {k: v for k, v in record.items()
                if k not in ("proof_hash", "ipfs_cid", "signature")}
    canonical = json.dumps(hashable, sort_keys=True, ensure_ascii=True)
    record["proof_hash"] = hashlib.sha256(canonical.encode()).hexdigest()

    return record


# ============================================================
# MINING REGISTRATION
# ============================================================

@dataclass
class MinerRegistration:
    """
    Anyone who reads the whitepaper and wants to mine hexis
    can register their intent before the system is live.

    Registration is NOT a commitment. It is a signal of interest.
    It creates a timestamped record on IPFS that says:
    "This wallet address intends to participate in the Hexis network."

    When the live system launches, registered miners are notified first.
    """
    wallet_address:    str    # ETH-compatible wallet (0x...)
    contact:           str    # Email or public social handle (optional)
    interest_areas:    list   # Types of events they want to verify
                              # e.g. ["political", "commercial", "community"]
    referral_source:   str    # How they found Hexis (whitepaper, twitter, etc.)
    registration_note: str    # Why they want to participate (optional)


def build_registration_record(reg: MinerRegistration) -> dict:
    """
    Builds a timestamped registration record for a prospective miner.
    This gets pinned to IPFS as proof of early interest.
    """
    record = {
        "type":              "miner_registration",
        "protocol":          "Hexis",
        "version":           "0.1",
        "registered_at":     datetime.now(timezone.utc).isoformat(),
        "wallet_address":    reg.wallet_address,
        "contact":           reg.contact or "not provided",
        "interest_areas":    reg.interest_areas,
        "referral_source":   reg.referral_source,
        "registration_note": reg.registration_note or "",
        "acknowledgment": (
            "I have read the Hexis whitepaper v0.3. "
            "I understand this protocol may be useless. "
            "I am registering my interest to participate as a miner/verifier "
            "when the system launches. This registration carries no financial "
            "commitment and no guarantee of hexis allocation."
        ),
    }

    canonical = json.dumps(
        {k: v for k, v in record.items() if k != "registration_hash"},
        sort_keys=True, ensure_ascii=True
    )
    record["registration_hash"] = hashlib.sha256(canonical.encode()).hexdigest()

    return record


# ============================================================
# MINING FLOW EXPLAINER
# ============================================================

MINING_GUIDE = """
HOW TO MINE HEXIS — Step by Step
==================================

WHAT IS MINING IN HEXIS?

Unlike Bitcoin, you do not need a GPU or electricity to mine hexis.
You need to identify a real-world event where someone behaved honestly
under costly circumstances, and submit it to the network for verification.

If the network verifies it and the HEXIS value meets the minimum threshold,
hexis is minted and credited to your wallet address.

STEP 1: IDENTIFY AN EVENT
─────────────────────────
Find a public behavior event that meets the criteria:
    - A person made a claim or took an action
    - They had the opportunity to betray / defect
    - They did not
    - There are independent witnesses
    - The outcome is verifiable

Examples:
    - A politician made a specific prediction that proved correct
    - A CEO disclosed bad news instead of hiding it
    - A judge ruled against the party that appointed them
    - A whistleblower reported misconduct at personal cost

STEP 2: FILL IN THE HUMAN-SUPPLIED VARIABLES
─────────────────────────────────────────────
These four values require your judgment:

    asset_could_have_taken:
        How much could the actor have gained by betraying?
        Use market prices, prediction market odds, or expert estimates.

    asset_actually_returned:
        How much did they actually give up / return / honor?
        For full integrity: equals asset_could_have_taken.

    prob_betrayal_detected:
        If they had betrayed, how likely was detection?
        Public figures in media: 0.8–0.95
        Anonymous actors in small communities: 0.03–0.10

    gain_if_betrayed:
        In dollar terms, what would betrayal have gained?
        Political: estimate from prediction markets
        Financial: market cap impact
        Social: estimated reputation value

These cannot be automated. Your judgment IS the proof of work.

STEP 3: SUBMIT TO hexisfoundation.org
──────────────────────────────────────
When the submission portal is live:
    1. Connect your wallet (MetaMask or equivalent)
    2. Fill in the event form with the above variables
    3. Paste the URL or reference of the public claim
    4. Submit

The system will automatically:
    - Collect witness count from News APIs
    - Count mentions from GDELT
    - Classify witnesses as adversarial/neutral/allied
    - Calculate HEXIS value
    - Submit to the verification panel

STEP 4: VERIFICATION PANEL REVIEW
───────────────────────────────────
A panel of 5 independent verifiers reviews your submission.
They can:
    - Approve (hexis is minted)
    - Request adjustment to the human-supplied variables
    - Reject with explanation

If approved: hexis is minted to your wallet address.
Verifiers are paid in stable coin for their time.

STEP 5: HEXIS CREDITED TO YOUR WALLET
───────────────────────────────────────
The IPFS CID of the verified record is linked to your wallet.
You can:
    - Hold (stake for verifier yield)
    - Transfer to another wallet (subject to wallet cap of 10,000)
    - Use as a trust signal for commercial purposes

TIMING MATTERS:
───────────────
Submit events as soon as they happen — before the outcome is known.
A claim verified BEFORE outcome → Timing Score = 1.0
A claim verified AFTER outcome  → Timing Score decays

The earlier you submit, the more hexis you earn.
This is the anti-gaming mechanism.

REGISTER NOW (before launch):
───────────────────────────────
Email: register@hexisfoundation.org
Include: your wallet address and areas of interest
You will be notified when the submission portal is live.
"""


# ============================================================
# EXAMPLE: Build the genesis record
# ============================================================

def run_example():
    print("=" * 65)
    print("HEXIS GENESIS ALLOCATION SYSTEM v0.1")
    print("=" * 65)

    # --- Vesting schedule preview ---
    print("\n[1] FOUNDER VESTING SCHEDULE (192,000 HEXIS over 10 years)")
    print("-" * 50)
    schedule = VestingSchedule.founder("2026-04-28")
    for event in schedule:
        print(f"  {event['unlock_date']}  +{event['amount']:>6,}  "
              f"(cumulative: {event['cumulative']:>7,}  {event['pct_total']}%)")
    today = date.today().isoformat()
    unlocked = VestingSchedule.unlocked_by(schedule, today)
    print(f"\n  Unlocked as of today ({today}): {unlocked:,} hexis")

    print("\n[2] EARLY BELIEVERS VESTING SCHEDULE (256,000 HEXIS over 4 years)")
    print("-" * 50)
    early_schedule = VestingSchedule.early("2026-04-28")
    for event in early_schedule:
        print(f"  {event['unlock_date']}  +{event['amount']:>6,}  "
              f"(cumulative: {event['cumulative']:>7,}  {event['pct_total']}%)")

    # --- Genesis record preview ---
    print("\n[3] GENESIS ALLOCATION RECORD PREVIEW")
    print("-" * 50)
    print("  (In production: real wallet addresses used)")
    print("  (Record signed by founder's private key)")
    print("  (Pinned to IPFS at Block 0 — CID is source of truth)")
    print()

    example_recipients = [
        Recipient(
            name           = "Founder",
            wallet_address = "0xFOUNDER_ADDRESS_REPLACE_BEFORE_BLOCK_0",
            allocation     = FOUNDER_ALLOC,
            vest_type      = "founder",
            notes          = "Protocol creator. 10-year vest, 1-year cliff.",
        ),
        Recipient(
            name           = "Early Believer A",
            wallet_address = "0xEARLY_A_ADDRESS_REPLACE_BEFORE_BLOCK_0",
            allocation     = EARLY_ALLOC // 2,   # Example: 2 early believers split equally
            vest_type      = "early",
            notes          = "3+ years in project. 4-year vest.",
        ),
        Recipient(
            name           = "Early Believer B",
            wallet_address = "0xEARLY_B_ADDRESS_REPLACE_BEFORE_BLOCK_0",
            allocation     = EARLY_ALLOC // 2,
            vest_type      = "early",
            notes          = "3+ years in project. 4-year vest.",
        ),
    ]

    record = build_genesis_record(
        genesis_date     = "2026-04-28",  # placeholder — replace with actual Block 0 date
        recipients       = example_recipients,
        founder_message  = (
            "The last judge has been captured. "
            "The protocol belongs to the behavior it records."
        ),
    )

    print(f"  Protocol:         {record['protocol']}")
    print(f"  Total supply:     {record['supply']['total']:,}")
    print(f"  Genesis burn:     {record['supply']['genesis_burn']:,} → {BURN_ADDRESS[:10]}...")
    print(f"  Pre-mint:         {record['supply']['pre_mint']:,}")
    print(f"  Public mine:      {record['supply']['public_mine']:,}")
    print(f"  Foundation hexis: {record['foundation']['hexis_held']}")
    print(f"  Proof hash:       {record['proof_hash'][:48]}...")
    print(f"  IPFS CID:         (assigned after pinning)")
    print(f"  Signature:        (assigned by founder private key)")

    # --- Mining registration example ---
    print("\n[4] EXAMPLE MINING REGISTRATION")
    print("-" * 50)
    reg = MinerRegistration(
        wallet_address    = "0xYOUR_WALLET_ADDRESS",
        contact           = "your@email.com",
        interest_areas    = ["political", "commercial", "community"],
        referral_source   = "whitepaper v0.3",
        registration_note = "Want to verify political integrity events.",
    )
    reg_record = build_registration_record(reg)
    print(f"  Wallet:           {reg_record['wallet_address']}")
    print(f"  Registered at:    {reg_record['registered_at']}")
    print(f"  Interest areas:   {reg_record['interest_areas']}")
    print(f"  Hash:             {reg_record['registration_hash'][:48]}...")

    print("\n[5] MINING GUIDE")
    print("-" * 50)
    print(MINING_GUIDE)

    print("=" * 65)
    print("SUMMARY: HOW PRE-MINT IS PROTECTED")
    print("=" * 65)
    print("""
  1. Genesis allocation document created at Block 0
     → Lists every recipient, wallet address, amount, vesting schedule
     → Signed by founder's private key
     → Pinned to IPFS → CID is permanent and immutable

  2. Each recipient's entitlement = their entry in the genesis record
     → Proof = IPFS CID + wallet address match
     → Cannot be altered without changing the CID
     → Public — anyone can verify anyone's allocation

  3. Vesting = annual IPFS records signed by founder
     → "Year 1 unlock: 19,200 hexis to wallet 0x..."
     → Each unlock event is its own IPFS record
     → References the genesis allocation CID

  4. When production ledger launches (smart contract)
     → All genesis allocations migrated automatically
     → IPFS CID is the authoritative source
     → Smart contract enforces vesting schedule onchain

  BEFORE LEDGER EXISTS:
  → Genesis record on IPFS IS the proof of entitlement
  → This is exactly how Ethereum handled pre-sale allocations
    before the mainnet launched in 2015
    """)
    print("=" * 65)


if __name__ == "__main__":
    run_example()
