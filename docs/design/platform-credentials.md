# Credentials for unattended jobs are the platform's own grants

Status: design, 11-15, not built. Measured the same day (Claude Code 2.1.323 from the store, Codex 0.167.0). Nothing
here is implemented; the owner's open questions are at the end.

## The problem

The platform's scheduled and unattended agent runs log in with the headless token and never reach the macOS Keychain:
every token run has a refusing `security` first on PATH (DV5 (c), `runtime-versions.md` section 3a and the run table in
step 2 item 5; known-issues "The OS sandbox cannot block the Keychain"). Claude Code keeps the owner's subscription login
**and** every MCP server's OAuth tokens in one Keychain item, so a job can't be given one server's token without the
login and every other server's. The Keychain also can't be read while the screen is locked (measured 11-14 at 02:30:
"couldn't read the stored credentials").

So a project's daily agent job that needs a remote, OAuth-protected MCP server (Upwork's) has failed since 11-13: run
sealed on the token, it has no server; run on the shared config dir, it would need the Keychain, which DV5 keeps from it
and which is locked at night anyway.

The plan of 11-14 (an owner-only `mcp_servers:` on a job, run without the refusing `security`) was reviewed blind; its B2
was that it reopens the whole Keychain item to that job. The owner rejected it, and any other stopgap that opens the
Keychain to a job, on 11-15.

## The owner's decision (11-15)

**Credentials for unattended jobs are separate grants held by the platform itself.**

- They live in the platform's secrets directory (files, mode 600), are declared in the registry's `secrets:`, are
  checked daily by `brunna/secrets` and are in the nightly backup. Files, so they can be read with the screen locked.
- For an OAuth MCP server the platform gets **its own** authorization, separate from the owner's interactive one, so a
  refresh-token rotation on one side never invalidates the other. It keeps the grant fresh and hands it only to the
  job's server connection: the model never sees it and nothing in the run touches the Keychain.
- DV5 stays as it is: no platform run ever reads the Keychain.

## Measurements (11-15)

Each case ran the store's Claude Code in a throwaway `HOME` and `CLAUDE_CONFIG_DIR`, with a refusing `security` first on
PATH that logs its arguments (as `conformance_claude` does). The server was a small local streamable-HTTP MCP server with
one tool that logs every request's headers. It answers 401, with RFC 9728 resource metadata, unless it gets its dummy
bearer token. No external account was used. Only M6 called a model (one haiku run on the headless token, taken in memory
the way `llm.py` takes it). Codex was checked by its CLI and binary only.

**M1, a header in the server's config.** `{"type": "http", "url": …, "headers": {"Authorization": "Bearer ${VAR}"}}` is
accepted, expanded from Claude's environment and sent on every request: `server/discover`, `initialize`, `tools/list`
and the GET stream.
- With the header, Claude runs no OAuth for that server: no `/.well-known` request, and the Keychain calls are only the
  login lookups any run makes (`find-generic-password` on the config dir's own hashed items), the same count as a run
  with no server.
- A rejected header fails visibly: "Server rejected the configured Authorization header (HTTP 401) … OAuth fallback is
  disabled when headers.Authorization is set".
- An unset variable is sent as the literal text `${VAR}`, with a warning in `claude mcp list`.
- The debug log writes `"Authorization":"[REDACTED]"`. `claude mcp get` prints the configured value: the `${VAR}`
  template, or a literal header in clear.

**M2, the same server with no header.** "Needs authentication". Claude fetched the protected-resource and
authorization-server metadata, made 16 credential lookups, and made three `security -i` calls. The stub logs only
arguments, not what `-i` reads on stdin, so that these are Keychain **writes** is an inference (`-i` is how a caller keeps
a secret off the command line). A side finding: for the same reason the conformance check's logging stub can't tell
these calls from reads either. Its refusal still holds.

**M3, an authenticating proxy (loopback TCP).** Claude was pointed at a local proxy that adds the real token and
forwards only `/mcp`. It connected.
- With no header of its own, an upstream 401 passed through made Claude start OAuth against the proxy: metadata, a
  `POST /register` (Dynamic Client Registration), and two `security -i` calls.
- With a per-run header (a nonce the proxy checks and replaces), the same 401 failed visibly, with no OAuth and no
  `-i` call. So with an HTTP connection, a header of the run's own is what stops the fallback.

**M4, a stdio bridge.** A small stdio MCP server, started by Claude from the run's config, that reads the token from a
file, forwards to the remote server and passes `tools/call` only for the tools it was given. It connected.
- The Keychain calls were the login lookups only.
- A rejected token reached Claude as a JSON-RPC error ("credential rejected by the server"): failed, no OAuth (a stdio
  server has none).
- `claude mcp list` prints the bridge's command line: paths, never a value.
- **Not measured, though C5 chooses this delivery** (measured only through `claude mcp list` in a throwaway config dir):
  - no `tools/call` through the bridge (M6's model run went through the proxy);
  - the bridge's tool filter and refusals (the dummy server has one tool);
  - a `-p --strict-mcp-config --mcp-config` run with the bridge;
  - SSE answers or the GET stream (the dummy answers JSON only; the measurement proxy and bridge buffer);
  - a run on the shared `~/.claude` that agent jobs actually run on (the run table's agent-shaped row).

  Step 3 measures all of these before it is merged.

**M5, the `--mcp-config` path the platform's runs use.** A `-p` run with `--strict-mcp-config --mcp-config <file>` loads
a header-bearing HTTP server from that file and connects before the model is called. The platform's own stdio server is
attached the same way today (`confined-mail-tools.md` M2).

**M6, what the model can reach.** One haiku run, stream-json, `dontAsk`, allowed only `Bash(printenv PCRED_CANARY)` and
the dummy server's tool, through the nonce-bound proxy:
- The Bash tool printed a canary variable set only in Claude's own environment, so **the model's shell inherits
  Claude's environment** (one canary, in one run). The binary also holds an env scrub setting
  (`CLAUDE_CODE_SUBPROCESS_ENV_SCRUB`). It wasn't set in this run, and what it removes wasn't measured. The platform
  doesn't rely on it.
- The tool call reached the server with the real token.
- The init event listed the server as `connected`, with no URL or header in it.
- The nonce appeared in no stdout, transcript or file of the run's config dir. The headless token appeared in none of
  them either.

**M7, Upwork's discovery metadata** (unauthenticated GETs only; nothing registered, nothing logged in):
- Protected resource: `https://mcp.upwork.com/mcp`, authorization server `https://mcp.upwork.com`,
  `bearer_methods_supported: ["header"]`.
- Authorization server:
  - endpoints: authorize at `https://www.upwork.com/ab/account-security/oauth2/authorize`, token at
    `https://www.upwork.com/api/v3/oauth2/token`, and a revocation endpoint;
  - `grant_types_supported: authorization_code, refresh_token, client_credentials`, PKCE `S256`;
  - `token_endpoint_auth_methods_supported: none, client_secret_basic, client_secret_post, private_key_jwt`;
  - `client_id_metadata_document_supported: true`;
  - `registration_endpoint: https://www.upwork.com/register`.
- `openid-configuration`: 404. No scopes and no token lifetimes are published.
- Unverified, because only a real attempt would tell:
  - whether that registration endpoint is RFC 7591 (the path looks like the human sign-up page; checking it means
    POSTing a registration);
  - which client the owner's interactive grant used (probably Claude Code's own client metadata document);
  - whether Upwork lets a second grant for the same user coexist with the first;
  - the token lifetimes.

**M8, Codex** (CLI and binary, throwaway `CODEX_HOME`, no model):
- `[mcp_servers.<id>]` takes `url` with `http_headers` (literal), `env_http_headers` (header from a variable),
  `bearer_token_env_var`, an `http_headers_helper`, and its own OAuth store (`mcp_oauth_credentials_store`).
- `codex mcp list` showed "Bearer token" as the auth for such servers, and `codex mcp get` masks header values.
- `mcp list` doesn't connect, so whether the header is sent is unmeasured.
- The platform's Codex runs load no tool server today (`runtime_codex`: `tool_servers` unsupported). Stdio servers are
  ordinary there.

## The design

### C1. A platform credential in the registry

A `secrets:` entry gets an optional `kind`:

```yaml
secrets:
- name: upwork
  kind: oauth            # or static (a bearer token or API key the owner pastes)
  server:
    id: upwork           # the tool prefix the job's rules already use: mcp__upwork__…
    url: https://mcp.upwork.com/mcp
  why: the daily job's own grant (the owner's interactive one is separate)
```

- **Owner-only.** An entry with `kind` is a security field: a `manifest.update` that adds, changes or removes one is
  returned whole, as `PLATFORM_OWNED` fields are (a `PLATFORM_OWNED_NESTED`-style rule over `secrets[kind]`).
- **No `path`.** The platform places the file itself (C3), so `check-secrets`, the backup and the sandbox denies find it
  without the project naming a place. `registry.py` refuses `path`, `env_key` or `expires` on such an entry.
- `server.id` follows `ToolServer.id`'s spelling (`[a-z0-9-]`, not reserved) and is unique across the project's
  credential entries. `server.url` is `https` and has no userinfo, query or fragment. Its origin is the only one the
  bridge (C5) ever connects to, apart from the token endpoint found when the grant is made.
- `static` is the same without a refresh: for servers that take an API key or a long-lived bearer token.

### C2. The owner grants it once

`brunna credentials grant <project> <name>` is run by the owner in a terminal on the hub machine (`bin/credentials.py`).

1. **Refusals.** It refuses inside any platform run (the job/lease variables are set, or the instance's run marker) and
   without a terminal. These are reinforcement only: the consent that counts is the owner's at the provider's page.
2. **Discovery.** RFC 9728 from `server.url`, then RFC 8414 (and OpenID discovery as a fallback). The issuer, the
   endpoints and what is supported are recorded.
3. **Client.** The command uses one route, named by the owner (owner question 1), and records it with the grant. With
   none named, it takes (a) when it is configured and the server supports it, else it stops and asks for (c). It never
   tries (b) unasked.
   - (a) A **client ID metadata document**
     (`credentials.client_metadata_url` in `brunna.json`: an https URL the owner controls, holding a document whose
     `client_id` is that URL and whose redirect URIs are loopback).
   - (b) **RFC 7591 dynamic registration** (`--register`), only when the provider documents its
     `registration_endpoint` as RFC 7591. Upwork's points at what looks like its human sign-up page. It registers a
     public client named for the platform, `token_endpoint_auth_method: none`, loopback redirects.
   - (c) **The owner's own client**: `--client-id`, with a client secret read from the terminal without echo (for a
     provider's developer-portal app). The secret is stored with the grant, never in `brunna.json`.
   A route that fails says why and stops. It never silently falls back to borrowing another tool's client ID.
4. **Authorization code with PKCE `S256`**, `state`, and RFC 8707 `resource=<server.url>`. Scopes come from the
   resource metadata's `scopes_supported`, or `--scope`, else none requested. The redirect goes to a one-shot listener on
   `127.0.0.1:<free port>`. When the owner's browser is on another machine, they paste the full redirected URL back into
   the terminal (the page fails to load there, but its URL carries the code).
5. **Exchange and store** (C3), then **verify**: `initialize` and `tools/list` through the bridge's own code path (C5),
   never a `tools/call`. It prints the tool count, the scopes granted and the access token's lifetime. It never prints a
   token.

`brunna credentials set <project> <name>` stores a `static` value read from the terminal without echo.
`brunna credentials revoke <project> <name>` calls the revocation endpoint (RFC 7009) with the refresh token, then
deletes the file. The owner can also revoke at the provider's connected-apps page. A later refresh then fails, and C4
says so.

### C3. Storage

- The file is `<secrets_dir>/credentials/<project>/<name>.json`, mode 600, in folders of mode 700, written atomically
  (temp file, fsync, rename).
- It holds the issuer, the endpoints, the client (ID, and secret for route (c)), the resource, the scopes, the access
  token and when it expires, the refresh token, when the grant was made, and the route.
- Beside it is a **status file** with no secret in it, `<name>.status.json`: access expiry, last refresh and its result,
  last error, refresh-token age, `needs_grant`. `check-secrets` and the status page read this file, never the
  credential. (`max_age_days` restarts at every refresh, so it can't stand in for this.)
- The secrets directory is already in every sandbox's read deny (`policy.secret_dirs`) and in the backup
  (`backup.covers`).
- On Linux the same files and modes apply. No OS keyring is used anywhere, so the same thing works on both OSes and with
  the screen locked.

### C4. Refresh

- **Who.** One function, `credentials.fresh(project, name, for_seconds)`, used by:
  - the bridge at its start, for the run's timeout plus a margin;
  - the bridge again on a 401 mid-run (once);
  - a platform job, `brunna/credentials` (hourly, zero LLM), which refreshes anything due within the next two hours and
    any refresh token older than a day. Refresh tokens that expire when unused are then used at least daily, even when
    no job runs.
- **Locking.** `flock` on `<name>.lock` beside the file. A second refresher waits, then reads the fresh token rather
  than refreshing again (rotating refresh tokens are often single-use).
- **Rotation.** The token response is written to `<name>.json.new` and fsynced before it replaces the file. A crash
  between the provider's answer and the rename leaves `.new`, which the next refresher takes first.
- **Failure.**
  - `invalid_grant` or a revoked grant sets `needs_grant` and raises an alert (`alert.py`: once, again daily, and a
    recovery when it is granted again). Owner question 6 asks whether it also adds an owner to-do line through the
    project's STATE.md handling.
  - A run that needs the credential then doesn't start: `job.failed` with "the platform's grant for upwork needs the
    owner: `brunna credentials grant …`".
  - A network error is retried at the next tick. The run fails only when the access token has actually expired.
  - **Never a fallback**: not the Keychain, not the owner's interactive grant, not the subscription login.

### C5. Delivery: a stdio bridge the platform attaches to the run

The token reaches the remote server and nothing else in the run:

- The run stays sealed (`--strict-mcp-config`), on the headless token, with the refusing `security`: the run table's
  current rows, unchanged.
- `llm.py` attaches one more `runtime_ports.ToolServer` per credential the job uses, the way it attaches the platform's
  mail server:
  - `id` = `server.id`;
  - `command` = the platform's Python;
  - `args` = `bin/credential_bridge.py --run <run_dir>/credential-<id>.json`;
  - `env` = only `HOME` and the platform's location variables;
  - `tools` = the tools of that server the job's `allow:` names.
- The run file (600, in the run folder, which `llm.py` removes when the run ends) names the project, the credential and
  the allowed tools. The bridge reads the credential file itself: **the token is never in Claude's environment, args,
  config file or the run folder** (M6: the model's shell inherits Claude's environment).
- **The bridge** (stdio MCP ⇄ streamable HTTP):
  - It passes `initialize`, `server/discover`, `ping`, notifications, `tools/list` (filtered to the allowed tools) and
    `tools/call` for an allowed tool. Everything else gets a JSON-RPC error: other methods, server-to-client requests
    (sampling, elicitation), resources and prompts unless a later owner decision adds them, and batches.
  - It connects only to `server.url`'s origin and the recorded token endpoint, follows no redirect to another origin,
    and streams SSE answers through.
  - It turns an upstream 401 or 403 into a JSON-RPC error after its one refresh, so the client never sees an HTTP 401
    to start its own OAuth (M3).
  - It writes a request log with no secret in it: method, tool name, status and duration.
- **Why not the alternatives:**
  - A header from `${VAR}`: the token would be in Claude's environment, which the model's Bash inherits (M6).
  - A literal header in the run's config: `claude mcp get` prints it (M1), and the config sits in a folder the run's
    tools must never be allowed to read.
  - A loopback proxy (M3): it works with a per-run nonce header and a tool filter, but its port can be reached by any
    local process, it needs its own lifetime and port handling, and the header syntax would be the adapter's. Kept as
    the fallback for a runtime that can't start stdio servers (none known).
- **Neutral by construction.** A stdio server is the one MCP shape every runtime has. The adapter only writes what it
  already writes for `ToolServer` (`runtime_claude._server_config`, Codex's `[mcp_servers]` when its runs gain tool
  servers). No header or OAuth syntax enters the platform. A runtime without `run_server` makes these jobs
  `Unsupported`, with its reason.

### C6. Which jobs may use which credential

- A job lists `credentials: [<name>]` (owner-only). Such a job is **frozen whole**, as `connector_jobs()` freezes a
  `connectors: true` job (the earlier plan's B1): `allow:`, prompt, `cwd` and schedule. A letter can't change what runs
  with the grant.
- `registry.py` refuses `credentials:` in four cases:
  - on event jobs, mailbox points and jobs without `allow:`;
  - with `connectors: true`;
  - when the job's rules name tools of `server.id` but the job doesn't list the credential, or the other way round;
  - when an unsandboxed project's job `allow:` holds a rule that runs code or reads outside the job's folder (`Bash`,
    any command rule, a `Read` wider than its folders): such a run could read the credential file directly. A
    sandboxed project's runs can't read the secrets directory (OS deny), so there the rule is the usual confined one.
- The bridge's tool list is computed from the registry at run time (S2 of the earlier review), never from the run's
  environment.

### C7. Sandboxed projects and the network

- **Network.** The model never connects to the server, the bridge does, so the server's domain is **not** added to the
  project's `network:` list.
- **Unmeasured: is the bridge sandboxed?** Claude Code's OS sandbox wraps the Bash tool, not the MCP servers it starts.
  If a later version sandboxes stdio servers, the bridge can't read the secrets directory and fails visibly. The
  conformance check (Tests) catches that before such a version is promoted. Measure it once on a sandboxed project's
  run before step 4.
- **Hardening, not required.** On macOS the bridge can run under the platform's own Seatbelt profile: network to the two
  origins only, and reads of its credential file and the platform's code. This has to be measured, because a second
  Seatbelt layer can't start inside one (`seatbelt.py`). For now it is a follow-up.

### C8. Status, doctor, check-secrets

- `check-secrets` gets a row per credential: granted or not, access expiry, last refresh and its result, refresh-token
  age, and `needs_grant` (from the status file). A missing credential is a problem, as a missing secret is. It never
  reads the credential file beyond whether it exists and its mode.
- The status page gets the same rows, plus the jobs each credential serves.
- Doctor gets a feature row: "unattended jobs on the platform's own MCP grants", supported where the runtime has
  `run_server`.
- `registry.py check --secrets-report` lists which jobs use which credential.

### C9. What this replaces and what it leaves

- **DV5 is unchanged.** No platform run reads the Keychain. The jobs that move off `connectors: true` (and the shared
  config dir) gain the refusing `security`.
- **`connectors: true` stays** for claude.ai connectors (the account's servers, whose OAuth Anthropic holds and which
  only the subscription login loads). A connector that is a standard remote MCP server can move to a platform grant if
  the owner wants (owner question 7).
- The earlier plan's `mcp_servers:` and its unsealed run are not built.

## Moving the daily job

1. The owner answers question 1 (which client the grant uses).
2. The owner adds the credential entry and `credentials: [upwork]` to the job's registry entry (owner-only fields: a
   change in the config repo with the owner's yes). The job's `allow:` stays as it is: it names only `mcp__upwork__…`
   tools, `ToolSearch`, `PushNotification` and its own folder, with no code-running rule, so C6 accepts it.
3. The owner runs `brunna credentials grant <project> upwork` and approves the consent page.
4. `schedulectl run <project>/daily-check`. The run is sealed, on the token, with the stub, and the bridge is its only
   server.
5. The owner's interactive Upwork server in Claude Code is untouched. It is a separate grant.

The project hears about it with a `brunna send --type info` once it works (its session needs nothing).

## Tests

- **Registry:**
  - `kind` entries are owner-only (a manifest.update that touches one is returned whole);
  - the field rules of C1 and C6;
  - a credentials job is frozen;
  - the code-running refusal for unsandboxed projects.
- **`credentials.py` against a fake authorization server** (local HTTP; no real provider):
  - discovery;
  - each client route and its refusal;
  - PKCE and state checked;
  - the paste-back path;
  - refresh under two concurrent refreshers (one refresh, both get the new token);
  - rotation with a crash between the answer and the rename;
  - `invalid_grant` → `needs_grant`, an alert, and the run refused;
  - revoke.
- **The bridge against a dummy MCP server** (the measurement's, made a fixture):
  - the header is added;
  - only allowed tools are listed and called;
  - other methods are refused;
  - SSE answers are streamed;
  - a 401 becomes a refresh, then a JSON-RPC error;
  - no request goes to another origin;
  - the log holds no token (a canary token, grepped).
- **llm.py / adapter:**
  - the run's `--mcp-config` holds the bridge entry with no `$` and no token;
  - the run stays sealed and on the stub (`test_llm_golden` with a fake store);
  - a canary token appears in no file of the run folder, env or args.
- **Conformance (`conformance_claude`, a new check per version, no account)**, which turns M1–M4 into a gate. A version
  where a stdio server's tools don't load, or where the Keychain is reached for it, is held back:
  - a stdio server from `--mcp-config` loads under `--strict-mcp-config` with the stub;
  - its tools are listed;
  - the stub logs only the config dir's own login items, and no `-i` call.
- `check-secrets` rows from status files only. `bin/linux-test` runs the store and refresh on Linux.

## Order of work

1. Registry: `kind`, `server`, `credentials:`, owner-only and frozen, the C6 refusals. Plus `check-secrets` and status
   rows from status files. No credential exists yet; the rows say "not granted".
2. `credentials.py`: storage, status file, `fresh()` with the lock and rotation, the `brunna/credentials` job, alerts.
   Tested against the fake authorization server.
3. `credential_bridge.py`, and its attachment in `llm.py` beside the mail server, plus the conformance check.
   Measured once on a sandboxed project (C7).
4. `brunna credentials grant|set|revoke`, with the three client routes.
5. The daily job's move (above), by the owner.

Each step is merged on its own through `merge-branch`. Before step 3 is built, the design and then the code each get a
cold review.

## Open owner questions

1. **Which client the platform's Upwork grant uses:**
   - (a) a client ID metadata document at an https URL the owner controls (one for this deployment; or brunna publishes
     one for all installs, which makes brunna a named client at every provider);
   - (b) dynamic registration, only if Upwork documents its `registration_endpoint` as RFC 7591. The advertised URL
     looks like the human sign-up page, so posting a registration there unasked is not done;
   - (c) the owner's own Upwork developer app (`--client-id`/secret), which also opens `client_credentials` if Upwork
     allows it for the MCP.

   Recommended: (a) if the owner has an https place for the document, else (c). (b) only after Upwork's developer docs
   confirm it. Whether a second grant coexists with the owner's interactive one is learned at that first grant: if
   granting the platform's copy logs the owner's Claude Code out of Upwork, the routes that share a client are out.
2. **How the grant reaches the run.** Recommended: a stdio bridge the platform attaches (C5). Every runtime has stdio,
   it opens no port, and its lifetime is the run's. The alternative is a loopback proxy the platform runs per job, with
   a per-run nonce header (M3). That keeps the token out of every process the agent starts, but it needs a port, a
   lifetime of its own and each runtime's header syntax. The bridge is the less measured of the two (M4's list), so
   step 3 measures it first. If it fails there, the proxy is the fallback.
3. **Code-running rules.** Recommended: no Bash or command rule in an unsandboxed project's credentials job (C6). The
   alternative is to allow them only once the project is sandboxed.
4. **Resources and prompts.** Should the bridge pass MCP resources and prompts as well as tools? Recommended: tools only
   until a job needs more.
5. **The refresh job's cadence.** Hourly, with the daily use of refresh tokens (C4). Fine for a provider with
   short-lived refresh tokens; nothing is published for Upwork.
6. **A broken grant.** Is an alert enough, or should it also be an owner to-do line (an owner-tagged line in the
   project's STATE.md, registered with the assistant when the assistant module is on)?
7. **The other connectors jobs.** Should they move to platform grants where the connector is a standard remote MCP
   server, and leave `connectors: true` only for what only the account can load?

## Risks

- **The bridge as a confused deputy.** It holds a token with the owner's Upwork rights.
  - What bounds it: only the allowed tools, only the declared origin, only for a run `llm.py` started (its run file).
  - The residual: a run whose rules could read the run file or the credential. C6 refuses those in unsandboxed
    projects, and the OS denies them in sandboxed ones.
- **Scope.** The grant has whatever the consent page grants, often more than the job's read-only tools (Upwork publishes
  no scopes). The bridge's tool filter is the boundary, not the token's scope. Request narrower scopes where the
  provider lists them.
- **The token at rest.** A file readable by the owner's user, like the headless token and the backup keys. Any process
  of the owner's that isn't sandboxed can read it. This is the same trust as the platform's other secrets, and narrower
  than the Keychain item it replaces (one server, not the login and every server).
- **Restoring a backup.** A restored file holds a refresh token that may since have rotated. Using it fails as
  `invalid_grant`, and on providers with reuse detection it may revoke the whole grant family. After a restore, expect
  `needs_grant` and re-grant. The restore notes say so; never retry a stale refresh in a loop.
- **Provider policy.** A provider may limit grants per user and client, or rotate or expire refresh tokens on its own
  schedule. Both show as `needs_grant` with an alert, never as a silent fallback.
- **Protocol drift.** The bridge's method allowlist fails closed on a new MCP method that a newer client needs at start
  (like `server/discover`, seen in M1). The conformance check runs the bridge against each new version before it is
  promoted.
- **Revocation lag.** Revoking at the provider takes effect at its next check. `brunna credentials revoke` deletes the
  local copy at once.
