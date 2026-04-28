"""
HEXIS DATA COLLECTOR v0.1
==========================
Module 1 of 3 for the Hexis MVP pipeline.

Automatically collects the two data inputs that the core mining algorithm needs:
    1. witness_sources  -> from News API (who reported this event)
    2. mention_counts   -> from GDELT   (how often it is referenced over time)

You need two free API keys:
    - News API:  https://newsapi.org/register  (free tier: 100 requests/day)
    - GDELT:     No key required. Free public API.

Run this file standalone to test:
    python hexis_data_collector.py

Then import DataCollector into your main pipeline:
    from hexis_data_collector import DataCollector
"""

import json
import time
import hashlib
import urllib.request
import urllib.parse
from datetime import datetime, timedelta, timezone
from typing import Optional


# ============================================================
# CONFIGURATION — edit these before running
# ============================================================

NEWS_API_KEY = "YOUR_NEWSAPI_KEY_HERE"   # Get from https://newsapi.org/register
# GDELT needs no key


# ============================================================
# MODULE 1A: WITNESS COLLECTOR (News API)
# ============================================================

class WitnessCollector:
    """
    Searches News API for outlets that independently reported on a claim.
    Returns a list of witness_sources ready for HexisMiner.

    News API free tier:
        - 100 requests/day
        - Articles from past 30 days only
        - Upgrade to paid for historical data

    Endpoint used:
        GET https://newsapi.org/v2/everything
        ?q=<query>
        &from=<date>
        &sortBy=relevancy
        &pageSize=100
        &apiKey=<key>
    """

    BASE_URL = "https://newsapi.org/v2/everything"

    def __init__(self, api_key: str):
        self.api_key = api_key

    def collect(
        self,
        query: str,
        from_date: str,          # format: "2026-04-13"
        to_date: Optional[str] = None,
        max_sources: int = 200,
    ) -> list:
        """
        Searches for all news sources that reported on the query.

        Args:
            query:       Keywords describing the claim. Be specific.
                         Example: "Hormuz strait Navy mines cleared"
            from_date:   When the event occurred. Format: YYYY-MM-DD
            to_date:     End of search window. Defaults to 7 days after from_date.
            max_sources: Maximum witnesses to collect.

        Returns:
            List of dicts ready for BehaviorEvent.witness_sources:
            [
                {"type": "unknown", "name": "reuters.com", "url": "...", "published": "..."},
                {"type": "unknown", "name": "cnn.com", ...},
                ...
            ]
            Note: "type" is set to "unknown" here.
                  The AdversarialClassifier (Module 3) will update it to
                  "adversarial", "neutral", or "allied".
        """
        if to_date is None:
            from_dt = datetime.strptime(from_date, "%Y-%m-%d")
            to_dt   = from_dt + timedelta(days=7)
            to_date = to_dt.strftime("%Y-%m-%d")

        params = {
            "q":        query,
            "from":     from_date,
            "to":       to_date,
            "sortBy":   "relevancy",
            "pageSize": min(100, max_sources),
            "language": "en",
            "apiKey":   self.api_key,
        }

        url = self.BASE_URL + "?" + urllib.parse.urlencode(params)

        try:
            with urllib.request.urlopen(url, timeout=10) as response:
                data = json.loads(response.read().decode())
        except Exception as e:
            print(f"[WitnessCollector] News API error: {e}")
            return []

        if data.get("status") != "ok":
            print(f"[WitnessCollector] API returned: {data.get('message', 'unknown error')}")
            return []

        articles = data.get("articles", [])
        witnesses = []
        seen_sources = set()

        for article in articles:
            source_name = article.get("source", {}).get("name", "unknown")
            source_id   = article.get("source", {}).get("id", source_name)
            url_str     = article.get("url", "")
            published   = article.get("publishedAt", "")

            # Deduplicate by source
            if source_id in seen_sources:
                continue
            seen_sources.add(source_id)

            witnesses.append({
                "type":      "unknown",   # AdversarialClassifier will fill this in
                "name":      source_name,
                "source_id": source_id,
                "url":       url_str,
                "published": published,
            })

            if len(witnesses) >= max_sources:
                break

        print(f"[WitnessCollector] Found {len(witnesses)} unique sources for: '{query}'")
        return witnesses


# ============================================================
# MODULE 1B: MENTION COUNTER (GDELT)
# ============================================================

class MentionCounter:
    """
    Uses the GDELT 2.0 Doc API to count how many times a claim
    is mentioned in global news over time.

    GDELT is free, no API key, updated every 15 minutes.
    Covers 65+ languages, 250+ countries.

    Endpoints:
        Timeline volume:
        https://api.gdeltproject.org/api/v2/doc/doc
            ?query=<keywords>
            &mode=timelinevol
            &format=json
            &startdatetime=<YYYYMMDDHHMMSS>
            &enddatetime=<YYYYMMDDHHMMSS>

    Returns mention_counts dict:
        {"30d": 1250, "1y": 450, "5y": 120}
    """

    BASE_URL = "https://api.gdeltproject.org/api/v2/doc/doc"

    def collect(
        self,
        query: str,
        event_date: str,        # format: "2026-04-13"
    ) -> dict:
        """
        Counts news mentions of a query at three time horizons:
            - 30 days after the event
            - 1 year after the event
            - 5 years after the event (if available)

        For events less than 1 year old, estimates are used for longer horizons.

        Args:
            query:       Keywords. Same as WitnessCollector.
            event_date:  Date event occurred. Format: YYYY-MM-DD

        Returns:
            {"30d": int, "1y": int, "5y": int}
        """
        event_dt = datetime.strptime(event_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        now_dt   = datetime.now(timezone.utc)

        mention_counts = {}

        # --- 30-day window ---
        end_30d = event_dt + timedelta(days=30)
        count_30d = self._query_gdelt(query, event_dt, min(end_30d, now_dt))
        mention_counts["30d"] = count_30d

        # --- 1-year window ---
        end_1y = event_dt + timedelta(days=365)
        if now_dt > end_1y:
            count_1y = self._query_gdelt(query, event_dt, end_1y)
            mention_counts["1y"] = count_1y
        else:
            # Event is less than 1 year old — extrapolate from 30-day trend
            days_elapsed = (now_dt - event_dt).days
            if days_elapsed > 0 and count_30d > 0:
                daily_rate = count_30d / min(days_elapsed, 30)
                mention_counts["1y"] = int(daily_rate * 365 * 0.3)  # decay factor 0.3
                print(f"[MentionCounter] Event < 1yr old. 1y estimate: {mention_counts['1y']}")
            else:
                mention_counts["1y"] = 0

        # --- 5-year window ---
        end_5y = event_dt + timedelta(days=1825)
        if now_dt > end_5y:
            count_5y = self._query_gdelt(query, event_dt, end_5y)
            mention_counts["5y"] = count_5y
        else:
            # Too early to know — estimate from 1-year trend
            if mention_counts.get("1y", 0) > 0:
                mention_counts["5y"] = int(mention_counts["1y"] * 0.2)  # strong decay
                print(f"[MentionCounter] Event < 5yr old. 5y estimate: {mention_counts['5y']}")
            else:
                mention_counts["5y"] = 0

        print(f"[MentionCounter] Mention counts for '{query}': {mention_counts}")
        return mention_counts

    def _query_gdelt(
        self,
        query: str,
        start_dt: datetime,
        end_dt: datetime,
    ) -> int:
        """
        Queries GDELT timeline volume API and returns total article count
        for the given time window.
        """
        fmt = "%Y%m%d%H%M%S"
        params = {
            "query":         urllib.parse.quote(query),
            "mode":          "timelinevol",
            "format":        "json",
            "startdatetime": start_dt.strftime(fmt),
            "enddatetime":   end_dt.strftime(fmt),
        }

        url = (
            self.BASE_URL
            + "?query=" + urllib.parse.quote(query)
            + "&mode=timelinevol"
            + "&format=json"
            + "&startdatetime=" + start_dt.strftime(fmt)
            + "&enddatetime="   + end_dt.strftime(fmt)
        )

        try:
            with urllib.request.urlopen(url, timeout=15) as response:
                raw = response.read().decode()
                data = json.loads(raw)
        except Exception as e:
            print(f"[MentionCounter] GDELT error: {e}")
            return 0

        # GDELT returns timeline bins — sum all values
        timeline = data.get("timeline", [])
        total = 0
        for series in timeline:
            for point in series.get("data", []):
                total += int(point.get("value", 0))

        return total


# ============================================================
# COMBINED DATA COLLECTOR
# ============================================================

class DataCollector:
    """
    Main interface. Combines WitnessCollector + MentionCounter.

    Usage:
        collector = DataCollector(news_api_key="YOUR_KEY")
        data = collector.collect(
            query      = "Trump Hormuz Navy mines cleared",
            event_date = "2026-04-13",
            actor_id   = "potus_47",
        )

        # data["witness_sources"] -> ready for BehaviorEvent
        # data["mention_counts"]  -> ready for BehaviorEvent
    """

    def __init__(self, news_api_key: str):
        self.witness_collector = WitnessCollector(news_api_key)
        self.mention_counter   = MentionCounter()

    def collect(
        self,
        query: str,
        event_date: str,
        actor_id: str,
        description: str = "",
    ) -> dict:
        """
        Full collection pipeline for one event.

        Returns a dict you can unpack directly into BehaviorEvent:
        {
            "event_id":        str,
            "actor_id":        str,
            "timestamp":       float,
            "description":     str,
            "witness_sources": list,   <- pass to BehaviorEvent
            "mention_counts":  dict,   <- pass to BehaviorEvent
        }

        You still need to provide manually:
            - asset_could_have_taken
            - asset_actually_returned
            - prob_betrayal_detected
            - gain_if_betrayed
        These require human judgment and cannot be automated.
        """
        print(f"\n[DataCollector] Collecting data for: '{query}'")
        print(f"[DataCollector] Event date: {event_date}")
        print(f"[DataCollector] Actor: {actor_id}")
        print()

        # Step 1: Collect witnesses
        witness_sources = self.witness_collector.collect(
            query      = query,
            from_date  = event_date,
        )

        # Small delay to respect rate limits
        time.sleep(1)

        # Step 2: Count mentions
        mention_counts = self.mention_counter.collect(
            query      = query,
            event_date = event_date,
        )

        # Step 3: Generate event_id from content hash
        event_content = f"{actor_id}:{query}:{event_date}"
        event_id = hashlib.sha256(event_content.encode()).hexdigest()[:16]

        # Step 4: Parse timestamp
        event_dt  = datetime.strptime(event_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        timestamp = event_dt.timestamp()

        result = {
            "event_id":        event_id,
            "actor_id":        actor_id,
            "timestamp":       timestamp,
            "description":     description or query,
            "witness_sources": witness_sources,
            "mention_counts":  mention_counts,
            "collection_meta": {
                "query":              query,
                "event_date":         event_date,
                "collected_at":       datetime.now(timezone.utc).isoformat(),
                "witness_count_raw":  len(witness_sources),
            }
        }

        print(f"\n[DataCollector] Collection complete.")
        print(f"  Witnesses found:  {len(witness_sources)}")
        print(f"  Mention counts:   {mention_counts}")
        print(f"  Event ID:         {event_id}")

        return result


# ============================================================
# STANDALONE TEST — run this file directly to verify setup
# ============================================================

if __name__ == "__main__":

    print("=" * 60)
    print("HEXIS DATA COLLECTOR — Setup Test")
    print("=" * 60)

    # --- Test GDELT (no API key needed) ---
    print("\n[TEST 1] GDELT mention counter (no API key needed)")
    counter = MentionCounter()
    counts = counter.collect(
        query      = "Hormuz strait commercial shipping",
        event_date = "2026-04-01",
    )
    print(f"  Result: {counts}")

    # --- Test News API (requires your key) ---
    if NEWS_API_KEY == "YOUR_NEWSAPI_KEY_HERE":
        print("\n[TEST 2] News API witness collector")
        print("  SKIPPED — add your API key at the top of this file.")
        print("  Get a free key at: https://newsapi.org/register")
    else:
        print("\n[TEST 2] News API witness collector")
        collector = WitnessCollector(NEWS_API_KEY)
        witnesses = collector.collect(
            query     = "Hormuz strait Navy mines",
            from_date = "2026-04-10",
        )
        print(f"  First 3 witnesses found:")
        for w in witnesses[:3]:
            print(f"    - {w['name']} ({w['published'][:10]})")

    print("\n[TEST 3] Full DataCollector pipeline")
    print("  Note: witness collection requires a valid NEWS_API_KEY.")
    print("  GDELT test will run regardless.")
    dc = DataCollector(news_api_key=NEWS_API_KEY)
    result = dc.collect(
        query       = "Hormuz strait mines cleared US Navy",
        event_date  = "2026-04-13",
        actor_id    = "potus_47",
        description = "Presidential declaration on Hormuz mine clearing",
    )
    print(f"\n  Collection summary:")
    print(f"    event_id:       {result['event_id']}")
    print(f"    witness count:  {len(result['witness_sources'])}")
    print(f"    mention counts: {result['mention_counts']}")
    print(f"\n  Next step: pass result into AdversarialClassifier (Module 3)")
    print(f"  to update witness 'type' fields.")
    print("\n" + "=" * 60)
    print("Data collection ready. Proceed to hexis_ledger.py (Module 2).")
    print("=" * 60)
