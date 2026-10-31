import copy
import json
import os
import unittest
from datetime import date

from validate import validate

HERE = os.path.dirname(os.path.abspath(__file__))
TODAY = date(2026, 10, 31)


def load():
    with open(os.path.join(HERE, "examples", "2327_pass.json"), encoding="utf-8") as fh:
        return json.load(fh)


class ValidateTest(unittest.TestCase):
    def test_sample_passes(self):
        errors, _ = validate(load(), TODAY)
        self.assertEqual(errors, [])

    def test_followup_year_mismatch(self):
        d = load()
        d["followups"][0]["year"] = 2028  # 財報狗的 bug:追問年份晚於主表
        errors, _ = validate(d, TODAY)
        self.assertTrue(any("followup_year_mismatch" in e for e in errors))

    def test_needs_three_followups(self):
        d = load()
        d["followups"].pop()
        errors, _ = validate(d, TODAY)
        self.assertTrue(any("H4" in e for e in errors))

    def test_fixed_questions_must_be_exact(self):
        d = load()
        d["fixed_questions"][0] = "公司做什麼"
        errors, _ = validate(d, TODAY)
        self.assertTrue(any("H5" in e for e in errors))

    def test_single_point_rejected(self):
        d = load()
        d["eps_by_year"][1].update(range_low=None, range_high=None)
        errors, _ = validate(d, TODAY)
        self.assertTrue(any("C3" in e and "2027" in e for e in errors))

    def test_table_years_must_match(self):
        d = load()
        d["table_years"] = [2027, 2028]
        errors, _ = validate(d, TODAY)
        self.assertTrue(any("C2" in e for e in errors))

    def test_missing_disclaimer(self):
        d = load()
        d["disclaimer"] = ""
        errors, _ = validate(d, TODAY)
        self.assertTrue(any("免責" in e for e in errors))

    def test_loss_requires_business_eps(self):
        d = copy.deepcopy(load())
        d["eps_by_year"][0].update(median=-0.3, range_low=-0.67, range_high=-0.13)
        errors, _ = validate(d, TODAY)
        self.assertTrue(any("C9" in e for e in errors))
        d["eps_by_year"][0]["eps_business"] = -0.34
        errors, _ = validate(d, TODAY)
        self.assertFalse(any("C9" in e for e in errors))

    def test_actual_row_not_counted_as_forecast(self):
        d = load()
        d["table_years"] = [2025, 2026, 2027]
        d["eps_by_year"].insert(0, {"absolute_year": 2025, "relative_year": "Y0", "is_actual": True, "median": 47.13})
        errors, _ = validate(d, TODAY)
        self.assertEqual(errors, [])


if __name__ == "__main__":
    unittest.main()
