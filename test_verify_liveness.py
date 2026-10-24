"""Stdlib only: python3 -m unittest test_verify_liveness"""
import unittest
import verify_audit_chain as v

DAY = 86400
def ev(t, kind): return {"action_type": kind, "timestamp_unix": t}


class Liveness(unittest.TestCase):
    def test_ages_ignore_machine_rows(self):
        now = 100 * DAY
        evs = [ev(10 * DAY, "daily_seal"), ev(40 * DAY, "hexis_mint"),
               ev(99 * DAY, "ledger_reconcile"), ev(99 * DAY, "stake_expiry_sweep")]
        self.assertEqual(v.liveness_ages(evs, now), (90.0, 60.0))

    def test_only_machine_rows_means_nobody(self):
        self.assertEqual(v.liveness_ages([ev(1, "ledger_reconcile")], 2 * DAY), (None, None))

    def test_stale_seal_fails_fresh_passes_none_fails(self):
        now = 100 * DAY
        r = v.Report(); v.check_liveness([ev(now - 3 * DAY, "daily_seal")], r, now)
        self.assertEqual(len(r.failures), 1)
        r = v.Report(); v.check_liveness([ev(now - 1 * DAY, "daily_seal")], r, now)
        self.assertEqual(r.failures, [])
        r = v.Report(); v.check_liveness([ev(now, "hexis_mint")], r, now)
        self.assertEqual(len(r.failures), 1)


if __name__ == "__main__":
    unittest.main()
