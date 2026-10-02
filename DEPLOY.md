# Deploying for free — step by step (about 20 minutes)

| Piece | Service (free plan) | Notes |
|---|---|---|
| App | **Render** free web service | Sleeps after 15 min without visitors; the next visit takes ~1 minute to wake up (see step 5 to avoid this). |
| Database | **Neon** free Postgres | 0.5 GB, doesn't expire. Render's own free database is deleted after 30 days, so don't use it. |
| Address | `mortgages.shaunmalhotra.com` | A subdomain of your domain at Namecheap. Your GitHub Pages site on `shaunmalhotra.com` is untouched. |

GitHub Pages only serves static pages, so it can't run this app — it keeps hosting your main site.

## 1. Create the database (Neon)

1. Go to <https://neon.tech> and sign up (GitHub sign-in is easiest).
2. Create a project: name `mortgages`, region **AWS US East 2 (Ohio)** (same area as the app).
3. On the project dashboard click **Connect**. Make sure **Connection pooling is off**, then copy the
   connection string. It looks like
   `postgresql://neondb_owner:…@ep-something.us-east-2.aws.neon.tech/neondb?sslmode=require`
   Keep it handy for step 2 — it contains the database password, so don't share it.

## 2. Create the app (Render)

1. Go to <https://dashboard.render.com> and sign up with GitHub.
2. **New +** → **Blueprint** → connect GitHub and allow access to **private-mortgage-manager** → select it.
3. Render reads `render.yaml` and asks for three values:
   - `DATABASE_URL` — paste the Neon connection string from step 1.3.
   - `PMM_ADMIN_EMAIL` — the email you'll sign in with.
   - `PMM_ADMIN_PASSWORD` — at least 10 characters (use a password manager).
4. **Deploy Blueprint**. The first build takes 5–10 minutes; the service shows **Live** when done.
5. Click the service's URL (`https://private-mortgage-manager-xxxx.onrender.com`) and sign in to check it works.

## 3. Use your domain (Namecheap)

1. In Render: service → **Settings → Custom Domains → Add Custom Domain** → `mortgages.shaunmalhotra.com`.
   Render shows the target to point at (your `…onrender.com` host name).
2. In Namecheap: **Domain List → shaunmalhotra.com → Manage → Advanced DNS → Add New Record**:
   - Type **CNAME Record**, Host **`mortgages`**, Value **the `…onrender.com` host name from Render**, TTL Automatic.
   - Leave the existing GitHub Pages records alone.
3. Back in Render, click **Verify**. DNS can take a few minutes (rarely up to an hour). Render then issues
   the HTTPS certificate automatically.
4. Open <https://mortgages.shaunmalhotra.com> and sign in.

## 4. First-day checklist

- [ ] **Market & rates** → rates should appear within a few seconds (press **Refresh now** once if not).
- [ ] **Mortgages** → **Import from spreadsheet** (download the template) or add them one by one.
- [ ] **Bank statements** → upload last month's CSV/OFX export and reconcile.
- [ ] **Reports** → check last month and download the Excel file.
- [ ] Bottom-left → **Users** → add your accountant as a **viewer**.
- [ ] Optional: Render → service → **Environment** → delete `PMM_ADMIN_EMAIL` / `PMM_ADMIN_PASSWORD`
      (your account is already saved).

## 5. Optional: no wake-up wait

The free app sleeps after 15 minutes idle. To keep it awake, create a free monitor at
<https://uptimerobot.com>: type HTTP(s), URL `https://mortgages.shaunmalhotra.com/healthz`, every 5 minutes.
Render's free hours (750/month) cover one app running all month. The health check doesn't touch the
database, so Neon still sleeps when you're not using the app.

## Limits to keep an eye on

- **Storage:** Neon free is 0.5 GB. Mortgages, transactions and statements are tiny; uploaded documents
  are what use space — prefer compressed PDFs over large photo scans. Neon's dashboard shows usage.
- **Backups:** free plans keep only a short restore history. Once a month use
  **Users & data → Export all data (Excel)** and keep the file somewhere safe.
- If you outgrow the free plans, upgrading is a plan change in Render/Neon — no code changes.

## Updates

Every push to `main` redeploys automatically; database changes are applied on start-up and your data stays.
