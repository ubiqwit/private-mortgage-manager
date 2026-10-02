"""Month-end report: on screen, Excel workbook and CSV."""
import re

from flask import Blueprint, Response, abort, g, render_template, request

from ..services import reports
from ..services.market import latest_prime

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
                           selected=f"{year:04d}-{month:02d}")


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
