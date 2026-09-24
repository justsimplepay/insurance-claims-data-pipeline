from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from generator.generate_data import Generator


class GeneratorTests(unittest.TestCase):
    def test_clean_contract_and_raw_counts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = Generator(Path(tmp)).run()
            self.assertEqual(result["manifest"]["clean_counts"]["customers"], 150)
            self.assertEqual(result["manifest"]["clean_counts"]["policies"], 180)
            self.assertEqual(result["manifest"]["clean_counts"]["claims"], 210)
            self.assertEqual(result["manifest"]["json_files"], 200)
            self.assertEqual(result["manifest"]["raw_counts"]["Customer.csv"], 157)
            self.assertEqual(result["manifest"]["raw_counts"]["Policy.csv"], 183)
            self.assertEqual(result["manifest"]["raw_counts"]["Claim.csv"], 220)
            self.assertEqual(result["manifest"]["raw_counts"]["Claim_Payment.csv"], 203)
            self.assertFalse(result["qa"]["errors"])
            retention = result["qa"]["retention"]
            self.assertGreaterEqual(retention["eligible_customers"], 100)
            self.assertLessEqual(retention["eligible_customers"], 120)
            self.assertGreaterEqual(retention["churned_customers"], 12)
            self.assertLessEqual(retention["churned_customers"], 18)
            self.assertEqual(retention["replacement_cases"], 4)
            self.assertEqual(result["qa"]["phase_claim_counts"]["A"], 175)
            self.assertEqual(result["qa"]["phase_claim_counts"]["B"], 35)
            self.assertFalse(result["manifest"]["raw_qa"]["errors"])
            self.assertGreaterEqual(result["manifest"]["raw_qa"]["affected_record_file_share"], 0.05)
            self.assertLessEqual(result["manifest"]["raw_qa"]["affected_record_file_share"], 0.15)
            counts = result["manifest"]["raw_qa"]["defect_counts"]
            self.assertEqual(counts["JSON_MALFORMED"], 2)
            self.assertEqual(counts["JSON_ORPHAN"], 4)
            self.assertEqual(counts["JSON_MISSING_FILE"], 14)
            self.assertEqual(counts["DUP_EXACT"], 26)  # 6 claims + 5 payments + 15 premiums
            self.assertEqual(counts["ORPHAN_FK"], 13)  # 2 policies + 3 claims + 3 payments + 5 premiums

    def test_reference_run_is_reproducible(self) -> None:
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            ma = Generator(Path(a)).run()["manifest"]
            mb = Generator(Path(b)).run()["manifest"]
            self.assertEqual(ma["sha256"], mb["sha256"])
            self.assertEqual(ma["raw_counts"], mb["raw_counts"])
            self.assertEqual(ma["raw_qa"], mb["raw_qa"])


if __name__ == "__main__":
    unittest.main()
