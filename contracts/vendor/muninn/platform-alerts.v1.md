# Alerts from the platform Muninn runs on (v1)

The platform that runs Muninn and the user's other projects watches its own parts (scheduled jobs, services, the
addresses they are reached at) and tells the user when one breaks. This is how it tells Muninn: Muninn puts the alert
with the user's other reminders and pushes it to the phone. Muninn reads only what is written here
(`src/muninn/core/platform_alerts.py`).

Muninn is one way the platform tells the user, not the only one: when Muninn itself is down, nothing sent here arrives,
so a platform keeps another way out.

## Sending

`POST /api/alerts`, the ingest token as a Bearer token, a JSON body:

| Field | |
|---|---|
| `v` | `1` |
| `source` | what is reporting: lowercase letters, digits, `-`, `_` (at most 64 characters), e.g. `brunna` |
| `key` | the platform's stable name for the problem (at most 200 characters). It names a kind of problem, which may cover several things at once (several jobs failing): the list changing is the same key with a new `at` and `text` |
| `state` | `open`: the problem is there; `resolved`: it is gone |
| `at` | when this notice was made, RFC 3339 with a time zone. Sending the same notice again (a retry) keeps the same `at` |
| `text` | one line the user reads first, as it is shown (in the user's language). For `open`, what is wrong (the first thing and how many); for `resolved`, what is working again |
| `detail` | optional: more lines (the whole list, what to do) |

Members not listed are ignored.

## What Muninn does

- A source and key are one problem. Each `open` notice with a newer `at` is said again: back among the unread
  reminders, as one reminder with a count, not one more beside it, and pushed. The user saying they saw it is not
  the problem going away: the next `open` notice says it again. How often that is, the platform decides (Muninn adds
  no timer of its own).
- These pushes do not count against Muninn's daily push limit. Quiet hours hold them: a notice that arrives during
  them is kept unread, and pushed when they end if its problem is still open by then.
- `resolved` takes the open problem's reminder away and says, once and without a push, what is working again.
- A notice whose `at` is not newer than the last one for that source and key (a retry, or one overtaken on the way)
  changes nothing.

## The answer

Only a 200 means Muninn has the notice. A platform tries again after anything else.

| Status | Body | |
|---|---|---|
| 200 | `{"ok": true, "outcome": "new"}` | a problem not open before |
| 200 | `{"ok": true, "outcome": "reminded"}` | the open problem, said again |
| 200 | `{"ok": true, "outcome": "repeat"}` | a notice already had (same or older `at`): nothing changed |
| 200 | `{"ok": true, "outcome": "resolved"}` | the open problem is gone |
| 200 | `{"ok": true, "outcome": "not_open"}` | `resolved` for a problem not open: nothing to take away |
| 400 | `{"error": "..."}` | the body does not keep to this contract: sending it again will not help |
| 401 | `{"error": "unauthorized"}` | not the ingest token |
| 5xx, or no answer | | not had: try again |
