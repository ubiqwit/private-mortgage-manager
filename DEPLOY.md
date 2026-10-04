# Deploying for free — step by step (about 20 minutes)

| Piece | Service (free plan) | Notes |
|---|---|---|
| App | **Render** free web service | Would sleep after 15 idle minutes; the app keeps itself awake (step 5). |
| Database | **Neon** free Postgres | 0.5 GB, doesn't expire. Render's own free database is deleted after 30 days, so don't use it. |
| Address | `mortgages.shaunmalhotra.com` | A subdomain of your domain at Namecheap. Your GitHub Pages site on `shaunmalhotra.com` is untouched. |

GitHub Pages only serves static pages, so it can't run this app — it keeps hosting your main site.

## 1. Create the database (Neon)

1. Go to <https://neon.tech> and sign up (GitHub sign-in is easiest).
2. Create a project: name `mortgages`, region **AWS US East 2 (Ohio)** (same area as the app).
3. On the project dashboard click **Connect** and copy the connection string. It looks like
   `postgresql://neondb_owner:…@ep-something.us-east-2.aws.neon.tech/neondb?sslmode=require`
   Either the pooled string (host contains `-pooler`) or the direct one works — the app adjusts.
   Keep it handy for step 2 — it contains the database password, so don't share it.
   (No Neon CLI, `neon.ts` or `neon deploy` is needed: the app only needs this connection string.)

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
- [ ] Render → service → **Environment** → delete `PMM_ADMIN_PASSWORD` (your account is already saved;
      see "Locked out" below if you ever need it again).

## 5. Staying awake (automatic)

Render's free plan sleeps after 15 minutes with no visitors. The app prevents that by itself: while it runs
on Render it requests its own `/healthz` page every 10 minutes, which counts as a visitor. Nothing to set up.

- One always-on free service uses about 744 of Render's 750 free hours a month, so don't run a second
  free web service in the same Render account, or both will be paused near month-end.
- `/healthz` doesn't touch the database, so Neon still sleeps when you're not using the app.
- To turn it off: Render → service → **Environment** → `PMM_KEEP_AWAKE` = `0`.
- After a deploy or a Render restart the first visit (or Render's own health check) starts it again.

## Locked out / forgot your password

The free plan has no shell, so recovery is done from Render's settings:

1. Render → service → **Environment**: set `PMM_ADMIN_EMAIL` (your email), `PMM_ADMIN_PASSWORD`
   (a new password, 10+ characters) and `PMM_ADMIN_RESET` = `1`. **Save** — the app restarts.
2. Sign in with the new password.
3. Delete `PMM_ADMIN_RESET` (and the other two if you like) and save again, so it doesn't reset on every restart.

## Limits to keep an eye on

- **Storage:** Neon free is 0.5 GB. Mortgages, transactions and statements are tiny; uploaded documents
  are what use space — prefer compressed PDFs over large photo scans. Neon's dashboard shows usage.
- **Backups:** free plans keep only a short restore history. Once a month use
  **Users & data → Export all data (Excel)** and keep the file somewhere safe.
- If you outgrow the free plans, upgrading is a plan change in Render/Neon — no code changes.

## Updates

Every push to `main` redeploys automatically; database changes are applied on start-up and your data stays.
