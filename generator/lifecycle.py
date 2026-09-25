from __future__ import annotations

import math
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP
from typing import Any

import numpy as np
import pandas as pd

from . import config as C

Q = Decimal("0.01")


def money(x: Any) -> Decimal:
    return Decimal(str(x)).quantize(Q, rounding=ROUND_HALF_UP)


def choose(r: np.random.Generator, values, probs=None):
    return values[int(r.choice(len(values), p=probs))]


def random_date(r: np.random.Generator, lo: date, hi: date) -> date:
    if hi < lo:
        raise ValueError(f"invalid date range {lo}..{hi}")
    return lo + timedelta(days=int(r.integers(0, (hi - lo).days + 1)))


def add_months(d: date, n: int) -> date:
    y = d.year + (d.month - 1 + n) // 12
    m = (d.month - 1 + n) % 12 + 1
    md = [31, 29 if y % 4 == 0 and (y % 100 != 0 or y % 400 == 0) else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][m - 1]
    return date(y, m, min(d.day, md))


def age(dob: date, at: date) -> int:
    return at.year - dob.year - ((at.month, at.day) < (dob.month, dob.day))


def age_band(dob: date, at: date) -> str:
    a = age(dob, at)
    for band, lo, hi, _ in C.AGE_BANDS:
        if lo <= a <= hi:
            return band
    raise ValueError(a)


def premium_status_as_of(due_date: date, paid_date: date | None, as_of: date) -> str | None:
    """Derive the observable premium state at a historical cutoff.

    Payments after the cutoff are censored. An instalment unpaid for at least
    30 days is treated as missed; a more recent unpaid instalment remains
    outstanding rather than inheriting its eventual extract-end status.
    """
    if due_date > as_of:
        return None

    if paid_date is not None and not pd.isna(paid_date) and paid_date <= as_of:
        return "paid" if paid_date <= due_date else "late"

    if due_date <= as_of - timedelta(days=30):
        return "missed"

    return "outstanding"


def age_factor(band: str) -> Decimal:
    return Decimal(str(next(x[3] for x in C.AGE_BANDS if x[0] == band)))


def quotas(total: int, weights: dict[str, float]) -> dict[str, int]:
    raw = {k: total * v / sum(weights.values()) for k, v in weights.items()}
    out = {k: int(math.floor(v)) for k, v in raw.items()}
    for k in sorted(raw, key=lambda x: (raw[x] - out[x], x), reverse=True)[: total - sum(out.values())]:
        out[k] += 1
    return out


def build_policies(customers: pd.DataFrame, r: np.random.Generator) -> pd.DataFrame:
    """S2: build 180 policies with a controlled 112-customer snapshot HD cohort."""
    ids = customers["customer_id"].tolist()
    hd_customers = list(r.choice(ids, size=112, replace=False))
    non_hd = [x for x in ids if x not in set(hd_customers)]

    hd_plans: list[str] = []
    travel_plans: list[str] = []
    for plan, n in C.PLAN_COUNTS.items():
        target = hd_plans if C.PLANS[plan]["type"] in {"health", "dental"} else travel_plans
        target.extend([plan] * n)
    r.shuffle(hd_plans)
    r.shuffle(travel_plans)

    hd_owners = hd_customers + list(r.choice(hd_customers, size=len(hd_plans) - len(hd_customers), replace=True))
    r.shuffle(hd_owners)

    travel_only = list(r.choice(non_hd, size=min(15, len(non_hd)), replace=False))
    travel_owners = travel_only + list(r.choice(ids, size=len(travel_plans) - len(travel_only), replace=True))
    r.shuffle(travel_owners)

    plan_owner = list(zip(hd_plans, hd_owners)) + list(zip(travel_plans, travel_owners))
    r.shuffle(plan_owner)

    rows = []
    for i, (plan, cid) in enumerate(plan_owner):
        x = C.PLANS[plan]
        typ = x["type"]
        if typ == "travel":
            st = random_date(r, C.HISTORY_START, date(2026, 5, 31))
            en = st + timedelta(days=int(r.integers(20, 76))) if plan == "TravelStar" else st + timedelta(days=364)
            status = "expired" if en <= C.EXTRACT_END else "active"
        else:
            st = random_date(r, date(2022, 1, 1), date(2025, 12, 31))
            en = None
            status = "active"

        if plan == "TravelStar":
            freq = "single"
        elif plan == "StudentPlan":
            freq = choose(r, ["monthly", "yearly"], [0.7, 0.3])
        elif x["freq"] == ["monthly"]:
            freq = "monthly"
        else:
            freq = choose(r, ["monthly", "quarterly", "yearly"], [0.7, 0.2, 0.1])

        rows.append(
            {
                "policy_id": f"P{i + 1:05d}",
                "customer_id": cid,
                "policy_type": typ,
                "plan_name": plan,
                "coverage_type": choose(r, list(C.COVERAGE_SHARE), list(C.COVERAGE_SHARE.values())),
                "start_date": st,
                "end_date": en,
                "status": status,
                "sales_channel": choose(r, list(C.SALES_SHARE), list(C.SALES_SHARE.values())),
                "coverage_amount": money(x["coverage"]),
                "deductible_amount": money(choose(r, x["deductibles"])),
                "_freq": freq,
                "_replacement_case": False,
                "_replacement_for": None,
                "_forced_missed_due": None,
            }
        )

    # Reserve four outcome-window replacement records from HD rows whose original owner
    # has another HD policy, so the snapshot cohort does not shrink.
    hd_indices = [i for i, row in enumerate(rows) if row["policy_type"] in {"health", "dental"}]
    owner_count: dict[str, int] = {}
    for i in hd_indices:
        owner_count[rows[i]["customer_id"]] = owner_count.get(rows[i]["customer_id"], 0) + 1
    replacement_rows = [i for i in hd_indices if owner_count[rows[i]["customer_id"]] >= 2][-4:]

    used_predecessor_customers: set[str] = set()
    for j, ridx in enumerate(replacement_rows):
        rtype = rows[ridx]["policy_type"]
        predecessor = next(
            i
            for i in hd_indices
            if i not in replacement_rows
            and rows[i]["policy_type"] == rtype
            and rows[i]["customer_id"] not in used_predecessor_customers
            and rows[i]["start_date"] <= C.SNAPSHOT_DATE
        )
        pred = rows[predecessor]
        used_predecessor_customers.add(pred["customer_id"])
        rows[ridx]["customer_id"] = pred["customer_id"]
        rows[ridx]["start_date"] = date(2026, 4, 15) + timedelta(days=j * 14)
        rows[ridx]["end_date"] = None
        rows[ridx]["status"] = "active"
        rows[ridx]["_replacement_case"] = True
        rows[ridx]["_replacement_for"] = pred["policy_id"]

    return pd.DataFrame(rows)


def premium_due_dates(policy: dict[str, Any], start_exclusive: date | None, end_inclusive: date) -> list[date]:
    start = policy["start_date"]
    end = min(policy["end_date"] or end_inclusive, end_inclusive)
    if start > end:
        return []
    freq = policy["_freq"]
    if freq == "single":
        return [start] if (start_exclusive is None or start > start_exclusive) and start >= C.HISTORY_START else []
    step = {"monthly": 1, "quarterly": 3, "yearly": 12}[freq]
    d = start
    while d < C.HISTORY_START:
        d = add_months(d, step)
    if start_exclusive is not None:
        while d <= start_exclusive:
            d = add_months(d, step)
    dates: list[date] = []
    while d <= end:
        dates.append(d)
        d = add_months(d, step)
    return dates


def generate_premiums(
    customers: pd.DataFrame,
    policies: pd.DataFrame,
    r: np.random.Generator,
    *,
    phase: str,
) -> pd.DataFrame:
    """Generate only premiums observable in the requested time phase."""
    cmap = customers.set_index("customer_id").to_dict("index")
    rows: list[dict[str, Any]] = []
    start_exclusive = None if phase == "A" else C.SNAPSHOT_DATE
    end_inclusive = C.SNAPSHOT_DATE if phase == "A" else C.EXTRACT_END

    for p in policies.to_dict("records"):
        for due in premium_due_dates(p, start_exclusive, end_inclusive):
            c = cmap[p["customer_id"]]
            band = age_band(c["date_of_birth"], due)
            freq = p["_freq"]
            if p["plan_name"] == "TravelStar":
                amt = money(min(400, max(40, 60 + (p["end_date"] - p["start_date"]).days * 2)))
            else:
                months = {"monthly": 1, "quarterly": 3, "yearly": 12}.get(freq, 1)
                amt = (
                    Decimal(str(C.PLANS[p["plan_name"]]["base"]))
                    * age_factor(band)
                    * Decimal(str(C.COVERAGE_FACTOR[p["coverage_type"]]))
                    * Decimal(months)
                ).quantize(Q)

            forced = p.get("_forced_missed_due")
            if forced == due:
                status = "missed"
            else:
                probs = C.PREMIUM_STATUS_PROBS[c["_reliability"]]
                status = choose(r, ["paid", "late", "missed"], list(probs))
                as_of = C.SNAPSHOT_DATE if phase == "A" else C.EXTRACT_END
                if status == "missed" and due > as_of - timedelta(days=30):
                    status = "paid"

            if status == "missed":
                paid = None
            elif status == "late":
                paid = due + timedelta(days=int(r.integers(3, 21 if c["_reliability"] != "poor" else 36)))
            else:
                paid = due - timedelta(days=int(r.integers(0, 4)))

            rows.append(
                {
                    "policy_id": p["policy_id"],
                    "customer_id": p["customer_id"],
                    "premium_amount": amt,
                    "premium_frequency": freq,
                    "age_band": band,
                    "due_date": due,
                    "paid_date": paid,
                    "payment_status": status,
                    "payment_method": None if status == "missed" else choose(r, C.PREMIUM_METHODS),
                    "_phase": phase,
                }
            )
    return pd.DataFrame(rows)


def finalize_premiums(a: pd.DataFrame, b: pd.DataFrame) -> pd.DataFrame:
    df = pd.concat([a, b], ignore_index=True).sort_values(["due_date", "policy_id", "_phase"]).reset_index(drop=True)
    df.insert(0, "premium_id", [f"PRM{i + 1:06d}" for i in range(len(df))])
    return df.drop(columns=["_phase"])


def _phase_claim_type_counts(phase: str) -> dict[str, dict[str, int]]:
    b: dict[str, dict[str, int]] = {}
    for product, type_counts in C.CLAIM_COUNTS.items():
        b[product] = quotas(C.PHASE_B_PRODUCT_COUNTS[product], type_counts)
    if phase == "B":
        return b
    return {
        product: {claim_type: n - b[product][claim_type] for claim_type, n in type_counts.items()}
        for product, type_counts in C.CLAIM_COUNTS.items()
    }


def schedule_claims(
    customers: pd.DataFrame,
    policies: pd.DataFrame,
    r: np.random.Generator,
    *,
    phase: str,
) -> pd.DataFrame:
    cmap = customers.set_index("customer_id").to_dict("index")
    counts = _phase_claim_type_counts(phase)
    specs: list[tuple[str, str]] = []
    for product, type_counts in counts.items():
        for claim_type, n in type_counts.items():
            specs.extend([(product, claim_type)] * n)
    r.shuffle(specs)

    total_expected = C.PHASE_A_CLAIMS if phase == "A" else C.PHASE_B_CLAIMS
    if len(specs) != total_expected:
        raise AssertionError(f"{phase} claim quota {len(specs)} != {total_expected}")

    rows: list[dict[str, Any]] = []
    for i, (product, claim_type) in enumerate(specs):
        force_pending = phase == "B" and i < 15
        lo = C.HISTORY_START if phase == "A" else C.OUTCOME_START
        hi = C.SNAPSHOT_DATE - timedelta(days=21) if phase == "A" else C.EXTRACT_END
        candidates: list[dict[str, Any]] = []
        weights: list[float] = []
        for p in policies.to_dict("records"):
            if p["policy_type"] != product:
                continue
            if force_pending and (p["start_date"] > C.EXTRACT_END or (p["end_date"] is not None and p["end_date"] < C.EXTRACT_END)):
                continue
            a = max(lo, p["start_date"])
            b = min(hi, p["end_date"] or hi)
            if a > b:
                continue
            c = cmap[p["customer_id"]]
            w = (
                C.PROPENSITY_MULT[c["_propensity"]]
                * C.PROVINCE_FREQ_MULT[c["province"]]
                * C.PLAN_FREQ_MULT[p["plan_name"]]
            )
            candidates.append(p)
            weights.append(w)
        if not candidates:
            raise AssertionError(f"no {phase} candidate policy for {product}/{claim_type}")

        p = candidates[int(r.choice(len(candidates), p=np.array(weights) / sum(weights)))]
        if force_pending:
            service = C.EXTRACT_END
            submit = C.EXTRACT_END
        else:
            a = max(lo, p["start_date"])
            b = min(hi, p["end_date"] or hi)
            service = random_date(r, a, b)
            submit = min(b, service + timedelta(days=int(r.integers(0, 15))))

        rows.append(
            {
                "_tmp": f"X{phase}{i + 1:04d}",
                "phase": phase,
                "_force_pending": force_pending,
                "policy_id": p["policy_id"],
                "customer_id": p["customer_id"],
                "product_line": product,
                "claim_type": claim_type,
                "service_date": service,
                "claim_date": submit,
            }
        )
    return pd.DataFrame(rows)


def _next_due_after(policy: dict[str, Any], after: date) -> date | None:
    dates = premium_due_dates(policy, after, C.EXTRACT_END)
    return dates[0] if dates else None


def apply_retention(
    customers: pd.DataFrame,
    policies: pd.DataFrame,
    premiums_a: pd.DataFrame,
    claims_a: pd.DataFrame,
    payments_a: pd.DataFrame,
    r: np.random.Generator,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """S7: compute snapshot features, sample churn, then realize Phase-B policy state."""
    p = policies.copy()
    active_snapshot = p[
        (p["policy_type"].isin(["health", "dental"]))
        & (p["start_date"] <= C.SNAPSHOT_DATE)
        & (p["end_date"].isna() | (p["end_date"] > C.SNAPSHOT_DATE))
    ].copy()
    eligible = sorted(active_snapshot["customer_id"].unique().tolist())

    replacement_rows = p[p["_replacement_case"] == True]  # noqa: E712
    replacement_customers = set(replacement_rows["customer_id"].tolist())

    # Reconstruct premium behaviour exactly as it was observable at the
    # retention snapshot. Payments after the snapshot must not influence the
    # churn-generating features.
    premiums_snapshot = premiums_a.copy()
    premiums_snapshot["snapshot_status"] = premiums_snapshot.apply(
        lambda row: premium_status_as_of(
            row["due_date"],
            row["paid_date"],
            C.SNAPSHOT_DATE,
        ),
        axis=1,
    )
    prem_obs = premiums_snapshot[
        (premiums_snapshot["due_date"] >= C.OBS_START)
        & (premiums_snapshot["due_date"] <= C.SNAPSHOT_DATE)
    ]
    prem_90 = premiums_snapshot[
        (premiums_snapshot["due_date"] > C.SNAPSHOT_DATE - timedelta(days=90))
        & (premiums_snapshot["due_date"] <= C.SNAPSHOT_DATE)
    ]
    denied_customers = set(
        claims_a[
            (claims_a["claim_date"] >= C.OBS_START)
            & (claims_a["claim_date"] <= C.SNAPSHOT_DATE)
            & (claims_a["claim_status"] == "denied")
        ]["customer_id"]
    )

    cmap = customers.set_index("customer_id").to_dict("index")
    band_order = {b[0]: i for i, b in enumerate(C.AGE_BANDS)}
    rows = []
    uniforms = {cid: float(r.random()) for cid in eligible}
    for cid in eligible:
        cp = prem_obs[prem_obs["customer_id"] == cid]
        cp90 = prem_90[prem_90["customer_id"] == cid]
        missed90 = int((cp90["snapshot_status"] == "missed").any())
        late2 = int((cp["snapshot_status"] == "late").sum() >= 2)
        denied = int(cid in denied_customers)
        short = int((C.SNAPSHOT_DATE - cmap[cid]["customer_since"]).days < 365)
        bands = [band_order[x] for x in cp.sort_values("due_date")["age_band"].tolist()]
        band_increase = int(bool(bands) and max(bands) > min(bands))
        z = (
            C.CHURN_COEFS["intercept"]
            + C.CHURN_COEFS["missed_90d"] * missed90
            + C.CHURN_COEFS["late_2plus"] * late2
            + C.CHURN_COEFS["denied_12m"] * denied
            + C.CHURN_COEFS["short_tenure"] * short
            + C.CHURN_COEFS["age_band_increase"] * band_increase
        )
        prob = min(0.70, max(0.03, 1.0 / (1.0 + math.exp(-z))))
        churned = int(uniforms[cid] < prob and cid not in replacement_customers)
        rows.append(
            {
                "customer_id": cid,
                "had_missed_premium_last_90d": missed90,
                "had_2plus_late_premiums_12m": late2,
                "had_denied_claim_12m": denied,
                "tenure_under_365d": short,
                "age_band_increase_12m": band_increase,
                "p_churn": round(prob, 6),
                "replacement_case": int(cid in replacement_customers),
                "churned": churned,
            }
        )
    retention = pd.DataFrame(rows)

    # Reserved replacement negative cases: all snapshot-active HD policies terminate,
    # then the future replacement starts. Customer churn remains 0.
    for _, repl in replacement_rows.iterrows():
        cid = repl["customer_id"]
        cutoff = repl["start_date"] - timedelta(days=1)
        mask = (
            (p["customer_id"] == cid)
            & (p["policy_type"].isin(["health", "dental"]))
            & (p["start_date"] <= C.SNAPSHOT_DATE)
            & (p["end_date"].isna() | (p["end_date"] > C.SNAPSHOT_DATE))
        )
        p.loc[mask, "end_date"] = cutoff
        p.loc[mask, "status"] = "cancelled"

    # Realize sampled churn. All snapshot-active HD policies terminate; at most one
    # suitable monthly policy lapses, the rest cancel.
    churners = retention.loc[retention["churned"] == 1, "customer_id"].tolist()
    premiums_by_policy = premiums_snapshot.groupby("policy_id")
    for cid in churners:
        idxs = p.index[
            (p["customer_id"] == cid)
            & (p["policy_type"].isin(["health", "dental"]))
            & (p["start_date"] <= C.SNAPSHOT_DATE)
            & (p["end_date"].isna() | (p["end_date"] > C.SNAPSHOT_DATE))
        ].tolist()
        lapse_idx = None
        lapse_end = None
        forced_due = None

        if r.random() < 0.70:
            # Prefer an already-observed missed instalment within 90 days.
            for idx in idxs:
                pid = p.at[idx, "policy_id"]
                if pid not in premiums_by_policy.groups:
                    continue
                pp = premiums_by_policy.get_group(pid)
                missed = pp[
                    (pp["snapshot_status"] == "missed")
                    & (pp["due_date"] > C.SNAPSHOT_DATE - timedelta(days=90))
                    & (pp["due_date"] <= C.SNAPSHOT_DATE)
                ]
                if len(missed):
                    due = missed.sort_values("due_date").iloc[-1]["due_date"]
                    earliest = max(C.OUTCOME_START, due + timedelta(days=30))
                    latest = min(C.EXTRACT_END, due + timedelta(days=60))
                    if earliest <= latest:
                        lapse_idx = idx
                        lapse_end = random_date(r, earliest, latest)
                        break

            # Otherwise create the cause in Phase B, but only on a cadence that
            # allows 30-60 days between missed instalment and lapse by extract end.
            if lapse_idx is None:
                for idx in idxs:
                    row = p.loc[idx].to_dict()
                    if row["_freq"] != "monthly":
                        continue
                    due = _next_due_after(row, C.SNAPSHOT_DATE)
                    if due is not None and due + timedelta(days=30) <= C.EXTRACT_END:
                        lapse_idx = idx
                        forced_due = due
                        latest = min(C.EXTRACT_END, due + timedelta(days=60))
                        lapse_end = random_date(r, due + timedelta(days=30), latest)
                        break

        cancel_anchor = random_date(r, date(2026, 4, 20), date(2026, 6, 20))
        for idx in idxs:
            if idx == lapse_idx:
                p.at[idx, "end_date"] = lapse_end
                p.at[idx, "status"] = "lapsed"
                p.at[idx, "_forced_missed_due"] = forced_due
            else:
                end = min(C.EXTRACT_END, max(C.OUTCOME_START, cancel_anchor + timedelta(days=int(r.integers(-10, 11)))))
                p.at[idx, "end_date"] = end
                p.at[idx, "status"] = "cancelled"

    return p, retention
