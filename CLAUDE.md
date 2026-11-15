# Collectivize: notes for coding agents

## STOP: don't build on `main` right now

**All work is happening on the `vercel-demo` branch** while we run a public demo on Vercel
(https://collectivize-tau.vercel.app). Switch before doing anything:

```
git fetch origin
git switch vercel-demo
```

Then read `VERCEL.md` and `CLAUDE.md` on that branch. `main` is parked as the Railway /
real-server version. `vercel-demo` gets merged back here when we switch to Railway; this note
goes away then.

## Rules (from `rules.md`, on every branch)

1. Every change gets an entry in `docs/CHANGES.md`.
2. Keep it modular.
3. Keep it object-oriented.
