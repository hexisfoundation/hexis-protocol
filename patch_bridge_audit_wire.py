#!/usr/bin/env python3
"""
patch_bridge_audit_wire.py
Wire STATE.log() to audit.log_action() via monkey-patch in FastAPI startup event.

Idempotent: detects if wrapper already installed, aborts if so.
Anchor: requires prior P1.5 audit wiring (run patch_bridge_audit.py first).

Single insertion point at FastAPI startup. Wraps STATE.log() so every
existing log call site automatically also writes to audit chain.
No handler code is modified.

Usage:
    python3 patch_bridge_audit_wire.py             # dry-run
    python3 patch_bridge_audit_wire.py --apply     # write changes + backup
"""

import shutil
import ast
import sys
from datetime import datetime

BRIDGE = "/opt/hexis_newflow/hexis_bridge_v0.6.2.py"
BACKUP = BRIDGE + ".before_audit_wire2"
APPLY = "--apply" in sys.argv

WIRE_BLOCK = '''
# === Wire audit to STATE.log() (added __TODAY__) ===
def _audit_derive_action(msg, level, data, state_ref):
    """Map STATE.log() args to audit.log_action() kwargs. Returns dict or None."""
    data = data or {}
    if level in ("warn", "error"):
        return None
    if msg.startswith("Genesis block initialized"):
        return None
    if "demo mode" in msg.lower():
        return None

    if msg.startswith("Worker registered:") and "address" in data:
        addr = data["address"]
        info = state_ref.workers.get(addr, {})
        return {
            "actor_id": addr,
            "action_type": "worker_register",
            "payload": {
                "country": info.get("country"),
                "hardware_tier": info.get("hardware_tier"),
                "faucet_ok": data.get("faucet_ok"),
            },
        }

    if msg.startswith("Job created:") and "job_id" in data:
        job = state_ref.jobs.get(data["job_id"])
        if not job:
            return None
        trust = data.get("trust")
        return {
            "actor_id": job["consumer_address"],
            "counterparty_id": job["worker_address"],
            "action_type": "job_request",
            "payload": {
                "job_id": data["job_id"],
                "task_type": job.get("task_type"),
                "fee_ecu": job.get("fee_ecu"),
                "compute_units": job.get("compute_units"),
                "sensitivity_tier": job.get("sensitivity_tier"),
                "trust_grade": trust.get("grade") if isinstance(trust, dict) else None,
            },
        }

    if msg.startswith("Job complete:") and "job_id" in data:
        job = state_ref.jobs.get(data["job_id"])
        if not job:
            return None
        hexis = data.get("hexis")
        if not isinstance(hexis, dict):
            hexis = {}
        return {
            "actor_id": job["worker_address"],
            "counterparty_id": job["consumer_address"],
            "action_type": "job_complete",
            "payload": {
                "job_id": data["job_id"],
                "fee_ecu": job.get("fee_ecu"),
                "completed_at": job.get("completed_at"),
                "hexis_mined": hexis.get("hexis_raw"),
            },
        }

    if msg.startswith("HEXIS mined for") and isinstance(data, dict):
        actor = data.get("actor_id") or data.get("worker_id")
        if not actor:
            return None
        return {
            "actor_id": actor,
            "action_type": "hexis_mint",
            "payload": data,
        }

    return None


@app.on_event("startup")
async def _wire_audit_to_state_log():
    if getattr(STATE, "_audit_wired", False):
        return
    _original_log = STATE.log

    def _wrapped_log(msg, level="info", data=None):
        entry = _original_log(msg, level=level, data=data)
        try:
            spec = _audit_derive_action(msg, level, data, STATE)
            if spec:
                audit.log_action(**spec)
        except Exception as _e:
            print(f"[audit-wire] exception: {type(_e).__name__}: {_e}")
        return entry

    STATE.log = _wrapped_log
    STATE._audit_wired = True
    print("[audit-wire] STATE.log wrapped with audit.log_action passthrough")
'''


def main():
    with open(BRIDGE, "r", encoding="utf-8") as f:
        src = f.read()

    if "_audit_derive_action" in src or "_wire_audit_to_state_log" in src:
        print("ABORT: audit wire already installed.")
        sys.exit(1)

    if "from hexis_audit import" not in src:
        print("ABORT: hexis_audit not imported. Run patch_bridge_audit.py first.")
        sys.exit(1)

    if "audit = AuditLogger" not in src:
        print("ABORT: 'audit = AuditLogger(...)' not found in bridge file.")
        sys.exit(1)

    lines = src.split("\n")

    anchor = None
    for i, line in enumerate(lines):
        if "app.include_router(get_audit_router(audit))" in line:
            anchor = i
            break
    if anchor is None:
        print("ABORT: anchor 'app.include_router(get_audit_router(audit))' not found.")
        sys.exit(1)

    print(f"  anchor    -> line {anchor + 1}: {lines[anchor]}")

    today = datetime.now().strftime("%Y-%m-%d")
    wire_lines = WIRE_BLOCK.replace("__TODAY__", today).split("\n")
    while wire_lines and wire_lines[-1] == "":
        wire_lines.pop()
    while wire_lines and wire_lines[0] == "":
        wire_lines.pop(0)

    new_lines = list(lines)
    new_lines.insert(anchor + 1, "")
    for j, ln in enumerate(wire_lines):
        new_lines.insert(anchor + 2 + j, ln)
    new_lines.insert(anchor + 2 + len(wire_lines), "")
    new_src = "\n".join(new_lines)

    try:
        ast.parse(new_src)
    except SyntaxError as e:
        print(f"ABORT: patched source has syntax error: {e}")
        sys.exit(1)

    print(f"  inserting {len(wire_lines) + 2} new lines after line {anchor + 1}")
    print(f"  syntax check -> OK ({len(new_lines)} lines, was {len(lines)})")

    if not APPLY:
        print("")
        print("DRY-RUN ONLY. Re-run with --apply to write changes.")
        return

    shutil.copy(BRIDGE, BACKUP)
    with open(BRIDGE, "w", encoding="utf-8") as f:
        f.write(new_src)
    print(f"  backup    -> {BACKUP}")
    print("PATCH APPLIED.")


if __name__ == "__main__":
    main()
