# Private Mortgage Manager

A web app for private mortgage lenders: track every mortgage you hold, see the state of
your book at a glance, follow the rate environment, reconcile your bank statements
against mortgage payments, and hand your accountant a clean month-end income workbook.

It is built to run in the cloud so you (and, read-only, your accountant) can sign in
from anywhere.

## What it does

| Area | Highlights |
|---|---|
| **Mortgages** | Borrower, mortgagees (who owns each mortgage and their percentage — filter the list by mortgagee to see their share of the balance; the dashboard and reports show the split too), security (value, position, prior charges, CLTV), terms (fixed, or prime + spread with a floor; monthly / semi-annual / annual compounding; interest-only or blended; any payment frequency), fees, broker, lawyer, insurance, notes. Payment schedule with balloon. Record payments, prepayments, fees, NSFs, payouts and advances; the interest/principal/fee split is suggested and editable. Renewals keep the old terms for past periods. Payment history grid (on time / late / missed), documents (appraisals, commitments, title, insurance), and an activity log with follow-up reminders. Import your existing book from a spreadsheet. |
| **Dashboard** | Capital deployed, interest run-rate, weighted average rate and CLTV, collected vs scheduled this month, YTD income, arrears, 12-month income chart, maturity ladder, CLTV bands, concentration by position / property type / city, and a "needs attention" list (arrears, maturities, expiring insurance, stale appraisals, high CLTV). |
| **Market & rates** | Live Bank of Canada policy rate, prime, posted 5-yr mortgage rate, GoC 2/5/10-yr yields and CPI; rate-decision history (hikes/cuts); news feeds; what a ±25/50 bp prime move does to your income; how your fixed rates compare to the market; renewals coming up. |
| **Bank statements** | Upload CSV, Excel or OFX/QFX exports from any Canadian bank (column layout is auto-detected and can be corrected). Duplicates are skipped. Deposits are matched to mortgages automatically by learned description, borrower name, amount and due date; you confirm, split a deposit across mortgages, or mark it as not mortgage-related. |
| **Month-end report** | Per mortgage: opening/closing balance, principal repaid, interest and fees received (cash), interest earned (accrual), scheduled vs received, arrears. Transactions, bank reconciliation and YTD income by month. Download as an Excel workbook (with formulas) or CSV, or print to PDF. Close the books once it's sent so nothing in that month can change. An annual income summary (with Excel export) covers tax time. |
| **Security** | Individual logins with roles (admin / editor / read-only viewer), hashed passwords, lockout after failed attempts, CSRF protection, secure cookies, CSP, audit log of every change, full data export. |

## Your monthly routine

1. **Download last month's statement** from online banking as CSV, Excel or OFX/QFX
   (PDF can't be read reliably) and upload it under **Bank statements**.
2. **Reconcile**: high-confidence matches are accepted automatically; confirm or correct
   the rest, and mark personal/transfer deposits as *not a mortgage item*. Each match you
   make teaches the app that borrower's bank description.
3. Open **Month-end report**, check the unmatched-deposits warning is gone, and download
   the **Excel workbook** for your accountant (or give them a *viewer* login).
4. **Close books** for the month (admin) so the numbers you sent can't drift.

Payments can also be recorded by hand on a mortgage's page; when the bank statement
arrives the deposit is linked to that payment instead of creating a duplicate.

## Quick start (on your computer)

```bash
python3 -m venv .venv
source .venv/bin/activate                       # Windows: .venv\Scripts\activate
pip install -r requirements.txt
flask --app run create-user you@example.com     # create your login
python run.py                                   # open http://127.0.0.1:5000
```

Want to look around first? `flask --app run seed-demo` loads a sample book into an
empty database. Locally, data lives in `instance/mortgages.db` (SQLite).

## Running in the cloud (access from anywhere)

Use a host that provides HTTPS and a managed **PostgreSQL** database (automatic backups).
The app applies database migrations itself on start-up, so deploying a new version is
all an upgrade takes. Uploaded statements are stored in the database, so ephemeral
container disks are fine.

### Option A — free: Render + Neon (see [DEPLOY.md](DEPLOY.md) for click-by-click steps)

1. Create a free PostgreSQL database on [Neon](https://neon.tech) and copy its connection string.
2. In Render: **New → Blueprint** and pick this repository. `render.yaml` creates a free web
   service and asks for `DATABASE_URL` (the Neon string), `PMM_ADMIN_EMAIL` and `PMM_ADMIN_PASSWORD`.
3. Point a subdomain (e.g. `mortgages.yourdomain.com`) at the service with a CNAME record.

The free plan would sleep after 15 idle minutes; the app pings itself every 10 minutes to stay awake
(`app/keepalive.py`, off with `PMM_KEEP_AWAKE=0`). Moving to paid plans later is
a plan change only.

### Option B — any Docker host (Fly.io, Railway, DigitalOcean, AWS, Azure…)

```bash
docker build -t mortgage-manager .
docker run -p 8000:8000 \
  -e PMM_SECRET_KEY=$(python -c "import secrets; print(secrets.token_hex(32))") \
  -e DATABASE_URL=postgresql://user:pass@host:5432/mortgages \
  -e PMM_ADMIN_EMAIL=you@example.com -e PMM_ADMIN_PASSWORD='a-long-password' \
  mortgage-manager
```

Always serve it over HTTPS (the platforms above do this for you).

### Settings

| Variable | Purpose |
|---|---|
| `PMM_ENV` | `production` enables secure cookies, HSTS and proxy headers (set in the Docker image). |
| `PMM_SECRET_KEY` | Required in production. Long random string; signs sessions. |
| `DATABASE_URL` | PostgreSQL connection string (`postgres://…` and `postgresql://…` both work). Unset = local SQLite. |
| `PMM_ADMIN_EMAIL`, `PMM_ADMIN_PASSWORD` | Create the first admin on start-up when no users exist. |
| `PMM_TIMEZONE` | Your time zone (default `America/Toronto`) — "today", due dates and arrears roll over at your midnight, not the server's. |
| `PMM_MARKET_FETCH` | `0` disables calls to the Bank of Canada and news feeds. |

See `.env.example` for a template.

### Users and access

* **admin** — everything, plus users, closing/reopening months and the full data export.
* **editor** — add, change and delete mortgages, transactions and statements.
* **viewer** — read-only; ideal for your accountant to pull reports themselves.

Five failed sign-ins lock an account for 15 minutes; changing a password signs out every
other device; the Users page shows the audit log. From a shell:

```bash
flask --app run create-user someone@example.com --role viewer
flask --app run reset-password you@example.com
```

### Backups

Your host's database backups are the first line of defence. In addition, **Users & data →
Export all data** downloads every table as one Excel file — keep a copy somewhere you
control.

## How the numbers work

* **Balance** = principal advanced − principal repaid (+ any additional advances).
* **Interest-only payments** are the periodic rate on the balance at the time; rates
  quoted with semi-annual compounding (the Canadian standard for blended mortgages) are
  converted to the payment frequency correctly.
* **Arrears** = scheduled payments more than 5 days past due − regular payments received.
* **Accrued interest** (month-end report) = interest earned on each day's balance at the
  rate in force that day, so a full month at a steady balance equals one month's
  contractual interest; prepayments, payouts and renewals mid-month are pro-rated.
* **Renewals / rate changes** record the previous terms with an end date. Past months
  keep the old rate and payment; variable loans use prime as it was on each date.

## Live market data

The **Market & rates** page uses the free [Bank of Canada Valet API](https://www.bankofcanada.ca/valet/docs)
(no key needed) and RSS/Atom feeds you can edit (Bank of Canada press releases and
Google News searches by default). It refreshes in the background when the page is
opened and the data is more than 6 hours old, or on demand. If your server can't reach
the Bank of Canada, enter prime by hand on the same page.

## Development

```bash
pip install -r requirements-dev.txt
pytest                       # SQLite
PMM_TEST_DATABASE_URL=postgresql+psycopg://user:pass@localhost/test_db pytest   # PostgreSQL
ruff check app tests migrations
```

The schema is managed with Alembic (`migrations/`). After changing `app/models.py`:

```bash
flask --app run db migrate -m "describe the change"
```

and commit the generated file — `tests/test_migrations.py` fails if models and migrations
drift apart. GitHub Actions runs lint and the tests on SQLite and PostgreSQL for every push.

Project layout:

```
app/
  models.py            data model (mortgages, transactions, term history, bank lines, users…)
  services/            calc (mortgage maths), ledger (payment splits), portfolio (dashboard),
                       statements (bank file parsing), matching, reports, market, periods,
                       importer, backup, safety
  routes/              one blueprint per section
  templates/, static/  Bootstrap UI (vendored, works offline) and Chart.js charts
migrations/            Alembic migrations, applied automatically on start-up
tests/                 pytest suite
```
