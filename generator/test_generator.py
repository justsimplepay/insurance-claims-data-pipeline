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

    def test_reference_run_is_reproducible(self) -> None:
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            ma = Generator(Path(a)).run()["manifest"]
            mb = Generator(Path(b)).run()["manifest"]
            self.assertEqual(ma["sha256"], mb["sha256"])
            self.assertEqual(ma["raw_counts"], mb["raw_counts"])
            self.assertEqual(ma["raw_qa"], mb["raw_qa"])


if __name__ == "__main__":
    unittest.main()
