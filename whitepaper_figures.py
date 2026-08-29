"""
whitepaper_figures.py — refuse to boot when the paper and the code disagree
                        about a NUMBER
===========================================================================

WHAT THIS CHECKS, AND WHAT IT DOES NOT
--------------------------------------
This checks **numbers only**. Nothing else. It compares a figure printed in
the published whitepaper against the live constant in the code that the paper
says produces it, and refuses to start the service when the two differ.

**A passing boot does not mean the whitepaper is verified.** It means no
checked number in it contradicts the code today. It says nothing about whether
any described mechanism exists, works, or does what the sentence around the
number claims. Those are two different failures and only one of them is
mechanically detectable:

    "MAX_SUPPLY is 950,000"          <- a number. Checked here.
    "the wallet cap is enforced"     <- a mechanism. NOT checked here, and
                                        the paper itself marks it
                                        [NOT ENFORCED] precisely because the
                                        constant exists and the enforcement
                                        does not. The constant matches. The
                                        claim was still false.

That example is in the table below, deliberately, as the standing reminder
that this validator can pass while a sentence lies.

WHY IT EXISTS
-------------
The 2026-08-23 whitepaper audit (WHITEPAPER_AUDIT.md) graded 104 testable
claims and found 19 false as run. Those 19 split into two kinds:

  * **Assertion — 14 rows.** Never true. A control designed, named, given a
    constant or a docstring, and then not built. Those died with the audit;
    no validator can resurrect a mechanism that was never written, and no
    validator can detect one that is merely absent.

  * **Drift — 7 rows, 5 of them false as run.** True when written, then
    overtaken by a change that landed in the code and not in the paper.
    §4's supply table, Appendix B's four figures, Appendix D's C range,
    §17's version numbers.

Drift is the kind a machine can close, and this closes it. The audit's own
conclusion named the gap exactly:

    "the paper has no equivalent of the nightly host-claims block — nothing
     re-runs its numbers against the system."

This is that equivalent, and it runs at boot rather than nightly, because a
number that has gone wrong should stop a deploy rather than file a report.

HOW THE PAPER SIDE IS PINNED
----------------------------
Comparing code against a number transcribed by hand into this file would only
move the drift: someone edits the paper, this table keeps the old value, and
the check passes while the two disagree. So both sides are pinned.

A copy of the published document sits at `whitepaper/HEXIS_Whitepaper_v0.7.md`
and this module holds its sha256. That hash is not self-asserted — it is the
hash recorded in `document_seal` on the chain, at the sequence named by
`WHITEPAPER_SEAL_SEQUENCE` below and printed by this module's own CLI. The
number is not repeated in this prose: a sequence written twice is a sequence
that can rot in one copy, which is the exact failure this module exists to
refuse. Anyone can confirm all three agree:

    curl -s https://hexisfoundation.org/HEXIS_Whitepaper_v0.7.md | sha256sum
    curl -s https://bridge.hexisfoundation.org/audit/HEXIS_Whitepaper_v0.7.md
    sha256sum whitepaper/HEXIS_Whitepaper_v0.7.md

Every figure below also carries the exact sentence it was read out of, and the
check confirms that sentence is still present in the copy. So the paper cannot
be edited underneath this table without the boot failing: change the document
and the hash breaks; change a figure's wording and its quote stops matching.

WHAT HAPPENS ON A MISMATCH
--------------------------
The service refuses to start, the same as every other boot validator here. The
fix is never to loosen the check. It is to decide which side is wrong — the
paper drifted or the code changed — and correct that side, publicly, dated, in
the manner CORRECTIONS.md already sets out.

A check that cannot run also refuses: a missing copy, a hash that does not
match, a quote that has vanished. A validator that shrugs when its own inputs
are missing is worse than no validator, because the passing line still gets
printed.
"""

import hashlib
import os
import re
from typing import Callable, List, NamedTuple, Optional, Union

import hexis_mining
import scs_engine

HERE = os.path.dirname(os.path.abspath(__file__))
WHITEPAPER_NAME = "HEXIS_Whitepaper_v0.7.md"
WHITEPAPER_PATH = os.path.join(HERE, "whitepaper", WHITEPAPER_NAME)

# The hash recorded in document_seal at the sequence in
# WHITEPAPER_SEAL_SEQUENCE, which is also what hexisfoundation.org serves.
# The sequence lives in exactly one place — the constant below — because the
# first version of this comment hardcoded 394, the constant said 559, and the
# two disagreed in a file whose whole purpose is to catch two numbers
# disagreeing. Updating the published paper means updating both constants,
# re-reading every quote below, and sealing the new document — in that order,
# and never by deleting the check.
WHITEPAPER_SHA256 = (
    "5a4911b75f69e5e6ea9a3a2fe42333d75fa4a66341a050d3138b06f1e115708e")
WHITEPAPER_SEAL_SEQUENCE = 559

# Inline source tags on claims about the world, added 2026-08-28. The audit
# listed 16 such claims; two are asserted in two places each, so 18 tags.
EXTERNAL_CLAIM_TAGS = 18

# Floats printed to a few decimals must not fail on binary representation.
# Nothing here is a computed quantity; these are all declared constants, so an
# exact-to-9-places comparison is strict without being brittle.
TOLERANCE = 1e-9


class Figure(NamedTuple):
    """One number that appears in both the paper and the code."""
    where: str            # section of the paper, for the failure message
    quote: str            # exact substring that must still be in the paper
    paper_value: float    # the figure as the paper prints it
    code_ref: str         # file and symbol a reader can open
    source: Union[Callable[[], float], str]
    # A callable reads an importable constant. A string is a key into the
    # `live` dict the caller supplies, for values that belong to the running
    # service rather than to a module — the bridge's own version string, the
    # sampling rate actually in force — because this module must not import
    # the bridge that imports it.
    note: str = ""
    off_value: Optional[float] = None
    off_means: str = ""
    # Some figures describe a feature that a node can legitimately run with
    # switched off, and `off_value` is the setting that means off. It exists
    # for exactly one reason, discovered the first time this validator was
    # booted against an empty database:
    #
    #   §18 says PoSP is "deployed and live at σ=0.1". On the foundation node
    #   that is true. On a NEW node, sampling starts at σ=0 — a deliberate
    #   safety gate — so a strict check refuses the boot, and the second
    #   independent node, the thing this whole protocol is missing, could
    #   never be stood up. A validator that makes the network impossible to
    #   join is not strict, it is broken.
    #
    # This is not a shrug. Off is reported at WARN and named in the boot log,
    # and ANY third value still refuses: σ=0.05 is drift and fails, σ=0 is a
    # node that has not turned sampling on and says so out loud.


FIGURES: tuple = (

    # -- §4, ECU supply. The drift that started this: the paper's table says
    # -- 39,000,000 and the mint engine enforces 950,000. Both numbers are now
    # -- printed in the paper, the second inside the 2026-08-23 correction, so
    # -- both are checkable and both are checked.
    Figure(
        where="§4 supply table, TOTAL row",
        quote="**39,000,000**|**100%**",
        paper_value=39_000_000,
        code_ref="hexis_bridge_v0.6.2.py GENESIS_TOTAL_ECU",
        source="genesis_total_ecu",
        note="the genesis allocation the bridge boots with",
    ),
    Figure(
        where="§4 correction, the cap the code enforces",
        quote="950,000 ECU (`scs_engine.py`, `MAX_SUPPLY`)",
        paper_value=950_000,
        code_ref="scs_engine.py MAX_SUPPLY",
        source=lambda: scs_engine.MAX_SUPPLY,
        note="41x below the table above; the paper says so in the same section",
    ),
    Figure(
        where="§4 correction, halving interval",
        quote="halves every 237,500 ECU",
        paper_value=237_500,
        code_ref="scs_engine.py HALVING_INTERVAL",
        source=lambda: scs_engine.HALVING_INTERVAL,
    ),

    # -- §5.3 and §6, HEXIS supply. Every row of the tokenomics block has a
    # -- named constant behind it, so every row is checked.
    Figure(
        where="§5.3 total supply",
        quote="HEXIS:  12,800,000",
        paper_value=12_800_000,
        code_ref="hexis_mining.py TOTAL_SUPPLY",
        source=lambda: hexis_mining.TOTAL_SUPPLY,
    ),
    Figure(
        where="§6 pre-mint total",
        quote="Pre-mint (9.5%):            1,216,000  HEXIS",
        paper_value=1_216_000,
        code_ref="hexis_mining.py PRE_MINT_TOTAL",
        source=lambda: hexis_mining.PRE_MINT_TOTAL,
    ),
    Figure(
        where="§6 founder allocation",
        quote="Founder      (1.5%):    192,000  HEXIS",
        paper_value=192_000,
        code_ref="hexis_mining.py PRE_MINT_FOUNDER",
        source=lambda: hexis_mining.PRE_MINT_FOUNDER,
    ),
    Figure(
        where="§6 early allocation",
        quote="Early (3yr+) (2.0%):    256,000  HEXIS",
        paper_value=256_000,
        code_ref="hexis_mining.py PRE_MINT_EARLY",
        source=lambda: hexis_mining.PRE_MINT_EARLY,
    ),
    Figure(
        where="§6 HEXIS genesis burn",
        quote="Genesis Burn (6.0%):    768,000  HEXIS",
        paper_value=768_000,
        code_ref="hexis_mining.py GENESIS_BURN",
        source=lambda: hexis_mining.GENESIS_BURN,
    ),
    Figure(
        where="§6 public mine",
        quote="Public mine       (90.5%): 11,584,000  HEXIS",
        paper_value=11_584_000,
        code_ref="hexis_mining.py PUBLIC_MINE",
        source=lambda: hexis_mining.PUBLIC_MINE,
    ),
    Figure(
        where="§6 wallet hard cap",
        quote="Wallet hard cap:                10,000  HEXIS",
        paper_value=10_000,
        code_ref="hexis_mining.py WALLET_HARD_CAP",
        source=lambda: hexis_mining.WALLET_HARD_CAP,
        note="THE CONSTANT MATCHES AND THE CLAIM AROUND IT WAS STILL FALSE — "
             "the cap is not enforced anywhere, which is why the paper now "
             "prints [NOT ENFORCED] beside it. Kept in this table as the "
             "worked example of what a passing boot does not mean",
    ),

    # -- §5.5, sensitivity tiers. The paper's multipliers and the severity
    # -- module's damage multipliers are the same four numbers and have to
    # -- stay that way; §5.5's whole argument is that they are. Read live and
    # -- not from DEFAULT_CONFIG, for the reason given at the sigma row below:
    # -- severity is calibrated by UPDATE on its config table, so the default
    # -- is what the code shipped with, not what the network runs.
    Figure(
        where="§5.5 tier 1 multiplier",
        quote="|1   |Public      |1.0",
        paper_value=1.0,
        code_ref="severity_config table, key 'damage_mult_t1' (live)",
        source="severity_mult_t1",
    ),
    Figure(
        where="§5.5 tier 2 multiplier",
        quote="|2   |Internal    |5.0",
        paper_value=5.0,
        code_ref="severity_config table, key 'damage_mult_t2' (live)",
        source="severity_mult_t2",
    ),
    Figure(
        where="§5.5 tier 3 multiplier",
        quote="|3   |Confidential|20.0",
        paper_value=20.0,
        code_ref="severity_config table, key 'damage_mult_t3' (live)",
        source="severity_mult_t3",
    ),
    Figure(
        where="§5.5 tier 4 multiplier",
        quote="|4   |Regulated   |100.0",
        paper_value=100.0,
        code_ref="severity_config table, key 'damage_mult_t4' (live)",
        source="severity_mult_t4",
    ),

    # -- §7 and Appendix D, the context multiplier's clamp. This is drift row
    # -- 28: the paper carried [0.5, 2.0] for weeks after the clamp narrowed.
    Figure(
        where="§7 factor table, C range lower bound",
        quote="Since 2026-08-17 the range is [0.8, 1.25]",
        paper_value=0.8,
        code_ref="hexis_mining.py C_MIN",
        source=lambda: hexis_mining.C_MIN,
    ),
    Figure(
        where="§7 factor table, C range upper bound",
        quote="Since 2026-08-17 the range is [0.8, 1.25]",
        paper_value=1.25,
        code_ref="hexis_mining.py C_MAX",
        source=lambda: hexis_mining.C_MAX,
    ),

    # -- §17, version numbers. Drift row 73: the paper named v0.6.2 and v0.6.1
    # -- while both services reported 0.8.0 at /health. Strings, not numbers,
    # -- but the same failure and the same fix.
    Figure(
        where="§17 bridge version",
        quote="HEXIS × NEWFLOW Bridge (v0.8.0):",
        paper_value="0.8.0",
        code_ref="hexis_bridge_v0.6.2.py SERVER_VERSION",
        source="bridge_version",
    ),
    Figure(
        where="§17 trust API version",
        quote="HEXIS Trust API (v0.8.0):",
        paper_value="0.8.0",
        code_ref="hexis_api_v0.6.1.py SERVER_VERSION",
        source=lambda: _api_server_version(),
    ),

    # -- §18 roadmap, the sampling rate. The paper asserts a LIVE value, so
    # -- the live value is what gets read — the module default is 0.0, a
    # -- deliberate safety gate, and checking that instead would pass while
    # -- the paper's claim was false.
    Figure(
        where="§18 v0.8 row, PoSP sampling rate",
        quote="deployed and live at σ=0.1 since 2026-08-17",
        paper_value=0.1,
        code_ref="sampling_config table, key 'sigma' (live, not the default)",
        source="sampling_sigma",
        note="read from the running configuration; the module default is 0.0",
        off_value=0.0,
        off_means="sampling is not enabled on this node — §18's sentence "
                  "describes the foundation node, not this one",
    ),
)


def _api_server_version() -> str:
    """
    Read SERVER_VERSION out of the Trust API's source.

    The API is a separate service in the same directory and is not importable
    from here — importing it would start building its app. Reading the
    assignment is enough, and a file that is not there is a failure rather
    than a skip: the two are deployed together, so an absent one means the
    deploy is wrong, not that the check is inapplicable.
    """
    path = os.path.join(HERE, "hexis_api_v0.6.1.py")
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            m = re.match(r'^SERVER_VERSION\s*=\s*"([^"]+)"', line)
            if m:
                return m.group(1)
    raise RuntimeError(
        f"no SERVER_VERSION assignment found in {path} — the check cannot run")


def _read_whitepaper() -> str:
    """The sealed copy, or an exception naming exactly what is wrong."""
    if not os.path.exists(WHITEPAPER_PATH):
        raise RuntimeError(
            f"{WHITEPAPER_PATH} is missing. The figure check compares the code "
            f"against the published paper and cannot run without it. Fetch the "
            f"sealed copy:\n"
            f"  curl -sO https://hexisfoundation.org/{WHITEPAPER_NAME}\n"
            f"and put it in whitepaper/. Its sha256 must be "
            f"{WHITEPAPER_SHA256}")
    with open(WHITEPAPER_PATH, "rb") as fh:
        raw = fh.read()
    got = hashlib.sha256(raw).hexdigest()
    if got != WHITEPAPER_SHA256:
        raise RuntimeError(
            f"whitepaper copy hash mismatch.\n"
            f"  expected {WHITEPAPER_SHA256} (document_seal, chain sequence "
            f"{WHITEPAPER_SEAL_SEQUENCE})\n"
            f"  found    {got}\n"
            f"Either the copy is not the published document, or the document "
            f"was republished without updating this module. If the paper "
            f"changed: re-read every quote in FIGURES against the new text, "
            f"update WHITEPAPER_SHA256 and WHITEPAPER_SEAL_SEQUENCE, and seal "
            f"the new document. Do not edit the hash on its own — that turns "
            f"the check off while leaving it looking on.")
    return raw.decode("utf-8")


def audit_whitepaper_figures(live: Optional[dict] = None) -> tuple:
    """
    Compare every figure in FIGURES against the code.

    `live` supplies the values that belong to the running service rather than
    to an importable module. A figure whose source names a key that is absent
    is reported as a failure, not skipped.

    Returns `(problems, notices)`:
      problems — mismatches. Any one of these must refuse the boot.
      notices  — figures sitting at their `off_value`: a feature this node has
                 not switched on, so the paper's sentence about it describes
                 some other node. These do not refuse; they must be logged at
                 WARN, because an unannounced off switch is how a claim stays
                 published after it stopped being true here.

    Raises RuntimeError when the check itself cannot run.
    """
    live = live or {}
    text = _read_whitepaper()
    problems: List[str] = _audit_world_tags(text)
    notices: List[str] = []

    for fig in FIGURES:
        # The paper still has to say what this table claims it says.
        if fig.quote not in text:
            problems.append(
                f"{fig.where}: the quoted text is no longer in the paper — "
                f"looked for {fig.quote!r}. The figure may have been reworded "
                f"or removed; re-read the section and update FIGURES")
            continue

        if callable(fig.source):
            code_value = fig.source()
        else:
            if fig.source not in live:
                problems.append(
                    f"{fig.where}: the caller supplied no {fig.source!r} value, "
                    f"so this figure could not be checked against "
                    f"{fig.code_ref}")
                continue
            code_value = live[fig.source]

        if isinstance(fig.paper_value, str):
            agrees = str(code_value) == fig.paper_value
        else:
            agrees = abs(float(code_value) - float(fig.paper_value)) < TOLERANCE

        if agrees:
            continue

        if (fig.off_value is not None
                and not isinstance(code_value, str)
                and abs(float(code_value) - float(fig.off_value)) < TOLERANCE):
            notices.append(
                f"{fig.where}: {fig.code_ref} is {code_value} — "
                f"{fig.off_means}. The paper prints {fig.paper_value}")
            continue

        problems.append(
            f"{fig.where}: the paper prints {fig.paper_value} and "
            f"{fig.code_ref} is {code_value}")

    return problems, notices


def _audit_world_tags(text: str) -> List[str]:
    """
    Every external factual claim must carry a source and a read date.

    The 2026-08-23 audit listed 16 claims about the world that this repo
    cannot test and that carried no citation. On 2026-08-28 each was looked
    up and tagged inline. Two of the sixteen are asserted in two places, so
    there are 18 tags.

    Two things are checked and both are structural, not editorial:

      * the count. Delete a tag and the boot refuses. This is the half that
        matters, because a tag is easiest to remove exactly when it has become
        inconvenient — five of the eighteen record that the source does not
        say what the paper says.
      * every tag carries a read date. A source without a date is not a
        citation, it is a gesture: the reader cannot tell whether it was
        checked this month or eighteen months ago.

    Adding a NEW claim about the world means adding a tag and raising the
    count here, in that order. The count going up is a deliberate act; a tag
    quietly going missing is not.
    """
    problems: List[str] = []
    tags = re.findall(r"\[world · [^\]]*\]", text)

    if len(tags) != EXTERNAL_CLAIM_TAGS:
        problems.append(
            f"external source tags: expected {EXTERNAL_CLAIM_TAGS}, found "
            f"{len(tags)}. Either a tag was removed from the paper — check "
            f"which claim lost its citation — or a claim about the world was "
            f"added and EXTERNAL_CLAIM_TAGS was not raised with it")

    undated = [t for t in tags if "read 20" not in t]
    if undated:
        problems.append(
            f"{len(undated)} source tag(s) carry no read date, e.g. "
            f"{undated[0][:90]!r} — a source without a date does not say "
            f"whether it was checked this month or last year")

    return problems


def figure_summary() -> str:
    """One line for the boot log."""
    return (f"{len(FIGURES)} whitepaper figures checked against code and "
            f"{EXTERNAL_CLAIM_TAGS} external-claim source tags present "
            f"(numbers only — this says nothing about mechanisms)")


if __name__ == "__main__":
    import sys

    # The CLI cannot know the running service's values, so it names them
    # instead of guessing. Anything guessed here would be a number this file
    # invented, which is the failure the whole module exists to prevent.
    demo = {
        "genesis_total_ecu": 39_000_000,
        "bridge_version": "0.8.0",
        "sampling_sigma": 0.1,
        "severity_mult_t1": 1.0,
        "severity_mult_t2": 5.0,
        "severity_mult_t3": 20.0,
        "severity_mult_t4": 100.0,
    }
    print(__doc__)
    print(f"paper: {WHITEPAPER_PATH}")
    print(f"sha256 pinned to document_seal sequence "
          f"{WHITEPAPER_SEAL_SEQUENCE}: {WHITEPAPER_SHA256}")
    print()
    print("Values marked (supplied) are read from the running service at boot "
          "and are shown here with the values expected at the time of writing:")
    for k, v in demo.items():
        print(f"  {k} = {v!r}  (supplied)")
    print()
    bad, notices = audit_whitepaper_figures(demo)
    for fig in FIGURES:
        src = "(supplied)" if isinstance(fig.source, str) else fig.code_ref
        print(f"  {fig.paper_value!r:>14}  {fig.where}  <- {src}")
    print()
    for n in notices:
        print(f"  NOT ENABLED HERE: {n}")
    if bad:
        print(f"MISMATCHES ({len(bad)}):")
        for b in bad:
            print(f"  - {b}")
        sys.exit(1)
    print(figure_summary())
    print("no mismatches")
