from __future__ import annotations

import tempfile
import unittest
from datetime import date
from pathlib import Path

import pandas as pd

from generator.generate_data import Generator
from generator.lifecycle import premium_status_as_of


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


    def test_snapshot_premium_status_censors_future_payments(self) -> None:
        snapshot = date(2026, 3, 31)
        self.assertEqual(
            premium_status_as_of(date(2026, 3, 18), date(2026, 4, 16), snapshot),
            "outstanding",
        )
        self.assertEqual(
            premium_status_as_of(date(2026, 2, 1), date(2026, 4, 16), snapshot),
            "missed",
        )
        self.assertEqual(
            premium_status_as_of(date(2026, 3, 1), date(2026, 3, 10), snapshot),
            "late",
        )
        self.assertEqual(
            premium_status_as_of(date(2026, 3, 1), date(2026, 3, 1), snapshot),
            "paid",
        )
        self.assertIsNone(
            premium_status_as_of(date(2026, 4, 1), None, snapshot)
        )

    def test_destructive_defects_do_not_remove_signals_or_repair_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            generator = Generator(Path(tmp))
            generator.run()

            raw = Path(tmp) / "data" / "raw"
            defects = pd.read_csv(raw / "_defect_log.csv")
            claims = pd.read_csv(raw / "Claim.csv", dtype=str)

            protected_claims = {
                cid for cid, traits in generator.signal.items() if traits
            }

            destructive_claim_defects = defects[
                (defects["source"] == "Claim.csv")
                & defects["defect_type"].isin(
                    {
                        "ORPHAN_FK",
                        "INV_CLAIM_BEFORE_POLICY",
                        "INV_DATE_ORDER",
                        "INV_NEGATIVE_AMOUNT",
                        "OUT_ERROR",
                        "MISS_ERROR",
                        "XF_OWNER_CONFLICT",
                    }
                )
            ]
            self.assertFalse(
                protected_claims
                & set(destructive_claim_defects["record_key"].astype(str))
            )

            protected_rows = claims[
                claims["claim_id"].astype(str).isin(protected_claims)
            ].drop_duplicates("claim_id")
            protected_policy_ids = set(protected_rows["policy_id"].astype(str))
            protected_customer_ids = set(protected_rows["customer_id"].astype(str))

            policy_destructive = defects[
                (defects["source"] == "Policy.csv")
                & defects["defect_type"].isin(
                    {"ORPHAN_FK", "INV_DATE_ORDER", "MISS_ERROR"}
                )
            ]
            self.assertFalse(
                protected_policy_ids
                & set(policy_destructive["record_key"].astype(str))
            )

            customer_destructive = defects[
                (defects["source"] == "Customer.csv")
                & defects["defect_type"].isin({"INV_AGE", "MISS_ERROR"})
            ]
            self.assertFalse(
                protected_customer_ids
                & set(customer_destructive["record_key"].astype(str))
            )

            repair_dependent = set(
                defects[
                    (defects["source"] == "Claim.csv")
                    & defects["defect_type"].isin(
                        {"INV_NEGATIVE_AMOUNT", "OUT_ERROR", "MISS_ERROR"}
                    )
                    & defects["field"].isin({"claim_amount", "service_date"})
                ]["record_key"].astype(str)
            )
            destructive_json = set(
                defects[
                    defects["defect_type"].isin(
                        {"JSON_MISSING_FILE", "JSON_MALFORMED"}
                    )
                ]["record_key"].astype(str)
            )
            self.assertFalse(repair_dependent & destructive_json)

            outlier_claims = {
                cid
                for cid, traits in generator.signal.items()
                if "OUTLIER" in traits
            }
            self.assertEqual(len(outlier_claims), 3)
            self.assertTrue(outlier_claims <= set(claims["claim_id"].astype(str)))

    def test_reference_run_is_reproducible(self) -> None:
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            ma = Generator(Path(a)).run()["manifest"]
            mb = Generator(Path(b)).run()["manifest"]
            self.assertEqual(ma["sha256"], mb["sha256"])
            self.assertEqual(ma["raw_counts"], mb["raw_counts"])
            self.assertEqual(ma["raw_qa"], mb["raw_qa"])


if __name__ == "__main__":
    unittest.main()
