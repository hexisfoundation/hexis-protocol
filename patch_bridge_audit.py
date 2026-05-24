#!/usr/bin/env python3
"""
patch_bridge_audit.py
Wire hexis_audit.py into /opt/hexis_newflow/hexis_bridge_v0.6.2.py via
anchor-based surgical insertion. Safe by design:
  - dry-run by default (no write unless --apply)
  - creates backup .before_audit_wire before any write
  - validates patched source with ast.parse, aborts on syntax error
  - refuses to run twice (detects existing 'from hexis_audit' import)

Usage:
    python3 patch_bridge_audit.py             # dry-run, prints anchors only
    python3 patch_bridge_audit.py --apply     # write changes + backup
"""

import re
import shutil
import ast
import sys
from datetime import datetime

BRIDGE = "/opt/hexis_newflow/hexis_bridge_v0.6.2.py"
BACKUP = BRIDGE + ".before_audit_wire"
APPLY = "--apply" in sys.argv


def main():
    with open(BRIDGE, "r", encoding="utf-8") as f:
        src = f.read()

    if "from hexis_audit" in src or "import hexis_audit" in src:
        print("ABORT: hexis_audit already wired in bridge file.")
        sys.exit(1)

    lines = src.split("\n")

    # Anchor 1: first top-level 'app = FastAPI(' line
    app_anchor = None
    for i, line in enumerate(lines):
        if re.match(r"^app\s*=\s*FastAPI\s*\(", line):
            app_anchor = i
            break
    if app_anchor is None:
        print("ABORT: no top-level 'app = FastAPI(' line found.")
        sys.exit(1)

    # Anchor 2: LAST top-level import BEFORE app_anchor (so new import sits
    # above the AuditLogger() call)
    import_anchor = None
    for i in range(app_anchor):
        line = lines[i]
        s = line.strip()
        if ((s.startswith("import ") or s.startswith("from "))
                and not line.startswith((" ", "\t"))):
            import_anchor = i
    if import_anchor is None:
        print("ABORT: no top-level import line found before app = FastAPI.")
        sys.exit(1)

    # Walk parens to find end of FastAPI(...) block (may span multiple lines)
    app_end = app_anchor
    depth = lines[app_anchor].count("(") - lines[app_anchor].count(")")
    while depth > 0 and app_end < len(lines) - 1:
        app_end += 1
        depth += lines[app_end].count("(") - lines[app_end].count(")")

    print(f"  last import       -> line {import_anchor + 1}: {lines[import_anchor]}")
    print(f"  FastAPI app start -> line {app_anchor + 1}: {lines[app_anchor]}")
    print(f"  FastAPI app end   -> line {app_end + 1}: {lines[app_end]}")

    today = datetime.now().strftime("%Y-%m-%d")
    audit_init = [
        "",
        f"# === P1.5 Audit & Compliance Layer (wired {today}) ===",
        'audit = AuditLogger("/opt/hexis_newflow/bridge.db")',
        "app.include_router(get_audit_router(audit))",
        "",
    ]
    import_ins = "from hexis_audit import AuditLogger, get_audit_router"

    new_lines = list(lines)
    # Insert audit init AFTER FastAPI(...) block ends, using original index
    for j, ln in enumerate(audit_init):
        new_lines.insert(app_end + 1 + j, ln)
    # Insert import AFTER last import line. import_anchor < app_anchor, so
    # this region is unaffected by the previous insertions.
    new_lines.insert(import_anchor + 1, import_ins)

    new_src = "\n".join(new_lines)

    try:
        ast.parse(new_src)
    except SyntaxError as e:
        print(f"ABORT: patched source has syntax error: {e}")
        sys.exit(1)

    print(f"  syntax check      -> OK ({len(new_lines)} lines, was {len(lines)})")

    if not APPLY:
        print("")
        print("DRY-RUN ONLY. Re-run with --apply to write changes.")
        return

    shutil.copy(BRIDGE, BACKUP)
    with open(BRIDGE, "w", encoding="utf-8") as f:
        f.write(new_src)
    print(f"  backup            -> {BACKUP}")
    print("PATCH APPLIED.")


if __name__ == "__main__":
    main()
