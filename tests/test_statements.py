from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from app.services import statements as st

FIX = Path(__file__).parent / "fixtures"


def load(name):
    content = (FIX / name).read_bytes()
    rows = st.read_rows(name, content)
    mapping = st.guess_mapping(rows)
    return st.apply_mapping(rows, mapping)[0], mapping


def test_parse_amount_variants():
    assert st.parse_amount("$1,234.56") == Decimal("1234.56")
    assert st.parse_amount("(1,234.56)") == Decimal("-1234.56")
    assert st.parse_amount("12.00 DR") == Decimal("-12.00")
    assert st.parse_amount("12.00CR") == Decimal("12.00")
    assert st.parse_amount("45.10-") == Decimal("-45.10")
    assert st.parse_amount("") is None and st.parse_amount("-") is None


def test_date_format_detection():
    assert st.detect_date_format(["01/02/2026", "01/15/2026"]) == "%m/%d/%Y"
    assert st.detect_date_format(["01/02/2026", "15/01/2026"]) == "%d/%m/%Y"
    assert st.detect_date_format(["2026-01-02"]) == "%Y-%m-%d"
    assert st.detect_date_format(["20260102"]) == "%Y%m%d"


def test_rbc_with_header():
    lines, m = load("rbc.csv")
    assert m.has_header and m.amount == 6 and m.description == [4, 5]
    assert lines[0].date == date(2026, 1, 2)
    assert lines[0].amount == Decimal("2289.58")
    assert lines[0].description == "E-TRANSFER - AUTODEPOSIT JANE SMITH"
    assert lines[1].amount == Decimal("-4.95")


def test_td_headerless_debit_credit():
    lines, m = load("td.csv")
    assert not m.has_header and (m.debit, m.credit) == (2, 3)
    assert [ln.amount for ln in lines] == [Decimal("2289.58"), Decimal("-4.95"), Decimal("-250000.00")]


def test_bmo_preamble_and_compact_dates():
    lines, m = load("bmo.csv")
    assert m.has_header and m.header_row == 1  # blank lines are dropped
    assert lines[0].date == date(2026, 1, 2) and lines[0].amount == Decimal("2289.58")
    assert "JANE SMITH" in lines[0].description


def test_ofx():
    lines, acct = st.parse_ofx((FIX / "sample.ofx").read_bytes())
    assert acct == "12345678"
    assert len(lines) == 2
    assert lines[0].fitid == "9001" and lines[0].amount == Decimal("2289.58")
    assert lines[0].description == "E-TRANSFER JANE SMITH"


def test_xlsx(tmp_path):
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.append(["Date", "Description", "Amount"])
    ws.append([date(2026, 1, 2), "E-TRANSFER JANE SMITH", 2289.58])
    path = tmp_path / "s.xlsx"
    wb.save(path)
    lines, m = load_bytes("s.xlsx", path.read_bytes())
    assert lines[0].date == date(2026, 1, 2) and lines[0].amount == Decimal("2289.58")


def load_bytes(name, content):
    rows = st.read_rows(name, content)
    return st.apply_mapping(rows, st.guess_mapping(rows))


def test_pdf_rejected_with_help():
    with pytest.raises(st.StatementError, match="CSV"):
        st.read_rows("statement.pdf", b"%PDF-1.7 ...")


def test_fingerprints_distinguish_same_day_duplicates():
    a = st.ParsedLine(date(2026, 1, 2), "E-TRANSFER", Decimal("500"))
    b = st.ParsedLine(date(2026, 1, 2), "E-TRANSFER", Decimal("500"))
    fps = st.fingerprints([a, b], "chq")
    assert fps[0] != fps[1]
    assert st.fingerprints([a, b], "chq") == fps  # stable on re-import
