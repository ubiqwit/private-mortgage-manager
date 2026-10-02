# Deploying — step by step (about 15 minutes)

This puts the app on the internet at a private `https://…onrender.com` address with its
own PostgreSQL database. Render bills monthly for the web service ("Starter") and the
database ("Basic 256 MB"); check their pricing page for current costs.

## 1. Create the service

1. Go to <https://dashboard.render.com> and sign up / sign in (signing in with GitHub is easiest).
2. Click **New +** → **Blueprint**.
3. Connect GitHub if asked, and give Render access to the **private-mortgage-manager** repository.
4. Select the repository. Render reads `render.yaml` and shows two resources:
   *private-mortgage-manager* (web service) and *mortgage-db* (database).
5. It asks for two values:
   - `PMM_ADMIN_EMAIL` — the email you'll sign in with.
   - `PMM_ADMIN_PASSWORD` — at least 10 characters. Use a password manager.
6. Click **Apply** / **Deploy Blueprint**. The first build takes 5–10 minutes.
   The web service shows **Live** when it's done.

## 2. Sign in

1. Open the service in the Render dashboard and click its URL (`https://private-mortgage-manager-xxxx.onrender.com`).
2. Sign in with the email and password from step 1.5.
3. Bookmark the URL on your computer and phone.

## 3. First-day checklist

- [ ] **Market & rates** → wait a few seconds; rates should appear. If you see
      "couldn't reach", press **Refresh now** once. Tell Claude if it still fails.
- [ ] **Mortgages** → **Import from spreadsheet** (download the template, fill it in) or add them one at a time.
- [ ] **Bank statements** → upload last month's CSV/OFX export and reconcile.
- [ ] **Reports** → check last month's report and download the Excel file.
- [ ] **Users & data** (your name, bottom-left → Users) → add your accountant as a **viewer**.
- [ ] Optional: in Render → *private-mortgage-manager* → **Environment**, delete
      `PMM_ADMIN_EMAIL` / `PMM_ADMIN_PASSWORD` (your account is already saved).

## Updates

Every push to `main` on GitHub redeploys automatically (database changes are applied on start-up).
Your data is untouched by redeploys.

## Backups

Render backs up the database daily. For your own copy: **Users & data → Export all data (Excel)** —
do this monthly and keep it somewhere safe.

## Custom address (optional)

Render → service → **Settings → Custom Domains** to use e.g. `mortgages.yourcompany.ca`.
