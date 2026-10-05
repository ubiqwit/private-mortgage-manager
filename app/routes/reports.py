"""Month-end report: on screen, Excel workbook and CSV."""
import re
from datetime import timedelta

from flask import Blueprint, Response, abort, flash, g, redirect, render_template, request, url_for

from .. import db
from ..models import audit
from ..services import periods, reports
from ..services.market import latest_prime
from ..timeutil import today as local_today

bp = Blueprint("reports", __name__, url_prefix="/reports")


def _selected_month():
    raw = request.values.get("month", "")
    if raw:
        if not re.fullmatch(r"\d{4}-\d{2}", raw):
            abort(400)
        year, month = map(int, raw.split("-"))
        if not 1 <= month <= 12:
            abort(400)
        return year, month
    return reports.default_month()


@bp.route("/month-end")
def month_end():
    year, month = _selected_month()
    report = reports.month_end_report(year, month, latest_prime())
    return render_template("reports/month_end.html", r=report, months=reports.previous_months(),
                           selected=f"{year:04d}-{month:02d}", closed_through=periods.closed_through(),
                           month_over=report["end"] < local_today())


@bp.route("/close", methods=["POST"])
def close_month():
    if not g.user.is_admin:
        abort(403)
    year, month = _selected_month()
    _, end = reports.month_range(year, month)
    if end >= local_today():
        flash("You can only close a month after it has ended.", "warning")
    else:
        periods.set_closed_through(end)
        audit("books_closed", f"through {end}")
        db.session.commit()
        flash(f"Books closed through {end:%B %Y}. Transactions, matches and imports dated on or before "
              f"{end:%b %d, %Y} are now locked.", "success")
    return redirect(url_for("reports.month_end", month=f"{year:04d}-{month:02d}"))


@bp.route("/reopen", methods=["POST"])
def reopen_month():
    if not g.user.is_admin:
        abort(403)
    year, month = _selected_month()
    start, _ = reports.month_range(year, month)
    current = periods.closed_through()
    if current and current >= start:
        new_end = start - timedelta(days=1)
        periods.set_closed_through(new_end)
        audit("books_reopened", f"from {start} (was closed through {current})")
        db.session.commit()
        flash(f"Reopened {start:%B %Y}" + (" and later months" if current.month != month or current.year != year else "")
              + ". Remember to resend the report if anything changes.", "info")
    return redirect(url_for("reports.month_end", month=f"{year:04d}-{month:02d}"))


@bp.route("/month-end.xlsx")
def month_end_xlsx():
    year, month = _selected_month()
    report = reports.month_end_report(year, month, latest_prime())
    data = reports.month_end_workbook(report, prepared_by=g.user.display_name)
    return Response(data, mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f'attachment; filename="mortgage-income-{year:04d}-{month:02d}.xlsx"'})


@bp.route("/month-end-transactions.csv")
def month_end_csv():
    year, month = _selected_month()
    report = reports.month_end_report(year, month, latest_prime())
    return Response(reports.transactions_csv(report), mimetype="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="mortgage-transactions-{year:04d}-{month:02d}.csv"'})


def _selected_year():
    year = request.args.get("year", type=int) or local_today().year
    if not 2000 <= year <= 2100:
        abort(400)
    return year


@bp.route("/annual")
def annual():
    year = _selected_year()
    report = reports.annual_report(year, latest_prime())
    this_year = local_today().year
    return render_template("reports/annual.html", r=report, years=list(range(this_year, this_year - 8, -1)))


@bp.route("/annual.xlsx")
def annual_xlsx():
    year = _selected_year()
    data = reports.annual_workbook(reports.annual_report(year, latest_prime()), prepared_by=g.user.display_name)
    return Response(data, mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f'attachment; filename="mortgage-income-{year}.xlsx"'})
