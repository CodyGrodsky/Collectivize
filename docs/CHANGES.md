# Changes

This is a running log of changes made to Collectivize, newest first. Each entry says what changed, why, and any decisions made along the way.

---

## 2026-11-13 — Separate test and production databases

**Request:** "Make it so there is a test database that stores mockup information and then a production database that stores real data."

There are now two SQLite databases. The test database holds fictional mock data; the production database holds real data. The `COLLECTIVIZE_ENV` variable picks which one is used. Several guards keep mock data out of production and stop either database from being opened as the other.

### How to use it

| Task | Command |
|------|---------|
| Create the test DB with mock data | `uv run python scripts/seed_test_db.py` |
| Wipe and re-seed the test DB | `uv run python scripts/seed_test_db.py --reset` |
| Use the test DB in code (the default) | `with open_graph() as graph: ...` |
| Use the production DB in code | Run with `COLLECTIVIZE_ENV=production` |
| Use a different database file | Set `COLLECTIVIZE_DB_PATH=/path/to/file.db` |

The default files are `data/test.db` and `data/production.db`.

### Added
- **`src/backend/config.py`:** the `Environment` setting (`test` or `production`) and `load_config()`, which reads `COLLECTIVIZE_ENV` and `COLLECTIVIZE_DB_PATH`. It defaults to **test**, so nothing writes real data by accident. Unknown values are rejected.
- **`src/backend/graph/sqlite_store.py`:** `SqliteGraphStore`, a persistent store with the same interface as the in-memory store. It has three tables: `meta`, `nodes` and `edges` (with indexes on source and target). Records are stored as JSON. `transaction()` makes a block of writes all-or-nothing.
- **`src/backend/graph/codec.py`:** converts records to and from JSON exactly (enums, datetimes, durations, nested objects, change-log values). Loaded data goes back through each class's validation. Unknown types or fields are rejected.
- **`src/backend/database.py`:** `open_graph()`, which opens the configured database and closes it when you're done.
- **`src/backend/seed.py`:** `seed_mock_data()` creates a fictional campus: 6 orgs, 5 people, 5 tags and 8 events. That includes a weekly meeting series, a co-hosted fundraiser, a cancelled event, a draft, an unclaimed org and a pending membership request. All writes go through the normal services, so the mock data follows every graph rule.
- **`scripts/seed_test_db.py`:** a command-line script for seeding, with a `--reset` option.
- **Tests:**
  - `tests/backend/test_storage.py`: config, the JSON conversion, the SQLite store, and transactions on both stores.
  - `tests/backend/test_seed.py`: mock data is clearly fake and follows the rules; seeding production is refused.
- **`data/test.db`:** created and seeded.

### Changed
- **`src/backend/graph/store.py`:**
  - `GraphStore` is now an interface that both stores implement.
  - The in-memory store is renamed `InMemoryGraphStore`.
  - It gained `transaction()`, which restores a snapshot if the block fails, and `close()`.
- **`src/backend/graph/__init__.py`:** `CollectivizeGraph()` still uses the in-memory store by default. Pass a `SqliteGraphStore` to persist data.
- **`src/backend/graph/privacy.py`:** `erase_person` now runs in a transaction, so an account deletion can never be left half-done.
- **`tests/backend/conftest.py`:** every service test now runs against **both** stores. There are now 71 tests, up from 33.
- **`.gitignore`:** added `data/`, so database files, and especially real production data, are never committed.

### Safeguards
1. **Seeding production is refused,** both in `seed_mock_data()` and in the seed script.
2. **Each database file is stamped with its environment** the first time it's opened. Opening a production file as test, or the reverse, raises `EnvironmentMismatchError`.
3. **`--reset` only deletes a file stamped as a test database.** I confirmed it refuses when `COLLECTIVIZE_DB_PATH` points at a production file.
4. **Mock records are obviously fake:** org names start with `[Demo]`, people are labelled "(demo …)", and events have `is_demo=True`, which can never be removed.

### Decisions
- **SQLite** (built into Python, no new dependencies) is enough for the pilot. The services only use the `GraphStore` interface, so a Postgres store could be dropped in later.
- **JSON records with indexed edge columns,** rather than one table per type, so adding fields doesn't need a database migration yet.

### Not done yet
- No database migrations or schema versioning.
- No backups for production.
- No dedicated staging environment.

---

## 2026-11-13 — Graph architecture from `user_Orgs.md`

**Request:** "Create the architecture for user_Orgs.md."

The graph spec in `user_Orgs.md` is now an object-oriented Python backend, as `rules.md` requires. Every node and edge type from the spec exists. The four cross-cutting rules (provenance, visibility, recommendation consent, lifecycle) and all the MVP rules are enforced in code. The "Later" types are defined so the schema has a place for them, but their rules aren't enforced yet.

### Added

**Domain objects: `src/backend/objects/`**

All of these are immutable (frozen dataclasses). Changing one returns a new copy.

| File | Contents |
|------|----------|
| `base.py` | `GraphRecord`, the base for every node and edge: id, `Provenance`, `Visibility`, `RecordState`. Also `DomainRuleError` and `most_restrictive()`. |
| `person.py` | `Person` and `VerificationLevel` (records *which* level of identity verification was reached, not how). |
| `organization.py` | `Organization` with `OrgKind`, `ClaimState` (unclaimed, pending, claimed), external links (http/https only), and derived `freshness()` (active, quiet, stale). |
| `event.py` | `Event` with its lifecycle (draft, published, cancelled, ended, archived) and a change log for time and location edits. The demo flag can't be cleared. `EventSeries` holds defaults that its events inherit. |
| `taxonomy.py` | `Tag` (curated, kebab-case slug) and `Venue` (Later). |
| `edges.py` | All edges: `MemberOf`, `PartOf`, `PartnersWith`, `Hosts`, `InstanceOf`, `Rsvp`, `Attended`, `Follows`, `Connected`, `Tagged`, `InterestedIn`. Edges a person owns carry `RecommendationUse`, which defaults to `OWN_ONLY`. |

**Graph services: `src/backend/graph/`**

| File | Rules it enforces |
|------|-------------------|
| `store.py` | In-memory storage for nodes and edges. Hard delete is reserved for account erasure. |
| `membership.py` | The person claims a membership and the org confirms it. Only admins grant roles. Only the person controls their membership's visibility. An org must always have at least one admin. Also handles the claim flow: the org goes from unclaimed to claim pending, and becomes claimed once staff approve. |
| `hierarchy.py` | A `PART_OF` link needs both orgs to accept or staff verification. Only certain org types can link (see `ALLOWED_PART_OF`). An org can have several parents but can never become its own ancestor. |
| `events.py` | Exactly one primary host. Co-hosts count only after they accept and can remove themselves. Only admins of a host org can edit an event. Publishing updates the host org's activity. Admins tag their own orgs and events. |
| `series.py` | An event belongs to at most one series. `publish_occurrences` publishes a semester in one action. Editing the series updates only future events that still use the old default. |
| `privacy.py` | Effective visibility is the most restrictive of the edge and both endpoints. `can_view` checks who is looking. Draft events are visible only to host admins. `erase_person` truly deletes the person's edges. |
| `__init__.py` | `CollectivizeGraph`, which wires the store and all services together. |

**Tests: `tests/backend/`**
- 33 pytest tests covering the objects, membership, hierarchy, events, series and privacy.
- Run them with `uv run --group dev pytest`.

### Changed
- `src/backend/objects/organization.py`: replaced the empty stub with the full `Organization` model.
- `pyproject.toml`: added a `dev` dependency group with pytest, and pytest config (`pythonpath = ["src"]`, `testpaths = ["tests"]`).

### Removed
- `src/backend/objects/user.py`: the empty `User` stub. `Person` in `person.py` replaces it, to match the spec's terms.

### Generated
- `uv.lock` and `.venv/`, created by uv. `.venv` is already gitignored.

### Decisions (the spec doesn't specify these)
- **Visibility order:** public < network < members < connections < private.
- **Freshness:** quiet after 30 days and stale after 90. Measured from the more recent of the last profile confirmation and the last published event.
- **Allowed `PART_OF` links:** chapter → national; chapter or club → council; anything except an institution → institution.
- **Staff actions:** `staff_id` is trusted as given. A future auth layer must check it.
- **Series edits:** an event that was edited on its own keeps its edit.
- **Erasing the last admin's account:** the org goes back to unclaimed instead of blocking the deletion.
- **Hierarchy cycle check:** pending links count too, so accepting one later can never create a cycle.

### Bug fixed
- `HierarchyService.accept` let any user approve a pending link. It checked the link's existing approvals instead of whether the person approving is an admin. A test caught it, and it's fixed.

### Not done yet
- Rules for the "Later" edges (RSVP freezing, small-count suppression, attendance timing) aren't enforced.
- There's no API layer, persistent database or authentication yet.
