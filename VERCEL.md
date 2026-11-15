# Vercel demo (branch `vercel-demo`)

**Live:** https://collectivize-tau.vercel.app

Until further notice, **all UI and feature work happens on this branch** and ships to the live
URL above. `main` is parked: it stays the Railway / real-server version and gets this branch
merged back when we switch over (see "Switching back to Railway").

## What this deploy is

- The whole app (pages + JSON API) runs as **one Vercel Python function**: `api/index.py`.
  It uses the real request handler in `src/backend/server.py`. Nothing in the backend was
  forked or rewritten, so a change to `src/` shows up here exactly as it does locally.
- **All data is fictional.** It comes from `demo/collectivize-demo.db`, a database seeded by
  `scripts/seed_test_db.py` and committed to this branch. No real people, no real accounts.
- **Data resets.** Each function instance copies the demo DB to `/tmp`. Changes (RSVPs, stars,
  profile edits) last while that instance stays warm (minutes), then everything goes back to
  the demo data. Two visitors can see slightly different states. This is on purpose.
- **Everyone is signed in automatically** as `drew.demo@northeastern.edu`. No email codes.
- **AI search is off** (keyword search only), so the public link can't run up an Anthropic bill.
  No API keys or secrets are used by this deploy at all.
- **`/admin` is locked.** No admin logins exist on the deploy.

## Shipping a change

1. Work on `vercel-demo` (branch from it for anything bigger, then PR back into `vercel-demo`).
2. Run it locally first, the same way as always:

   ```
   uv run python scripts/serve.py --no-login --no-ai
   ```

3. Push to `vercel-demo`, then deploy. Today a deploy is one command run by someone who is
   logged in to the Vercel project (currently Cody):

   ```
   vercel deploy --prod --yes
   ```

   (On Windows PowerShell with scripts blocked, type `vercel.cmd`.)
   Auto-deploy on every push is being set up; when it is, this step goes away and this file
   will say so.

## Rules that still apply

- `rules.md`: log every change in `docs/CHANGES.md`, keep it modular, keep it object-oriented.
- Never commit `data/` (local databases) or `config/*.local.json` (secrets).
- Don't make the deploy depend on secrets, real data, or `COLLECTIVIZE_ENV=production`.
  The public URL must stay safe to share with anyone.

## Files that exist only for Vercel

| File | What it does |
|---|---|
| `api/index.py` | The function. Copies the demo DB to `/tmp`, builds the server state once per instance, and hands each request to the normal `RequestHandler`. |
| `vercel.json` | Sends every address to the function (`/api/index?__path=<original>`), and bundles `src/`, `public/`, `app/` and `demo/` with it. |
| `.vercelignore` | Keeps `.venv`, `data/`, tests and local secrets out of the upload. |
| `demo/collectivize-demo.db` | The fictional demo database. |

To refresh the demo data after a seed or schema change:

```
COLLECTIVIZE_DB_PATH=demo/collectivize-demo.db uv run python scripts/seed_test_db.py --reset
```

## Switching back to Railway

The backend is untouched, so switching back is a merge, not a migration:

1. Merge `vercel-demo` into `main`.
2. Delete `api/`, `vercel.json`, `.vercelignore` and `demo/` (or keep them for preview deploys:
   they don't affect `scripts/serve.py`).
3. Deploy `main` to Railway with a persistent volume for `data/`.
4. Delete the "work on vercel-demo" notes from `CLAUDE.md` and `AGENTS.md` on `main`.
