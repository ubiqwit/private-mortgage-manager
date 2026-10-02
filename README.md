# Private Mortgage Manager

A self-hosted web app for private mortgage lenders to track their mortgage book,
reconcile bank deposits against mortgage payments, and produce month-end income
reports for their accountant.

## Quick start

```bash
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
flask --app run create-user you@example.com   # create your login
python run.py                                 # open http://127.0.0.1:5000
```

Data is stored in `instance/mortgages.db` (SQLite). Back this file up regularly.

## Running tests

```bash
pip install -r requirements-dev.txt
pytest
ruff check app tests
```

## Running in the cloud (access from anywhere)

The app is built to be hosted so you can sign in from any computer or phone:

* **Sign-in is always required.** Each person gets their own login with a role:
  `admin` (everything + manage users), `editor` (add/change data) or `viewer`
  (read-only — give this to your accountant so they can pull reports themselves).
* Passwords are hashed; 5 failed attempts lock an email for 15 minutes; changing a
  password signs out every other device; all forms are CSRF-protected; cookies are
  `Secure`/`HttpOnly` in production; every sign-in and change is written to an audit log
  (Users page).
* Use **PostgreSQL** in the cloud (`DATABASE_URL`) — managed databases give you
  automatic backups. Uploaded statements are stored in the database, not on disk,
  so the app works on hosts with ephemeral file systems.

### Option A — Render (simplest)

1. Push this repo to GitHub (already done if you're reading this there).
2. In Render: **New → Blueprint**, choose the repo. `render.yaml` creates the web
   service and a PostgreSQL database.
3. When prompted, set `PMM_ADMIN_EMAIL` and `PMM_ADMIN_PASSWORD` — this creates your
   first login on startup. Open the `https://….onrender.com` URL and sign in.

### Option B — any Docker host (Fly.io, Railway, DigitalOcean, AWS, Azure…)

```bash
docker build -t mortgage-manager .
docker run -p 8000:8000 \
  -e PMM_SECRET_KEY=$(python -c "import secrets; print(secrets.token_hex(32))") \
  -e DATABASE_URL=postgresql://user:pass@host:5432/mortgages \
  -e PMM_ADMIN_EMAIL=you@example.com -e PMM_ADMIN_PASSWORD='a-long-password' \
  mortgage-manager
```

Always put it behind HTTPS (all the platforms above do this for you). See
`.env.example` for every setting.

### Managing users from the command line

```bash
flask --app run create-user you@example.com --role admin
flask --app run reset-password you@example.com
```

## Live market data

The **Market & rates** page pulls the Bank of Canada policy rate, prime, the posted
5-year mortgage rate, Government of Canada 2/5/10-year yields and CPI from the free
[Bank of Canada Valet API](https://www.bankofcanada.ca/valet/docs), plus news from RSS
feeds you can edit (Bank of Canada press releases and Google News searches by default).
Data refreshes in the background when the page is opened and is more than 6 hours old,
or on demand. To refresh on a schedule (e.g. a Render cron job):

```bash
flask --app run refresh-market
```

Variable-rate mortgages (prime + spread, optional floor) are priced from the latest
prime. If your server can't reach the Bank of Canada you can enter prime by hand on the
same page.
