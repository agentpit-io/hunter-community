"""规则编辑回归：边界校验、身份隔离、参数生效及扣费后回测。"""
import unittest
from datetime import date, timedelta
from types import SimpleNamespace
from unittest.mock import patch
from app.services.quant import agent_manual as m, agent_limitup as lu


class ManualTests(unittest.TestCase):
    def values(self):
        return dict(amount=10000, hold_days=1, growth_only=False, main_only=True,
                    require_ma5=False, amp_max=None, no_all_shrink=False)

    def test_validation(self):
        self.assertEqual(m.validate(self.values())["hold_days"], 1)
        for changes in (dict(amount=float("nan")), dict(amount=True), dict(hold_days=1.5),
                        dict(amp_max=0), dict(growth_only=True), dict(require_ma5=1)):
            with self.assertRaises(ValueError):
                m.validate(dict(self.values(), **changes))
        with self.assertRaises(ValueError):
            m.validate(dict(self.values(), extra=1))

    def test_user_and_branch_isolation(self):
        self.assertNotEqual(m.key_for("a", "limitup"), m.key_for("b", "limitup"))
        with self.assertRaises(ValueError):
            m.key_for("a", "base")

    def test_ma_and_board_switches(self):
        bars = [(date(2026, 1, 1) + timedelta(days=i), c, c, c, 100)
                for i, c in enumerate([20, 10, 11, 11.1, 11.2, 11.3])]
        ind = lu.indicators(bars)
        p = dict(lu.PARAMS, **self.values())
        self.assertTrue(lu.entry_checks(ind, "600000", "", p)["ok"])
        self.assertFalse(lu.entry_checks(ind, "600000", "", dict(p, require_ma5=True))["ok"])
        self.assertFalse(lu.entry_checks(ind, "600000", "", dict(p, main_only=False, growth_only=True))["ok"])
        self.assertIn("不要求", next(x["condition"] for x in lu.rules_for(p) if x["id"] == "L-07"))

    def test_simulation_fees_and_hold_days(self):
        days = [date(2026, 1, 1) + timedelta(days=i) for i in range(9)]
        prices = [20, 10, 11, 11.1, 11.2, 11.3, 11.4, 11.5, 11.6]
        arr = [(c, c, c, 100, c) for c in prices]
        ctx = SimpleNamespace(store={"bench":dict.fromkeys(days, 1), "last":days[-1],
                              "codes":{"600000":(days, arr)}}, snap={})
        p = dict(lu.PARAMS, **self.values())
        progress = []
        r = m.calculate(ctx, p, days[5], days[-1], progress.append)
        self.assertEqual(r["closed"], 1)
        self.assertEqual(r["open"], 0)
        sell = next(x for x in r["trades"] if x["side"] == "sell")
        self.assertEqual(sell["date"], str(days[6]))
        self.assertLess(sell["net_pnl"], sell["pnl_abs"])
        self.assertAlmostEqual(r["net_pnl"], sell["net_pnl"], places=2)
        r2 = m.calculate(ctx, dict(p, hold_days=2), days[5], days[-1], lambda _: None)
        self.assertEqual(next(x for x in r2["trades"] if x["side"] == "sell")["date"], str(days[7]))
        self.assertEqual(progress[-1], 100)
        with self.assertRaises(ValueError):
            m.calculate(ctx, p, days[0]-timedelta(days=1), days[-1], lambda _:None)


if __name__ == "__main__":
    unittest.main()
