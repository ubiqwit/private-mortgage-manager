# Private Mortgage Manager

A self-hosted web app for private mortgage lenders to track their mortgage book,
reconcile bank deposits against mortgage payments, and produce month-end income
reports for their accountant.

## Quick start

```bash
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python run.py                      # open http://127.0.0.1:5000
```

Data is stored in `instance/mortgages.db` (SQLite). Back this file up regularly.

## Running tests

```bash
pytest
```
