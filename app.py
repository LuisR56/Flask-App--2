from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Tuple

from flask import Flask, jsonify, render_template, request

app = Flask(__name__)

DATA_PATH = os.path.join(os.path.dirname(__file__), "data", "finance.json")


# -----------------------------
# Tax tables (2025) - simplified
# -----------------------------
# Federal: IRS Rev. Proc. 2024-40 (tax year 2025 inflation adjustments)
# We implement marginal brackets for:
# - Single
# - Married Filing Jointly
# - Head of Household
#
# Standard deduction: we use the IRS announcement values for tax year 2025.
# (Press release / announcement)  :contentReference[oaicite:0]{index=0}
#
# NOTE: This is a simplified estimator. It does not handle:
# - credits, AMT, additional medicare tax, NIIT
# - pre-tax deductions (401k/HSA), itemized deductions
# - dependents, special cases
#
FED_STANDARD_DEDUCTION_2025 = {
    "single": 15000,
    "mfj": 30000,
    "hoh": 22500,
}

# Brackets: (upper_limit, rate) over taxable income
# Values are derived from IRS Rev. Proc. 2024-40 tables (2025). :contentReference[oaicite:1]{index=1}
FED_BRACKETS_2025 = {
    "single": [
        (11925, 0.10),
        (48475, 0.12),
        (103350, 0.22),
        (197300, 0.24),
        (250525, 0.32),
        (626350, 0.35),
        (float("inf"), 0.37),
    ],
    "mfj": [
        (23850, 0.10),
        (96950, 0.12),
        (206700, 0.22),
        (394600, 0.24),
        (501050, 0.32),
        (751600, 0.35),
        (float("inf"), 0.37),
    ],
    "hoh": [
        (17000, 0.10),
        (64850, 0.12),
        (103350, 0.22),
        (197300, 0.24),
        (250500, 0.32),
        (626350, 0.35),
        (float("inf"), 0.37),
    ],
}

# State income tax (demo set):
# - CA: use 2025 CA Tax Rate Schedules PDF (FTB) for marginal brackets (single only here). :contentReference[oaicite:2]{index=2}
# - NY: tax rate schedule in IT-201 instructions for 2025 (single only here). :contentReference[oaicite:3]{index=3}
# - WA: no individual income tax (WA DOR). :contentReference[oaicite:4]{index=4}
# - FL: we treat as 0 state income tax; the Florida Constitution text is available via DOS PDF index (official). :contentReference[oaicite:5]{index=5}
#
# If you want all 50 states, we can extend the dataset; the UI/logic is already designed to support it.

STATE_BRACKETS_2025_SINGLE = {
    "CA": [
        (10756, 0.01),
        (25500, 0.02),
        (40245, 0.04),
        (55866, 0.06),
        (70606, 0.08),
        (360659, 0.093),
        (432787, 0.103),
        (720110, 0.113),
        (float("inf"), 0.123),
    ],
    # NYS (single) schedule for 2025 is documented in IT-201 instructions. :contentReference[oaicite:6]{index=6}
    "NY": [
        (8500, 0.04),
        (11700, 0.045),
        (13900, 0.0525),
        (80650, 0.055),
        (215400, 0.06),
        (1077550, 0.0685),
        (5000000, 0.0965),
        (25000000, 0.103),
        (float("inf"), 0.109),
    ],
    "WA": [(float("inf"), 0.0)],
    "FL": [(float("inf"), 0.0)],
}

SUPPORTED_STATES = [
    ("CA", "California"),
    ("NY", "New York"),
    ("WA", "Washington (no income tax)"),
    ("FL", "Florida (no income tax)"),
]


# -----------------------------
# JSON storage helpers
# -----------------------------
def _load_data() -> Dict[str, Any]:
    if not os.path.exists(DATA_PATH):
        os.makedirs(os.path.dirname(DATA_PATH), exist_ok=True)
        with open(DATA_PATH, "w", encoding="utf-8") as f:
            json.dump({"net_income_entries": [], "activity": []}, f, indent=2)
    with open(DATA_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def _save_data(data: Dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(DATA_PATH), exist_ok=True)
    with open(DATA_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _log_activity(data: Dict[str, Any], message: str) -> None:
    data.setdefault("activity", [])
    data["activity"].append({"ts": _now_iso(), "message": message})
    # keep a reasonable size
    data["activity"] = data["activity"][-50:]


# -----------------------------
# Tax math
# -----------------------------
def calc_marginal_tax(taxable_income: float, brackets: List[Tuple[float, float]]) -> float:
    tax = 0.0
    remaining = max(0.0, taxable_income)
    lower = 0.0
    for upper, rate in brackets:
        if remaining <= 0:
            break
        band = min(remaining, upper - lower)
        if band > 0:
            tax += band * rate
            remaining -= band
        lower = upper
    return tax


def estimate_federal_tax_2025(gross_annual: float, filing_status: str) -> Dict[str, float]:
    filing_status = filing_status.lower()
    if filing_status not in FED_BRACKETS_2025:
        filing_status = "single"

    standard_deduction = float(FED_STANDARD_DEDUCTION_2025[filing_status])
    taxable = max(0.0, gross_annual - standard_deduction)
    tax = calc_marginal_tax(taxable, FED_BRACKETS_2025[filing_status])
    return {
        "standard_deduction": standard_deduction,
        "taxable_income": taxable,
        "federal_tax": tax,
    }


def estimate_state_tax_2025_single(gross_annual: float, state_code: str) -> Dict[str, float]:
    state_code = state_code.upper()
    brackets = STATE_BRACKETS_2025_SINGLE.get(state_code, [(float("inf"), 0.0)])

    # For states with brackets, taxable income is simplified as gross income (no state deductions modeled).
    taxable = max(0.0, gross_annual)
    tax = calc_marginal_tax(taxable, brackets)
    return {
        "state_taxable_income": taxable,
        "state_tax": tax,
    }


def annualize(amount: float, frequency: str) -> float:
    freq = frequency.lower()
    if freq == "weekly":
        return amount * 52
    if freq == "bi-weekly" or freq == "biweekly":
        return amount * 26
    if freq == "monthly":
        return amount * 12
    return amount  # yearly


# -----------------------------
# Routes
# -----------------------------
@app.route("/")
def index():
    data = _load_data()
    last_activity = (data.get("activity") or [])[-1] if data.get("activity") else None
    last_entry = (data.get("net_income_entries") or [])[-1] if data.get("net_income_entries") else None
    return render_template("index.html", last_activity=last_activity, last_entry=last_entry)


@app.route("/net_income")
def net_income():
    data = _load_data()
    last_entry = (data.get("net_income_entries") or [])[-1] if data.get("net_income_entries") else None
    return render_template("net_income.html", last_entry=last_entry, states=SUPPORTED_STATES)


@app.route("/api/estimate", methods=["POST"])
def api_estimate():
    payload = request.get_json(force=True) or {}
    try:
        gross_annual = float(payload.get("gross_annual", 0))
    except (TypeError, ValueError):
        gross_annual = 0.0

    filing_status = (payload.get("filing_status") or "single").lower()
    state = (payload.get("state") or "WA").upper()

    fed = estimate_federal_tax_2025(gross_annual, filing_status)
    st = estimate_state_tax_2025_single(gross_annual, state)

    total_tax = fed["federal_tax"] + st["state_tax"]
    net_annual = max(0.0, gross_annual - total_tax)

    return jsonify(
        {
            "gross_annual": gross_annual,
            "filing_status": filing_status,
            "state": state,
            "federal": fed,
            "state_detail": st,
            "total_tax": total_tax,
            "net_annual": net_annual,
        }
    )


@app.route("/api/net_income", methods=["POST"])
def api_save_net_income():
    payload = request.get_json(force=True) or {}
    label = (payload.get("label") or "Net income").strip()[:60]

    try:
        net_amount = float(payload.get("net_amount", 0))
    except (TypeError, ValueError):
        net_amount = 0.0

    frequency = (payload.get("frequency") or "monthly").lower()
    if frequency not in {"weekly", "bi-weekly", "biweekly", "monthly", "yearly"}:
        frequency = "monthly"

    net_annual = annualize(net_amount, frequency)

    data = _load_data()
    entry = {
        "id": int(datetime.now(timezone.utc).timestamp() * 1000),
        "ts": _now_iso(),
        "label": label,
        "net_amount": net_amount,
        "frequency": frequency,
        "net_annual_equivalent": net_annual,
    }
    data.setdefault("net_income_entries", [])
    data["net_income_entries"].append(entry)
    data["net_income_entries"] = data["net_income_entries"][-100:]

    _log_activity(data, f"Saved net income: {net_amount:,.2f} ({frequency})")
    _save_data(data)

    return jsonify({"ok": True, "entry": entry})


@app.route("/api/net_income/latest", methods=["PUT"])
def api_update_latest_net_income():
    payload = request.get_json(force=True) or {}

    data = _load_data()
    entries = data.get("net_income_entries") or []
    if not entries:
        return jsonify({"ok": False, "error": "No net income entry to edit."}), 400

    latest = entries[-1]

    label = (payload.get("label") or latest.get("label") or "Net income").strip()[:60]
    try:
        net_amount = float(payload.get("net_amount", latest.get("net_amount", 0)))
    except (TypeError, ValueError):
        net_amount = float(latest.get("net_amount", 0))

    frequency = (payload.get("frequency") or latest.get("frequency") or "monthly").lower()
    if frequency not in {"weekly", "bi-weekly", "biweekly", "monthly", "yearly"}:
        frequency = "monthly"

    latest["label"] = label
    latest["net_amount"] = net_amount
    latest["frequency"] = frequency
    latest["net_annual_equivalent"] = annualize(net_amount, frequency)
    latest["ts_edited"] = _now_iso()

    _log_activity(data, f"Edited latest net income: {net_amount:,.2f} ({frequency})")
    _save_data(data)

    return jsonify({"ok": True, "entry": latest})


if __name__ == "__main__":
    app.run(debug=True)
