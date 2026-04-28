"""
HEXIS PIPELINE v0.1
====================
Full end-to-end mining pipeline. Ties all three modules together.

    Module 1: hexis_data_collector.py  -> collects witnesses + mention counts
    Module 2: hexis_ledger.py          -> stores proof on IPFS
    Module 3: hexis_classifier.py      -> classifies witnesses as adversarial/neutral/allied
    Core:     hexis_mining_v0.1.py     -> computes HEXIS value

FULL PIPELINE:

    Event occurs in real world
            |
            v
    DataCollector.collect()        <- Module 1: gather raw witnesses + mention counts
            |
            v
    AdversarialClassifier          <- Module 3: classify each witness
            |
            v
    Human inputs sacrifice data    <- YOU: S, BO inputs (cannot be automated)
            |
            v
    HexisMiner.calculate_hexis()   <- Core: compute HEXIS score
            |
            v
    HexisLedger.store()            <- Module 2: pin proof to IPFS permanently
            |
            v
    IPFS CID returned              <- Permanent, verifiable, decentralized record


WHAT YOU PROVIDE MANUALLY (cannot be automated):
    - asset_could_have_taken:   How much could the actor have taken/kept?
    - asset_actually_returned:  How much did they actually return/honor?
    - prob_betrayal_detected:   How likely was betrayal to be caught?
    - gain_if_betrayed:         How much would betrayal have gained?

These four numbers require human judgment.
They are the "proof of work" that humans do to validate an event.
The system does not automate them intentionally — doing so would
undermine the human verification philosophy of Hexis.

SETUP:
    1. Get News API key:   https://newsapi.org/register
    2. Get Pinata JWT:     https://app.pinata.cloud/keys
    3. Set both below.
    4. Run: python hexis_pipeline.py

DEPENDENCIES:
    Standard library only (no pip installs needed for Approach A classifier).
    Optional: pip install transformers torch  (for NLP fallback classifier)
"""

import dataclasses
from datetime import timezone

# ---- Import all modules ----
# Place all four files in the same directory.
# Run from that directory.

import sys, os
sys.path.insert(0, os.path.dirname(__file__))

from hexis_mining_v0_1    import HexisMiner, BehaviorEvent      # noqa (rename file if needed)
from hexis_data_collector import DataCollector
from hexis_ledger         import HexisLedger
from hexis_classifier     import AdversarialClassifier


# Note: if the mining file is named hexis_mining_v0.1.py, Python cannot import it
# directly due to the dot in the name. Rename to hexis_mining.py first:
#     mv hexis_mining_v0.1.py hexis_mining.py
# Then change the import above to:
#     from hexis_mining import HexisMiner, BehaviorEvent


# ============================================================
# CONFIGURATION
# ============================================================

NEWS_API_KEY = "YOUR_NEWSAPI_KEY_HERE"
PINATA_JWT   = "YOUR_PINATA_JWT_HERE"


# ============================================================
# MAIN PIPELINE FUNCTION
# ============================================================

def mine_event(
    # --- Event identification ---
    query:             str,    # Keywords to search for this event
    event_date:        str,    # Format: "YYYY-MM-DD"
    actor_id:          str,    # Unique identifier for the actor
    actor_description: str,    # Plain text. e.g. "US President Trump"
    description:       str,    # Full description of the behavior

    # --- Human-supplied sacrifice variables (REQUIRED) ---
    asset_could_have_taken:  float,  # What could have been taken?
    asset_actually_returned: float,  # What was actually returned/honored?
    prob_betrayal_detected:  float,  # Probability of being caught if betrayal occurred
    gain_if_betrayed:        float,  # How much would betrayal have gained?

    # --- Options ---
    use_nlp_classifier: bool = False,  # True = slower but handles unknown sources
    dry_run:            bool = False,  # True = skip IPFS storage (for testing)
) -> dict:
    """
    Full mining pipeline for one event.

    Returns:
        {
            "success":    bool,
            "event_id":   str,
            "hexis_raw":  float,
            "ipfs_cid":   str or None,
            "result":     dict,    # full mining result
            "summary":    str,     # human-readable summary
        }
    """
    print("\n" + "=" * 65)
    print("HEXIS MINING PIPELINE v0.1")
    print("=" * 65)
    print(f"Event:  {description[:80]}")
    print(f"Actor:  {actor_id}")
    print(f"Date:   {event_date}")
    print("=" * 65)


    # ---- STEP 1: Collect witnesses and mention counts ----
    print("\n[STEP 1/4] Collecting data...")

    collector = DataCollector(news_api_key=NEWS_API_KEY)
    collected = collector.collect(
        query       = query,
        event_date  = event_date,
        actor_id    = actor_id,
        description = description,
    )

    witness_sources = collected["witness_sources"]
    mention_counts  = collected["mention_counts"]


    # ---- STEP 2: Classify witnesses ----
    print("\n[STEP 2/4] Classifying witnesses...")

    classifier = AdversarialClassifier(use_nlp_fallback=use_nlp_classifier)
    classified_witnesses = classifier.classify_witness_list(
        witnesses         = witness_sources,
        actor_id          = actor_id,
        actor_description = actor_description,
    )


    # ---- STEP 3: Build BehaviorEvent and mine ----
    print("\n[STEP 3/4] Computing HEXIS value...")

    event = BehaviorEvent(
        event_id                = collected["event_id"],
        actor_id                = actor_id,
        timestamp               = collected["timestamp"],
        description             = description,
        asset_could_have_taken  = asset_could_have_taken,
        asset_actually_returned = asset_actually_returned,
        prob_betrayal_detected  = prob_betrayal_detected,
        gain_if_betrayed        = gain_if_betrayed,
        witness_sources         = classified_witnesses,
        mention_counts          = mention_counts,
    )

    miner  = HexisMiner()
    result = miner.calculate_hexis_value(event)

    if not result.get("eligible"):
        print(f"\n[Pipeline] Event NOT eligible for minting.")
        print(f"  Reason: {result.get('reason')}")
        return {
            "success":   False,
            "event_id":  collected["event_id"],
            "hexis_raw": 0.0,
            "ipfs_cid":  None,
            "result":    result,
            "summary":   f"Not minted: {result.get('reason')}",
        }

    print(f"\n  S   (Sacrifice):            {result['S']}")
    print(f"  BO  (Betrayal Opportunity): {result['BO']}")
    print(f"  W   (Witness Score):        {result['W']}")
    print(f"  TDR (Time Decay):           {result['TDR']}")
    print(f"  {'─' * 40}")
    print(f"  HEXIS VALUE:                {result['hexis_raw']}")
    print(f"  Grade:                      {result['interpretation']}")


    # ---- STEP 4: Store on IPFS ----
    ipfs_cid = None

    if not dry_run:
        print("\n[STEP 4/4] Storing proof on IPFS...")

        ledger  = HexisLedger(pinata_jwt=PINATA_JWT)
        event_dict = dataclasses.asdict(event)
        ipfs_cid = ledger.store(result, event_dict)

        if ipfs_cid:
            print(f"\n  Permanent record: https://ipfs.io/ipfs/{ipfs_cid}")
        else:
            print("\n  IPFS storage skipped (no Pinata JWT or local daemon).")
            print("  Configure PINATA_JWT to enable permanent storage.")
    else:
        print("\n[STEP 4/4] Dry run — skipping IPFS storage.")


    # ---- Summary ----
    summary = (
        f"HEXIS MINTED\n"
        f"  Actor:      {actor_id}\n"
        f"  Event:      {description[:60]}\n"
        f"  HEXIS:      {result['hexis_raw']}\n"
        f"  Grade:      {result['interpretation']}\n"
        f"  Proof Hash: {result['proof_hash'][:32]}...\n"
        f"  IPFS CID:   {ipfs_cid or 'not stored'}"
    )

    print("\n" + "=" * 65)
    print(summary)
    print("=" * 65)

    return {
        "success":   True,
        "event_id":  result["event_id"],
        "hexis_raw": result["hexis_raw"],
        "ipfs_cid":  ipfs_cid,
        "result":    result,
        "summary":   summary,
    }


# ============================================================
# EXAMPLE RUN
# ============================================================

if __name__ == "__main__":

    output = mine_event(
        # Event identification
        query             = "Trump Hormuz Navy mines cleared strait reopens",
        event_date        = "2026-04-13",
        actor_id          = "potus_47",
        actor_description = "US President Trump",
        description       = (
            "US President publicly declares that the US Navy is actively clearing "
            "Iranian mines and the Strait of Hormuz will reopen for commercial "
            "shipping within 72 hours. Declaration verified accurate after 68 hours "
            "by Kpler vessel tracking data and CENTCOM official statement."
        ),

        # Human-supplied sacrifice variables
        # These require your judgment — no automation can replace this
        asset_could_have_taken  = 50_000_000.0,  # Political capital at risk from declaration
        asset_actually_returned = 50_000_000.0,  # Chose transparency — honored the claim
        prob_betrayal_detected  = 0.95,           # Very high scrutiny — would definitely be caught
        gain_if_betrayed        = 10_000_000.0,  # Short-term political gain from being vague

        # Options
        use_nlp_classifier = False,   # Set True to enable NLP for unknown sources
        dry_run            = True,    # Set False to enable IPFS storage
    )

    print(f"\nPipeline output:")
    print(f"  Success:   {output['success']}")
    print(f"  HEXIS:     {output['hexis_raw']}")
    print(f"  IPFS CID:  {output['ipfs_cid']}")
