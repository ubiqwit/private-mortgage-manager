"""Multi-company support: which company the signed-in user is working in, and keeping
every query inside it.

Two layers keep companies apart:

1. **Automatic scoping.** A SQLAlchemy ``do_orm_execute`` hook adds "this company only"
   criteria to every ORM SELECT that touches company data — mortgages, statements and
   bank lines directly, and everything hanging off a mortgage (transactions, term
   history, activity, documents) through its mortgage. List pages, reports, the
   dashboard and matching therefore only ever see the current company.
2. **Ownership checks.** Loading a record by id from a URL goes through
   :func:`get_owned_or_404`, which also checks the company explicitly (a record already
   in the session's identity map isn't re-queried, so the hook alone isn't enough there).

Outside a request (CLI, tests poking the database directly) no company is selected and
nothing is filtered.
"""
from __future__ import annotations

from flask import abort, g, has_app_context, session
from sqlalchemy import event, select
from sqlalchemy.orm import Session, with_loader_criteria

from . import db

DEFAULT_COMPANY_NAME = "My company"
SKIP = "skip_company_scope"


def current_company_id():
    if not has_app_context():
        return None
    return g.get("company_id")


def current_company():
    return g.get("company") if has_app_context() else None


def all_companies(query):
    """Run a query across every company (admin-only screens, maintenance)."""
    return query.execution_options(**{SKIP: True})


_installed = False


def install_scoping():
    """Register the scoping hook once per process (create_app may run many times in tests)."""
    global _installed
    if _installed:
        return
    _installed = True
    from .models import (
        BankTransaction,
        Mortgage,
        MortgageActivity,
        MortgageDocument,
        MortgageTransaction,
        StatementImport,
        TermHistory,
    )

    direct = (Mortgage, StatementImport, BankTransaction)
    via_mortgage = (MortgageTransaction, TermHistory, MortgageActivity, MortgageDocument)

    @event.listens_for(Session, "do_orm_execute")
    def _scope_to_company(state):
        if not state.is_select or state.is_column_load or state.execution_options.get(SKIP):
            return
        cid = current_company_id()
        if cid is None:
            return
        options = [
            with_loader_criteria(model, lambda cls: cls.company_id == cid, include_aliases=True)
            for model in direct
        ] + [
            with_loader_criteria(
                model,
                lambda cls: cls.mortgage_id.in_(select(Mortgage.id).where(Mortgage.company_id == cid)),
                include_aliases=True,
            )
            for model in via_mortgage
        ]
        state.statement = state.statement.options(*options)


def owner_company_id(obj):
    """The company a record belongs to (directly, or through its mortgage)."""
    if hasattr(obj, "company_id"):
        return obj.company_id
    mortgage = getattr(obj, "mortgage", None)
    return mortgage.company_id if mortgage is not None else None


def get_owned_or_404(model, ident):
    obj = db.session.get(model, ident) if ident is not None else None
    if obj is None or owner_company_id(obj) != current_company_id():
        abort(404)
    return obj


def ensure_default_company():
    """Every installation has at least one company (fresh test databases included)."""
    from .models import Company

    company = Company.query.order_by(Company.id).first()
    if company is None:
        company = Company(name=DEFAULT_COMPANY_NAME)
        db.session.add(company)
        db.session.commit()
    return company


def select_company_for(user):
    """Pick the request's company: the one chosen in the session if the user may use it,
    otherwise their first company. Returns None if they have no company yet."""
    ensure_default_company()
    companies = user.accessible_companies()
    if not companies:
        return None
    wanted = session.get("company_id")
    company = next((c for c in companies if c.id == wanted), companies[0])
    session["company_id"] = company.id
    return company
