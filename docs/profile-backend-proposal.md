# Profile backend proposal

**Status:** proposal only. Nothing here is implemented. The profile page (`src/frontend/profile.html`) currently runs on sample data. Its `ProfileDataSource` class in `profile.js` expects the `GET /api/profile` JSON described in section 2f.

Written 2026-11-14 against `main` at `23663c7`. If line numbers have moved, search for the names.

---

## 1. The demo server as it is today (`src/backend/server.py`)

- **Run it:** `uv run python scripts/serve.py` (add `--no-ai` or `--port 8080` if needed) to serve `http://127.0.0.1:8000/`. It needs the test database (`scripts/seed_test_db.py`) and refuses to run against production. It serves the static pages as well as the API.
- **Endpoints:**

| Method and URL | Request | Response |
|---|---|---|
| `GET /api/personas` | nothing | `{personas: [{id, name, description, friends, featured}]}` |
| `GET /api/tags` | nothing | `{tags: [{slug, label}]}` |
| `GET /api/discover?viewer=&page=&near=1&tag=` | query string | `{items: [Item], total, page, per_page, pages}` |
| `POST /api/search` | `{viewer, query, near}` | `{goal, used_ai, note, steps: [{tool, text, error}], items: [Item], cached}` |

  Each `Item` is `{id, title, description, starts_at, ends_at, location, host: {name, kind}, score, reasons, friends_going, going_count, my_rsvp, distance_km, is_cancelled}`.
- **Viewer:** the person id the request names in `viewer`. It's a demo persona, not authentication.
- **Errors:** `{"error": "..."}`. `ApiError` uses its own status code. `DomainRuleError`, `NotFoundError` and `PermissionDeniedError` all return **400**. Anything else returns 500 with "something went wrong". POST bodies must be JSON of at most **4096 bytes**.

---

## 2. Proposal

This follows the conventions in `docs/BACKEND_REFERENCE.md`: records are immutable, the person acting and `now` are passed in, objects validate their own fields, and services check permissions.

### a. New `Person` fields (`objects/person.py`)

| Field | Rule | Notes |
|---|---|---|
| `username: str` | Required. `^[a-z0-9_]{3,20}$` | Checked in `__post_init__`, as `Organization` checks its URLs. |
| `display_name: str = ""` | Blank allowed, max 50 | A new `shown_name` property returns `display_name or username`. About 8 places use `.display_name` as a label and would switch: network, rsvps, roster, data_access, and 3 in server.py. |
| `bio: str = ""` | At most 120 characters, no line breaks | |
| `about: str = ""` | At most 1000 characters | **Replaces `profile`** (a rename). `profile` has no limit and is barely used: one key in data_access and 3 test lines. |
| `top3: Top3 \| None = None` | `Top3(title ≤ 30, items: tuple of exactly 3, each ≤ 40)` | A new frozen dataclass. The codec already handles nested records. |
| `website: str \| None = None` | https only, with a host | Stricter than the org URL check, which also allows http. |
| `colors: ProfileColors` | `background` and `outline`, each `#RRGGBB` | Defaults: `#F5F4F0` and `#202B29`. |
| `profile_visibility` | See (b) | |

**Avatars: later.** Show initials in a ring for now. When avatars are added, use a separate `Avatar` record keyed by the person's id, not a field on `Person`.
- **What it holds:** a WebP, PNG or JPEG, checked by its file signature, resized in the browser to 256×256, with a cap of **150 KB**.
- **How it's served:** its own endpoint, with a larger body limit for that route only.
- **Why separate:** some functions load every `Person` at once (`validate_email`, `personas`), and images would slow them down.

### b. Profile visibility

Add a **separate field** and leave `visibility` alone.
- **Why not reuse `visibility`:** it decides whether the person exists for a viewer at all, and the privacy code applies it to every edge they touch. A private person disappears from rosters, the network graph, "friends going" and event hosts, and their page reads as not found instead of locked.
- **The new field:** `profile_visibility: Visibility = Visibility.NETWORK`. It reuses the existing scale: `NETWORK` means public to logged-in users, `CONNECTIONS` means friends only, and `PRIVATE` means only you.
- **What it controls:** only whether a viewer gets the `full` profile or a `locked` one. It's one field checked in one place, so the states are easy to change later.

### c. Update function

A new `ProfileService` in `graph/profiles.py`, reached as `graph.profiles`:

```python
EDITABLE_FIELDS = {"username", "display_name", "bio", "about", "top3",
                   "website", "colors", "profile_visibility"}
def update(self, person_id: str, by_person_id: str, now: datetime, **changes) -> Person
```

- **Permission:** you can only edit yourself. Anyone else gets `PermissionDeniedError`.
- **Unknown fields:** rejected with `DomainRuleError("cannot edit fields: [...]")`, as `Event.revised` does.
- **Validation:** happens when the new `Person` copy is built, so every limit in (a) raises `DomainRuleError`.
- **Username taken:** `DomainRuleError("that username is taken")`.
- **Missing person:** `NotFoundError`.
- **Saving:** runs in `store.transaction()` and stamps `provenance.confirmed(now)`.

### d. Viewer function

`ProfileService.view(username, viewer_id, now) -> ProfileView` returns:

- **`person`:** the person.
- **`access`:** `FULL` or `LOCKED`, from `profile_visibility` using `PrivacyService`'s existing rules. You always get `FULL` on your own profile.
- **`connection`:** `self`, `none`, `pending_out`, `pending_in` or `connected`, from `connections.between`.
- **`in_common`:** `network.in_common(viewer, person).summary`. Skipped for yourself and for logged-out visitors.
- **`orgs`:** confirmed, current memberships where `privacy.can_view(viewer, edge)` is true.
- **`hosting`:** `calendar.host_events(person.id, viewer)`, public and private combined.
- **`going`:** upcoming RSVPs marked going. Each must pass `privacy.can_view` on the RSVP and `can_view_listing` on the event.

**Locked** returns only the username, shown name, verified flag, colours, connection state and in-common summary; the lists are empty.

**Hidden people:** if the viewer can't see the person at all, for example a logged-out visitor, it raises `NotFoundError`. This keeps the existing rule that hidden looks missing.

### e. Signup

`invites.redeem` sets `verification_level=UNIVERSITY_EMAIL`. That's always correct, because `validate_email` already rejects any address outside the allowed domains. Signup also takes the username: `redeem(code, email, username, now, *, display_name="")`. That changes 26 calls in the tests.

### f. Endpoints

A new `ProfileApi` class in `server.py`, next to `DiscoverApi`:

- `GET /api/profile?username=&viewer=` returns:
  ```js
  { username, name, verified, bio, about, top3: {title, items} | null, website,
    colors: {background, outline},
    access: "full" | "locked", connection, in_common: string | null,
    orgs: [{id, name}], hosting: [ItemLite], going: [ItemLite], is_self }
  ```
  - `name` is the display name as stored, possibly `""`. The page falls back to `@username`.
  - `ItemLite` is `{id, title, starts_at, ends_at, location, host: {name, kind}, is_cancelled}`.
- `POST /api/profile` takes `{viewer, ...changes}`, where `viewer` is the person acting (the same demo shortcut Discover uses). It returns the updated profile in the same shape.
- **Also needed:**
  - Raise the body limit to **8 KB** for this route only: 1000 characters of emoji can be about 4 KB.
  - Serve `/p/<username>` as `profile.html`. The sample-data page uses `profile.html?u=<username>` for now.

### g. Looking people up by username, and uniqueness

- **Lookup:** `ProfileService.by_username` reads through all people. That's fine at demo scale and matches `validate_email`.
- **Uniqueness:** checked in the service and in `redeem`, inside a transaction. The integrity audit (`integrity.py`) also flags duplicate usernames.
- **Remaining risk:** two signups at the same moment on the threaded server could both pass. A real guarantee needs a unique SQLite index, which means changing `sqlite_store.py` and is Keaton's call.

### h. Seed data and the existing database

The seed files belong to caumartin-maker, so these need coordinating:
- **`seed.py:267`:** the Person factory, also used by `seed_network`. It needs `username` (e.g. "drew"), and a couple of demo people could get a bio, about and Top 3.
- **`seed_bulk.py:373`:** usernames built from the generated names, with a number added when two collide.
- **Signup calls:** any `redeem` calls need the `username` argument.

**Re-seed is required.** The codec rejects stored records with unknown fields (the old `profile`) or missing required ones (`username`). Each of you runs `scripts/seed_test_db.py --reset` once. `data/` isn't in git.

### i. Tests

- **New `tests/backend/test_profiles.py`, run on both stores:**
  - every limit, just inside and just outside it
  - only you can edit, unknown fields are rejected, usernames are unique
  - `view` returns full or locked correctly for each visibility setting and viewer: logged out, stranger, friend, self
  - hidden looks missing
  - private orgs and RSVPs never leak
  - `redeem` sets the verified level
- **hypothesis:** the username pattern, hex colours, and saving and reloading `Person`.
- **`test_server.py`:** the profile endpoints' JSON, error codes and body limit.
- **Existing tests:** 18 places that build a `Person` (in 6 files) need `username`, and 3 `profile=` lines change.
- **Not yet checked:** the suite couldn't be run when this was written, because `uv` wasn't installed and `.venv` was missing `anthropic`.

### Files

- **Edit:**
  - `objects/person.py`, `objects/__init__.py`
  - `graph/invites.py`, `graph/network.py`, `graph/rsvps.py`, `graph/roster.py`, `graph/data_access.py`, `graph/integrity.py`, `graph/__init__.py`
  - `server.py`, `docs/BACKEND_REFERENCE.md`, `docs/CHANGES.md`
  - the test files above
- **New:** `graph/profiles.py`, `tests/backend/test_profiles.py`.
- **Commit order:**
  1. Fields, service and `redeem`, with tests.
  2. Server endpoints, with tests.
  3. Seed changes, made the same day, since tests and the demo database break until the seed has usernames.
  4. Connect the frontend `ProfileDataSource` to the real endpoint.
  5. Docs.

---

## 3. Conflicts with the profile spec, and open questions

**Conflicts:**
1. **Display name:** required in the backend, optional in the spec. Handled by `shown_name`.
2. **Profile privacy:** using `visibility` would cascade everywhere, hence the separate `profile_visibility`.
3. **Error codes:** not-found and not-allowed both return 400. A profile page wants 404 for "no such user", but changing that may break `test_server.py`.
4. **Who can see people:** `user_Orgs.md` says Person nodes are for admins only in the MVP. Later work has moved past that, so that line looks out of date.
5. **Website rule:** org websites allow http; the spec wants https for people.
6. **Request limit:** 4096 bytes is too small for a full profile edit.

**Open questions:**
1. **Usernames at signup:** ask for one, or suggest one from the email and let the person change it?
2. **Reserved names:** should some usernames be off-limits, like `admin`, `me` or `collectivize`?
3. **Changing usernames:** if usernames can change, old `/p/` links break. Is that acceptable?
4. **Defaults:** should new profiles start visible to all logged-in users (proposed) or friends-only?
5. **Avatars:** OK to defer them, with initials for now?
6. **Error codes:** 404 and 403 for every endpoint, or only for the new ones?
