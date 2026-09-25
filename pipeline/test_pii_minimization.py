from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class PiiMinimizationTests(unittest.TestCase):
    def read(self, relative: str) -> str:
        return (ROOT / relative).read_text(encoding="utf-8")

    def test_core_customer_schema_keeps_only_necessary_personal_attributes(self) -> None:
        sql = self.read("sql/04_create_core_tables.sql")
        create_block = sql.split("-- Compatibility migration", 1)[0]
        for forbidden in (
            "first_name",
            "last_name",
            "address",
            "city",
            "postal_code",
            "phone",
            "email",
        ):
            self.assertNotIn(forbidden, create_block)
        self.assertIn("date_of_birth", create_block)
        self.assertIn("province", create_block)
        self.assertIn("fsa", create_block)

    def test_core_load_does_not_copy_unnecessary_direct_identifiers(self) -> None:
        sql = self.read("sql/20_core_load.sql")
        customer_section = sql.split("-- 2. Policies.", 1)[0]
        for forbidden in (
            "first_name",
            "last_name",
            "address",
            "city",
            "postal_code",
            "phone",
            "email",
        ):
            self.assertNotIn(forbidden, customer_section)
        self.assertIn("c.fsa", customer_section)

    def test_staging_scrubs_direct_identifiers_after_identity_resolution(self) -> None:
        sql = self.read("sql/10_staging_transformations.sql")
        self.assertIn("UPDATE staging.customers", sql)
        for field in (
            "first_name",
            "last_name",
            "address",
            "city",
            "postal_code",
            "phone",
            "email",
        ):
            self.assertIn(f"{field} = NULL", sql)
        self.assertIn("'pii_value_redacted', true", sql)

    def test_marts_do_not_expose_city_or_full_postal_code(self) -> None:
        for relative in (
            "sql/30_fraud_mart.sql",
            "sql/31_retention_mart.sql",
            "sql/32_operations_mart.sql",
            "sql/33_region_mart.sql",
            "sql/34_policy_mart.sql",
        ):
            sql = self.read(relative)
            self.assertNotIn("postal_code", sql, relative)
            self.assertNotIn("cu.city", sql, relative)
            self.assertNotIn("f.city", sql, relative)
        self.assertIn("cu.fsa", self.read("sql/30_fraud_mart.sql"))
        self.assertIn("cu.fsa", self.read("sql/31_retention_mart.sql"))

    def test_export_redacts_customer_pii_values(self) -> None:
        code = self.read("pipeline/export_outputs.py")
        self.assertIn("pii_value_redacted", code)
        for field in (
            "first_name",
            "last_name",
            "date_of_birth",
            "address",
            "city",
            "postal_code",
            "phone",
            "email",
        ):
            self.assertIn(field, code)

    def test_end_to_end_validator_checks_privacy_in_each_layer(self) -> None:
        code = self.read("pipeline/run_pipeline.py")
        self.assertIn("staging customer direct identifiers are scrubbed", code)
        self.assertIn("core customers exclude unnecessary direct-identifier columns", code)
        self.assertIn("marts exclude direct and fine-grained quasi identifiers", code)
        self.assertIn("staging DQ log redacts customer PII values", code)


if __name__ == "__main__":
    unittest.main()
