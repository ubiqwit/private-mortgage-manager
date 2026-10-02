"""Match bank statement lines to mortgages and record them as mortgage transactions."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import timedelta
from decimal import Decimal

from .. import db
from ..models import OPEN_STATUSES, BankTransaction, Mortgage, MortgageTransaction
from . import calc, ledger

HIGH, MEDIUM = 80, 45
ZERO = Decimal("0.00")
# Words that say nothing about who paid.
STOPWORDS = {
    "inc", "ltd", "corp", "corporation", "holdings", "limited", "the", "and", "co", "company", "dev", "group",
    "e-transfer", "etransfer", "transfer", "deposit", "payment", "mortgage", "autodeposit", "interac",
    "mobile", "cheque", "online", "branch", "credit", "debit", "from", "to", "for",
}


def normalise(text: str) -> str:
    """Upper-case, drop tokens containing digits (reference numbers change every time)."""
    tokens = re.split(r"[\s/,;:#*]+", (text or "").upper())
    return " ".join(t for t in tokens if t and not re.search(r"\d", t))


def name_tokens(name: str) -> list[str]:
    words = re.findall(r"[A-Za-z][A-Za-z'\-]+", name or "")
    return [w.upper() for w in words if len(w) >= 3 and w.lower() not in STOPWORDS]


@dataclass
class Suggestion:
    mortgage: Mortgage
    score: int
    txn_type: str
    amount: Decimal
    reasons: list[str] = field(default_factory=list)
    existing: MortgageTransaction | None = None  # manually recorded twin to link instead of duplicating

    @property
    def confidence(self):
        return "high" if self.score >= HIGH else "medium" if self.score >= MEDIUM else "low"


def remaining(bank: BankTransaction) -> Decimal:
    return calc.money(abs(Decimal(str(bank.amount))) - bank.allocated)


def candidate_mortgages():
    return Mortgage.query.filter(Mortgage.status.in_(OPEN_STATUSES + ("paid_out",))).all()


def score(bank: BankTransaction, m: Mortgage, prime=None) -> Suggestion | None:
    amount = remaining(bank)
    desc = normalise(bank.description)
    reasons = []
    pts = 0
    deposit = Decimal(str(bank.amount)) > 0

    if m.status == "paid_out":
        last = m.last_payment()
        if not last or (bank.date - last.date).days > 60:
            return None
    if bank.date < m.funded_date - timedelta(days=10):
        return None

    # --- who paid -------------------------------------------------------
    padded = f" {desc} "
    keyword_hit = next((k for k in m.keywords if normalise(k) and f" {normalise(k)} " in padded), None)
    if keyword_hit:
        pts += 55
        reasons.append(f"description contains “{keyword_hit}”")
    else:
        hits = [t for t in name_tokens(m.borrower_name) if re.search(rf"\b{re.escape(t)}\b", desc)]
        if hits:
            pts += 40 if len(hits) > 1 else 25
            reasons.append("borrower name in description")

    # --- how much -----------------------------------------------------------
    txn_type = "payment" if deposit else "advance"
    if deposit:
        payment = m.regular_payment(prime)
        balance = m.balance(as_of=bank.date)
        if payment and amount == payment:
            pts += 40
            reasons.append("exact scheduled payment amount")
        elif payment and abs(amount - payment) <= max(payment * Decimal("0.01"), Decimal("1.00")):
            pts += 28
            reasons.append("within 1% of the scheduled payment")
        elif payment and amount > payment and (amount % payment == 0) and amount / payment <= 6:
            pts += 20
            reasons.append(f"exactly {int(amount / payment)} scheduled payments (catch-up)")
        elif balance > 0 and amount >= balance:
            pts += 30
            txn_type = "payout"
            reasons.append("covers the full balance — looks like a payout")
        elif m.nsf_fee and amount == calc.money(m.nsf_fee):
            pts += 15
            txn_type = "fee"
            reasons.append("equals the NSF fee")
        elif m.renewal_fee and amount == calc.money(m.renewal_fee):
            pts += 15
            txn_type = "fee"
            reasons.append("equals the renewal fee")
        # --- when ---------------------------------------------------------------
        if txn_type == "payment":
            near = [d for d in m.due_dates(start=bank.date - timedelta(days=7), end=bank.date + timedelta(days=7))]
            if near:
                pts += 10
                reasons.append(f"near due date {near[0]:%b %d}")
    else:
        net = calc.money(Decimal(str(m.principal_amount)) - Decimal(str(m.lender_fee or 0)))
        if amount in (calc.money(m.principal_amount), net) and abs((bank.date - m.funded_date).days) <= 7:
            pts += 60
            reasons.append("matches the advance on the funding date")
        else:
            return None

    existing = find_manual_twin(m, bank, amount)
    if existing is not None:
        pts += 15
        txn_type = existing.type
        reasons.append(f"already recorded by hand on {existing.date:%b %d} — will link it, not duplicate")

    if pts < 20:
        return None
    return Suggestion(m, pts, txn_type, amount, reasons, existing)


def find_manual_twin(m: Mortgage, bank: BankTransaction, amount: Decimal):
    """A transaction entered by hand (no bank line yet) that this bank line evidences."""
    want_advance = Decimal(str(bank.amount)) < 0
    for t in m.transactions:
        if t.bank_transaction is not None or t.bank_transaction_id is not None:
            continue
        if (t.type == "advance") != want_advance:
            continue
        if calc.money(t.amount) == amount and abs((t.date - bank.date).days) <= 7:
            return t
    return None


def suggestions(bank: BankTransaction, mortgages=None, prime=None, limit=3) -> list[Suggestion]:
    mortgages = mortgages if mortgages is not None else candidate_mortgages()
    out = [s for s in (score(bank, m, prime) for m in mortgages) if s]
    out.sort(key=lambda s: s.score, reverse=True)
    # A clear winner only counts as "high" when it is not tied with another mortgage.
    if len(out) > 1 and out[0].score >= HIGH and out[1].score >= out[0].score - 10:
        out[0].score = HIGH - 1
    return out[:limit]


def learn_keyword(m: Mortgage, description: str):
    """Remember the stable part of a bank description for future matches."""
    key = normalise(description)
    # Generic lines ("MOBILE CHEQUE DEPOSIT") would match everyone — only learn identifying text.
    if not key or not any(t.lower() not in STOPWORDS and len(t) >= 3 for t in key.split()):
        return
    existing = [normalise(k) for k in m.keywords]
    if key in existing:
        return
    m.match_keywords = "\n".join(m.keywords + [key])


def allocate(bank: BankTransaction, m: Mortgage, txn_type: str, amount=None, split=None,
             remember=True, prime=None, notes=None, existing: MortgageTransaction | None = None) -> MortgageTransaction:
    """Record (part of) a bank line against a mortgage, or link it to a manually entered twin."""
    if existing is not None:
        if existing.mortgage_id != m.id or existing.bank_transaction is not None:
            raise ValueError("That transaction is already linked to a bank line")
        if calc.money(existing.amount) > remaining(bank):
            raise ValueError(f"Only {remaining(bank)} is left to allocate on this bank line")
        existing.bank_transaction = bank
        bank.status = "matched" if remaining(bank) <= 0 else "unmatched"
        if remember:
            learn_keyword(m, bank.description)
        return existing
    amount = calc.money(amount if amount is not None else remaining(bank))
    if amount <= 0:
        raise ValueError("Nothing left to allocate on this bank line")
    if amount > remaining(bank):
        raise ValueError(f"Only {remaining(bank)} is left to allocate on this bank line")
    split = split or ledger.suggest_split(m, amount, bank.date, txn_type, prime)
    txn = MortgageTransaction(
        date=bank.date, type=txn_type, amount=amount, interest=split["interest"], principal=split["principal"],
        fees=split["fees"], bank_transaction=bank, notes=notes, source="bank",
    )
    txn.mortgage = m
    db.session.add(txn)
    bank.status = "matched" if remaining(bank) <= 0 else "unmatched"
    if remember and txn_type in ("payment", "payout", "prepayment", "fee"):
        learn_keyword(m, bank.description)
    if txn_type == "payout" and m.balance() <= 0:
        m.status = "paid_out"
    return txn


def unmatch(bank: BankTransaction, keep_linked_manual=True):
    """Detach a bank line. Transactions created from it are deleted; ones that were
    entered by hand and only linked to it are kept (just unlinked)."""
    for t in list(bank.mortgage_transactions):
        m = t.mortgage
        bank.mortgage_transactions.remove(t)
        if keep_linked_manual and t.source == "manual":
            continue  # entered by hand and only linked: keep it, just unlinked
        m.transactions.remove(t)
        db.session.delete(t)
        reopen_if_needed(m)
    bank.status = "unmatched"


def reopen_if_needed(m: Mortgage):
    """A paid-out mortgage whose payout was removed is open again."""
    if m.status == "paid_out" and m.balance() > 0:
        m.status = "active"


def auto_match(lines, prime=None) -> int:
    """Accept every high-confidence suggestion. Returns the number matched."""
    mortgages = candidate_mortgages()
    matched = 0
    for bank in lines:
        if bank.status != "unmatched":
            continue
        sugg = suggestions(bank, mortgages, prime, limit=2)
        if sugg and sugg[0].confidence == "high":
            allocate(bank, sugg[0].mortgage, sugg[0].txn_type, prime=prime, existing=sugg[0].existing)
            db.session.flush()
            matched += 1
    return matched
