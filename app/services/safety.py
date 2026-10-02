"""Defences for files we hand to other people (your accountant opens these in Excel).

Bank descriptions and e-transfer memos are written by third parties. A memo such as
``=HYPERLINK("http://evil", "click")`` must stay text, never become a live formula.
"""
FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def csv_safe(value):
    """OWASP CSV-injection defence: neutralise text that a spreadsheet would evaluate."""
    if isinstance(value, str) and value.startswith(FORMULA_PREFIXES):
        return "'" + value
    return value


def set_text_cell(cell, value):
    """Write ``value`` to an openpyxl cell, forcing strings to stay plain text."""
    cell.value = value
    if isinstance(value, str) and value.startswith(FORMULA_PREFIXES):
        cell.data_type = "s"
    return cell
