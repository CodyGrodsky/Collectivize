# Collectivize: notes for coding agents

## STOP: don't build on `main` right now

(What happened and why: `HANDOFF.md`.)

**All work is happening on `main` of the fork
https://github.com/CodyGrodsky/Collectivize** while we run a public demo on Vercel
(https://collectivize-tau.vercel.app). Pushes to it deploy themselves. Work in a clone of the fork:

```
git clone https://github.com/CodyGrodsky/Collectivize.git collectivize-vercel
```

Then read `VERCEL.md` and `CLAUDE.md` there. This repo's `main` is parked as the Railway /
real-server version. The fork's `main` gets merged back here when we switch to Railway; this note
goes away then.

## Rules (from `rules.md`, on every branch)

1. Every change gets an entry in `docs/CHANGES.md`.
2. Keep it modular.
3. Keep it object-oriented.
