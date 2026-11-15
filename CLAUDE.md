# Collectivize: notes for coding agents

## Where to work right now

**All work happens on the `vercel-demo` branch.** It deploys to https://collectivize-tau.vercel.app.
Read `VERCEL.md` before changing anything about how the app is served. `main` is parked (it is
the Railway / real-server version); don't build features there until `VERCEL.md` says we've
switched back.

## The project

- Python 3.13 backend (standard library HTTP server + SQLite) in `src/backend/`, plain HTML/JS/CSS
  frontend in `src/frontend/`. Dependencies through `uv`.
- Run locally: `uv run python scripts/serve.py --no-login --no-ai` (http://127.0.0.1:3000).
- Tests: `uv run pytest`.
- Deeper references: `docs/BACKEND_REFERENCE.md`, `docs/collectivize-v1.1-spec.md`.

## Rules (from `rules.md`)

1. Every change gets an entry in `docs/CHANGES.md` (newest first, same format as the others).
2. Keep it modular.
3. Keep it object-oriented.

## Never

- Commit `data/` or `config/*.local.json`.
- Make the Vercel deploy depend on secrets, real data, or `COLLECTIVIZE_ENV=production`.
