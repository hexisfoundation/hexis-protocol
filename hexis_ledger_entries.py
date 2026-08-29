"""
Bridge ledger — the second record the bridge's balances can be checked against
==============================================================================

The bridge half of the standing reconcile item, and the thing that made it a
standing item: `stake_escrow.balance` is a running total, updated in place, one
`UPDATE ... balance = balance - ?` per movement. Nothing recorded the movements.
A total with no rows beneath it cannot disagree with anything, and a number that
cannot disagree cannot report a breach — the Harmony failure mode, recorded in
CORRECTIONS.md, arrived at from the money side instead of the reputation side.

`consumer_stake` / `worker_stake` are *not* that record. They hold locks and how
each lock ended, which is a subset: a `credit()`, a `transfer()`, a PoSP audit
fee and a severity repayment all move escrow and appear in neither table.

So: `ledger_entries`, append-only, one row per leg of every movement, written
**inside the same transaction as the balance change it describes**. Not beside
it, not afterwards — the same transaction, so there is no interval in which one
exists without the other, and no failure mode where the money moved and the
record did not.

Double entry, and why it is enforced at write time
--------------------------------------------------
Every operation writes legs that sum to zero. Money leaving escrow arrives
somewhere nameable: `locked` when a stake is locked, another actor's `escrow` on
a transfer, `burn` when a slash destroys it. Money entering escrow comes from
somewhere nameable too, and `issuance` is a name — an ugly one, deliberately,
because `credit()` creates ECU from nothing and the ledger should say so out
loud rather than let it appear as an unexplained increase.

The sum is checked **before the rows are written**, and an operation whose legs
do not balance raises, which rolls back the transaction it is inside. So the
failure mode is "the money did not move", never "the money moved and the books
are wrong". That is the whole reason to do the check at write time rather than
leave it to the reconcile: the reconcile can only tell you afterwards.

What the record proves here, and what `hexis.db`'s does not
-----------------------------------------------------------
Every reconcile run writes a `ledger_reconcile` event into the bridge's audit
chain carrying the ledger head (sequence and hash). That chain is hash-linked
and signed daily with an Ed25519 key that is not on the host.

So unlike `reconcile_hexis_db.jsonl`, this record **is tamper-evident**. Someone
who rewrites `bridge.db` — ledger rows, escrow balances and audit chain together
— still cannot produce the Foundation signature over the rewritten head, and
`verify_audit_chain.py` says so from off the host. That is the difference the
`hexis.db` module had to admit it could not offer, and it is the reason the
bridge half was worth waiting for rather than copying.

It still does not prove the history is *complete*. A movement that never called
this module leaves no trace to disagree with; that is what the boot audit of
`hexis_stake.py` (`audit_escrow_write_sites`) is for, and why `_debit` and
`_credit_in_tx` refuse to run outside an open operation.

Entry points
------------
    init_schema(conn)                      create the table
    ledger_op(conn, op, ...)               context manager; legs must sum to 0
    ensure_opening_balances(conn)          one-time migration for balances that
                                           predate the ledger
    reconcile(conn, ...)   -> Result       pure read, names actor and delta
    format_report(result)  -> str
    head(conn)             -> (seq, hash)

Accounts
--------
    escrow:<actor>          mirrors stake_escrow.balance
    locked:<actor>          mirrors the LOCKED rows in consumer_stake/worker_stake
    issuance                ECU created by credit() — a mint, named as one
    burn                    ECU destroyed by a slash
    opening                 balances that existed before this ledger did
    chain:<address>         NEWFLOW ChainState balances (in memory, see below)
    chain_issuance          genesis allocation and faucet payouts

`chain:*` is different in kind and is labelled so everywhere it is reported.
`ChainState` is rebuilt from scratch on every start, so the ledger's chain
entries outlive the balances they describe. They are reconciled only against
what the *current process* did — entries carrying the current `boot_id` — which
is a real check of the running process and is not a claim that chain balances
are durable. They are not. See `durability_block()` in the bridge.
"""

import hashlib
import json
import os
import sqlite3
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Iterator, List, Optional, Tuple

# --- accounts ---------------------------------------------------------------

ESCROW = "escrow"
LOCKED = "locked"
ISSUANCE = "issuance"
BURN = "burn"
OPENING = "opening"
CHAIN = "chain"
CHAIN_ISSUANCE = "chain_issuance"

#: Accounts with no actor. Their `actor_id` is the empty string, not a name that
#: could collide with a real address.
SYSTEM_ACCOUNTS = frozenset({ISSUANCE, BURN, OPENING, CHAIN_ISSUANCE})

#: Accounts holding real, durable ECU. `chain` is deliberately not here.
DURABLE_ACCOUNTS = frozenset({ESCROW, LOCKED})

ALL_ACCOUNTS = frozenset({ESCROW, LOCKED, CHAIN}) | SYSTEM_ACCOUNTS

#: This process. Chain entries are only reconcilable within one of these.
BOOT_ID = uuid.uuid4().hex

#: Same reasoning as hexis_reconcile.tolerance: SQLite ≥ 3.44 sums floats with
#: compensated arithmetic while a running `balance = balance + ?` does not, so
#: the two disagree in the last bits and `==` would refuse to boot over them.
TOLERANCE_FLOOR = 1e-9
TOLERANCE_PER_TERM = 1e-15

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS ledger_entries (
    seq        INTEGER PRIMARY KEY,
    entry_id   TEXT NOT NULL UNIQUE,
    op_id      TEXT NOT NULL,
    op         TEXT NOT NULL,
    account    TEXT NOT NULL,
    actor_id   TEXT NOT NULL,
    delta      REAL NOT NULL,
    job_id     TEXT,
    reason     TEXT,
    boot_id    TEXT NOT NULL,
    ts_iso     TEXT NOT NULL,
    ts_unix    REAL NOT NULL,
    prev_hash  TEXT NOT NULL,
    entry_hash TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_le_account ON ledger_entries(account, actor_id);
CREATE INDEX IF NOT EXISTS ix_le_op      ON ledger_entries(op_id);
CREATE INDEX IF NOT EXISTS ix_le_job     ON ledger_entries(job_id);

CREATE TABLE IF NOT EXISTS ledger_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

#: Set once, in the same transaction as the opening balances. See
#: `ensure_opening_balances` for why emptiness is not a substitute.
META_OPENED_AT = "opened_at"
META_OPENED_TOTAL = "opened_total"

GENESIS_HASH = "0" * 64


class LedgerImbalance(Exception):
    """
    Raised before anything is written when an operation's legs do not sum to
    zero. It propagates out of the `with` block that owns the connection, so
    the balance changes made alongside it roll back with it.
    """


def tolerance(magnitude: float, n_terms: int) -> float:
    return TOLERANCE_FLOOR + TOLERANCE_PER_TERM * max(n_terms, 1) * abs(magnitude)


def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA_SQL)


def head(conn: sqlite3.Connection) -> Tuple[int, str]:
    """The last entry's sequence and hash — what a reconcile anchors."""
    row = conn.execute(
        "SELECT seq, entry_hash FROM ledger_entries ORDER BY seq DESC LIMIT 1"
    ).fetchone()
    if row is None:
        return 0, GENESIS_HASH
    return int(row[0]), str(row[1])


def _entry_hash(prev_hash: str, seq: int, op_id: str, op: str, account: str,
                actor_id: str, delta: float, job_id: Optional[str],
                ts_unix: float) -> str:
    # repr() of a float round-trips exactly, so the hash is over the number
    # stored rather than over a rounded rendering of it.
    payload = "|".join([
        prev_hash, str(seq), op_id, op, account, actor_id,
        repr(float(delta)), job_id or "", repr(float(ts_unix)),
    ])
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------

class LedgerOp:
    """
    One operation's legs, held until the block ends and then written together.

    Nothing is written leg by leg, because a half-written operation is exactly
    the state this table exists to make impossible. The legs are checked, then
    inserted, then the caller's transaction commits them alongside the balance
    changes they describe.
    """

    def __init__(self, conn: sqlite3.Connection, op: str, *,
                 job_id: Optional[str] = None, reason: Optional[str] = None,
                 boot_id: str = BOOT_ID):
        self.conn = conn
        self.op = op
        self.job_id = job_id
        self.reason = reason
        self.boot_id = boot_id
        self.op_id = uuid.uuid4().hex
        self.legs: List[Tuple[str, str, float]] = []

    def leg(self, account: str, actor_id: str, delta: float) -> None:
        if account not in ALL_ACCOUNTS:
            raise ValueError(f"unknown ledger account: {account!r}")
        if account in SYSTEM_ACCOUNTS:
            actor_id = ""
        elif not actor_id:
            raise ValueError(f"account {account!r} needs an actor_id")
        if delta == 0:
            return          # a zero leg records nothing and hides nothing
        self.legs.append((account, actor_id, float(delta)))

    # -- internals ---------------------------------------------------------

    def _imbalance(self) -> float:
        return sum(d for _, _, d in self.legs)

    def write(self) -> int:
        """Returns the number of rows written. Raises if the legs disagree."""
        if not self.legs:
            return 0
        total = self._imbalance()
        magnitude = sum(abs(d) for _, _, d in self.legs)
        if abs(total) > tolerance(magnitude, len(self.legs)):
            raise LedgerImbalance(
                f"operation {self.op!r} does not balance: legs sum to {total!r} "
                f"(job_id={self.job_id}) — "
                + "; ".join(f"{a}:{i or '-'} {d:+}" for a, i, d in self.legs)
            )
        now = time.time()
        ts_iso = datetime.now(timezone.utc).isoformat()
        seq, prev = head(self.conn)
        rows = []
        for account, actor_id, delta in self.legs:
            seq += 1
            h = _entry_hash(prev, seq, self.op_id, self.op, account, actor_id,
                            delta, self.job_id, now)
            rows.append((seq, h, self.op_id, self.op, account, actor_id, delta,
                         self.job_id, self.reason, self.boot_id, ts_iso, now,
                         prev, h))
            prev = h
        self.conn.executemany(
            "INSERT INTO ledger_entries(seq,entry_id,op_id,op,account,actor_id,"
            "delta,job_id,reason,boot_id,ts_iso,ts_unix,prev_hash,entry_hash) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            rows,
        )
        return len(rows)


@contextmanager
def ledger_op(conn: sqlite3.Connection, op: str, *, job_id: Optional[str] = None,
              reason: Optional[str] = None,
              boot_id: str = BOOT_ID) -> Iterator[LedgerOp]:
    """
    Open an operation on `conn`'s transaction.

    `BEGIN IMMEDIATE` is taken up front when no transaction is open yet. The
    sequence and the hash chain are read-then-written, so two writers who both
    read the same head would produce two entries claiming the same sequence.
    Taking the write lock at the start makes the second writer wait instead —
    at this system's traffic the wait is free, and the alternative is a
    `UNIQUE` violation on a money path.
    """
    if not conn.in_transaction:
        conn.execute("BEGIN IMMEDIATE")
    op_obj = LedgerOp(conn, op, job_id=job_id, reason=reason, boot_id=boot_id)
    yield op_obj
    op_obj.write()          # only on the non-exception path; see the docstring


def record_op(conn: sqlite3.Connection, op: str, legs: List[Tuple[str, str, float]],
              *, job_id: Optional[str] = None, reason: Optional[str] = None,
              boot_id: str = BOOT_ID) -> int:
    """One-shot form, for callers with nothing to interleave (the chain side)."""
    with ledger_op(conn, op, job_id=job_id, reason=reason, boot_id=boot_id) as o:
        for account, actor_id, delta in legs:
            o.leg(account, actor_id, delta)
    return len(o.legs)


# ---------------------------------------------------------------------------
# Opening balances
# ---------------------------------------------------------------------------

def ensure_opening_balances(conn: sqlite3.Connection) -> Optional[Dict[str, Any]]:
    """
    Give the balances that predate this ledger the one entry they are missing.

    On the live database this is not academic: escrow held 3,320 and 350 ECU
    when the table was created, and locked stake existed too. Without this the
    very first reconcile would refuse the boot over money that is genuinely
    there and simply older than the record of it.

    The counter-account is `opening`, not `issuance`, and the distinction is the
    point: `issuance` means this ledger watched ECU being created, `opening`
    means the ledger was switched on with money already in the room and is
    taking the previous total's word for it. Everything after this line is
    derived; this line is asserted. Anyone reading the ledger can see exactly
    how much was asserted and when, which is the most an opening balance can
    honestly offer.

    Runs at most once per database, and the marker that says so is a row in
    `ledger_meta`, not the emptiness of the durable accounts.

    That distinction was a real hole, found by `test_bridge_ledger.py [9]`
    before this was ever deployed. The first version treated "no escrow or
    locked entries yet" as "this must be the first boot after the ledger
    arrived". On a system that starts empty — a fresh install, a restored
    database — that condition stays true indefinitely, so the first balance to
    appear by any route that bypassed the ledger would be **absorbed as an
    opening balance and declared reconciled**. A check whose failure mode is to
    legitimise the thing it is checking for is worse than no check.

    Returns None when there was nothing to do.
    """
    marker = conn.execute(
        "SELECT value FROM ledger_meta WHERE key=?", (META_OPENED_AT,)
    ).fetchone()
    if marker:
        return None

    # Belt and braces: if the marker is gone but durable history exists, the
    # marker was removed rather than never written. Opening on top of existing
    # entries would double every balance, so refuse instead.
    row = conn.execute(
        "SELECT COUNT(*) FROM ledger_entries WHERE account IN (?,?,?)",
        (ESCROW, LOCKED, OPENING),
    ).fetchone()
    if row and row[0]:
        raise RuntimeError(
            f"{row[0]} durable ledger entr(ies) exist but the {META_OPENED_AT!r} "
            "marker in ledger_meta is missing. Opening balances now would count "
            "every balance twice. Restore the row, or the database, before "
            "starting."
        )

    escrow = conn.execute(
        "SELECT actor_id, balance FROM stake_escrow WHERE balance <> 0"
    ).fetchall()
    locked = _locked_by_actor(conn)

    legs: List[Tuple[str, str, float]] = []
    for actor_id, balance in escrow:
        legs.append((ESCROW, actor_id, float(balance)))
    for actor_id, amount in sorted(locked.items()):
        legs.append((LOCKED, actor_id, float(amount)))
    total = sum(d for _, _, d in legs)
    legs.append((OPENING, "", -total))

    # The marker goes in the same transaction as the legs. An opening that was
    # written without its marker would be re-applied on the next start.
    with ledger_op(conn, "opening_balance",
                   reason="balances predating the ledger, asserted not derived") as o:
        for account, actor_id, delta in legs:
            o.leg(account, actor_id, delta)
        conn.execute("INSERT INTO ledger_meta(key,value) VALUES(?,?),(?,?)",
                     (META_OPENED_AT, datetime.now(timezone.utc).isoformat(),
                      META_OPENED_TOTAL, repr(float(total))))

    return {
        "escrow_actors": len(escrow),
        "locked_actors": len(locked),
        "total_ecu": round(total, 8),
        # Zero legs are not written, so an empty system opens with a marker and
        # no entries. That is still worth recording: it dates the moment the
        # ledger became the record, which is what a later balance is measured
        # against.
        "entries": len([l for l in legs if l[2] != 0]),
    }


def _locked_by_actor(conn: sqlite3.Connection) -> Dict[str, float]:
    """
    ECU sitting in open locks, per actor.

    The consumer's side is `amount + job_value_ecu`: `lock()` debits the fee
    into escrow alongside the stake, so both are held, and a figure that
    counted only `amount` would report the fee as missing on every open job.
    """
    out: Dict[str, float] = {}
    for actor_id, amount in conn.execute(
        "SELECT consumer_id, COALESCE(SUM(amount + job_value_ecu),0) "
        "FROM consumer_stake WHERE status='locked' GROUP BY consumer_id"
    ):
        out[actor_id] = out.get(actor_id, 0.0) + float(amount)
    for actor_id, amount in conn.execute(
        "SELECT worker_id, COALESCE(SUM(amount),0) "
        "FROM worker_stake WHERE status='locked' GROUP BY worker_id"
    ):
        out[actor_id] = out.get(actor_id, 0.0) + float(amount)
    return {k: v for k, v in out.items() if v != 0}


# ---------------------------------------------------------------------------
# The audit that keeps the choke point a choke point
# ---------------------------------------------------------------------------

#: The only functions allowed to write to `stake_escrow`. `_debit` and
#: `_credit_in_tx` add ledger legs; `_init_schema` creates the table.
ESCROW_WRITERS = frozenset({"_debit", "_credit_in_tx", "_init_schema"})

_WRITE_VERBS = ("insert into", "update ", "delete from", "replace into")


def audit_escrow_write_sites(module: Any) -> List[str]:
    """
    Refuse to serve if anything else can change an escrow balance.

    Every entry in this ledger exists because `_debit` and `_credit_in_tx` are
    the only two functions that write `stake_escrow`. That is not a property of
    the design, it is a property of the current source, and the next person to
    need a balance adjusted will reach for a one-line `UPDATE` — which would
    move money silently past the ledger and leave a reconcile mismatch nobody
    can explain, at boot, with the service refusing to start.

    So it is checked, at boot, from the source itself rather than from memory,
    the same way the write-route audit is. The failure mode this prevents is
    not malice: it is a reasonable-looking two-line change.

    Returns a list of offending `function: statement` strings; empty is a pass.
    """
    import ast
    import inspect

    try:
        source = inspect.getsource(module)
    except OSError as e:                                    # pragma: no cover
        return [f"cannot read the source of {module!r}: {e}"]

    problems: List[str] = []
    tree = ast.parse(source)

    def walk(node: ast.AST, enclosing: str) -> None:
        for child in ast.iter_child_nodes(node):
            name = enclosing
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                name = child.name
            elif isinstance(child, ast.Constant) and isinstance(child.value, str):
                text = child.value.lower()
                if "stake_escrow" in text and any(v in text for v in _WRITE_VERBS):
                    if enclosing not in ESCROW_WRITERS:
                        flat = " ".join(child.value.split())[:80]
                        problems.append(f"{enclosing or '<module>'}: {flat}")
            walk(child, name)

    walk(tree, "")
    return problems


#: `ChainState` methods that change a balance.
CHAIN_MUTATORS = frozenset({
    "apply_genesis", "apply_transfer", "apply_block", "apply_faucet_claim",
    "rollback",
})

#: The only two functions in the bridge allowed to call one.
CHAIN_CALLERS = frozenset({"_init_genesis", "chain_transfer"})


def audit_chain_write_sites(module: Any) -> List[str]:
    """
    The same audit for the chain half, where the choke point is a wrapper
    rather than a private method — so it is easier to bypass, not harder.

    A direct `STATE.chain.apply_transfer(...)` moves ECU with no entry behind
    it, and because chain balances are in memory the mismatch disappears at the
    next restart. It would be a discrepancy that heals itself, which is the
    worst kind to leave findable only by hand.
    """
    import ast
    import inspect

    try:
        source = inspect.getsource(module)
    except OSError as e:                                    # pragma: no cover
        return [f"cannot read the source of {module!r}: {e}"]

    problems: List[str] = []
    tree = ast.parse(source)

    def walk(node: ast.AST, enclosing: str) -> None:
        for child in ast.iter_child_nodes(node):
            name = enclosing
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                name = child.name
            elif isinstance(child, ast.Call):
                fn = child.func
                if (isinstance(fn, ast.Attribute) and fn.attr in CHAIN_MUTATORS
                        and isinstance(fn.value, ast.Attribute)
                        and fn.value.attr == "chain"
                        and enclosing not in CHAIN_CALLERS):
                    problems.append(
                        f"{enclosing or '<module>'}: chain.{fn.attr}() at line {child.lineno}")
            walk(child, name)

    walk(tree, "")
    return problems


# ---------------------------------------------------------------------------
# Reconciling
# ---------------------------------------------------------------------------

@dataclass
class Discrepancy:
    scope: str          # entry | op | ledger | escrow | locked | chain
    key: str
    field: str
    stored: float
    recomputed: float
    note: str = ""

    @property
    def delta(self) -> float:
        """Stored minus recomputed. Positive means the stored side is high."""
        return self.stored - self.recomputed

    def describe(self) -> str:
        exact = self.scope in ("entry", "op")
        fmt = "{:.0f}" if self.field in ("sequence",) else "{:.9f}"
        line = "  {:<7} {:<44} {:<14} stored {}  recomputed {}  delta {}".format(
            self.scope, self.key[:44], self.field,
            fmt.format(self.stored), fmt.format(self.recomputed),
            ("+" if self.delta >= 0 else "") + fmt.format(self.delta),
        )
        if exact and self.note:
            return "  {:<7} {:<44} {}".format(self.scope, self.key[:44], self.note)
        return line + (f"  ({self.note})" if self.note else "")

    def as_dict(self) -> Dict[str, Any]:
        d = {"scope": self.scope, "key": self.key, "field": self.field,
             "stored": self.stored, "recomputed": self.recomputed,
             "delta": self.delta}
        if self.note:
            d["note"] = self.note
        return d


@dataclass
class Result:
    checked_at: str
    source: str
    db_path: str
    entries: int
    ops: int
    head_seq: int
    head_hash: str
    duration_ms: float
    discrepancy_count: int
    discrepancies: List[Discrepancy]
    totals: Dict[str, float]
    chain_checked: bool
    unreconciled: Dict[str, str] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.discrepancy_count == 0

    def as_record(self, extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        rec = {
            "checked_at": self.checked_at,
            "source": self.source,
            "db": self.db_path,
            "ok": self.ok,
            "entries": self.entries,
            "ops": self.ops,
            "head_seq": self.head_seq,
            "head_hash": self.head_hash,
            "duration_ms": round(self.duration_ms, 3),
            "discrepancy_count": self.discrepancy_count,
            "discrepancies": [d.as_dict() for d in self.discrepancies],
            "discrepancies_reported": len(self.discrepancies),
            "totals": self.totals,
            "chain_checked": self.chain_checked,
            "boot_id": BOOT_ID,
            "pid": os.getpid(),
        }
        if self.unreconciled:
            rec["unreconciled"] = self.unreconciled
        if extra:
            rec.update(extra)
        return rec


@contextmanager
def read_snapshot(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """
    One snapshot across every SELECT the reconcile makes.

    Without this the checks read the ledger and the balances at different
    instants, and a write landing between them produces a mismatch that never
    existed — which, at boot, means refusing to start over nothing. That exact
    bug was found in the `hexis.db` reconcile by testing it; see
    `test_reconcile_hexis_db.py [8]`.
    """
    conn.execute("BEGIN DEFERRED")
    try:
        yield conn
    finally:
        try:
            conn.execute("COMMIT")
        except sqlite3.OperationalError:
            pass


def reconcile(conn: sqlite3.Connection, *, source: str = "cli",
              db_path: str = "", chain_balances: Optional[Dict[str, float]] = None,
              boot_id: str = BOOT_ID, max_reported: int = 20) -> Result:
    """
    Recompute every balance the bridge stores from the entries that produced it.

    Pure read. Never repairs — see DEPLOY.md for why there is no `--repair` on
    the other reconcile either.
    """
    started = time.perf_counter()
    checked_at = datetime.now(timezone.utc).isoformat()
    found: List[Discrepancy] = []
    count = 0

    def report(d: Discrepancy) -> None:
        nonlocal count
        count += 1
        if len(found) < max_reported:
            found.append(d)

    with read_snapshot(conn):
        rows = conn.execute(
            "SELECT seq, entry_id, op_id, op, account, actor_id, delta, job_id, "
            "boot_id, ts_unix, prev_hash, entry_hash "
            "FROM ledger_entries ORDER BY seq"
        ).fetchall()

        # 1. The chain of entries itself. A ledger that has been edited is not
        #    a ledger, and every check below would be reading the edit.
        expected_seq = 0
        prev = GENESIS_HASH
        for r in rows:
            (seq, entry_id, op_id, op, account, actor_id, delta, job_id,
             _boot, ts_unix, prev_hash, entry_hash) = r
            expected_seq += 1
            if seq != expected_seq:
                report(Discrepancy("entry", f"seq {seq}", "sequence",
                                   float(seq), float(expected_seq),
                                   note=f"sequence jumps: expected {expected_seq}, found {seq}"))
                expected_seq = seq
            if prev_hash != prev:
                report(Discrepancy("entry", f"seq {seq}", "prev_hash", 0.0, 0.0,
                                   note="prev_hash does not match the entry before it"))
            recomputed = _entry_hash(prev_hash, seq, op_id, op, account,
                                     actor_id, delta, job_id, ts_unix)
            if recomputed != entry_hash:
                report(Discrepancy("entry", f"seq {seq}", "entry_hash", 0.0, 0.0,
                                   note="entry_hash does not match the row's own contents"))
            prev = entry_hash

        # 2. Every operation balances, and 3. the whole ledger does.
        by_op: Dict[str, List[Any]] = {}
        totals: Dict[str, float] = {}
        grand = 0.0
        for r in rows:
            seq, _eid, op_id, op, account, actor_id, delta = r[0], r[1], r[2], r[3], r[4], r[5], r[6]
            by_op.setdefault(op_id, [op, 0.0, 0, seq])
            by_op[op_id][1] += delta
            by_op[op_id][2] += 1
            totals[account] = totals.get(account, 0.0) + delta
            grand += delta
        for op_id, (op, total, n, first_seq) in by_op.items():
            if abs(total) > tolerance(total, n):
                report(Discrepancy("op", f"{op} @seq{first_seq}", "legs_sum",
                                   total, 0.0,
                                   note=f"{n} leg(s) do not sum to zero"))
        if abs(grand) > tolerance(grand, max(len(rows), 1)):
            report(Discrepancy("ledger", "all entries", "sum", grand, 0.0,
                               note="the ledger as a whole does not balance"))

        # 4. Escrow: the stored balance against the entries that produced it.
        ledger_escrow: Dict[str, float] = {}
        ledger_locked: Dict[str, float] = {}
        ledger_chain: Dict[str, float] = {}
        n_terms: Dict[str, int] = {}
        for r in rows:
            account, actor_id, delta, row_boot = r[4], r[5], r[6], r[8]
            if account == ESCROW:
                ledger_escrow[actor_id] = ledger_escrow.get(actor_id, 0.0) + delta
                n_terms[actor_id] = n_terms.get(actor_id, 0) + 1
            elif account == LOCKED:
                ledger_locked[actor_id] = ledger_locked.get(actor_id, 0.0) + delta
            elif account == CHAIN and row_boot == boot_id:
                ledger_chain[actor_id] = ledger_chain.get(actor_id, 0.0) + delta

        stored_escrow = {
            str(a): float(b) for a, b in
            conn.execute("SELECT actor_id, balance FROM stake_escrow")
        }
        for actor_id in sorted(set(stored_escrow) | set(ledger_escrow)):
            stored = stored_escrow.get(actor_id, 0.0)
            recomputed = ledger_escrow.get(actor_id, 0.0)
            if abs(stored - recomputed) > tolerance(stored, n_terms.get(actor_id, 1)):
                note = ""
                if actor_id not in stored_escrow:
                    note = "no stake_escrow row for an actor the ledger has entries for"
                elif actor_id not in ledger_escrow:
                    note = "escrow balance with no entries behind it"
                report(Discrepancy("escrow", actor_id, "balance", stored,
                                   recomputed, note=note))

        # 5. Locked stake, the other durable place ECU sits.
        stored_locked = _locked_by_actor(conn)
        for actor_id in sorted(set(stored_locked) | set(ledger_locked)):
            stored = stored_locked.get(actor_id, 0.0)
            recomputed = ledger_locked.get(actor_id, 0.0)
            if abs(stored - recomputed) > tolerance(stored, 8):
                report(Discrepancy("locked", actor_id, "locked_ecu", stored,
                                   recomputed))

        # 6. The chain, when a live one was handed in. Only this process's
        #    entries: ChainState is rebuilt at every start, so entries from a
        #    previous boot describe balances that no longer exist anywhere.
        chain_checked = chain_balances is not None
        if chain_balances is not None:
            for addr in sorted(set(chain_balances) | set(ledger_chain)):
                stored = float(chain_balances.get(addr, 0))
                recomputed = ledger_chain.get(addr, 0.0)
                if abs(stored - recomputed) > tolerance(stored, 8):
                    report(Discrepancy("chain", addr, "balance_ecu", stored,
                                       recomputed,
                                       note="in-memory, this process only"))

        head_seq, head_hash = head(conn)

    return Result(
        checked_at=checked_at,
        source=source,
        db_path=db_path,
        entries=len(rows),
        ops=len(by_op),
        head_seq=head_seq,
        head_hash=head_hash,
        duration_ms=(time.perf_counter() - started) * 1000.0,
        discrepancy_count=count,
        discrepancies=found,
        totals={k: round(v, 8) for k, v in sorted(totals.items())},
        chain_checked=chain_checked,
        unreconciled={
            "slash_log.amount": "reported, not reconciled: it records how much a "
                                "slash took, which the burn leg already carries",
        },
    )


def format_report(result: Result) -> str:
    head_line = (
        "bridge ledger reconcile [{}]: {} — {} entr(ies), {} op(s), head {}, {:.1f}ms".format(
            result.source,
            "OK" if result.ok else f"MISMATCH ({result.discrepancy_count})",
            result.entries, result.ops, result.head_seq, result.duration_ms,
        )
    )
    if result.ok:
        return head_line
    lines = [head_line] + [d.describe() for d in result.discrepancies]
    lines.append("  (showing {} of {})".format(
        len(result.discrepancies), result.discrepancy_count))
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI: read the books without the service and without the port
# ---------------------------------------------------------------------------

def _main(argv: List[str]) -> int:
    if len(argv) < 2:
        print("usage: python3 hexis_ledger_entries.py <bridge.db>")
        return 2
    db = argv[1]
    conn = sqlite3.connect(db, timeout=10)
    try:
        conn.execute("PRAGMA query_only=ON")
        result = reconcile(conn, source="cli", db_path=os.path.abspath(db))
    finally:
        conn.close()
    print(format_report(result))
    print(json.dumps(result.totals, indent=2, sort_keys=True))
    return 0 if result.ok else 1


if __name__ == "__main__":
    import sys
    raise SystemExit(_main(sys.argv))
