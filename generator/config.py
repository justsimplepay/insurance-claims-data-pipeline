from __future__ import annotations

from dataclasses import dataclass
from datetime import date

MASTER_SEED = 20260923
HISTORY_START = date(2023, 7, 1)
OBS_START = date(2025, 4, 1)
SNAPSHOT_DATE = date(2026, 3, 31)
OUTCOME_START = date(2026, 4, 1)
EXTRACT_END = date(2026, 6, 30)

BASE_CUSTOMERS = 150
BASE_POLICIES = 180
BASE_CLAIMS = 210
PHASE_A_CLAIMS = 175
PHASE_B_CLAIMS = 35
JSON_FILES_TOTAL = 200

STAGE_IDS = {
    "customers": 1, "policies": 2, "premiums_a": 3, "claims_a": 4,
    "details_a": 5, "adjudication_a": 6, "retention": 7, "premiums_b": 8,
    "claims_b": 9, "details_b": 10, "adjudication_b": 11, "raw_shape": 20, "defects": 21,
}

PROVINCES = {
    "SK": {"share": 0.35, "postal": ["S"], "area": ["306", "639", "474"], "cities": ["Regina", "Saskatoon", "Moose Jaw", "Prince Albert", "Swift Current", "Yorkton"]},
    "AB": {"share": 0.20, "postal": ["T"], "area": ["403", "780", "587", "825"], "cities": ["Calgary", "Edmonton", "Red Deer", "Lethbridge"]},
    "MB": {"share": 0.15, "postal": ["R"], "area": ["204", "431"], "cities": ["Winnipeg", "Brandon", "Steinbach"]},
    "ON": {"share": 0.15, "postal": ["K", "L", "M", "N", "P"], "area": ["416", "647", "613", "905", "519"], "cities": ["Toronto", "Ottawa", "Hamilton", "London"]},
    "BC": {"share": 0.08, "postal": ["V"], "area": ["604", "250", "778"], "cities": ["Vancouver", "Victoria", "Kelowna"]},
    "NS": {"share": 0.025, "postal": ["B"], "area": ["902", "782"], "cities": ["Halifax"]},
    "PE": {"share": 0.015, "postal": ["C"], "area": ["902"], "cities": ["Charlottetown"]},
    "NL": {"share": 0.015, "postal": ["A"], "area": ["709"], "cities": ["St. John's"]},
    "YT": {"share": 0.0075, "postal": ["Y"], "area": ["867"], "cities": ["Whitehorse"]},
    "NT": {"share": 0.0075, "postal": ["X"], "area": ["867"], "cities": ["Yellowknife"]},
}
UNSERVED = {
    "QC": {"postal": ["G", "H", "J"], "city": "Montreal", "area": "514"},
    "NB": {"postal": ["E"], "city": "Moncton", "area": "506"},
    "NU": {"postal": ["X"], "city": "Iqaluit", "area": "867"},
}
AGE_BANDS = [
    ("Under 35", 0, 34, 1.00), ("35-44", 35, 44, 1.15), ("45-54", 45, 54, 1.40),
    ("55-64", 55, 64, 1.80), ("65-74", 65, 74, 2.40), ("75+", 75, 120, 3.20),
]
COVERAGE_FACTOR = {"single": 1.00, "couple": 1.90, "family": 2.60}
COVERAGE_SHARE = {"single": 0.55, "couple": 0.25, "family": 0.20}
SALES_SHARE = {"online": 0.45, "broker": 0.35, "call_centre": 0.20}

PLAN_COUNTS = {"BasicPlan": 30, "ExtendaPlan": 30, "OmniPlan": 24, "Replacement Health": 15, "Dental Basic": 32, "Dental Plus": 22, "TravelStar": 16, "StudentPlan": 11}
PLANS = {
    "BasicPlan": {"type": "health", "coverage": 1500.00, "deductibles": [0.00], "base": 45.00, "freq": ["monthly", "quarterly", "yearly"]},
    "ExtendaPlan": {"type": "health", "coverage": 3000.00, "deductibles": [0.00, 100.00], "base": 85.00, "freq": ["monthly", "quarterly", "yearly"]},
    "OmniPlan": {"type": "health", "coverage": 5000.00, "deductibles": [0.00, 100.00, 250.00], "base": 140.00, "freq": ["monthly", "quarterly", "yearly"]},
    "Replacement Health": {"type": "health", "coverage": 2000.00, "deductibles": [0.00], "base": 120.00, "freq": ["monthly"]},
    "Dental Basic": {"type": "dental", "coverage": 1000.00, "deductibles": [0.00, 50.00], "base": 35.00, "freq": ["monthly", "quarterly", "yearly"]},
    "Dental Plus": {"type": "dental", "coverage": 2000.00, "deductibles": [0.00, 50.00], "base": 60.00, "freq": ["monthly", "quarterly", "yearly"]},
    "TravelStar": {"type": "travel", "coverage": 5_000_000.00, "deductibles": [0.00, 250.00, 500.00], "base": None, "freq": ["single"]},
    "StudentPlan": {"type": "travel", "coverage": 2_000_000.00, "deductibles": [0.00], "base": 70.00, "freq": ["monthly", "yearly"]},
}
CLAIM_COUNTS = {
    "health": {"prescription_drugs": 41, "health_practitioner": 41, "vision": 14, "hearing_aids": 3, "medical_equipment": 8, "ambulance": 5, "hospital_cash": 4},
    "dental": {"preventive": 35, "basic": 22, "major": 6},
    "travel": {"emergency_medical": 14, "trip_cancellation": 8, "trip_interruption": 3, "baggage": 6},
}
PHASE_A_PRODUCT_COUNTS = {"health": 96, "dental": 52, "travel": 27}
PHASE_B_PRODUCT_COUNTS = {"health": 20, "dental": 11, "travel": 4}
LOGNORMAL = {
    "prescription_drugs": (4.0, 0.8), "health_practitioner": (4.9, 0.5), "vision": (5.3, 0.4),
    "hearing_aids": (7.3, 0.3), "medical_equipment": (5.8, 0.7), "ambulance": (5.9, 0.4),
    "hospital_cash": (5.0, 0.5), "preventive": (5.3, 0.3), "basic": (5.7, 0.5), "major": (7.0, 0.5),
    "emergency_medical": (7.5, 1.2), "trip_cancellation": (7.3, 0.6), "trip_interruption": (7.0, 0.6), "baggage": (6.3, 0.5),
}
PROVIDER_TYPES = {
    "prescription_drugs": ["pharmacy"], "health_practitioner": ["clinic", "practitioner_office"], "vision": ["optical_store"],
    "hearing_aids": ["hearing_clinic"], "medical_equipment": ["medical_supplier"], "ambulance": ["ambulance_service"],
    "hospital_cash": ["hospital"], "preventive": ["dental_clinic"], "basic": ["dental_clinic"], "major": ["dental_clinic"],
    "emergency_medical": ["foreign_hospital", "foreign_clinic", "hospital"], "trip_cancellation": ["travel_supplier"],
    "trip_interruption": ["travel_supplier"], "baggage": ["airline"],
}
REQUIRED_DOCS = {
    "prescription_drugs": ["receipt", "prescription"], "health_practitioner": ["receipt"], "vision": ["receipt", "prescription"],
    "hearing_aids": ["receipt", "referral"], "medical_equipment": ["receipt", "referral"], "ambulance": ["invoice"],
    "hospital_cash": ["hospital_discharge_summary"], "preventive": ["receipt"], "basic": ["receipt"], "major": ["receipt", "treatment_plan", "xray"],
    "emergency_medical": ["itemized_invoice", "medical_report", "proof_of_travel"],
    "trip_cancellation": ["cancellation_invoice", "booking_confirmation", "supporting_statement"],
    "trip_interruption": ["receipt", "booking_confirmation", "supporting_statement"],
    "baggage": ["baggage_irregularity_report", "receipt", "proof_of_travel"],
}
TRAVEL_INCIDENT = {"emergency_medical": "medical", "trip_cancellation": "cancellation", "trip_interruption": "interruption", "baggage": "baggage"}
TRAVEL_SUB_LIMIT = {"trip_interruption": 5000.0, "baggage": 1000.0}
TRAVEL_DESTINATIONS = [
    ("US", "USD", 1.33, 1.40, 0.45), ("MX", "MXN", 0.070, 0.082, 0.15), ("FR", "EUR", 1.45, 1.52, 0.08),
    ("IT", "EUR", 1.45, 1.52, 0.06), ("GB", "GBP", 1.70, 1.78, 0.08), ("JP", "JPY", 0.0090, 0.0098, 0.04),
    ("CA", "CAD", 1.00, 1.00, 0.14),
]
SUBMISSION_CHANNELS = {"online_portal": 0.45, "mobile_app": 0.25, "provider_direct_billing": 0.22, "mail": 0.08}
PREMIUM_METHODS = ["pad", "credit_card", "cheque"]
CLAIM_PAYMENT_METHODS = ["direct_deposit", "cheque"]
PRACTITIONERS = ["physiotherapist", "chiropractor", "massage_therapist", "psychologist", "naturopath", "optometrist"]
PAYMENT_RELIABILITY = {"good": 0.70, "mixed": 0.25, "poor": 0.05}
CLAIM_PROPENSITY = {"low": 0.35, "normal": 0.50, "high": 0.15}
PREMIUM_STATUS_PROBS = {"good": (0.96, 0.04, 0.00), "mixed": (0.82, 0.14, 0.04), "poor": (0.55, 0.25, 0.20)}
AGE_FREQ_MULT = {"Under 35": 0.80, "35-44": 0.90, "45-54": 1.00, "55-64": 1.15, "65-74": 1.30, "75+": 1.40}
PROVINCE_FREQ_MULT = {"SK": 1.00, "AB": 1.05, "MB": 0.95, "ON": 1.10, "BC": 1.15, "NS": 1.00, "PE": 1.00, "NL": 1.00, "YT": 1.00, "NT": 1.00}
PLAN_FREQ_MULT = {"BasicPlan": 0.90, "ExtendaPlan": 1.00, "OmniPlan": 1.15, "Replacement Health": 1.20, "Dental Basic": 0.90, "Dental Plus": 1.10, "TravelStar": 1.00, "StudentPlan": 1.00}
PROPENSITY_MULT = {"low": 0.65, "normal": 1.00, "high": 1.60}
SIGNAL_TARGETS = {"F1": 12, "F2": 6, "F3": 8, "F4_CLUSTERS": 4, "F5": 24, "F6": 5, "F7": 10, "F8": 12, "F9": 4, "OUTLIER": 3}
ADJUDICATION_BASE = {"approved": 0.72, "partially_approved": 0.18, "denied": 0.10}
QUEUE_GAMMA = (2.0, 0.75)
HANDLING_GAMMA = (2.5, 1.20)
SLOW_MULT = {"province_outside_sk": 1.15, "mail": 1.75, "travel": 1.80, "missing_docs": 1.60, "slow_adjuster": 1.80}
MAX_TIMING_MULT = 3.0
CHURN_COEFS = {"intercept": -2.40, "missed_90d": 1.40, "late_2plus": 1.50, "denied_12m": 0.80, "short_tenure": 0.50, "age_band_increase": 0.35}
DIRTY_POOLS = {"Customer.csv": 0.10, "Policy.csv": 0.10, "Claim.csv": 0.08, "Claim_Payment.csv": 0.08, "Policy_Premium.csv": 0.05, "json": 0.10}
ALNUM_POSTAL_LETTERS = list("ABCEGHJKLMNPRSTVWXYZ")

@dataclass(frozen=True)
class Paths:
    raw_dir: str = "data/raw"
    json_dir: str = "data/raw/json"
