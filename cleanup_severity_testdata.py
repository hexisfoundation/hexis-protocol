#!/usr/bin/env python3
"""
cleanup_severity_testdata.py — don rac test severity lo ra sau khi wire
victim payout. CHAY TREN VPS. Bridge PHAI dung truoc (backup nhat quan +
tranh race WAL). KHONG dung toi genesis/mint/settle/wipe — chi sua bang data.
"""
import sqlite3, sys

DB = "/opt/hexis_newflow/bridge.db"
INC_TEST = ("439e17dc99cd474a9236ca37d82602b1",   # sev_test_worker, "tier3 e2e"
            "27ab20c5450e447584dd6ac63288d8e2")   # 3d2H..., "posp_fraud e2e"
WORKER_REAL = "3d2H3mJoyH2DutLSnAGgD3HoG1h9xdzmQzDM83LzGNZcDGrEmTX"

c = sqlite3.connect(DB)
c.row_factory = sqlite3.Row

def snap(tag):
    print(f"\n----- {tag} -----")
    print("incidents:", c.execute("SELECT COUNT(*) n FROM incidents").fetchone()["n"])
    for r in c.execute("SELECT actor_id,total_debt,quarantine_until,blacklisted "
                       "FROM actor_flags WHERE total_debt>0 OR quarantine_until>0"):
        print(" flag:", dict(r))
    rr = c.execute("SELECT balance FROM stake_escrow WHERE actor_id='SEVERITY_RESERVE'").fetchone()
    print(" SEVERITY_RESERVE escrow:", rr["balance"] if rr else None)
    wr = c.execute("SELECT balance FROM stake_escrow WHERE actor_id=?", (WORKER_REAL,)).fetchone()
    print(" 3d2H worker escrow:", wr["balance"] if wr else None)

snap("BEFORE")

# 1) xoa 2 incident test
c.executemany("DELETE FROM incidents WHERE id=?", [(i,) for i in INC_TEST])
# 2) go debt-block cho worker THAT (giu escrow nguyen)
c.execute("UPDATE actor_flags SET total_debt=0, quarantine_until=0 WHERE actor_id=?",
          (WORKER_REAL,))
# 3) xoa flag actor test thuan
c.execute("DELETE FROM actor_flags WHERE actor_id='sev_test_worker'")
# 4) zero pool test ao
c.execute("UPDATE stake_escrow SET balance=0 WHERE actor_id='SEVERITY_RESERVE'")
c.commit()

snap("AFTER")

# sanity
assert c.execute("SELECT COUNT(*) n FROM incidents").fetchone()["n"] == 0, "incidents should be empty"
d = c.execute("SELECT COALESCE(total_debt,0) d FROM actor_flags WHERE actor_id=?",
              (WORKER_REAL,)).fetchone()
assert (d["d"] if d else 0) == 0, "worker debt not cleared"
print("\nOK: test data cleaned, real worker unblocked, no genesis touched.")
c.close()
