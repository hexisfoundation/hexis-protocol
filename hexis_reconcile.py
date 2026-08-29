"""
HEXIS reconcile — recompute what `hexis.db` stores from the rows that produced it
================================================================================

`hexis.db` keeps three running totals, each updated in place by one `+=` per
event, inside the same transaction that inserts the event:

    actors.hexis_score              += hexis_minted
    network_stats.total_hexis_mined += hexis_minted
    network_stats.total_events      += 1

The `events` table holds every movement that produced them. Until 2026-08-14
nothing compared the two, so each stored number *was* the record — corrupt it
and there was nothing left to disagree with it. That is the shape recorded in
CORRECTIONS.md under the Harmony entry: a total that cannot disagree with the
rows beneath it cannot report a breach.

This module is that comparison, and only that. It is the `hexis.db` half of the
standing reconcile item. The bridge half was closed two days later by
`hexis_ledger_entries.py`, which had to build the second record first — escrow
had none — and, because the bridge has an audit chain to anchor its result in,
ended up with the tamper-evidence this module says below that it cannot offer.

Entry points
------------
    reconcile_hexis_db(conn)     -> ReconcileResult      (pure read)
    format_report(result)        -> str                  (names actor + delta)
    append_record(path, result)  -> None                 (durable record)

Used by `hexis_api_v0.6.1.py` at boot, where a mismatch refuses to start, on a
timer while it runs, and from the command line:

    python3 hexis_api_v0.6.1.py --reconcile


What the record proves, and what it does not
--------------------------------------------
Every run appends one JSON line to `reconcile_hexis_db.jsonl`, beside the
database rather than inside it, and `fsync`s it. `hexis.db` has no audit chain,
so this is a log, not evidence, and the difference matters:

It **does** show that the check ran at a given time, against a named database,
and exactly what it found — including the passes, which is the part that gives
a later mismatch a date. It survives the thing it checks: deleting, restoring
or rewriting `hexis.db` does not touch the record, and `pull_backup_from_vps.sh`
copies it off the host, so the useful copy is not on the machine being asked
about.

It does **not** prove any of the following, and must not be quoted as if it
did:

  - **It is not tamper-evident.** No hash chain, no signature. Anyone who can
    rewrite `hexis.db` can also edit or truncate this file — it is the same
    root on the same disk. Chaining it to itself was considered and rejected:
    a chain with nothing anchoring it off-host proves only that whoever edited
    the file could also recompute it, while *reading* as though it were the
    bridge's sealed chain. That is the "documentation that lies" pattern this
    project keeps correcting, so the honest version is an ordinary log that
    says it is an ordinary log.
  - **It does not prove the history is authentic.** Reconciling proves the
    stored totals agree with `events` *as `events` now stands*. Delete an event
    row and decrement the totals to match and every run afterwards passes. This
    catches drift, bugs and partial writes; it does not catch a consistent
    rewrite. The bridge's audit chain is the thing that catches that, and
    `hexis.db` does not have one.
  - **The float check loses sensitivity as the table grows.** See `tolerance`.
    The integer checks (`event_count`, `total_events`) stay exact at every
    size, which is why they are here even though only the money-ish figures
    were asked for.
"""

import heapq
import json
import os
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Iterator, List, Optional

RECORD_FILENAME = "reconcile_hexis_db.jsonl"

# How many discrepancies are listed in a report. The *count* is never capped —
# a report always says "showing N of M" — because a truncated list that does
# not say it was truncated reads as a complete one.
DEFAULT_MAX_REPORTED = 20

# `network_stats` keys deliberately NOT reconciled, each with its reason. Same
# shape as UNGUARDED_WRITE_ROUTES in the API, for the same reason: an exemption
# with a reason beside it is a decision, an exemption without one is a hole.
UNRECONCILED_STATS: Dict[str, str] = {
    # Initialised to 0 by the schema and incremented by no code path — it is
    # dead, and CORRECTIONS.md records it as dead. Reconciling it would refuse
    # the boot the moment the first actor row exists: an outage caused entirely
    # by a field nobody maintains. Its value is still written into every record
    # (as `unreconciled`) so the deadness stays visible instead of becoming
    # invisible by exemption.
    "total_actors": "dead: initialised by the schema, incremented by nothing",
}


# ---------------------------------------------------------------------------
# Float comparison
# ---------------------------------------------------------------------------
#
# `hexis_score` is built by repeated addition in SQLite's own REAL arithmetic;
# the recomputation is a single `SUM()`. Since SQLite 3.44 `sum()` over floats
# uses compensated (Kahan-Babuska-Neumaier) summation, so it is *more* accurate
# than the running total and the two are not expected to agree bit for bit. The
# VPS runs 3.45.1. An `==` here would refuse to boot the service over the last
# bits of a double, which is a self-inflicted outage dressed as rigour.
#
# The bound below is the textbook worst case for naive summation, |error| <=
# n * u * sum|x_i| with u ~ 1.1e-16, rounded up an order of magnitude, plus an
# absolute floor for the empty and near-empty cases.
FLOAT_TOLERANCE_FLOOR = 1e-9
FLOAT_TOLERANCE_PER_ADDITION = 1e-15


def tolerance(expected: float, n_terms: int) -> float:
    """
    How far the running total may sit from the recomputation before it counts.

    Worth being explicit about the trade this makes. A real discrepancy is at
    least one event, and `compute_hexis` rounds to 6 decimals, so the smallest
    non-zero one is 1e-6 — a thousand times the floor. But the tolerance grows
    with the number of events, so at some table size a single missing event
    fits inside it and the float check stops seeing it. That size is around
    1e9 events at present values; long before then the exact integer checks
    (`event_count` per actor, `total_events`) are what would catch it, and they
    do not degrade. The float check is for corruption of the *value*, the
    integer checks are for loss of a *row*, and both are needed.
    """
    return FLOAT_TOLERANCE_FLOOR + FLOAT_TOLERANCE_PER_ADDITION * max(n_terms, 1) * abs(expected)


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------

@dataclass
class Discrepancy:
    """One disagreement, named. `scope`/`key` say whose, `field` says which."""
    scope: str          # "actor" | "network"
    key: str            # actor_id, or the network_stats key
    field: str          # hexis_score | event_count | total_events | ...
    stored: float
    recomputed: float
    note: str = ""

    @property
    def delta(self) -> float:
        """Stored minus recomputed. Positive means the stored total is high."""
        return self.stored - self.recomputed

    def describe(self) -> str:
        integral = self.field in ("event_count", "total_events")
        fmt = "{:.0f}" if integral else "{:.9f}"
        line = "  {:<7} {:<40} {:<18} stored {}  recomputed {}  delta {}".format(
            self.scope,
            self.key[:40],
            self.field,
            fmt.format(self.stored),
            fmt.format(self.recomputed),
            ("+" if self.delta >= 0 else "") + fmt.format(self.delta),
        )
        return line + (f"  ({self.note})" if self.note else "")

    def as_dict(self) -> Dict[str, Any]:
        d = {
            "scope": self.scope,
            "key": self.key,
            "field": self.field,
            "stored": self.stored,
            "recomputed": self.recomputed,
            "delta": self.delta,
        }
        if self.note:
            d["note"] = self.note
        return d


@dataclass
class ReconcileResult:
    checked_at: str
    source: str                      # "boot" | "periodic" | "cli"
    db_path: str
    actors_checked: int
    events_total: int
    duration_ms: float
    discrepancy_count: int           # the true total, never capped
    discrepancies: List[Discrepancy]  # the worst `max_reported` of them
    network: Dict[str, Dict[str, Any]]
    unreconciled: Dict[str, Any]

    @property
    def ok(self) -> bool:
        return self.discrepancy_count == 0

    def as_record(self, extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        rec = {
            "checked_at": self.checked_at,
            "source": self.source,
            "db": self.db_path,
            "ok": self.ok,
            "actors": self.actors_checked,
            "events": self.events_total,
            "duration_ms": round(self.duration_ms, 3),
            "discrepancy_count": self.discrepancy_count,
            "discrepancies": [d.as_dict() for d in self.discrepancies],
            "discrepancies_reported": len(self.discrepancies),
            "network": self.network,
            "unreconciled": self.unreconciled,
            "pid": os.getpid(),
        }
        if extra:
            rec.update(extra)
        return rec


class _Worst:
    """
    Keeps the `cap` largest-|delta| discrepancies without holding all of them.

    A reconcile that runs out of memory reporting how wrong the database is
    would be its own kind of joke. The count is tracked in full; only the list
    is bounded.
    """

    def __init__(self, cap: int):
        self.cap = cap
        self.total = 0
        self._seq = 0
        self._heap: list = []

    def add(self, d: Discrepancy) -> None:
        self.total += 1
        self._seq += 1
        # `_seq` breaks ties so the heap never has to compare two Discrepancy
        # objects, which are not ordered.
        heapq.heappush(self._heap, (abs(d.delta), self._seq, d))
        if len(self._heap) > self.cap:
            heapq.heappop(self._heap)

    def items(self) -> List[Discrepancy]:
        return [d for _, _, d in sorted(self._heap, key=lambda t: (-t[0], t[1]))]


# ---------------------------------------------------------------------------
# The check
# ---------------------------------------------------------------------------

@contextmanager
def read_snapshot(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """
    Hold one WAL read snapshot across every query in the reconcile.

    The API's read connections are autocommit (`isolation_level=None`), so
    without this each SELECT sees its own snapshot: the write queue commits a
    batch between the SUM over `events` and the read of `network_stats`, and
    the reconcile reports a mismatch that never existed — at boot, that means
    refusing to start over nothing. A deferred read transaction pins one
    version of the database for all of them.

    Allowed on the pool's `PRAGMA query_only=ON` connections: query_only
    forbids writing, not reading transactionally.
    """
    conn.execute("BEGIN DEFERRED")
    try:
        yield conn
    finally:
        try:
            conn.execute("COMMIT")
        except sqlite3.OperationalError:
            # No transaction active — nothing was read, or SQLite already
            # ended it. Either way there is nothing to release.
            pass


def reconcile_hexis_db(
    conn: sqlite3.Connection,
    *,
    source: str = "cli",
    db_path: Optional[str] = None,
    max_reported: int = DEFAULT_MAX_REPORTED,
) -> ReconcileResult:
    """
    Compare every stored total in `hexis.db` against a recomputation from
    `events`. Read-only: it repairs nothing and writes nothing to the database.

    Repair is not automatic anywhere in here, and that is deliberate — a
    mismatch does not say which side is wrong, and a reconcile that "fixes"
    the totals to match the rows would quietly destroy the evidence in the
    case where it is the rows that went missing.
    """
    started = time.perf_counter()
    worst = _Worst(max_reported)

    with read_snapshot(conn):
        events_total, minted_total = conn.execute(
            "SELECT COUNT(*), COALESCE(SUM(hexis_minted), 0.0) FROM events"
        ).fetchone()
        events_total = int(events_total or 0)
        minted_total = float(minted_total or 0.0)

        stats = {
            str(k): (float(v) if v is not None else 0.0)
            for k, v in conn.execute("SELECT key, value FROM network_stats")
        }

        # Per actor: the stored score and count against the events that made
        # them. LEFT JOIN so an actor row with no events at all still gets
        # compared rather than silently skipped.
        actors_checked = 0
        rows = conn.execute("""
            SELECT a.actor_id,
                   a.hexis_score,
                   a.event_count,
                   COALESCE(e.minted, 0.0),
                   COALESCE(e.n, 0)
            FROM actors a
            LEFT JOIN (
                SELECT actor_id, SUM(hexis_minted) AS minted, COUNT(*) AS n
                FROM events
                GROUP BY actor_id
            ) e ON e.actor_id = a.actor_id
        """)
        for actor_id, stored_score, stored_count, recomputed_score, recomputed_count in rows:
            actors_checked += 1
            stored_score = float(stored_score or 0.0)
            stored_count = int(stored_count or 0)
            recomputed_score = float(recomputed_score or 0.0)
            recomputed_count = int(recomputed_count or 0)

            if abs(stored_score - recomputed_score) > tolerance(recomputed_score, recomputed_count):
                worst.add(Discrepancy("actor", str(actor_id), "hexis_score",
                                      stored_score, recomputed_score))
            if stored_count != recomputed_count:
                worst.add(Discrepancy("actor", str(actor_id), "event_count",
                                      float(stored_count), float(recomputed_count)))

        # Events whose actor has no row at all. The FK is declared but SQLite
        # does not enforce it unless `PRAGMA foreign_keys=ON`, which nothing
        # here sets, so this is reachable rather than theoretical — and the
        # join above cannot see it.
        orphans = conn.execute("""
            SELECT e.actor_id, SUM(e.hexis_minted), COUNT(*)
            FROM events e
            LEFT JOIN actors a ON a.actor_id = e.actor_id
            WHERE a.actor_id IS NULL
            GROUP BY e.actor_id
        """)
        for actor_id, minted, n in orphans:
            worst.add(Discrepancy(
                "actor", str(actor_id), "missing_actor_row",
                0.0, float(minted or 0.0),
                note=f"{int(n)} event(s) credited to an actor with no row",
            ))

    # Network totals. `total_events` is compared exactly: it is a count, it is
    # incremented by 1 from 0, and doubles hold integers exactly to 2^53 — so
    # there is no float slack to allow and allowing any would only blunt it.
    network: Dict[str, Dict[str, Any]] = {}
    checks = (
        ("total_events", float(events_total), True),
        ("total_hexis_mined", minted_total, False),
    )
    for key, recomputed, exact in checks:
        if key not in stats:
            worst.add(Discrepancy("network", key, "missing_stat_row", 0.0, recomputed,
                                  note="row absent from network_stats"))
            network[key] = {"stored": None, "recomputed": recomputed}
            continue
        stored = stats[key]
        network[key] = {"stored": stored, "recomputed": recomputed}
        differs = (stored != recomputed) if exact else (
            abs(stored - recomputed) > tolerance(recomputed, events_total)
        )
        if differs:
            worst.add(Discrepancy("network", key, key, stored, recomputed))

    return ReconcileResult(
        checked_at=datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
        source=source,
        db_path=db_path or "",
        actors_checked=actors_checked,
        events_total=events_total,
        duration_ms=(time.perf_counter() - started) * 1000.0,
        discrepancy_count=worst.total,
        discrepancies=worst.items(),
        network=network,
        unreconciled={k: stats.get(k) for k in UNRECONCILED_STATS},
    )


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def format_report(result: ReconcileResult) -> str:
    head = (
        "hexis.db reconcile [{}]: {} — {} actor(s), {} event(s), {:.1f}ms".format(
            result.source,
            "OK" if result.ok else f"MISMATCH ({result.discrepancy_count})",
            result.actors_checked,
            result.events_total,
            result.duration_ms,
        )
    )
    if result.ok:
        return head
    lines = [head] + [d.describe() for d in result.discrepancies]
    # Always say how much of the list is being shown. A capped list that does
    # not admit it was capped reads as the whole story.
    lines.append("  (showing {} of {})".format(
        len(result.discrepancies), result.discrepancy_count))
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# The record
# ---------------------------------------------------------------------------

def default_record_path(db_path: str) -> str:
    """Beside the database, never inside it. See the module docstring."""
    return os.path.join(os.path.dirname(os.path.abspath(db_path)), RECORD_FILENAME)


def append_record(record_path: str, result: ReconcileResult,
                  extra: Optional[Dict[str, Any]] = None) -> None:
    """
    Append one JSON line and `fsync` it.

    fsync rather than a plain flush because the run this most needs to survive
    is the one where the host went down mid-incident, and a record sitting in
    the page cache is a record that describes a moment nobody can reach.
    """
    line = json.dumps(result.as_record(extra), sort_keys=True, separators=(",", ":"))
    with open(record_path, "a", encoding="utf-8") as fh:
        fh.write(line + "\n")
        fh.flush()
        os.fsync(fh.fileno())


def run_and_record(db_path: str, *, source: str = "cli",
                   record_path: Optional[str] = None,
                   max_reported: int = DEFAULT_MAX_REPORTED) -> ReconcileResult:
    """Open the database read-only, reconcile, record. Used by the CLI."""
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("PRAGMA query_only=ON")
        result = reconcile_hexis_db(conn, source=source, db_path=db_path,
                                    max_reported=max_reported)
    finally:
        conn.close()
    append_record(record_path or default_record_path(db_path), result)
    return result
