"""证据不足、幸运赢家、非法数字及试验留档的回归检查；测试数值不进产品。"""
import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("overfit_test_module", Path(__file__).parents[1]/"app/services/quant/agent_overfit.py")
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


class EvidenceTests(unittest.TestCase):
    def rows(self):
        return [dict(pnl_abs=10, entry_date=f"2026-{i//5+1:02d}-01", exit_date=f"2026-{i//5+1:02d}-15") for i in range(30)]

    def test_empty_is_not_zero_or_pass(self):
        r = m.assess([])
        self.assertIsNone(r["score"])
        self.assertEqual(r["label"], "证据不足")

    def test_good_history_does_not_prove_unseen_or_parameter_tests(self):
        r = m.assess(self.rows(), days=180, hypothesis="趋势持续", frozen_through="2025-12-31")
        self.assertEqual(r["score"], 35)
        for key in ("unseen", "parameters", "costs", "trials", "integrity"):
            check = next(c for c in r["checks"] if c["key"] == key)
            self.assertEqual(check["points"], 0)
        self.assertEqual(r["label"], "证据不足")

    def test_lucky_winner_cannot_hide_concentration(self):
        rows = self.rows()
        for row in rows:
            row["pnl_abs"] = -10
        rows[0]["pnl_abs"] = 10000
        r = m.assess(rows, days=180)
        self.assertEqual(next(c for c in r["checks"] if c["key"] == "concentration")["status"], "warn")

    def test_bad_numbers_not_serialized(self):
        r = m.assess([{"pnl_abs": float("nan")}, {"pnl_abs": float("inf")}, {"pnl_abs": True}])
        self.assertIsNone(r["score"])
        self.assertEqual(r["diagnostics"]["invalid_cycles"], 3)
        json.dumps(r, allow_nan=False)

    def test_other_branch_cannot_borrow_freeze_date(self):
        r = m.public_report(self.rows(), list(range(180)), {"best":"parent", "frozen_through":"2025-12-31"}, "child")
        check = next(c for c in r["checks"] if c["key"] == "unseen")
        self.assertIn("没有本方向", check["detail"])

    def test_fingerprint_and_zero_score(self):
        self.assertEqual(m.fingerprint({"a":1,"b":2}), m.fingerprint({"b":2,"a":1}))
        self.assertNotEqual(m.fingerprint({"a":1}), m.fingerprint({"a":2}))
        self.assertEqual(m.assess([dict(pnl_abs=-1)])["score"], 0)

    def test_failed_and_old_trials_survive_recent_list(self):
        memory = {}
        ar = SimpleNamespace(_meta_get=lambda cur,key: memory.get(key),
                             _meta_set=lambda cur,key,value: memory.__setitem__(key,json.loads(json.dumps(value))))
        with patch.dict(sys.modules, {"app.services.quant":SimpleNamespace(agent_run=ar)}):
            for i in range(12):
                run = dict(id=str(i), params={"hold":1}, status="queued")
                m.record_submit(None, "u:a", {"runs":[]}, run, {"hypothesis":"test"})
                run["status"] = "failed"
                m.record_result(None, "u:a", run)
            self.assertEqual(memory["overfit-count:u:a"]["count"], 12)
            self.assertEqual(memory["overfit-run:u:a:0"]["status"], "failed")
            other = dict(id="0", params={}, status="queued")
            m.record_submit(None, "u:b", {}, other, {})
            self.assertEqual(other["trial_number"], 1)

    def test_legacy_total_lower_bound_and_budget(self):
        run = dict(id="1",params={},trial_number=20,trial_budget=20,result={"trades":[],"nav":[]})
        self.assertIn("预算",m.manual_report(run)["next_step"])
        self.assertIsNone(m.manual_report(run)["score"])


if __name__ == "__main__":
    unittest.main()
