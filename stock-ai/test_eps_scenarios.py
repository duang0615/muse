"""eps_scenarios 離線測試:全部使用自編數字,不連 FinMind。"""
import json
import os
import unittest
from datetime import date

from eps_scenarios import build
from validate import validate

HERE = os.path.dirname(os.path.abspath(__file__))
TODAY = date(2026, 11, 2)
SHARES = 1_000_000_000  # 10 億股


def quarter(rev, margin, op_margin=None, nonop_ratio=0.1):
    ni = rev * margin
    pre = ni / 0.8 if ni else 1.0
    op = rev * (op_margin if op_margin is not None else margin / 0.8 * (1 - nonop_ratio))
    return {
        "Revenue": rev,
        "EquityAttributableToOwnersOfParent": ni,
        "EPS": ni / SHARES,
        "PreTaxIncome": pre,
        "TotalNonoperatingIncomeAndExpense": pre * nonop_ratio,
        "OperatingIncome": op,
    }


def make_data(month_rev=10e9, margin=0.10, y1_reported=(1, 2), months_through=9):
    """2025 全年 + 2026 指定季度財報;月營收 2025-01 到 2026-{months_through}。"""
    quarters = {(2025, q): quarter(month_rev * 3, margin) for q in range(1, 5)}
    for q in y1_reported:
        quarters[(2026, q)] = quarter(month_rev * 3 * 1.2, margin)
    months = {(2025, m): month_rev for m in range(1, 13)}
    months.update({(2026, m): month_rev * 1.2 for m in range(1, months_through + 1)})
    return quarters, months


def full_answer(result):
    """把情境結果包成完整回答骨架,檢查能否通過 validate。"""
    with open(os.path.join(HERE, "examples", "sample_pass.json"), encoding="utf-8") as fh:
        d = json.load(fh)
    d["table_years"] = result["table_years"]
    d["eps_by_year"] = result["eps_by_year"]
    for f in d["followups"]:
        f["year"] = None
    return d


class EpsScenarioTest(unittest.TestCase):
    def test_flat_business_hand_calculated(self):
        q, m = make_data()
        r = build("9999", "測試", q, m, TODAY)
        y1, y2 = r["eps_by_year"]
        # 每季營收 360 億 × 10% ÷ 10 億股 = 3.6 元;Q1Q2 實際 + Q3 實際月營收 + Q4 近 3 月平均
        self.assertAlmostEqual(y1["representative"], 3.6 * 4, places=2)
        self.assertEqual(r["table_years"], [2026, 2027])
        # 淨利率不變 → 三情境 Y1 只差在 Q4 營收係數
        self.assertAlmostEqual(y1["range_low"], 3.6 * 3 + 3.6 * 0.95, places=2)
        self.assertAlmostEqual(y1["range_high"], 3.6 * 3 + 3.6 * 1.05, places=2)
        # Y1 營收年增 20% → Y2 基準成長 15%
        self.assertAlmostEqual(y2["representative"], 14.4 * 1.15, places=2)
        self.assertEqual(r["flags"], [])

    def test_output_order_and_validate_compatible(self):
        q, m = make_data()
        r = build("9999", "測試", q, m, TODAY)
        for e in r["eps_by_year"]:
            self.assertLessEqual(e["range_low"], e["representative"])
            self.assertLessEqual(e["representative"], e["range_high"])
        errors, _ = validate(full_answer(r), TODAY)
        self.assertEqual(errors, [])

    def test_stock_dividend_after_statements_restates_eps(self):
        q, m = make_data()
        base = build("9999", "測試", q, m, TODAY)
        div = [{"StockExDividendTradingDate": "2026-09-02", "StockEarningsDistribution": 10.0,
                "StockStatutorySurplus": 0.0}]
        r = build("9999", "測試", q, m, TODAY, div)
        self.assertIn("share_base_changed", r["flags"])
        self.assertAlmostEqual(r["assumptions"]["share_multiplier"], 2.0)
        for a, b in zip(base["eps_by_year"], r["eps_by_year"]):
            self.assertAlmostEqual(b["representative"], a["representative"] / 2, places=2)
            self.assertIsNotNone(b["share_base_note"])

    def test_stock_dividend_before_statements_ignored(self):
        q, m = make_data()
        div = [{"StockExDividendTradingDate": "2026-03-15", "StockEarningsDistribution": 10.0}]
        r = build("9999", "測試", q, m, TODAY, div)
        self.assertNotIn("share_base_changed", r["flags"])

    def test_loss_gives_business_eps_and_passes_validate(self):
        q, m = make_data(margin=-0.02)
        r = build("9999", "測試", q, m, TODAY)
        self.assertIn("loss_quarters", r["flags"])
        self.assertTrue(all(e["eps_business"] is not None for e in r["eps_by_year"]))
        errors, _ = validate(full_answer(r), TODAY)
        self.assertEqual(errors, [])

    def test_large_nonoperating_flagged(self):
        q, m = make_data()
        q[(2026, 2)] = quarter(36e9, 0.10, nonop_ratio=0.6)
        r = build("9999", "測試", q, m, TODAY)
        self.assertIn("nonoperating_large", r["flags"])
        self.assertIsNotNone(r["eps_by_year"][0]["eps_business"])

    def test_incomplete_prior_year_revenue_rejected(self):
        q, m = make_data()
        del m[(2025, 6)]
        with self.assertRaises(ValueError):
            build("9999", "測試", q, m, TODAY)


if __name__ == "__main__":
    unittest.main()
