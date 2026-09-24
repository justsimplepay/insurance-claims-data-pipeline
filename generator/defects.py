from __future__ import annotations

import copy
import json
import math
from collections import Counter
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

import numpy as np
import pandas as pd

from . import config as C


def _serial(v: Any) -> Any:
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, (date, datetime)):
        return v.isoformat()
    if isinstance(v, dict):
        return {k: _serial(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_serial(x) for x in v]
    if isinstance(v, np.generic):
        return v.item()
    return v


class DefectInjector:
    """Project clean canonical facts into reproducible messy raw-source extracts."""

    def __init__(self, seed: int, signal: dict[str, set[str]]):
        self.rng = np.random.default_rng(np.random.SeedSequence([seed, C.STAGE_IDS["defects"]]))
        self.signal = signal
        self.log: list[dict[str, str]] = []
        self.touched: set[tuple[str, Any, str]] = set()

    def _log(self, defect: str, source: str, key: str, field: str, old: Any, new: Any) -> None:
        self.log.append(
            {
                "defect_type": defect,
                "source": source,
                "record_key": str(key),
                "field": field,
                "original_value": "" if old is None else str(old),
                "injected_value": "" if new is None else str(new),
            }
        )

    def _set(self, df: pd.DataFrame, source: str, idx: Any, key_col: str, field: str, value: Any, defect: str) -> bool:
        token = (source, idx, field)
        if token in self.touched:
            return False
        old = df.at[idx, field]
        df.at[idx, field] = value
        self.touched.add(token)
        self._log(defect, source, str(df.at[idx, key_col]), field, old, value)
        return True

    def _dirty_pool(self, df: pd.DataFrame, fraction: float, base_indices: list[Any]) -> list[Any]:
        n = max(1, int(round(len(base_indices) * fraction)))
        return list(self.rng.choice(base_indices, size=n, replace=False))

    def _pool_sample(self, pool: list[Any], share: float) -> list[Any]:
        n = max(1, int(round(len(pool) * share)))
        return list(self.rng.choice(pool, size=min(n, len(pool)), replace=False))

    @staticmethod
    def _date_alt(v: date, convention: str) -> str:
        if convention == "DD/MM/YYYY":
            return v.strftime("%d/%m/%Y")
        if convention == "MM/DD/YYYY":
            return v.strftime("%m/%d/%Y")
        if convention == "YYYYMMDD":
            return v.strftime("%Y%m%d")
        return v.strftime("%b %d, %Y")

    @staticmethod
    def _province_alt(v: str) -> str:
        return {
            "SK": "saskatchewan", "AB": "Alta.", "MB": "man.", "ON": "Ontario",
            "BC": "british columbia", "NS": "Nova Scotia", "PE": "p.e.i.",
            "NL": "Newfoundland", "YT": "Yukon", "NT": "NWT",
        }.get(v, f" {v.lower()} ")

    @staticmethod
    def _category_alt(v: Any) -> Any:
        if v is None or (isinstance(v, float) and math.isnan(v)):
            return v
        s = str(v)
        return s.replace("_", " ").title() + (" " if "_" not in s else "")

    @staticmethod
    def _amount_text(v: Any) -> str:
        d = Decimal(str(v))
        return "$" + f"{d:,.2f}"

    def _set_if_free(self, df, source, idx, key_col, field, value, defect):
        if (source, idx, field) in self.touched:
            return False
        return self._set(df, source, idx, key_col, field, value, defect)

    def _format_customer(self, c: pd.DataFrame, base: list[Any]) -> None:
        pool = self._dirty_pool(c, 0.10, base)
        for idx in self._pool_sample(pool, 0.40):
            for field in ["date_of_birth", "customer_since"]:
                v = c.at[idx, field]
                if isinstance(v, date) and self._set_if_free(c, "Customer.csv", idx, "customer_id", field, self._date_alt(v, "DD/MM/YYYY"), "FMT_DATE"):
                    break
        for idx in self._pool_sample(pool, 0.48):
            v = c.at[idx, "province"]
            if pd.notna(v):
                self._set_if_free(c, "Customer.csv", idx, "customer_id", "province", self._province_alt(str(v)), "FMT_PROVINCE")
        for idx in self._pool_sample(pool, 0.60):
            v = c.at[idx, "gender"]
            if pd.notna(v):
                self._set_if_free(c, "Customer.csv", idx, "customer_id", "gender", {"F": "Female", "M": "MALE", "X": "Non-binary"}.get(v, str(v).lower()), "FMT_GENDER")
        for idx in self._pool_sample(pool, 0.60):
            v = c.at[idx, "phone"]
            if pd.notna(v):
                digits = "".join(ch for ch in str(v) if ch.isdigit())[-10:]
                self._set_if_free(c, "Customer.csv", idx, "customer_id", "phone", f"({digits[:3]}) {digits[3:6]}-{digits[6:]}", "FMT_PHONE")
        for idx in self._pool_sample(pool, 0.60):
            v = c.at[idx, "postal_code"]
            if pd.notna(v):
                self._set_if_free(c, "Customer.csv", idx, "customer_id", "postal_code", str(v).replace(" ", "").lower(), "FMT_POSTAL")
        for idx in self._pool_sample(pool, 0.32):
            for field in ["first_name", "last_name", "city", "email"]:
                v = c.at[idx, field]
                if pd.notna(v) and self._set_if_free(c, "Customer.csv", idx, "customer_id", field, str(v).upper() + " ", "FMT_TEXT_WHITESPACE"):
                    break

    def _format_policy(self, p: pd.DataFrame, base: list[Any]) -> None:
        pool = self._dirty_pool(p, 0.10, base)
        for idx in self._pool_sample(pool, 0.12):
            v = p.at[idx, "customer_id"]
            if pd.notna(v):
                self._set_if_free(p, "Policy.csv", idx, "policy_id", "customer_id", str(v).lower() + " ", "FMT_ID")
        for idx in self._pool_sample(pool, 0.40):
            for field in ["start_date", "end_date"]:
                v = p.at[idx, field]
                if isinstance(v, date) and self._set_if_free(p, "Policy.csv", idx, "policy_id", field, self._date_alt(v, "MM/DD/YYYY"), "FMT_DATE"):
                    break
        for idx in self._pool_sample(pool, 0.40):
            for field in ["policy_type", "status", "coverage_type", "sales_channel"]:
                if self._set_if_free(p, "Policy.csv", idx, "policy_id", field, self._category_alt(p.at[idx, field]), "FMT_CATEGORY_CASE"):
                    break
        for idx in self._pool_sample(pool, 0.40):
            for field in ["coverage_amount", "deductible_amount"]:
                if self._set_if_free(p, "Policy.csv", idx, "policy_id", field, self._amount_text(p.at[idx, field]), "TYPE_AMOUNT_TEXT"):
                    break

    def _format_claim(self, cl: pd.DataFrame, base: list[Any]) -> None:
        pool = self._dirty_pool(cl, 0.08, base)
        for idx in self._pool_sample(pool, 0.12):
            for field in ["policy_id", "customer_id"]:
                v = cl.at[idx, field]
                if pd.notna(v) and self._set_if_free(cl, "Claim.csv", idx, "claim_id", field, str(v).lower() + " ", "FMT_ID"):
                    break
        for idx in self._pool_sample(pool, 0.40):
            for field in ["service_date", "claim_date"]:
                v = cl.at[idx, field]
                if isinstance(v, date) and self._set_if_free(cl, "Claim.csv", idx, "claim_id", field, self._date_alt(v, "DD/MM/YYYY"), "FMT_DATE"):
                    break
        for idx in self._pool_sample(pool, 0.40):
            v = cl.at[idx, "region"]
            if pd.notna(v):
                self._set_if_free(cl, "Claim.csv", idx, "claim_id", "region", self._province_alt(str(v)), "FMT_PROVINCE")
        for idx in self._pool_sample(pool, 0.40):
            for field in ["claim_status", "claim_type"]:
                if self._set_if_free(cl, "Claim.csv", idx, "claim_id", field, self._category_alt(cl.at[idx, field]), "FMT_CATEGORY_CASE"):
                    break
        for idx in self._pool_sample(pool, 0.32):
            for field in ["claim_amount", "approved_amount"]:
                v = cl.at[idx, field]
                if pd.notna(v) and self._set_if_free(cl, "Claim.csv", idx, "claim_id", field, self._amount_text(v), "TYPE_AMOUNT_TEXT"):
                    break

    def _format_payment(self, pay: pd.DataFrame, base: list[Any]) -> None:
        pool = self._dirty_pool(pay, 0.08, base)
        for idx in self._pool_sample(pool, 0.12):
            v = pay.at[idx, "claim_id"]
            if pd.notna(v):
                self._set_if_free(pay, "Claim_Payment.csv", idx, "payment_id", "claim_id", str(v).lower() + " ", "FMT_ID")
        for idx in self._pool_sample(pool, 0.40):
            for field in ["processing_start_date", "decision_date", "payment_date"]:
                v = pay.at[idx, field]
                if isinstance(v, date) and self._set_if_free(pay, "Claim_Payment.csv", idx, "payment_id", field, self._date_alt(v, "YYYYMMDD"), "FMT_DATE"):
                    break
        for idx in self._pool_sample(pool, 0.40):
            for field in ["decision_outcome", "payment_status", "payment_method"]:
                v = pay.at[idx, field]
                if pd.notna(v) and self._set_if_free(pay, "Claim_Payment.csv", idx, "payment_id", field, self._category_alt(v), "FMT_CATEGORY_CASE"):
                    break
        for idx in self._pool_sample(pool, 0.32):
            self._set_if_free(pay, "Claim_Payment.csv", idx, "payment_id", "payment_amount", self._amount_text(pay.at[idx, "payment_amount"]), "TYPE_AMOUNT_TEXT")

    def _format_premium(self, pr: pd.DataFrame, base: list[Any]) -> None:
        pool = self._dirty_pool(pr, 0.05, base)
        for idx in self._pool_sample(pool, 0.08):
            v = pr.at[idx, "policy_id"]
            if pd.notna(v):
                self._set_if_free(pr, "Policy_Premium.csv", idx, "premium_id", "policy_id", str(v).lower() + " ", "FMT_ID")
        for idx in self._pool_sample(pool, 0.20):
            for field in ["due_date", "paid_date"]:
                v = pr.at[idx, field]
                if isinstance(v, date) and self._set_if_free(pr, "Policy_Premium.csv", idx, "premium_id", field, self._date_alt(v, "MM/DD/YYYY"), "FMT_DATE"):
                    break
        for idx in self._pool_sample(pool, 0.20):
            for field in ["payment_status", "premium_frequency", "payment_method"]:
                v = pr.at[idx, field]
                if pd.notna(v) and self._set_if_free(pr, "Policy_Premium.csv", idx, "premium_id", field, self._category_alt(v), "FMT_CATEGORY_CASE"):
                    break
        for idx in self._pool_sample(pool, 0.20):
            self._set_if_free(pr, "Policy_Premium.csv", idx, "premium_id", "premium_amount", self._amount_text(pr.at[idx, "premium_amount"]) + " CAD", "TYPE_AMOUNT_TEXT")

    def build(self, customers, policies, claims, payments, premiums, docs):
        c = customers[[x for x in customers if not x.startswith("_")]].copy()
        p = policies[[x for x in policies if not x.startswith("_")]].copy()
        cl = claims[["claim_id", "policy_id", "customer_id", "claim_type", "service_date", "claim_date", "claim_amount", "approved_amount", "claim_status", "region"]].copy()
        pay = payments[["payment_id", "claim_id", "processing_start_date", "decision_date", "decision_outcome", "payment_date", "payment_amount", "payment_method", "payment_status", "transaction_reference", "denial_reason"]].copy()
        pr = premiums.copy()

        base_c, base_p, base_cl, base_pay, base_pr = list(c.index), list(p.index), list(cl.index), list(pay.index), list(pr.index)

        duplicate_ids = []
        for j in range(4):
            row = c.iloc[j].copy()
            original = row["customer_id"]
            row["customer_id"] = f"C{151 + j:05d}"
            duplicate_ids.append(row["customer_id"])
            row["phone" if j % 2 == 0 else "email"] = None
            row["last_updated"] = C.EXTRACT_END.isoformat() + "T12:00:00-06:00"
            c = pd.concat([c, pd.DataFrame([row])], ignore_index=True)
            self._log("DUP_ENTITY", "Customer.csv", row["customer_id"], "customer_id", original, row["customer_id"])
        for policy_idx, duplicate_id in zip(base_p[:2], duplicate_ids[:2]):
            policy_id = p.at[policy_idx, "policy_id"]
            p.at[policy_idx, "customer_id"] = duplicate_id
            cl.loc[cl["policy_id"] == policy_id, "customer_id"] = duplicate_id
            pr.loc[pr["policy_id"] == policy_id, "customer_id"] = duplicate_id

        for j, prov in enumerate(["QC", "NB", "NU"]):
            cid = f"C{155 + j:05d}"
            row = c.iloc[10 + j].copy()
            row["customer_id"], row["province"], row["city"] = cid, prov, C.UNSERVED[prov]["city"]
            prefix = C.UNSERVED[prov]["postal"][0]
            row["postal_code"] = f"{prefix}1A 1A1"
            row["phone"] = f"+1{C.UNSERVED[prov]['area']}55590{j + 1:02d}"
            c = pd.concat([c, pd.DataFrame([row])], ignore_index=True)
            self._log("INV_UNSERVED_PROVINCE", "Customer.csv", cid, "province", "served", prov)

            prow = p.iloc[5 + j].copy()
            prow["policy_id"], prow["customer_id"] = f"P{181 + j:05d}", cid
            prow["start_date"], prow["end_date"], prow["status"] = date(2025, 1 + j, 1), None, "active"
            p = pd.concat([p, pd.DataFrame([prow])], ignore_index=True)

            q = pr.iloc[5 + j].copy()
            q["premium_id"], q["policy_id"], q["customer_id"] = f"PRM{len(pr) + 1:06d}", prow["policy_id"], cid
            q["due_date"], q["paid_date"], q["payment_status"] = date(2026, 1 + j, 1), date(2026, 1 + j, 1), "paid"
            pr = pd.concat([pr, pd.DataFrame([q])], ignore_index=True)

        self._set(c, "Customer.csv", base_c[20], "customer_id", "date_of_birth", date(2030, 1, 1), "INV_AGE")
        self._set(c, "Customer.csv", base_c[21], "customer_id", "date_of_birth", date(1900, 1, 1), "INV_AGE")
        used_age_indices = {base_c[20], base_c[21]}
        age_idx = None
        st = None
        for policy_idx in base_p:
            owner = str(p.at[policy_idx, "customer_id"])
            matches = c.index[c["customer_id"] == owner].tolist()
            if matches and matches[0] not in used_age_indices:
                age_idx = int(matches[0])
                st = p.at[policy_idx, "start_date"]
                break
        if age_idx is None or st is None:
            raise AssertionError("could not reserve distinct INV_AGE row")
        self._set(c, "Customer.csv", age_idx, "customer_id", "date_of_birth", date(st.year - 15, st.month, min(st.day, 28)), "INV_AGE")
        for idx in base_c[22:24]:
            self._set(c, "Customer.csv", idx, "customer_id", "date_of_birth", None, "MISS_ERROR")
        for idx in base_c[24:26]:
            self._set(c, "Customer.csv", idx, "customer_id", "province", None, "MISS_ERROR")

        for j, idx in enumerate(base_p[10:12], 1):
            self._set(p, "Policy.csv", idx, "policy_id", "customer_id", f"C9{j:04d}", "ORPHAN_FK")
        for idx in base_p[12:14]:
            self._set(p, "Policy.csv", idx, "policy_id", "end_date", p.at[idx, "start_date"] - timedelta(days=1), "INV_DATE_ORDER")
        for idx in base_p[14:16]:
            self._set(p, "Policy.csv", idx, "policy_id", "sales_channel", None, "MISS_ERROR")
        for idx in base_p[16:18]:
            self._set(p, "Policy.csv", idx, "policy_id", "plan_name", None, "MISS_ERROR")

        protected_claims = {cid for cid, sig in self.signal.items() if "F6" in sig or "OUTLIER" in sig}
        protected_idx = set(cl.index[cl["claim_id"].isin(protected_claims)])
        for idx in base_cl[0:8]:
            old = cl.at[idx, "claim_status"]
            self._set(cl, "Claim.csv", idx, "claim_id", "claim_status", "denied" if old != "denied" else "approved", "XF_STATUS_CONFLICT")
        for idx in base_cl[8:16]:
            old = cl.at[idx, "region"]
            self._set(cl, "Claim.csv", idx, "claim_id", "region", "AB" if old != "AB" else "SK", "XF_REGION_CONFLICT")
        for idx in base_cl[16:21]:
            old = cl.at[idx, "customer_id"]
            self._set(cl, "Claim.csv", idx, "claim_id", "customer_id", "C00150" if old != "C00150" else "C00149", "XF_OWNER_CONFLICT")
        for j, idx in enumerate(base_cl[21:24], 1):
            self._set(cl, "Claim.csv", idx, "claim_id", "policy_id", f"P9{j:04d}", "ORPHAN_FK")

        pstart = policies.set_index("policy_id")["start_date"].to_dict()
        for idx in base_cl[24:27]:
            self._set(cl, "Claim.csv", idx, "claim_id", "service_date", pstart[cl.at[idx, "policy_id"]] - timedelta(days=1), "INV_CLAIM_BEFORE_POLICY")
        for idx in base_cl[27:29]:
            self._set(cl, "Claim.csv", idx, "claim_id", "service_date", cl.at[idx, "claim_date"] + timedelta(days=2), "INV_DATE_ORDER")

        eligible_amount = [i for i in base_cl if i not in protected_idx and i not in set(base_cl[:29])]
        for idx in eligible_amount[:3]:
            self._set(cl, "Claim.csv", idx, "claim_id", "claim_amount", -abs(Decimal(str(cl.at[idx, "claim_amount"]))), "INV_NEGATIVE_AMOUNT")
        for idx in eligible_amount[3:5]:
            self._set(cl, "Claim.csv", idx, "claim_id", "claim_amount", Decimal(str(cl.at[idx, "claim_amount"])) * Decimal("100"), "OUT_ERROR")
        for idx in eligible_amount[5:7]:
            self._set(cl, "Claim.csv", idx, "claim_id", "claim_amount", None, "MISS_ERROR")
        for idx in eligible_amount[7:9]:
            self._set(cl, "Claim.csv", idx, "claim_id", "service_date", None, "MISS_ERROR")

        clean_claim_dates = claims.set_index("claim_id")["claim_date"].to_dict()
        paid_rows = [i for i in base_pay if pd.notna(pay.at[i, "payment_date"])]
        for idx in paid_rows[:3]:
            cid = pay.at[idx, "claim_id"]
            self._set(pay, "Claim_Payment.csv", idx, "payment_id", "payment_date", clean_claim_dates[cid] - timedelta(days=1), "INV_PAYMENT_BEFORE_SUBMISSION")
        for idx in base_pay[3:5]:
            self._set(pay, "Claim_Payment.csv", idx, "payment_id", "decision_date", pay.at[idx, "processing_start_date"] - timedelta(days=1), "INV_DATE_ORDER")
        amount_rows = [i for i in base_pay[5:] if pay.at[i, "decision_outcome"] in {"approved", "partially_approved"}]
        for idx in amount_rows[:3]:
            self._set(pay, "Claim_Payment.csv", idx, "payment_id", "payment_amount", Decimal(str(pay.at[idx, "payment_amount"])) + Decimal("17.37"), "XF_PAYMENT_AMOUNT")
        for idx in base_pay[8:10]:
            self._set(pay, "Claim_Payment.csv", idx, "payment_id", "decision_date", None, "MISS_ERROR")
        for j in range(3):
            row = pay.iloc[10 + j].copy()
            row["payment_id"], row["claim_id"] = f"PAY{len(pay) + 1:05d}", f"H9{j + 1:04d}"
            pay = pd.concat([pay, pd.DataFrame([row])], ignore_index=True)
            self._log("ORPHAN_FK", "Claim_Payment.csv", row["payment_id"], "claim_id", "", row["claim_id"])

        for idx in base_pr[:10]:
            old = pr.at[idx, "customer_id"]
            self._set(pr, "Policy_Premium.csv", idx, "premium_id", "customer_id", "C00150" if old != "C00150" else "C00149", "XF_OWNER_CONFLICT")
        for j in range(5):
            row = pr.iloc[20 + j].copy()
            row["premium_id"], row["policy_id"] = f"PRM{len(pr) + 1:06d}", f"P9{j + 1:04d}"
            pr = pd.concat([pr, pd.DataFrame([row])], ignore_index=True)
            self._log("ORPHAN_FK", "Policy_Premium.csv", row["premium_id"], "policy_id", "", row["policy_id"])
        for idx in base_pr[25:29]:
            self._set(pr, "Policy_Premium.csv", idx, "premium_id", "premium_amount", -abs(Decimal(str(pr.at[idx, "premium_amount"]))), "INV_NEGATIVE_AMOUNT")
        for idx in base_pr[29:34]:
            due = pr.at[idx, "due_date"]
            self._set(pr, "Policy_Premium.csv", idx, "premium_id", "paid_date", due + timedelta(days=7), "INV_DERIVED_STATUS")
            self._set(pr, "Policy_Premium.csv", idx, "premium_id", "payment_status", "paid", "INV_DERIVED_STATUS")
        nonmiss = [i for i in base_pr[34:] if pr.at[i, "payment_status"] in {"paid", "late"}]
        for idx in nonmiss[:4]:
            self._set(pr, "Policy_Premium.csv", idx, "premium_id", "paid_date", None, "MISS_ERROR")

        self._format_customer(c, base_c)
        self._format_policy(p, base_p)
        self._format_claim(cl, base_cl)
        self._format_payment(pay, base_pay)
        self._format_premium(pr, base_pr)

        ids = sorted(docs)
        missing, malformed = set(ids[:14]), set(ids[14:16])
        physical = {k: copy.deepcopy(v) for k, v in docs.items() if k not in missing}
        for cid in sorted(missing):
            self._log("JSON_MISSING_FILE", f"json/{cid}.json", cid, "*", cid, "")

        parseable = [cid for cid in sorted(physical) if cid not in malformed]
        json_pool = list(self.rng.choice(parseable, size=max(1, int(round(len(parseable) * 0.10))), replace=False))
        self.rng.shuffle(json_pool)
        missing_key_ids = json_pool[0:4]
        key_drift_ids = json_pool[4:6]
        type_drift_ids = json_pool[6:10]
        shape_drift_ids = json_pool[10:12]

        for cid in missing_key_ids:
            d = physical[cid]
            choices = ["adjuster_id", "documents_submitted", "submission_channel", "provider.type"]
            field = choices[int(self.rng.integers(0, len(choices)))]
            if field == "provider.type":
                old = d["provider"].pop("type", None)
            else:
                old = d.pop(field, None)
            self._log("JSON_MISSING_KEY", f"json/{cid}.json", cid, field, old, "<absent>")

        for cid in key_drift_ids:
            d = physical[cid]
            if self.rng.random() < 0.5 and "line_items" in d:
                d["lineItems"] = d.pop("line_items")
                self._log("JSON_KEY_DRIFT", f"json/{cid}.json", cid, "line_items", "line_items", "lineItems")
            elif "claim_id" in d:
                d["ClaimID"] = d.pop("claim_id")
                self._log("JSON_KEY_DRIFT", f"json/{cid}.json", cid, "claim_id", "claim_id", "ClaimID")

        for cid in type_drift_ids:
            d = physical[cid]
            if isinstance(d.get("line_items"), list) and d["line_items"]:
                item = d["line_items"][0]
                field = "amount" if self.rng.random() < 0.5 else "unit_amount"
                old = item[field]
                item[field] = "$" + f"{Decimal(str(old)):.2f}"
                self._log("JSON_TYPE_DRIFT", f"json/{cid}.json", cid, f"line_items[0].{field}", old, item[field])
            elif d.get("travel"):
                old = d["travel"]["exchange_rate_to_cad"]
                d["travel"]["exchange_rate_to_cad"] = str(old)
                self._log("JSON_TYPE_DRIFT", f"json/{cid}.json", cid, "travel.exchange_rate_to_cad", old, str(old))

        for cid in shape_drift_ids:
            d = physical[cid]
            if isinstance(d.get("documents_submitted"), list):
                old = d["documents_submitted"]
                d["documents_submitted"] = ",".join(old)
                self._log("JSON_SHAPE_DRIFT", f"json/{cid}.json", cid, "documents_submitted", old, d["documents_submitted"])
            elif isinstance(d.get("line_items"), list) and len(d["line_items"]) == 1:
                old = d["line_items"]
                d["line_items"] = d["line_items"][0]
                self._log("JSON_SHAPE_DRIFT", f"json/{cid}.json", cid, "line_items", old, "<object>")

        texts: dict[str, str] = {}
        malformed_sorted = sorted(malformed)
        for cid, d in sorted(physical.items()):
            text = json.dumps(_serial(d), indent=2, ensure_ascii=False)
            if cid in malformed:
                text = text[:-2] if cid == malformed_sorted[0] else text[:-2] + ",\n}"
                self._log("JSON_MALFORMED", f"json/{cid}.json", cid, "*", "valid JSON", "malformed JSON")
            texts[f"{cid}.json"] = text + "\n"

        for j, source_cid in enumerate(ids[16:20], 1):
            d = copy.deepcopy(docs[source_cid])
            orphan_id = f"H9{j:04d}"
            d["claim_id"] = orphan_id
            texts[f"{orphan_id}.json"] = json.dumps(_serial(d), indent=2, ensure_ascii=False) + "\n"
            self._log("JSON_ORPHAN", f"json/{orphan_id}.json", orphan_id, "claim_id", source_cid, orphan_id)

        claim_dups = []
        for idx in base_cl[:6]:
            row = cl.loc[idx].copy()
            claim_dups.append(row)
            self._log("DUP_EXACT", "Claim.csv", row["claim_id"], "*", "row", "exact duplicate")
        for idx in base_cl[6:10]:
            row = cl.loc[idx].copy()
            old = row["claim_id"]
            row["claim_id"] = str(old).lower() + " "
            claim_dups.append(row)
            self._log("DUP_NEAR_KEY", "Claim.csv", old, "claim_id", old, row["claim_id"])
        cl = pd.concat([cl, pd.DataFrame(claim_dups)], ignore_index=True)

        payment_dups = []
        for idx in base_pay[:5]:
            row = pay.loc[idx].copy()
            payment_dups.append(row)
            self._log("DUP_EXACT", "Claim_Payment.csv", row["payment_id"], "*", "row", "exact duplicate")
        pay = pd.concat([pay, pd.DataFrame(payment_dups)], ignore_index=True)

        premium_dups = []
        for idx in base_pr[:15]:
            row = pr.loc[idx].copy()
            premium_dups.append(row)
            self._log("DUP_EXACT", "Policy_Premium.csv", row["premium_id"], "*", "row", "exact duplicate")
        pr = pd.concat([pr, pd.DataFrame(premium_dups)], ignore_index=True)

        defect_log = pd.DataFrame(self.log).sort_values(["source", "record_key", "field", "defect_type"]).reset_index(drop=True)
        defect_log.insert(0, "defect_id", [f"DL{i + 1:05d}" for i in range(len(defect_log))])

        tables = {"Customer.csv": c, "Policy.csv": p, "Claim.csv": cl, "Claim_Payment.csv": pay, "Policy_Premium.csv": pr}
        raw_qa = self._raw_qa(tables, texts, defect_log)
        if raw_qa["errors"]:
            raise AssertionError("raw QA: " + "; ".join(raw_qa["errors"]))
        return tables, texts, defect_log, raw_qa

    def _raw_qa(self, tables, texts, log: pd.DataFrame) -> dict[str, Any]:
        errors: list[str] = []
        expected_rows = {"Customer.csv": 157, "Policy.csv": 183, "Claim.csv": 220, "Claim_Payment.csv": 203}
        for source, n in expected_rows.items():
            if len(tables[source]) != n:
                errors.append(f"{source} rows {len(tables[source])} != {n}")
        if len(texts) != 200:
            errors.append(f"JSON files {len(texts)} != 200")

        count_targets = {
            ("Customer.csv", "DUP_ENTITY"): 4, ("Customer.csv", "INV_UNSERVED_PROVINCE"): 3,
            ("Customer.csv", "INV_AGE"): 3, ("Customer.csv", "MISS_ERROR"): 4,
            ("Policy.csv", "ORPHAN_FK"): 2, ("Policy.csv", "INV_DATE_ORDER"): 2, ("Policy.csv", "MISS_ERROR"): 4,
            ("Claim.csv", "DUP_EXACT"): 6, ("Claim.csv", "DUP_NEAR_KEY"): 4, ("Claim.csv", "ORPHAN_FK"): 3,
            ("Claim.csv", "XF_STATUS_CONFLICT"): 8, ("Claim.csv", "XF_REGION_CONFLICT"): 8, ("Claim.csv", "XF_OWNER_CONFLICT"): 5,
            ("Claim.csv", "INV_CLAIM_BEFORE_POLICY"): 3, ("Claim.csv", "INV_DATE_ORDER"): 2,
            ("Claim.csv", "INV_NEGATIVE_AMOUNT"): 3, ("Claim.csv", "OUT_ERROR"): 2, ("Claim.csv", "MISS_ERROR"): 4,
            ("Claim_Payment.csv", "DUP_EXACT"): 5, ("Claim_Payment.csv", "ORPHAN_FK"): 3,
            ("Claim_Payment.csv", "INV_PAYMENT_BEFORE_SUBMISSION"): 3, ("Claim_Payment.csv", "INV_DATE_ORDER"): 2,
            ("Claim_Payment.csv", "XF_PAYMENT_AMOUNT"): 3, ("Claim_Payment.csv", "MISS_ERROR"): 2,
            ("Policy_Premium.csv", "DUP_EXACT"): 15, ("Policy_Premium.csv", "ORPHAN_FK"): 5,
            ("Policy_Premium.csv", "XF_OWNER_CONFLICT"): 10, ("Policy_Premium.csv", "INV_NEGATIVE_AMOUNT"): 4,
            ("Policy_Premium.csv", "INV_DERIVED_STATUS"): 10, ("Policy_Premium.csv", "MISS_ERROR"): 4,
            ("json", "JSON_MALFORMED"): 2, ("json", "JSON_ORPHAN"): 4, ("json", "JSON_MISSING_FILE"): 14,
        }
        for (source, defect), target in count_targets.items():
            mask = (log["source"].str.startswith("json/") if source == "json" else log["source"] == source) & (log["defect_type"] == defect)
            got = int(mask.sum())
            if got != target:
                errors.append(f"{source}/{defect} {got} != {target}")

        for defect, target in {"JSON_MISSING_KEY": 4, "JSON_KEY_DRIFT": 2, "JSON_TYPE_DRIFT": 4, "JSON_SHAPE_DRIFT": 2}.items():
            got = int(((log["source"].str.startswith("json/")) & (log["defect_type"] == defect)).sum())
            if got != target:
                errors.append(f"json/{defect} {got} != {target}")

        derived_rows = log[(log["source"] == "Policy_Premium.csv") & (log["defect_type"] == "INV_DERIVED_STATUS")]["record_key"].nunique()
        if derived_rows != 5:
            errors.append(f"Policy_Premium INV_DERIVED_STATUS rows {derived_rows} != 5")

        affected = log[["source", "record_key"]].drop_duplicates()
        total_records = sum(len(x) for x in tables.values()) + len(texts)
        affected_share = len(affected) / total_records if total_records else 0.0
        if not 0.05 <= affected_share <= 0.15:
            errors.append(f"affected share {affected_share:.3f} outside 0.05..0.15")
        if "is_fraud_synthetic_label" in tables["Claim.csv"].columns:
            errors.append("forbidden fraud target present")

        return {
            "errors": errors,
            "affected_record_file_share": round(affected_share, 4),
            "defect_log_entries": int(len(log)),
            "unique_affected_records_files": int(len(affected)),
            "defect_counts": dict(Counter(log["defect_type"])),
        }


def build_messy_raw(customers, policies, claims, payments, premiums, docs, signal, seed):
    return DefectInjector(seed, signal).build(customers, policies, claims, payments, premiums, docs)
