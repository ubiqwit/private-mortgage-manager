"""Mortgagees: who owns each mortgage, and their percentage of it."""
from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

from .. import db
from ..models import Mortgagee

HUNDRED = Decimal(100)
_SHARE = re.compile(r"^(?P<name>.*?)[\s:=-]*(?P<pct>\d+(?:\.\d+)?)\s*%$")


class MortgageeError(ValueError):
    pass


def _pct(raw):
    raw = str(raw if raw is not None else "").replace("%", "").strip()
    if raw == "":
        return None
    try:
        return Decimal(raw)
    except InvalidOperation:
        raise MortgageeError(f"Mortgagee share '{raw}' is not a number") from None


def complete(pairs):
    """Validate [(name, pct or None)] and fill in blank shares.

    Blank shares split whatever the others leave of 100% (one mortgagee alone owns 100%).
    Shares may add up to less than 100% (the rest is simply unassigned) but not more.
    """
    pairs = [(" ".join((name or "").split()), _pct(pct)) for name, pct in pairs]
    pairs = [(name, pct) for name, pct in pairs if name or pct is not None]
    for name, pct in pairs:
        if not name:
            raise MortgageeError("Every mortgagee share needs a name")
        if len(name) > 200:
            raise MortgageeError(f"Mortgagee name is too long: {name[:40]}…")
        if pct is not None and not (0 < pct <= HUNDRED):
            raise MortgageeError(f"{name}: share must be more than 0% and at most 100%")
    given = sum((pct for _, pct in pairs if pct is not None), Decimal(0))
    blanks = [i for i, (_, pct) in enumerate(pairs) if pct is None]
    if blanks:
        left = HUNDRED - given
        if left <= 0:
            raise MortgageeError("Enter a share for every mortgagee — the others already add up to 100%")
        each = (left / len(blanks)).quantize(Decimal("0.01"))
        for n, i in enumerate(blanks):
            share = left - each * (len(blanks) - 1) if n == len(blanks) - 1 else each
            pairs[i] = (pairs[i][0], share)
    total = sum((pct for _, pct in pairs), Decimal(0))
    if total > HUNDRED:
        raise MortgageeError(f"Mortgagee shares add up to {total.normalize():f}% — they can't exceed 100%")
    names = [name.lower() for name, _ in pairs]
    if len(set(names)) != len(names):
        raise MortgageeError("The same mortgagee is listed twice")
    return pairs


def parse_text(text):
    """'9929916 Canada Inc 50%; Suresh Malhotra 50%' -> [(name, pct)].

    Separate mortgagees with ';' (or ',' when each part has its own %). A name without a
    share gets what's left of 100%.
    """
    text = (text or "").strip()
    if not text:
        return []
    parts = [p.strip() for p in text.split(";")]
    if len(parts) == 1:
        commas = [p.strip() for p in text.split(",")]
        if len(commas) > 1 and all(_SHARE.match(p) for p in commas):
            parts = commas
    pairs = []
    for part in parts:
        if not part:
            continue
        m = _SHARE.match(part)
        pairs.append((m.group("name"), m.group("pct")) if m and m.group("name").strip() else (part, None))
    return complete(pairs)


def from_form(form):
    """Rows posted by the mortgage form, or None if the form didn't include the section."""
    if "mortgagees_form" not in form:
        return None
    names, pcts = form.getlist("mortgagee_name"), form.getlist("mortgagee_pct")
    pcts += [""] * (len(names) - len(pcts))
    return complete(list(zip(names, pcts, strict=False)))


def assign(mortgage, pairs):
    """Replace a mortgage's mortgagees with [(name, pct)] (already validated)."""
    mortgage.mortgagees.clear()
    for i, (name, pct) in enumerate(pairs):
        mortgage.mortgagees.append(Mortgagee(name=name, share_pct=pct, sort_order=i))


def names_in_use():
    """Every mortgagee name on file, for the list filter and the form's suggestions."""
    rows = db.session.query(Mortgagee.name).distinct()
    return sorted({name for (name,) in rows}, key=str.lower)
