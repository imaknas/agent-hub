# Showing the user a project's files during a lease (m10251606)

> **Status**: the owner confirmed A–E on 10-25 and it was implemented the same day; when done, ask the overall-design
> session to review it.

Source: the assistant's request `m10251606-7801` (10-25 16:06). At 10-25 16:03 the owner asked in idea-1's lease
"can you show me the image?"; the session had no tool to give it to him and could only tell him to go back to the
computer and open `results/render-v2/*.png`. Today a file can only travel from him to the session (attachments), there
is no way back. Done when (the assistant's criteria): idea-1's session can make the render-v2 png appear directly in the
lease; files outside the folder, of the wrong type or too large are blocked, with tests.

## In one sentence

The session calls the platform's **"show the user" tool** `show_file(path, summary?)` → the runner checks it and
**copies the file** into the lease's own directory → sends a v2 event `file` → the assistant fetches the file with
`GET /v1/leases/<id>/files/<file id>` and shows it inline in the lease.

## How the session says "show him this" (proposed: a platform tool, not a command, not guessing from the reply)

| Approach | Problem |
|---|---|
| Detect file names mentioned in the session's reply | Depends on the model's wording; mentioning is not wanting to show, and it misses some |
| Platform command `hub-show <path>` (Bash) | **discuss mode has no Bash** (a read-only (discuss) lease can't use it); in work mode the owner has to press allow for every command, once per image |
| **Platform tool `show_file` (proposed)** | One explicit call; works in both discuss and work; no allow to press |

- **The neutral part**: "show the user a file" is a platform service, not something belonging to one agent. The runner
  opens an internal endpoint `POST /v1/internal/<lease>/show {path, summary?}` (like today's
  `/v1/internal/<lease>/relay`, only for the lease's own relay); all checks, copying and events happen in the runner.
- **How Claude Code is wired**: the lease already has the platform's MCP server attached (`lease-relay.py`, today only
  the `approve` permission relay); it gets one more tool, `show_file`. The relay only forwards the call to the runner
  and returns the result (success / the reason it was blocked) to the session. The tool has to be on the auto-allow
  list of the lease judgment points (`llm-points` lease-session/lease-work; **that file currently belongs to another
  session**, see the end).
- One line added to the system prompt: "to let the owner see an image or file in the project, use show_file(path in
  the project, a one-line note); he sees it directly on his phone."
- Another agent: its implementation connects the same endpoint to its own tool mechanism; the event and the endpoint
  stay the same.

## The runner's checks (on failure the reason goes back to the session, no event is sent)

1. **Inside the project folder**: the path is resolved relative to the project folder; after resolving to the real
   path it must be inside the folder (`../` and symlinks pointing out are blocked), and it must be a regular file.
2. **Type**: only the assistant's list `png jpg jpeg webp gif pdf glb txt md json csv`; the mime comes from the
   extension, **and the start of the content is checked too** (magic bytes for png/jpg/gif/webp/pdf/glb; txt/md/json/csv
   must be UTF-8). Renaming the extension does not get through. **No svg or html** (the assistant is a web page, and
   these two can carry code).
3. **Size**: each file ≤ 20 MB; at most 100 files and 500 MB in total per lease within the last 24 hours (a lease
   is resumed for days, so a lifetime cap stopped one mid-evening, `ab58bc5`); the 30-day retention below bounds the
   disk.
4. **Nothing that looks like a credential**: file names like `.env*`, `*.pem`, `*.key`, `*secret*`, `*token*`,
   `credentials*`, or paths declared in the project registry's `secrets:`, are always refused (even if the extension is
   on the allow list, e.g. `secrets.json`).
5. **The owner does not press allow**: what he is shown is a file in his own project and it goes nowhere but to him;
   every time is recorded in the events (visible on the phone).

## Why copy it

- After the check the file is copied to `.state/lease-runner/<lease>/files/<file id>` (the session's sandbox cannot
  write there), and only that copy is ever sent:
  - If the file is swapped between the check and the sending (replaced by a symlink, content changed), it does not
    matter: what is sent is the copy taken at check time.
  - The assistant wants "still there when reopened": if the session later changes or deletes the file, the lease
    still shows the image as it was then.
- Each file is kept 30 days from when it was shown (the runner cleans up once an hour), about as long as the
  conversation log.

## Contract (v2)

**Event** `file` (added to the event types in `lease-runner.v2.schema.json`):
```
{type: "file", id, path (relative path in the project), name, mime, size, summary?}
```

**Fetching** `GET /v1/leases/<lease id>/files/<file id>` (the same Bearer token):
- 200: the file content, `Content-Type` = mime, `Content-Length`, `Content-Disposition: inline; filename=…`,
  `X-Content-Type-Options: nosniff`
- 404 `not_found` (no such lease/file, or past the retention period)
- Still fetchable after the lease is detached (within the retention period).


## Privacy

Idea sandboxes are `privacy: stats_only` (content is not sent to cloud models). Like photos the user sends in, files
**must be marked local_only on the assistant's side** and not sent to the cloud; say so clearly in the reply.

## Tests

- Checks: outside the folder (absolute path, `../`, symlink pointing out, a symlink inside the folder), not a regular
  file, wrong type (right extension but wrong content, svg, html), too large, over the lease limit, a credential-like
  file name, a secrets path declared in the registry: all blocked, no event sent, the session gets the reason.
- Normal: png → the event matches the v2 schema, fetching returns the same bytes and the right headers; if the original
  is later changed or deleted, fetching still returns the copy from then.
- The relay's `show_file` tool: forwards to the runner, returns the success/refusal reason to the session.
- Live test: idea-1's session (with the same settings as a lease) calls show_file on `results/render-v2/*.png`; the
  event and the fetch both work.

## For the owner to confirm

**A. Use a platform tool `show_file`** (proposed), not a command, no detection in replies.
**B. Copy after the check, then send** (proposed), kept 30 days.
**C. Types from the assistant's list, check the start of the content, no svg/html; 20 MB per file, 50 files / 200 MB
per lease** (proposed; changed the same day to 100 files / 500 MB within 24 hours, `ab58bc5`).
**D. Never send credential-like file names or secrets declared in the registry** (proposed).
**E. No allow to press** (proposed): it is a file of his own project, shown to him; every time is recorded in the
events.

## Split of work with the other session (overall design) (agreed 10-25)

- **Now**: in the current format of `contracts/llm-points.yaml`, this work adds one entry
  `"mcp__lease_relay__show_file"` to the `allowed_tools` of lease-session and lease-work, regenerates
  `tests/fixtures/llm-golden.json` with `LLM_GOLDEN_UPDATE=1`, and confirms the diff is **only** that entry added to
  `--allowedTools` for those two judgment points × 4 variations. `llm.py` and `runtime_claude.py` are not touched (the
  other session is changing layer 6 there).
- **Later**: when the overall design moves llm-points to the neutral format, it declares
  `platform_tools: [show_file]` (the neutral name of the platform tool), and `runtime_claude` translates it to
  `mcp__lease_relay__show_file` in the allow list: the same principle as translating kind, the translation table lives
  only in the runtime. Acceptance: the golden does not change at all after the switch.
- The runner, relay, contract and tests are done by this work.

## Order

1. Runner: `/v1/internal/<lease>/show` (check, copy, event) + `GET …/files/<id>` + v1 skips `file` + retention cleanup +
   tests.
2. Add the `show_file` tool to the relay; one line added to the system prompt.
3. Contract v2 describes the `file` event and the fetch endpoint.
4. llm-points lease-session/lease-work allow `mcp__lease_relay__show_file`, regenerate the golden, check the diff (the
   section above).
5. Live test with idea-1; reply to `muninn` (event, endpoint, privacy); `hubbus done`.
