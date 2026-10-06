"""Deleting mortgages and users, keeping the bank reconciliation consistent."""
from __future__ import annotations

from .. import db
from ..models import AuditLog, Mortgage, MortgageActivity, MortgageDocument, PendingUpload, User
from .periods import PeriodClosed, ensure_open


def closed_month_problem(m: Mortgage) -> str | None:
    """Why ``m`` can't be deleted (history in a closed month), or None."""
    try:
        ensure_open(*(t.date for t in m.transactions), m.funded_date, action="delete a mortgage with history in closed months")
    except PeriodClosed as exc:
        return str(exc)
    return None


def delete_mortgage(m: Mortgage) -> None:
    """Delete a mortgage and everything recorded on it; matched bank lines go back to the queue."""
    for t in m.transactions:
        bank = t.bank_transaction
        if bank is not None:
            others = [x for x in bank.mortgage_transactions if x.mortgage_id != m.id]
            bank.status = "matched" if others else "unmatched"
    db.session.delete(m)


def mortgages_added_by(user: User) -> list[Mortgage]:
    return Mortgage.query.filter_by(created_by_id=user.id).order_by(Mortgage.reference).all()


def delete_user(user: User) -> int:
    """Delete ``user`` and every mortgage they added. Returns how many mortgages went.

    Their other records stay but no longer point at them: audit-log entries, notes and
    documents on other people's mortgages, statement uploads in progress.
    """
    mortgages = mortgages_added_by(user)
    for m in mortgages:
        delete_mortgage(m)
    db.session.flush()
    for model, column in ((AuditLog, AuditLog.user_id), (MortgageActivity, MortgageActivity.user_id),
                          (MortgageDocument, MortgageDocument.uploaded_by_id)):
        db.session.query(model).filter(column == user.id).update({column: None}, synchronize_session=False)
    db.session.query(PendingUpload).filter(PendingUpload.user_id == user.id).delete(synchronize_session=False)
    db.session.delete(user)
    return len(mortgages)
