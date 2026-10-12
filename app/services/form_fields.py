"""Which optional fields each user sees on the mortgage form.

Required fields are always shown. Hidden fields keep whatever value they already
have: the form simply doesn't send them, and saving only changes fields it sends.
"""
from __future__ import annotations

import json

# (section, [(field, label), …]) in form order.
GROUPS = [
    ("Borrower", [("reference", "Reference"), ("borrower_name", "Borrower name")]),
    ("Security (property)", [
        ("property_address", "Street address"), ("property_city", "City"), ("property_value", "Property value"),
        ("position", "Charge position"), ("prior_charges", "Prior charges balance"),
    ]),
    ("Loan terms", [
        ("principal_amount", "Principal advanced"), ("rate_type", "Rate type (fixed / variable)"),
        ("interest_rate", "Interest rate"), ("prime_spread", "Spread over prime"), ("rate_floor", "Rate floor"),
        ("compounding", "Compounding"), ("payment_type", "Payment type"), ("payment_frequency", "Payment frequency"),
        ("amortization_months", "Amortization"), ("payment_amount", "Payment amount"),
        ("funded_date", "Funded date"), ("first_payment_date", "First payment date"), ("term_months", "Term (months)"),
        ("maturity_date", "Maturity date"), ("mortgagees", "Mortgagees (who owns it, with %)"), ("status", "Status"),
    ]),
    ("Fees & broker", [("lender_fee", "Lender fee"), ("broker_name", "Broker / group")]),
    ("Bank matching & notes", [("match_keywords", "Bank description keywords"), ("notes", "Notes")]),
]
ALL_FIELDS = [f for _, fields in GROUPS for f, _ in fields]
# Always shown: the app can't create a mortgage without them.
REQUIRED = {"borrower_name", "property_address", "principal_amount", "interest_rate", "funded_date", "term_months"}

SIMPLE = REQUIRED | {"property_city", "property_value", "position", "lender_fee", "mortgagees", "notes"}
STANDARD = SIMPLE | {
    "prior_charges", "rate_type", "prime_spread", "rate_floor", "payment_type", "payment_frequency",
    "amortization_months", "maturity_date", "status", "broker_name", "match_keywords",
}
PRESETS = {
    "simple": ("Simple", "Just the essentials: borrower, address, amount, rate, dates, lender fee, mortgagees", SIMPLE),
    "standard": ("Standard", "Adds prior charges, rate type, payment options, broker and status", STANDARD),
    "everything": ("Everything", "Every field the app supports", set(ALL_FIELDS)),
}


def visible_fields(user) -> set[str]:
    """The fields this user sees. Never chosen = everything (the original form)."""
    raw = getattr(user, "form_fields", None)
    if not raw:
        return set(ALL_FIELDS)
    try:
        chosen = set(json.loads(raw))
    except (ValueError, TypeError):
        return set(ALL_FIELDS)
    if "owners" in chosen:  # the field's earlier name
        chosen.add("mortgagees")
    return (chosen & set(ALL_FIELDS)) | REQUIRED


def save_fields(user, fields) -> None:
    chosen = (set(fields) & set(ALL_FIELDS)) | REQUIRED
    user.form_fields = json.dumps([f for f in ALL_FIELDS if f in chosen])


def preset_name(user) -> str | None:
    visible = visible_fields(user)
    for key, (_, _, fields) in PRESETS.items():
        if visible == fields:
            return key
    return None
