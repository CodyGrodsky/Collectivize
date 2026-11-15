# Collectivize: notes for coding agents

## STOP: don't build on `main` right now

**All work is happening on the `vercel-demo` branch of the fork
https://github.com/CodyGrodsky/Collectivize** while we run a public demo on Vercel
(https://collectivize-tau.vercel.app). Pushes to it there deploy themselves. Switch before doing anything:

```
git remote add fork https://github.com/CodyGrodsky/Collectivize.git
git fetch fork
git switch -c vercel-demo --track fork/vercel-demo
```

Push with `git push fork vercel-demo`.

Then read `VERCEL.md` and `CLAUDE.md` on that branch. `main` is parked as the Railway /
real-server version. `vercel-demo` gets merged back here when we switch to Railway; this note
goes away then.

## Rules (from `rules.md`, on every branch)

1. Every change gets an entry in `docs/CHANGES.md`.
2. Keep it modular.
3. Keep it object-oriented.
