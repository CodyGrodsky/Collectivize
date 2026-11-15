# Handoff: Vercel demo (2026-11-15, overnight)

**Why:** to prototype faster on a live site. We wanted a public URL the whole team can push UI and
feature changes to and see live in a minute, without waiting on real hosting (Railway, a database,
email, secrets).

**Live:** https://collectivize-tau.vercel.app (fictional demo data only)

## What was done

- **New branch `vercel-demo`** (cut from `main` at `37408bc`). The app runs on Vercel as one Python
  serverless function (`api/index.py`) that wraps the existing `RequestHandler`. **No backend code
  was changed.** The only new files are `api/`, `vercel.json`, `.vercelignore`, `demo/`, `VERCEL.md`.
- **Demo settings:** a freshly seeded fictional database is bundled with each deploy (writes reset
  after a few minutes), everyone is auto signed in as Drew, AI search is off, `/admin` is locked,
  and no secrets are used. Safe to share with anyone.
- **Fork `CodyGrodsky/Collectivize`**, where `vercel-demo` is the default branch, is connected to
  Cody's Vercel project. Every push there deploys, and PRs get preview links. A fork was needed
  because Vercel can only watch repos whose owner installs its GitHub app, and this repo is under
  your personal account.
- **On `main`:** two small commits. `CLAUDE.md`/`AGENTS.md` tell every agent to work on the fork's
  `vercel-demo` for now. The network map's "Mutual connections" switch (Cody's uncommitted work) was
  committed to both branches.

## Switching back (whenever you want)

1. PR `CodyGrodsky/Collectivize:vercel-demo` → `JamesKempel/Collectivize:main` and merge.
2. Remove `api/`, `vercel.json`, `.vercelignore`, `demo/`, or keep them for preview deploys
   (they don't affect `scripts/serve.py`).
3. Deploy `main` to Railway with a persistent volume for `data/`.
4. Remove the "work on the fork" note from `CLAUDE.md`/`AGENTS.md`, and this file.

**Optional:** to skip the fork, install the Vercel GitHub app on your account (or move the repo to a
GitHub org) and point the Vercel project here instead.

Full details: `VERCEL.md` on `vercel-demo`.
