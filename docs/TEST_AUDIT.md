# Test Audit: 2026-11-14

A full pass over the backend, the demo server, the scripts and the Discover page. Every bug below has a test that **fails on the current code**. Fixing the bug should make that test pass. No source code was changed.

## How to run

```
.venv/bin/python -m pytest -q tests/backend/test_audit_*.py      # 41 failed, 24 passed (~25 s)
```

> **Setup issue found first:** the committed `.venv` didn't have `anthropic` or `hypothesis`, and `uv` isn't installed on this machine. Because of that, the whole suite failed at collection (`ModuleNotFoundError: anthropic`). Both packages were installed into `.venv` with pip. Run `uv sync --group dev` (or install uv) so the environment matches `uv.lock`.

**Baseline before this audit:** 1415 passed, 28 failed, 3 skipped. All 28 failures are the known ones in `test_cohosting_gaps.py`, and they aren't repeated here.

| New test file | Area | Bugs | Failing / passing tests |
|---|---|---|---|
| `tests/backend/test_audit_server.py` | Demo server API | 4 | 5 / 9 |
| `tests/backend/test_audit_graph.py` | Core graph services, objects, storage | 6 | 10 / 1 |
| `tests/backend/test_audit_discovery_ai.py` | Ranking, discovery, AI search | 6 | 14 / 4 |
| `tests/backend/test_audit_seed_export.py` | Seeding, export, scripts | 8 | 12 / 10 |

In each file, tests under `# --- coverage` pass. They add checks for rules nobody had tested yet.

---

## 🔴 Privacy and consent bugs (fix first)

| # | Where | Bug | Test |
|---|---|---|---|
| P1 | `graph/calendar.py:60` | `host_events` filters with `can_view` and not `can_view_listing`. A co-host's calendar shows an event even when the viewer can't see its primary host. In the test, the event shows up in the **public** bucket. | `test_co_host_calendar_hides_event_whose_primary_host_viewer_cannot_see` |
| P2 | `graph/ai_search.py:217-220` | `top_orgs` counts every host org of events the viewer RSVP'd to, co-hosts included, without a visibility check. The name of a **PRIVATE** org is sent to Claude, which can repeat it back in reasons or the summary. | `test_ai_search_does_not_tell_the_ai_about_a_private_co_host_org` |
| P3 | `graph/search_tools.py:199-203` | The same leak exists in the agent's `get_active_orgs` tool. | `test_agent_active_orgs_tool_hides_a_private_co_host_org` |
| P4 | `graph/preferences.py:166-174` (`_org_tags`) | A membership marked `recommendation_use=NONE` still feeds its org's tags into the taste profile. Those tags reach the AI (`ai_search.py:216`, `search_tools.py:188`) and Discover's affinity score. This breaks the CHANGES.md promise that history marked NONE is never used. `activity.top_orgs` already checks the flag; `_org_tags` doesn't. | `test_membership_without_consent_does_not_feed_org_tags`, `test_ai_search_leaves_out_org_tags_from_a_membership_marked_none`, `test_agent_overview_leaves_out_org_topics_from_a_membership_marked_none` |
| P5 | `server.py:141-153` (`personas`) | The persona list shows each person's orgs no matter what visibility the membership has, so even PRIVATE memberships are listed to anyone. It's a demo-only endpoint, but it ignores the visibility rules. | `test_personas_do_not_reveal_private_memberships` |

## 🟠 Correctness bugs

| # | Where | Bug | Test |
|---|---|---|---|
| C1 | `graph/events.py:83-99` | `EventService.create` doesn't check that the event id is new. Passing an existing event with yourself as host overwrites it and adds a **second primary host**. That person can then edit the event, and `primary_host_id` breaks. | `test_create_cannot_take_over_an_existing_event` |
| C2 | `graph/series.py:100-117` | `update_defaults` isn't atomic and doesn't validate first. With `capacity=0` the call raises an error, but the series has already saved `default_capacity=0` and some instances have already been changed. `EventSeries` doesn't validate its defaults. | `test_series_update_defaults_is_all_or_nothing` |
| C3 | `graph/codec.py:101,121` | `ZoneInfo` time zones come back from SQLite as fixed offsets. Date arithmetic across a DST change is then off by one hour, so the memory and SQLite stores disagree. | `test_stored_datetimes_keep_their_time_zone[sqlite]` |
| C4 | `objects/edges.py:238-245` | `Rsvp.updated_at` accepts naive datetimes. Every other record calls `require_aware`. A naive value later crashes the decay math in preferences and activity. | `test_rsvp_rejects_naive_updated_at` |
| C5 | `graph/ranking.py:102` | `EventSignals.friends_going` is only checked with `< 0`, so NaN, inf and bools get through. NaN makes `likelihood` NaN, which breaks sorting. | `test_nan_friends_going_is_rejected_instead_of_giving_a_nan_likelihood` |
| C6 | `graph/discovery.py:119` | `RequestMatch(score=True)` is accepted, because a bool counts as an int. Discovery later crashes with `DomainRuleError`. | `test_request_match_rejects_a_boolean_score`, `test_a_request_match_that_validated_never_crashes_discovery` |
| C7 | `graph/search_tools.py:183-187` | `goes_to_categories` is sorted by strength only. Ties come out in set order, which depends on `PYTHONHASHSEED`, so the order the AI sees changes from run to run. `ai_search.py:210` breaks ties by label; this code doesn't. | `test_overview_orders_equally_strong_categories_deterministically` |
| C8 | `seed_network.py:97,102,104,148` | The demo seed isn't repeatable. It sorts random uuid4 ids before using the seeded random generator, so RSVPs, interests and travel differ between runs. The existing determinism test only compares titles and times, so it misses this. | `test_demo_seed_rsvps_interests_and_travel_are_the_same_every_time` |
| C9 | `seed_bulk.py:350,381,412` | Generated memberships start before the person's account exists: 38 of 198 at SMALL size. People are created 330–900 days ago, but memberships start 160–700 days ago. | `test_bulk_memberships_never_start_before_the_person_existed` |

## 🟡 Server (`src/backend/server.py`)

| # | Where | Bug | Test |
|---|---|---|---|
| S1 | `server.py:296-299` (`_int_param`) | `"²".isdigit()` is True but `int("²")` raises an error. So `GET /api/discover?page=²` returns **500** instead of 400. A fix is `value.isascii() and value.isdigit()`. | `test_unicode_superscript_page_is_a_400_not_a_500` |
| S2 | `server.py:192-211` | The search cache never expires and is never cleared. After a viewer RSVPs, repeating the same search still shows `my_rsvp: null` and old going counts until the server restarts. | `test_cached_search_reflects_a_new_rsvp` |
| S3 | `server.py:277-278` (`_step_json`) | Each step's input is the model's raw tool input, recorded even when the tool rejected it. If the model sends `tags: [1, 2]`, `", ".join` raises `TypeError` and the **whole search returns 500**. | `test_step_text_survives_odd_tool_input` |

Not tested, noted from review:
- `server.py:192`: a cache hit still waits for `_search_lock`. While one live AI search runs (several seconds), every cached search is blocked behind it.
- `server.py:371`: an empty or zero-length body returns **413 "request too large"**. 400 or 411 would be accurate.

## 🟡 Scripts

| # | Where | Bug | Test |
|---|---|---|---|
| X1 | `scripts/check_graph.py:33` | It's documented as read-only, but opening the file creates the tables and **stamps an unstamped file as a test DB**. That makes a real but unstamped database eligible for deletion by `seed_test_db --reset`. | `test_check_graph_never_changes_an_unstamped_file` |
| X2 | `scripts/check_graph.py:33` | It crashes with a traceback on a DB stamped for the other environment (`EnvironmentMismatchError`) and on a file that isn't a database (`sqlite3.DatabaseError`). | `test_check_graph_reports_a_wrong_environment_database_cleanly`, `test_check_graph_reports_a_file_that_is_not_a_database_cleanly` |
| X3 | `scripts/check_graph.py:28` (same pattern in `seed_test_db.py:44`, `export_ml_dataset.py:64`, `ai_search.py:43`, `serve.py:35`) | A bad `COLLECTIVIZE_ENV` such as `prod` crashes with an uncaught `ConfigError`. | `test_scripts_report_a_bad_environment_name_cleanly` |
| X4 | `scripts/export_ml_dataset.py:68` | It crashes with a traceback on a DB stamped for the other environment. | `test_export_reports_a_wrong_environment_database_cleanly` |
| X5 | `scripts/ai_search.py:66` | In the default agent mode, `--limit` and `--radius` are silently ignored. `--limit 1` printed 5 results. | `test_ai_search_agent_mode_honours_limit`, `test_ai_search_agent_mode_honours_radius` |
| X6 | `scripts/ai_search.py:56` | `--radius 0` is treated as not given, so a radius is sent without a starting point. `--radius 0`, `--radius -2` and `--limit 0` all crash with tracebacks. | `test_ai_search_bad_numbers_fail_cleanly` |

## 🟡 Frontend (`src/frontend/discover.js`), from review, no automated test

- **F1, line 132:** choosing **"Logged out"** doesn't stick after a reload. The saved viewer `""` isn't a persona id, so `known` is false and the page switches back to `personas[0]` (Drew).
- **F2, line 351:** when `going_count` is `null` because a small count is being hidden (1–4 people going), the card says **"Be one of the first"**. That's wrong when people are already going. Add a separate "hidden" state from the server.
- **F3, line 77:** a `?page=` above 1000 in the URL shows an error, because the server rejects it. The page should clamp it the way it already clamps pages past the end.

---

## Not confirmed: policy questions for the team

These were seen to happen, but whether they're bugs depends on what the spec intends:

- **Exports of private data.** `ml_dataset` exports memberships whatever their visibility; all 170 in SMALL are non-public. It also exports connections that a person has hidden.
- **`person_data` (data access)** has no staff check. Any caller who passes a `staff_purpose` string gets anyone's data.
- **`can_view` on `Connected` edges** ignores `hide_connections`. Only `network._connection_shown` checks it.
- **Lifecycle.**
  - Ended events can still be cancelled or archived.
  - Moving `ends_at` into the past on an event that's under way ends it at once and freezes RSVPs.
- **Erasure** leaves the erased person's id in `provenance.created_by` on records that don't otherwise involve them (`Hosts`, `PartOf`, `Tagged`, `InstanceOf`).
- **Hidden co-host orgs** still affect tag filters, affinity and the org filter (`discovery._tag_ids`, `_org_scope`). Filtering by a private org's id can reveal what it co-hosts.
- **AI search ranking.** An event outside the 60 candidates gets a neutral match score, while a candidate the AI skipped gets 0. So an event the AI never saw can outrank one it rated low.
- **`load_config`.**
  - `COLLECTIVIZE_ENV=""` is an error, but an empty `COLLECTIVIZE_DB_PATH` counts as unset.
  - A relative DB path resolves against the current directory, not the project root.
- **`EventSeries.instantiate(**overrides)`** raises a duplicate-keyword `TypeError` when given `ends_at` or `visibility`.
- **`person_data["hosted_events"]`** includes co-host invitations that are still pending.
- **Seeding speed.** CHANGES says a LARGE seed takes about 6 s; it took about 75 s into SQLite here.
- **`BulkScale`.** `chapters_per_campus > 10` crashes, and `campuses > 3` is silently capped.

## Checked and fine

- The integrity audit is clean on the demo, SMALL and LARGE worlds, both before and after reloading from SQLite.
- No credentials appear in errors or logs.
- The agent loop's caps and malformed-output handling are correct. `parse_answer` handles NaN and bad shapes.
- Static file serving blocks every traversal case that was tried. `discover.js` never uses `innerHTML`.
- Exports don't leak display names, real ids, or the pseudonyms of erased people.
