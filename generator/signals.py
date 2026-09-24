from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from typing import Any

import numpy as np
import pandas as pd

from . import config as C


def _random_date(r: np.random.Generator, lo: date, hi: date) -> date:
    if hi < lo:
        raise ValueError(f"invalid date range {lo}..{hi}")
    return lo + timedelta(days=int(r.integers(0, (hi - lo).days + 1)))


def plan_phase_a_signals(
    claims: pd.DataFrame,
    policies: pd.DataFrame,
    r: np.random.Generator,
) -> tuple[pd.DataFrame, dict[str, set[str]], dict[str, str]]:
    """Make every reserved F1-F9 carrier satisfy its rule by construction.

    Signal carriers are reserved on Phase-A claims so the retention snapshot can
    observe the relevant denied-claim history. Apart from the deliberate F5
    overlaps, carrier sets are disjoint. Exactly 12 claims carry F5 plus one
    other suspicious trait.
    """
    out = claims.copy()
    p_rows = policies.to_dict("records")
    signal: dict[str, set[str]] = defaultdict(set)
    forced_provider: dict[str, str] = {}
    used: set[int] = set()

    def rows_where(predicate, *, exclude_used=True) -> list[int]:
        return [
            i for i in out.index
            if (not exclude_used or i not in used) and predicate(out.loc[i])
        ]

    def policy_candidates(product: str, predicate=None) -> list[dict[str, Any]]:
        rows = [p for p in p_rows if p["policy_type"] == product and p["start_date"] <= C.SNAPSHOT_DATE]
        if predicate is not None:
            rows = [p for p in rows if predicate(p)]
        return rows

    def reassign(idx: int, p: dict[str, Any], service: date, submit: date | None = None) -> None:
        submit = submit or min(C.SNAPSHOT_DATE - timedelta(days=14), service + timedelta(days=2))
        if submit < service:
            submit = service
        out.at[idx, "policy_id"] = p["policy_id"]
        out.at[idx, "customer_id"] = p["customer_id"]
        out.at[idx, "service_date"] = service
        out.at[idx, "claim_date"] = submit

    def tag(idx: int, label: str, mark_used=True) -> None:
        signal[out.at[idx, "_tmp"]].add(label)
        if mark_used:
            used.add(idx)

    # F2 first because it must be a real 90-day dental waiting-period attempt.
    f2_rows = rows_where(lambda x: x["product_line"] == "dental" and x["claim_type"] in {"basic", "major"})[:6]
    dental_policies = policy_candidates(
        "dental",
        lambda p: p["start_date"] <= C.SNAPSHOT_DATE - timedelta(days=30)
        and p["start_date"] >= C.OBS_START - timedelta(days=75),
    )
    if not dental_policies:
        dental_policies = policy_candidates("dental", lambda p: p["start_date"] <= C.SNAPSHOT_DATE - timedelta(days=30))
    if len(f2_rows) != 6 or not dental_policies:
        raise AssertionError("unable to reserve F2 carriers")
    for j, idx in enumerate(f2_rows):
        p = dental_policies[j % len(dental_policies)]
        lo = max(p["start_date"] + timedelta(days=7), C.OBS_START)
        hi = min(p["start_date"] + timedelta(days=80), C.SNAPSHOT_DATE - timedelta(days=21))
        if lo > hi:
            lo = p["start_date"] + timedelta(days=7)
            hi = min(p["start_date"] + timedelta(days=80), C.SNAPSHOT_DATE - timedelta(days=21))
        service = _random_date(r, lo, hi)
        reassign(idx, p, service, service + timedelta(days=int(r.integers(0, 5))))
        tag(idx, "F2")

    # F3: guarantee six useful overlap candidates for F5 (3 pharmacy + 3 clinic).
    f3_selected: list[int] = []
    for claim_type, n in [("prescription_drugs", 3), ("health_practitioner", 3)]:
        candidates = rows_where(lambda x, ct=claim_type: x["claim_type"] == ct)
        if len(candidates) < n:
            raise AssertionError(f"unable to reserve F3 {claim_type}")
        f3_selected.extend(candidates[:n])
        used.update(candidates[:n])
    extra = rows_where(
        lambda x: not (x["product_line"] == "travel" and x["claim_type"] == "emergency_medical")
    )[:2]
    if len(extra) != 2:
        raise AssertionError("unable to reserve remaining F3 carriers")
    f3_selected.extend(extra)
    used.update(extra)
    for idx in f3_selected:
        signal[out.at[idx, "_tmp"]].add("F3")

    # F1: actual service within 30 days of policy effective date.
    f1_candidates = rows_where(lambda x: True)[:12]
    if len(f1_candidates) != 12:
        raise AssertionError("unable to reserve F1 carriers")
    for idx in f1_candidates:
        product = out.at[idx, "product_line"]
        policies_for_product = policy_candidates(
            product,
            lambda p: p["start_date"] >= C.HISTORY_START
            and p["start_date"] <= C.SNAPSHOT_DATE - timedelta(days=35),
        )
        if not policies_for_product:
            policies_for_product = policy_candidates(product, lambda p: p["start_date"] <= C.SNAPSHOT_DATE - timedelta(days=35))
        p = policies_for_product[int(r.integers(0, len(policies_for_product)))]
        service = p["start_date"] + timedelta(days=int(r.integers(3, 28)))
        reassign(idx, p, service, service + timedelta(days=int(r.integers(0, 5))))
        tag(idx, "F1")

    # F4: four real same-customer clusters, each with 3 claims inside 30 days.
    cluster_products = ["health", "health", "dental", "dental"]
    for cluster_no, product in enumerate(cluster_products):
        candidates = rows_where(lambda x, product=product: x["product_line"] == product)[:3]
        if len(candidates) != 3:
            raise AssertionError(f"unable to reserve F4 cluster {cluster_no + 1}")
        pols = policy_candidates(
            product,
            lambda p: p["start_date"] <= C.SNAPSHOT_DATE - timedelta(days=60),
        )
        p = pols[cluster_no % len(pols)]
        anchor_lo = max(p["start_date"] + timedelta(days=30), C.OBS_START)
        anchor_hi = C.SNAPSHOT_DATE - timedelta(days=45)
        if anchor_lo > anchor_hi:
            anchor_lo = p["start_date"] + timedelta(days=30)
        anchor = _random_date(r, anchor_lo, anchor_hi)
        for offset, idx in zip([0, 8, 17], candidates):
            service = anchor + timedelta(days=offset)
            reassign(idx, p, service, service + timedelta(days=1))
            tag(idx, "F4")

    # F6/F7/F8 are direct detail-layer traits; carrier rows remain otherwise ordinary.
    for label, n in [("F6", 5), ("F7", 10), ("F8", 12)]:
        candidates = rows_where(lambda x: True)[:n]
        if len(candidates) != n:
            raise AssertionError(f"unable to reserve {label} carriers")
        for idx in candidates:
            tag(idx, label)

    # F9: non-cancellation incidents outside an individual trip window but still
    # inside a StudentPlan annual policy term.
    f9_rows = rows_where(
        lambda x: x["product_line"] == "travel" and x["claim_type"] != "trip_cancellation"
    )[:4]
    student = policy_candidates(
        "travel",
        lambda p: p["plan_name"] == "StudentPlan"
        and p["start_date"] <= C.SNAPSHOT_DATE - timedelta(days=50)
        and (p["end_date"] is None or p["end_date"] >= p["start_date"] + timedelta(days=60)),
    )
    if len(f9_rows) != 4 or not student:
        raise AssertionError("unable to reserve F9 carriers")
    for j, idx in enumerate(f9_rows):
        p = student[j % len(student)]
        latest = min(C.SNAPSHOT_DATE - timedelta(days=30), (p["end_date"] or C.SNAPSHOT_DATE) - timedelta(days=20))
        service = _random_date(r, p["start_date"] + timedelta(days=15), latest)
        reassign(idx, p, service, service + timedelta(days=2))
        tag(idx, "F9")

    # Three large but legitimate travel emergency-medical claims.
    outlier_rows = rows_where(
        lambda x: x["product_line"] == "travel" and x["claim_type"] == "emergency_medical"
    )[:3]
    if len(outlier_rows) != 3:
        raise AssertionError("unable to reserve legitimate outliers")
    for idx in outlier_rows:
        tag(idx, "OUTLIER")

    # F5: exactly four hot providers × six claims. Twelve overlap an existing
    # suspicious trait (all F2 + 3 F3 pharmacy + 3 F3 clinic); twelve are F5-only.
    provider_groups = [
        ("PRV0001", lambda x: x["claim_type"] == "prescription_drugs", 3),
        ("PRV0002", lambda x: x["product_line"] == "dental", 6),
        ("PRV0003", lambda x: x["claim_type"] == "health_practitioner", 3),
        ("PRV0004", lambda x: x["claim_type"] in {"trip_cancellation", "trip_interruption"}, 0),
    ]
    f2_tmp = {out.at[i, "_tmp"] for i in f2_rows}
    f3_tmp = {out.at[i, "_tmp"] for i in f3_selected}
    total_overlap = 0
    for provider_id, predicate, desired_overlap in provider_groups:
        compatible = [i for i in out.index if predicate(out.loc[i])]
        if provider_id == "PRV0002":
            overlap_candidates = [i for i in compatible if out.at[i, "_tmp"] in f2_tmp]
        else:
            overlap_candidates = [i for i in compatible if out.at[i, "_tmp"] in f3_tmp]
        overlap = overlap_candidates[:desired_overlap]
        chosen = list(overlap)
        for idx in compatible:
            if len(chosen) == 6:
                break
            if idx in chosen:
                continue
            # F5-only carriers must be genuinely signal-free so the exact
            # 12-claim overlap target remains reproducible.
            if signal[out.at[idx, "_tmp"]]:
                continue
            chosen.append(idx)
        if len(chosen) != 6:
            raise AssertionError(f"unable to reserve six F5 carriers for {provider_id}")
        for idx in chosen:
            tmp = out.at[idx, "_tmp"]
            signal[tmp].add("F5")
            forced_provider[tmp] = provider_id
        total_overlap += sum(1 for idx in chosen if len(signal[out.at[idx, "_tmp"]] - {"F5"}) > 0)

    if total_overlap != 12:
        raise AssertionError(f"F5 overlap target 12, got {total_overlap}")

    multi = sum(1 for traits in signal.values() if len(traits & {"F1","F2","F3","F4","F5","F6","F7","F8","F9"}) >= 2)
    if not 10 <= multi <= 18:
        raise AssertionError(f"multi-signal carriers {multi} outside 10..18")

    return out, signal, forced_provider
