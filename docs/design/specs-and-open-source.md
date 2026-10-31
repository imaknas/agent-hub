# Specs, open source, articles: three tracks pushed together

> agent-hub session · 2026-10-27 · decided by the owner on 10-27 to push them together. Decisions: the first spec =
> task handoff between people and agents; license Apache-2.0; specs and articles mainly in English, Chinese written
> separately; the specs go in a new standalone repo (local only at first; going public needs the owner's confirmation).

## Why

Everyone is building their own agent wheel; what lasts is the protocol, not the implementation. But a spec that is only
a document with no implementation is rarely adopted (MCP came out as spec + SDK + example servers together). So: **from
the contracts agent-hub already runs, versioned and tested, extract the few pieces existing standards do not cover**,
write them as implementation-independent specs, with agent-hub as the reference implementation, and then make a second
implementation work too.

**Align first, don't rebuild**: tools and data → MCP; project instructions for agents → AGENTS.md (both in the Linux
Foundation's Agentic AI Foundation); delegating tasks between agents → A2A; the format of agent memory → a W3C
community group is working on it (focus: memory units, encryption, deletion). At these layers agent-hub is only a
user.

## Three tracks

| Track | Output | Prerequisites | What the owner does |
|---|---|---|---|
| Specs | `agent-ops-specs` repo: **Principal Handoff** (renamed and split from HTH 0.1 on 10-27): Core 0.2 + Tasks 0.2 + Questions 0.1, each with schema, conformance test data, reference implementation; agent-hub passes the Tasks (0.1) conformance tests | none | create the GitHub repo when going public |
| Open source | agent-hub public | finish new-user step 6 (git rm the old files) → personal-name ratchet to zero (313 now) → step 7, carefully rewrite history → replace AGENTS.md (align with the standard) → LICENSE, README, CONTRIBUTING | force push, make the repo public |
| Articles | long English pieces + Chinese versions: ① the operations layer for one person running a dozen-plus AI coding projects (memory, mailbox, permissions, scheduling); ② why things only you can handle need a protocol (introducing Principal Handoff); ~~③ config as code and isolated rehearsal~~ (10-27 the owner: nothing special, folded into ① as a section) | spec v0.1 has a draft (article ②), open source done (article ① needs to link it) | choose where to publish, final review |

Progress (10-27): HTH 0.1 draft, conformance test data and reference parser in `~/workspace/agent-ops-specs` (1200eb8,
local only). agent-hub runs the zh-hant set (`tests/test_hth_conformance.py`, 994a4f2): on the first run it caught two
deviations already in production: task registration did not accept full-width brackets or a key marker with no space
before the owner tag, and gen-todo, under an unrecognised group subheading, assigned lines to the previous group (lines with an
unrecognised tag were silently dropped). Both fixed; the keys of existing data did not change. A good example for
article ②: write the spec clearly first, and the conformance tests catch holes the implementation never noticed itself.

Order: the spec draft and the article outlines now (they touch nobody else's things, nothing external); open source
waits for tomorrow's wrap-up; article ② after spec v0.1, article ① after open source.

## Spec candidates (the next few)

1. **Principal Handoff** (the first, formerly Human Task Handoff): how an agent marks something "only a person can do",
   registers it under a stable key, how the person's side closing/cancelling/reopening comes back, how an agent that
   infers it is done gets the person to confirm. Sources: `contracts/state-todo.v1.md`, `bin/knas-tasks.py`, hubbus
   task/done/drop.
2. **Project State Document**: the structure and update rules of the status file (status, to-dos, references must point
   to files that exist).
3. **Session Lease & Handover**: borrowing a running project agent from another interface, with a structured handover at
   the end (`contracts/lease-runner.v2`, `lease-handover.v1`).
4. **Agent Project Manifest**: the project registry (jobs, services, privacy, permissions, credential declarations).

## Comparison with existing protocols (10-27, agent-ops-specs b657b33 `RELATED-WORK.md`)

The owner asked "does the AI world have no protocol like this?". Compared against the original texts of A2A 1.0, MCP
2026-07-28 (Tasks extension, elicitation), AG-UI 1.0, CHAP 0.3.3, iCalendar VTODO/iTIP, JSCalendar/JMAP Tasks,
TASKS.md, and the human-in-the-loop of the various agent SDKs:
- **All of them put the step that needs a person inside a unit that is still running** (one call, a task on an agent
  server, a run), and the system that created it decides whether it is done. HTH handles things that "only the owner can
  do, outlive any run, and are closed by the owner's side". Three independent studies reached the same conclusion.
- **But this piece is thin**: about 80% of the transport layer A2A already has (long server-side tasks, ListTasks,
  at-least-once webhooks, URI extensions). What is really HTH's own: only the owner's side counts (an agent's done is
  just a claim), reopening the same key, a deleted line only means "inferred done, the owner must confirm", keys
  computed from content, the Markdown binding. So it is positioned as "a thin layer of owner-authority semantics +
  Markdown binding on top of A2A, borrowing JSCalendar's vocabulary", not yet another agent protocol. Article ② is
  written from this angle.
- **The closest, CHAP** (Brightbeam, 2026-06 paper, Apache-2.0): a multi-person workspace for organisations, needs a
  coordinator server, any member can complete a task; no text form in the repo, no inferred completion. The overlap is
  only "a task assigned to a person".
- To decide for 0.2 (SPEC §12): which A2A binding (one long task per project carrying HTH events, or one A2A task per
  HTH task; the latter cannot express reopened), whether `dropped` maps to CANCELED or REJECTED, aligning state names
  with JSCalendar, writing the AAIF approval gate's "silence is not closing" as a rule.

## Converging on a direction (10-27, three rounds of blind review)

The owner's starting point: Muninn (the assistant) is a personal AI agent of the OpenClaw kind (with many competitors on
the market), and part of agent-hub's features will get wrapped into agent offerings; the goal is to separate the two
sides, so that other agent-hub-like platforms can connect to Muninn, and future agent frameworks can adopt this protocol
to connect to agent-hub-like platforms. Results of three rounds of blind review (third parties who could not see each
other's or my conclusions):
- **N×M is real, but the "personal agent ↔ coding agent" line is already filled by ACP + MCP** (verified: OpenClaw uses
  ACP to open Claude Code/Codex/Gemini sessions, and `openclaw mcp serve` gives conversations and permission prompts to
  coding agents). So no big separate "agent ↔ platform" protocol, but a **profile that combines existing standards**:
  borrowing a project session → **ACP** (the lease runner's methods map almost one to one); reading state, permission
  prompts → **MCP**; things only you can handle → **Principal Handoff** (our own piece).
- **Principal Handoff converges to**: Core (envelope, id, delivery, "only the principal's side can close; a claim is not
  closing; silence is not closing") + two extensions: **Tasks** (things like payments and signing contracts, written in
  STATE.md, key computed from the text) and **Questions** (things you have to answer or decide, as messages; the key is
  given by the asker, the answer can be amended, a default is only provisional, it records whether you or your assistant
  answered). Where a task and a question differ in nature: identity, who decides whether it is still needed, what
  amending means, the answer has to reach the project. "Principal" is domain jargon (the party represented in an agency
  relationship), because the recipient is not necessarily the person themselves (it may be an assistant).
- **Two rounds agreed**: the decoupling is mostly already done in the code; what is left is that Muninn's handoffs can
  only be fetched with a local command (`muninn handoffs --json`) and the record relies on reading file paths: that
  should become HTTP. Adoption comes from "things you can install" (an MCP server, an OpenClaw plugin), not spec
  documents; no more specs until someone outside implements one side.

## Proof of interoperability

~~Earlier: "the cheapest second implementation is a CalDAV/JMAP task list as the owner side"~~: the first round of
blind review pointed out that this lets agent-hub do without Muninn, the opposite of the owner's goal. Changed to one
proof on each side: **another platform connecting to Muninn** (build a minimal platform from the spec alone; wherever
it gets stuck is a gap), and **another agent framework connecting to agent-hub** (the lease runner speaks ACP; see
whether OpenClaw can borrow agent-hub's sessions directly). Experiments in order: ① the lease runner becomes an ACP
agent ② Muninn's handoffs get HTTP listing + confirmation ③ Muninn's local-projects aligns with the Tasks `en` format
(today it is TODO.md + heading keywords, inconsistent with the spec) ④ the principal side as an MCP server / OpenClaw
plugin. Only when all pass do we take it to the community (the AAIF Workflows working group).

## Principal Handoff 0.3 (10-27, agent-ops-specs c8727ed + 2fe74dc)

Redone from the ground up after five rounds of blind review, not patched: each item has two registers with owners (the
need belongs to the asker: raised/amended/withdrawn; the settlement belongs to the principal's side: settled/reopened);
claims and notes change no register; every settlement records who decided and the kind of authority (decided
personally / personally confirmed a proposal / decided on their behalf within delegated authority / determined from
evidence); principal = the configured root (a natural or legal person; it cannot be a software agent, nor a
participant; the agents in between are all delegates). Acts (formerly Tasks, keeping the Markdown binding) and
Decisions (formerly Questions) sit on top. Explicitly out of scope: the format of a delegation of authority, a group's
internal decision procedure, the principal asking a project (asks, linked with derived_from), transport. A last search
for precedent: nobody has done the whole set; the closest are OpenID AuthZEN's approval profile, IETF WIMSE AIMS, and
singla's definition of principal, now written into RELATED-WORK with how we align. A cold review found 17 defects, all
fixed (2fe74dc); the reference implementation passes 16 tests, independent of arrival order.

**Next: converge the implementations.** agent-hub currently runs HTH 0.1 (through an adapter), and Muninn does not
follow 0.3 yet either. For spec and implementations to converge, agent-hub's task registration/bus and Muninn's contract
(v2) have to move to 0.3 records: cross-project, to be coordinated with Muninn.

## Going public (10-27)

The owner on 10-27: "first make agent-ops-specs a public GitHub repo (with Pages), check it before opening it". Checked:
the tracked files, every version and commit message of the 8 commits on main (no hits for names, local paths or common
credential formats; gitleaks is not installed, scanned with patterns), commit email the same as the already public
sycophancy-eval / the-crucible. Only main and the tag were pushed; the agent-memory branch stays on the hub machine.

- repo: https://github.com/imaknas/agent-ops-specs (public), tag `principal-handoff-0.3`
- site: https://imaknas.github.io/agent-ops-specs/ (GitHub Pages, root of main; relative links turned into .html, the
  schemas and conformance data served as raw JSON)
- agent-hub's registry origin changed to GitHub, added to mirror-list (config f943f7f)
- Zenodo (following sycophancy-eval: `.zenodo.json` primary, CITATION.cff for GitHub; author: the owner, with ORCID):
  GitHub Release `release-2026-10-27` → DOI 10.5281/zenodo.23680065, concept (always the latest)
  10.5281/zenodo.23680064. The spec's version tag `principal-handoff-0.3` stays; the release uses a date tag, because
  Zenodo only takes releases made after the switch is on and whose snapshot contains `.zenodo.json`.
