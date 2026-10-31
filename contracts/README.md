# contracts/

The interfaces between modules, one file per interface, versioned. **A contract for other repos to read (it gets
vendored over there) states only the format, the meaning and the version, not which program or model on this side
produces it**: that goes in this README; files for others don't mention this platform's name either (some of those
repos are open source, and the vendored copy gets committed there).

| Contract | Who produces it (implementation on this side) | Who reads it |
|---|---|---|
| `todo-md.v2.md` (v1 alongside) | `bin/gen-todo.py` (the librarian, nightly, zero LLM; headings in the UI language, marker v1 for the zh-Hant set, v2 for the English one); the librarian then adds the memory-gaps and state-anchors blocks | the owner, the owner's personal assistant (vendors v1; needs v2 before it reads an English instance) |
| `state-todo.v2.md` (v1 alongside) | the librarian (model rewrite, in the content language's token set) + `bin/check-state-todo.py` (format check) + `bin/registrar.py` (stamps keys mechanically, deletes/re-adds by key); both token sets defined once in `bin/todo_tokens.py` | `bin/gen-todo.py`, `bin/registrar.py`, the personal assistant (vendors v1) |
| `reflect-week.v2.schema.json` (v1 alongside) | `bin/weekly-reflect.py` (every Monday 07:00; writes v2 since 2026-W45, the report page reads both) | the personal assistant's weekly reflection (vendors v1) |
| `hubbus.v1.schema.json` | `bin/hubbus` | the platform itself (sessions, bridge, librarian, status page) |
| `llm-points.v1.schema.json` | `contracts/llm-points.yaml` (edited by people; `bin/llm.py` builds the flags from it, contracts-check validates it nightly) | the personal assistant (its own judgment-point registry follows this format; the contents are not shared) |
| `project.v1.schema.json` | people (or a manifest.update letter) edit `projects/*.yaml`; `bin/registry.py` validates | the platform itself |
| `managed-files.md` | — | the list of files the platform writes into projects |
| `vendor/<project>/` | copies of contracts defined by other projects | the programs on this side |

- `{owner}`: the owner tag in machine-read rules; this side fills it from `hub.json` `owner.tag` (here, the owner's
  name); readers use their own settings.
- Versions: adding a field/adding explanation keeps the version; renaming or changing a meaning bumps it, and the
  producer keeps both versions side by side until the readers have switched.
- Checks: `tests/test_contracts.py` (the examples against their own contracts and the copies, ②),
  `bin/contracts-check.py` (nightly; copies vs originals compare only the machine-read parts: the regex/heading blocks
  of markdown, JSON schemas without `$id`/`title`/`description`, ①; plus real data).
- License: the contracts are Apache-2.0 like the whole repo (`LICENSE` at the root). No separate notice in file headers
  (JSON has no comments, and changing a header would also make a copy differ from the original), and no NOTICE file; a
  vendored copy placed in a repo that is also Apache-2.0 is enough, no NOTICE needed. Other parties' contracts vendored
  in here keep their original licenses.
