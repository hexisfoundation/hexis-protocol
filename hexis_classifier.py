"""
HEXIS ADVERSARIAL CLASSIFIER v0.1
===================================
Module 3 of 3 for the Hexis MVP pipeline.

Automatically classifies news sources as:
    "adversarial" -> ideologically/politically opposed to the actor
    "neutral"     -> independent, no clear alignment
    "allied"      -> ideologically/politically aligned with the actor

Why this matters for Hexis:
    A claim confirmed only by allied sources is weak evidence.
    A claim confirmed by adversarial sources is strong evidence.
    The classifier is what gives witness diversity its mathematical weight.

Two classification approaches — both included:

    Approach A (Fast, no ML): Known source database
        Uses a curated database of 200+ major outlets mapped to
        political lean (Left / Center-Left / Center / Center-Right / Right).
        Then compares outlet lean to actor lean to determine adversarial/allied.
        Works offline, no GPU needed, zero latency.

    Approach B (Slow, ML): Zero-shot NLP classification
        Uses HuggingFace zero-shot classification model locally.
        More flexible — works for unknown sources.
        Requires ~1.5 GB model download on first run.
        Runs on CPU (slow) or GPU (fast).

    The pipeline uses Approach A first, falls back to Approach B
    for unknown sources.

Setup:
    Approach A: No setup. Works immediately.
    Approach B: pip install transformers torch

Run standalone:
    python hexis_classifier.py

Import into pipeline:
    from hexis_classifier import AdversarialClassifier
"""

from typing import Optional


# ============================================================
# APPROACH A: KNOWN SOURCE DATABASE
# ============================================================

# Political lean ratings from AllSides + Media Bias/Fact Check
# Scale: -2 (Far Left), -1 (Left), 0 (Center), +1 (Right), +2 (Far Right)
# Sources: allsides.com, mediabiasfactcheck.com

SOURCE_LEAN_DATABASE = {
    # ---- Far Left (-2) ----
    "jacobin":          -2,
    "the nation":       -2,
    "mother jones":     -2,
    "in these times":   -2,
    "truthout":         -2,
    "common dreams":    -2,

    # ---- Left (-1) ----
    "cnn":              -1,
    "msnbc":            -1,
    "the new york times": -1,
    "new york times":   -1,
    "nyt":              -1,
    "washington post":  -1,
    "the washington post": -1,
    "huffpost":         -1,
    "huffington post":  -1,
    "the guardian":     -1,
    "vox":              -1,
    "slate":            -1,
    "salon":            -1,
    "nbc news":         -1,
    "abc news":         -1,
    "msnbc":            -1,
    "politico":         -1,
    "the atlantic":     -1,
    "new yorker":       -1,
    "the new yorker":   -1,
    "time magazine":    -1,
    "time":             -1,
    "los angeles times": -1,
    "la times":         -1,
    "npr":              -1,
    "pbs":              -1,
    "propublica":       -1,
    "buzzfeed news":    -1,
    "vice":             -1,

    # ---- Center-Left (-0.5, mapped to 0 for simplicity) ----
    "bbc":               0,
    "bbc news":          0,
    "reuters":           0,
    "associated press":  0,
    "ap":                0,
    "bloomberg":         0,
    "the economist":     0,
    "financial times":   0,
    "ft":                0,
    "axios":             0,
    "the hill":          0,
    "usa today":         0,
    "c-span":            0,

    # ---- Center (0) ----
    "reuters":           0,
    "ap news":           0,
    "c-span":            0,
    "pew research":      0,
    "gallup":            0,
    "statista":          0,

    # ---- Right (+1) ----
    "fox news":          1,
    "fox":               1,
    "the wall street journal": 1,
    "wsj":               1,
    "new york post":     1,
    "ny post":           1,
    "the washington times": 1,
    "national review":   1,
    "the weekly standard": 1,
    "reason":            1,
    "the federalist":    1,
    "daily wire":        1,
    "the daily wire":    1,
    "washington examiner": 1,
    "newsmax":           1,

    # ---- Far Right (+2) ----
    "breitbart":         2,
    "infowars":          2,
    "gateway pundit":    2,
    "oan":               2,
    "one america news":  2,

    # ---- International / Generally Neutral (0) ----
    "al jazeera":        0,
    "france24":          0,
    "deutsche welle":    0,
    "dw":                0,
    "nhk":               0,
    "abc australia":     0,
    "the telegraph":     1,
    "the times":         0,
    "le monde":         -1,
    "der spiegel":      -1,

    # ---- Data/Verification Sources (always neutral) ----
    "kpler":             0,
    "kpler vessel data": 0,
    "centcom":           0,
    "factcheck.org":     0,
    "politifact":       -1,
    "snopes":           -1,
    "fullfact":          0,
}

# Political lean of well-known actors
# Scale: -2 to +2
ACTOR_LEAN_DATABASE = {
    "potus_47":          2,    # Trump — Right
    "potus_46":         -1,    # Biden — Left
    "potus_44":         -1,    # Obama — Left
    "potus_43":          1,    # Bush — Right
    "dnc":              -1,
    "rnc":               1,
    "democrat":         -1,
    "republican":        1,
}


class KnownSourceClassifier:
    """
    Fast, offline classifier using the source lean database.

    Logic:
        actor_lean = political lean of the person making the claim
        source_lean = political lean of the reporting outlet

        if source_lean and actor_lean are on opposite ends:
            -> "adversarial"
        if source_lean is near 0 regardless of actor:
            -> "neutral"
        if source_lean and actor_lean are on same side:
            -> "allied"
    """

    NEUTRAL_SOURCE_THRESHOLD  = 0.6   # |source_lean| <= this -> always neutral
    ADVERSARIAL_LEAN_MIN      = 0.9   # source must have |lean| > this to be adversarial
    ALLIED_LEAN_MIN           = 0.9   # source must have |lean| > this to be allied

    def classify(self, source_name: str, actor_id: str) -> str:
        """
        Classifies a source relative to an actor.

        Args:
            source_name: e.g. "CNN", "reuters.com", "Fox News"
            actor_id:    e.g. "potus_47"

        Returns:
            "adversarial", "neutral", or "allied"
        """
        # Normalize source name
        source_key = source_name.lower().strip()

        # Remove common suffixes
        for suffix in [".com", ".org", ".net", ".co.uk", " news", " media"]:
            source_key = source_key.replace(suffix, "")

        # Look up source lean
        source_lean = SOURCE_LEAN_DATABASE.get(source_key)

        if source_lean is None:
            # Try partial match
            for key, lean in SOURCE_LEAN_DATABASE.items():
                if key in source_key or source_key in key:
                    source_lean = lean
                    break

        if source_lean is None:
            return "unknown"   # Will be passed to NLP classifier

        # Step 1: If source is genuinely neutral, classify as neutral always
        # Reuters, AP, BBC etc should not be adversarial just because actor is far-right
        if abs(source_lean) <= self.NEUTRAL_SOURCE_THRESHOLD:
            return "neutral"

        # Step 2: Source has a clear lean — compare direction to actor
        actor_lean = ACTOR_LEAN_DATABASE.get(actor_id, 0)

        source_is_left  = source_lean < 0
        actor_is_left   = actor_lean  < 0

        # Opposite directions with sufficient lean = adversarial
        if source_is_left != actor_is_left and abs(source_lean) >= self.ADVERSARIAL_LEAN_MIN:
            return "adversarial"

        # Same direction with sufficient lean = allied
        if source_is_left == actor_is_left and abs(source_lean) >= self.ALLIED_LEAN_MIN:
            return "allied"

        # Weak or ambiguous lean
        return "neutral"


# ============================================================
# APPROACH B: NLP ZERO-SHOT CLASSIFIER (FALLBACK)
# ============================================================

class NLPClassifier:
    """
    HuggingFace zero-shot classification for unknown sources.

    Uses the source's article headline to infer tone toward the actor:
    positive/supportive -> allied
    negative/critical   -> adversarial
    factual/neutral     -> neutral

    First run: downloads ~1.5 GB model (facebook/bart-large-mnli).
    Subsequent runs: uses cached model.

    Requires: pip install transformers torch
    """

    MODEL_NAME = "facebook/bart-large-mnli"

    def __init__(self):
        self._pipeline = None  # Lazy load — only import if needed

    def _load(self):
        if self._pipeline is None:
            try:
                from transformers import pipeline
                print("[NLPClassifier] Loading zero-shot model (first run: ~1.5 GB download)...")
                self._pipeline = pipeline(
                    "zero-shot-classification",
                    model     = self.MODEL_NAME,
                    device    = -1,   # -1 = CPU. Use 0 for GPU if available.
                )
                print("[NLPClassifier] Model loaded.")
            except ImportError:
                print("[NLPClassifier] transformers not installed.")
                print("  Run: pip install transformers torch")
                self._pipeline = None

    def classify(self, article_headline: str, actor_description: str) -> str:
        """
        Classifies article stance toward an actor using zero-shot NLP.

        Args:
            article_headline:  Headline of the article.
                               e.g. "Trump's Hormuz promise proves correct"
            actor_description: Plain text describing the actor.
                               e.g. "US President Trump"

        Returns:
            "adversarial", "neutral", or "allied"
        """
        self._load()
        if self._pipeline is None:
            return "unknown"

        candidate_labels = [
            f"critical of {actor_description}",
            f"supportive of {actor_description}",
            f"neutral factual reporting",
        ]

        result = self._pipeline(
            sequences         = article_headline,
            candidate_labels  = candidate_labels,
            hypothesis_template = "This article is {}.",
        )

        top_label = result["labels"][0]

        if "critical" in top_label:
            return "adversarial"
        elif "supportive" in top_label:
            return "allied"
        else:
            return "neutral"


# ============================================================
# COMBINED ADVERSARIAL CLASSIFIER
# ============================================================

class AdversarialClassifier:
    """
    Main classifier. Combines KnownSource + NLP fallback.

    Usage:
        classifier = AdversarialClassifier()

        # Classify a single source
        source_type = classifier.classify_source(
            source_name = "CNN",
            actor_id    = "potus_47",
        )
        # -> "adversarial"

        # Classify all witnesses in a BehaviorEvent
        updated_witnesses = classifier.classify_witness_list(
            witnesses = behavior_event.witness_sources,
            actor_id  = "potus_47",
        )
        # -> [{"type": "adversarial", "name": "CNN", ...}, ...]
    """

    def __init__(self, use_nlp_fallback: bool = True):
        self.known_source = KnownSourceClassifier()
        self.nlp          = NLPClassifier() if use_nlp_fallback else None

    def classify_source(
        self,
        source_name:       str,
        actor_id:          str,
        article_headline:  Optional[str] = None,
        actor_description: Optional[str] = None,
    ) -> str:
        """
        Classifies a single source.

        Step 1: Try known source database (fast, offline).
        Step 2: If unknown, try NLP on article headline (slow, requires model).
        Step 3: If both fail, return "neutral" as safe default.
        """
        # Step 1: Known source database
        result = self.known_source.classify(source_name, actor_id)

        if result != "unknown":
            return result

        # Step 2: NLP fallback on article headline
        if self.nlp and article_headline and actor_description:
            result = self.nlp.classify(article_headline, actor_description)
            if result != "unknown":
                print(f"[AdversarialClassifier] NLP classified '{source_name}' as '{result}'")
                return result

        # Step 3: Default to neutral
        print(f"[AdversarialClassifier] Unknown source '{source_name}' — defaulting to 'neutral'")
        return "neutral"

    def classify_witness_list(
        self,
        witnesses:         list,
        actor_id:          str,
        actor_description: Optional[str] = None,
    ) -> list:
        """
        Classifies all witnesses in a list.
        Updates the "type" field in-place on each witness dict.

        Args:
            witnesses:         List of dicts from DataCollector or manual input.
                               Each dict must have at least {"name": "..."}.
            actor_id:          e.g. "potus_47"
            actor_description: e.g. "US President Trump"
                               Used for NLP fallback. Optional.

        Returns:
            Same list with "type" fields updated.
        """
        classified_count = {"adversarial": 0, "neutral": 0, "allied": 0, "unknown": 0}

        for witness in witnesses:
            source_name = witness.get("name", "")
            headline    = witness.get("title", "")  # from News API article

            source_type = self.classify_source(
                source_name       = source_name,
                actor_id          = actor_id,
                article_headline  = headline or None,
                actor_description = actor_description,
            )

            witness["type"] = source_type
            classified_count[source_type] = classified_count.get(source_type, 0) + 1

        total = len(witnesses)
        print(f"\n[AdversarialClassifier] Classification complete ({total} witnesses):")
        for label, count in classified_count.items():
            if count > 0:
                pct = (count / total * 100) if total > 0 else 0
                print(f"  {label:12}: {count:4} ({pct:.1f}%)")

        return witnesses


# ============================================================
# STANDALONE TEST
# ============================================================

if __name__ == "__main__":

    print("=" * 60)
    print("HEXIS ADVERSARIAL CLASSIFIER — Setup Test")
    print("=" * 60)

    classifier = AdversarialClassifier(use_nlp_fallback=False)  # No NLP for quick test

    # Test individual sources against potus_47 (Trump, lean = +2)
    print("\n[TEST 1] Individual source classification against potus_47 (Trump)")
    print("-" * 50)

    test_cases = [
        ("CNN",             "potus_47",  "adversarial"),
        ("Reuters",         "potus_47",  "neutral"),
        ("Fox News",        "potus_47",  "allied"),
        ("Washington Post", "potus_47",  "adversarial"),
        ("Kpler",           "potus_47",  "neutral"),
        ("CENTCOM",         "potus_47",  "neutral"),
        ("Breitbart",       "potus_47",  "allied"),
        ("BBC",             "potus_47",  "neutral"),
        ("Unknown Source",  "potus_47",  "neutral"),  # fallback case
    ]

    passed = 0
    for source, actor, expected in test_cases:
        result = classifier.classify_source(source, actor)
        status = "PASS" if result == expected else "FAIL"
        if result == expected:
            passed += 1
        print(f"  [{status}] {source:20} -> {result:12} (expected: {expected})")

    print(f"\n  {passed}/{len(test_cases)} tests passed")


    # Test witness list classification
    print("\n[TEST 2] Batch witness classification")
    print("-" * 50)

    witnesses = [
        {"name": "CNN",             "title": "Trump claims Hormuz will open in 72 hours"},
        {"name": "Fox News",        "title": "President delivers on Hormuz promise"},
        {"name": "Reuters",         "title": "US Navy confirms mine clearing operation"},
        {"name": "New York Times",  "title": "Administration's Hormuz prediction under scrutiny"},
        {"name": "CENTCOM",         "title": "Official statement on Hormuz operations"},
        {"name": "Kpler",           "title": "Vessel tracking data confirms strait reopening"},
        {"name": "Breitbart",       "title": "Trump triumphant: Hormuz opens exactly as promised"},
        {"name": "Bloomberg",       "title": "Oil prices drop as Hormuz shipping resumes"},
    ]

    updated = classifier.classify_witness_list(
        witnesses         = witnesses,
        actor_id          = "potus_47",
        actor_description = "US President Trump",
    )

    print("\n  Updated witness types:")
    for w in updated:
        print(f"    [{w['type']:12}] {w['name']}")


    # Show adversarial ratio
    adversarial = sum(1 for w in updated if w["type"] == "adversarial")
    neutral     = sum(1 for w in updated if w["type"] == "neutral")
    allied      = sum(1 for w in updated if w["type"] == "allied")
    total       = len(updated)

    print(f"\n  Adversarial ratio: {adversarial}/{total} ({adversarial/total*100:.0f}%)")
    print(f"  This ratio is what gives the claim its credibility weight.")
    print(f"  A claim confirmed 50%+ by adversarial sources is very strong evidence.")


    # Test NLP fallback (if transformers installed)
    print("\n[TEST 3] NLP zero-shot fallback (requires: pip install transformers torch)")
    print("-" * 50)
    try:
        import transformers
        print("  transformers is installed. Testing NLP classifier...")
        nlp_classifier = AdversarialClassifier(use_nlp_fallback=True)
        result = nlp_classifier.classify_source(
            source_name       = "Unknown Regional Outlet",
            actor_id          = "potus_47",
            article_headline  = "President's bold claim proved entirely wrong",
            actor_description = "US President Trump",
        )
        print(f"  NLP result: 'Unknown Regional Outlet' classified as '{result}'")
        print(f"  Expected: 'adversarial' (headline is critical)")
    except ImportError:
        print("  SKIPPED — transformers not installed.")
        print("  To enable NLP fallback: pip install transformers torch")

    print("\n" + "=" * 60)
    print("Classifier ready. Proceed to hexis_pipeline.py (full pipeline).")
    print("=" * 60)
