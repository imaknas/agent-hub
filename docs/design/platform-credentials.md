# Credentials for unattended jobs are the platform's own grants

Status: design r2, 11-15, after one blind review (its findings and how each is taken are at the end); not built.
Measured the same day (Claude Code 2.1.323 from the store, Codex 0.167.0). The owner's open questions are near the end.

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

This is r2, after the blind review of 11-15. The section "r2 (11-15), after the blind review" at the end lists each
finding and how it is taken.

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

- **Owner-only.** A `manifest.update` that adds, changes or removes an entry with `kind` is returned whole.
  - This needs a check of its own, `credential_entries(doc)`, compared whole as `connector_jobs()` is.
  - `PLATFORM_OWNED_NESTED` can't do it, because it names fixed keys, not list items.
- **No `path`.** The platform places the file itself (C3), so `check-secrets`, the backup and the sandbox denies find it
  without the project naming a place. `registry.py` refuses `path`, `env_key` or `expires` on such an entry.
- **Spelling.**
  - `server.id` follows `ToolServer.id`'s spelling (`[a-z0-9-]`, not reserved) and is unique across the project's
    credential entries.
  - `server.url` is `https` and has no userinfo, query or fragment.
  - The bridge (C5) connects to that exact URL and to nothing else, apart from the token endpoint recorded at the grant.
- `static` is the same without a refresh: for servers that take an API key or a long-lived bearer token.
- **v1 is for unsandboxed projects only.** The registry refuses `credentials:` in a project with `enforce.sandbox`, and
  says why: a sandboxed project's confined runs can't name any `mcp__…` rule today (`rule_problems`, `SANDBOXED_TOOLS`).
  See C7.

### C2. The owner grants it once

`brunna credentials grant <project> <name>` is run by the owner in a terminal on the hub machine (`bin/credentials.py`).

1. **Refusals.** It refuses inside any platform run (the job/lease variables are set, or the instance's run marker) and
   without a terminal. These are reinforcement only: the consent that counts is the owner's at the provider's page.
2. **Discovery, checked.**
   - RFC 9728 from `server.url`: the metadata's `resource` must equal `server.url`, or the grant stops.
   - RFC 8414 from the named authorization server (OpenID discovery as a fallback): its `issuer` must equal the server it
     was fetched for, or the grant stops.
   - It prints the hosts of the authorization and token endpoints, and the owner confirms them before the browser opens.
   - The issuer, the endpoints and what is supported are recorded with the grant. They are **never re-discovered at a
     refresh**: a refresh uses the recorded token endpoint only.
3. **Client.** The command uses the one route the owner names (owner question 1) and records it with the grant. With
   none named, it takes (a) when it is configured and the server supports it, else it stops and asks for (c). It never
   tries (b) unasked.
   - (a) A **client ID metadata document** (`credentials.client_metadata_url` in `brunna.json`: an https URL the owner
     controls, holding a document whose `client_id` is that URL and whose redirect URIs are loopback).
   - (b) **RFC 7591 dynamic registration** (`--register`), only when the provider documents its `registration_endpoint`
     as RFC 7591. Upwork's points at what looks like its human sign-up page. It registers a public client named for the
     platform (`token_endpoint_auth_method: none`, loopback redirects).
   - (c) **The owner's own client**: `--client-id`, with a client secret read from the terminal without echo (for a
     provider's developer-portal app). The secret is stored with the grant, never in `brunna.json`.
   - **The redirect URI is recorded with the client**:
     - a loopback with any port when the route allows it (RFC 8252);
     - else the fixed URI the client was registered with (`--redirect-uri`, e.g. `http://127.0.0.1:<port>/callback`
       on a set port).
   - A route that fails says why and stops. It never silently falls back to borrowing another tool's client ID.
4. **Authorization code with PKCE `S256`**, `state`, and RFC 8707 `resource=<server.url>`.
   - Scopes come from the resource metadata's `scopes_supported`, or `--scope`, else none are requested.
   - The redirect goes to a one-shot listener on the recorded redirect URI.
   - When the owner's browser is on another machine, they paste the full redirected URL back into the terminal (the page
     fails to load there, but its URL carries the code).
   - On the redirect: `state` must match, and `iss` must equal the recorded issuer when the server advertises
     `authorization_response_iss_parameter_supported` (RFC 9207).
5. **Exchange and store** (C3), then **verify**.
   - It runs `initialize` and `tools/list` through the bridge's own code, never a `tools/call`.
   - It prints the tool count, the scopes granted and the access token's lifetime. It never prints a token.

`brunna credentials set <project> <name>` stores a `static` value read from the terminal without echo.

`brunna credentials revoke <project> <name>`:
- takes the lock (C4) and calls the revocation endpoint (RFC 7009) with the refresh token;
- deletes the file and any `.new`, **even when the revocation call fails**. A failed revocation raises an alert telling
  the owner to revoke at the provider's connected-apps page.

### C3. Storage

- The file is `<secrets_dir>/credentials/<project>/<name>.json`, mode 600, in folders of mode 700, written atomically
  (temp file, fsync, rename).
- It holds the issuer, the endpoints, the client (ID, secret for route (c), redirect URI), the resource, the scopes, the
  access token and when it expires, the refresh token, when the grant was made, and the route.
- Beside it is a **status file** with no secret in it, `<name>.status.json`: access expiry, last refresh and its result,
  last error, refresh-token age, `needs_grant`. `check-secrets` and the status page read this file, never the
  credential. (`max_age_days` restarts at every refresh, so it can't stand in for this.)
- **Backup.**
  - The secrets directory is already in the backup (`backup.covers`) and in every sandbox's read deny
    (`policy.secret_dirs`).
  - Whether credential files stay in the nightly cloud backup is owner question 3. A restored refresh token is normally
    stale, so the recommended answer is that they don't.
- On Linux the same files and modes apply. No OS keyring is used anywhere, so the same thing works on both OSes and with
  the screen locked.
- **Follows the project.**
  - `rename-project.sh` moves `credentials/<old>/` to `credentials/<new>/` with the registry entry, and its dry run
    lists the move.
  - `uninstall --purge` revokes every credential first (as `revoke` does), then deletes the folder.

### C4. Refresh

Access tokens are refreshed on demand, before a run. The scheduled job only keeps refresh tokens alive.

- **`credentials.fresh(project, name, need_s)`**, under the lock:
  - It returns the stored access token when more than `need_s` of its lifetime remains.
  - Otherwise it refreshes once and returns the new token **whatever its lifetime**, so a provider with short-lived
    tokens never loops.
  - `need_s` is the run's timeout plus five minutes, capped at an hour. A token that expires mid-run is handled on the
    401 (below).
- **Before the launch, in `llm.py`.** `llm.py` calls `fresh()` before it starts the runtime. A failure there is
  `job.failed` with the reason, and the runtime never starts. The bridge only reads the file, so nothing slow sits
  inside Claude's 30-second MCP connect timeout (the debug log in M1: "Starting connection with timeout of 30000ms").
- **On a 401 mid-run.** The bridge never refreshes in its own process: Claude kills it on a timeout or at the end of
  the run.
  - It starts one short, detached process, `bin/py -I bin/credentials.py refresh <project> <name> --failed <sha256 of
    the token that failed>`, in a new session with an explicit, minimal environment.
  - It waits up to 20 seconds for that process, then re-reads the file and retries the request once.
  - The refresh, under the lock, re-reads the file first. It refreshes only when the stored access token's hash is the
    one that failed; else another refresher already did it.
  - Any number of re-reads, at most one refresh per failed token.
- **The scheduled job, `brunna/credentials`** (hourly, zero LLM). It refreshes a credential only when its refresh token
  hasn't been used for about a day, so a provider that expires unused refresh tokens keeps the grant alive. It writes
  every status file. It never refreshes access tokens ahead of time.
- **Locking.** `flock` on `<name>.lock` beside the file.
- **Rotation.** The token response is written to `<name>.json.new` and fsynced before it replaces the file.
  - A later refresher that finds `.new` takes it only when it parses as a whole credential and is newer than the file
    (its issue time and its refresh token differ). Otherwise it deletes it and says so in the status file.
- **A lost response.** When a refresh request fails without an answer (timeout, connection lost after sending), the
  rotated refresh token may be gone. The refresher tries once more with the stored one. If that also fails, it sets
  `needs_grant`. It never loops.
- **Failure.**
  - `invalid_grant`, a revoked grant or the lost response above sets `needs_grant` and raises an alert (`alert.py`:
    once, again daily, and a recovery when it is granted again). Whether it is also an owner to-do line is owner
    question 4.
  - A run that needs the credential then doesn't start: `job.failed` with "the platform's grant for upwork needs the
    owner: `brunna credentials grant …`".
  - A network error at the launch's refresh fails that run with the reason. The next scheduled run tries again.
  - **Never a fallback**: not the Keychain, not the owner's interactive grant, not the subscription login.

### C5. Delivery: a stdio bridge the platform attaches to the run

Decided in r2 (it was owner question 2): a stdio bridge. The loopback proxy of M3 stays the documented fallback, if
step 3's measurements of the bridge fail.

- **The run stays sealed** (`--strict-mcp-config`), on the headless token, with the refusing `security`.
  - It keeps the run table's current row: agent-shaped token runs, agent jobs included, stay on the shared `~/.claude`.
  - `llm.py` refuses to attach a credential to a run that isn't sealed.
- **`llm.py` attaches one more `runtime_ports.ToolServer` per credential** the job uses, the way it attaches the
  platform's mail server.
  - `attach_server` appends to `task.servers`, never replaces it (today it sets `servers=(server,)`, `llm.py:539`).
  - `id` = `server.id`; `command` = the platform's Python with `-I`; `args` = `bin/credential_bridge.py --run
    <run_dir>/credential-<id>.json`; `env` = only `HOME` and the platform's location variables.
  - `tools` = the tools of that server the job's rules name, from the adapter port `tools_named(task, server_id)` (C6).
- **The run file** (600, in the run folder, which `llm.py` removes when the run ends) names the project, the job and
  the credential.
  - The bridge validates it as the mail server does (`confined-mail-tools.md` M3): opened with `O_NOFOLLOW`, regular
    file, the user's own, not group- or world-writable (fstat), a path inside `<STATE>/agent-jobs/.settings/`, read
    once.
  - Then it recomputes from the registry that this project's job lists this credential, and which tools it may call.
    Any mismatch refuses the start.
- **The token's path.** The bridge reads the credential file itself. The token is never in Claude's environment,
  arguments, config file or the run folder (M6: the model's shell inherits Claude's environment).
- **The bridge's own environment is Claude's whole environment**, the headless token included: a stdio server inherits
  it whatever the config's `env` says. So:
  - it runs under `-I`;
  - it never logs its environment;
  - it starts no process except the one fixed refresh of C4, which gets an explicit minimal environment.
- **Network.**
  - `POST` and `GET` go only to the exact `server.url`; the refresh goes only to the recorded token endpoint.
  - **No redirect is followed at all.** The standard library's opener re-sends the `Authorization` header on a 302
    (measured in r2: a 302 on a `POST` became a `GET` to another origin, carrying the header). A 3xx is an error.
  - It uses an explicit opener with an empty `ProxyHandler`, and ignores the proxy and CA environment variables (the
    system trust store only).
- **Methods.**
  - It passes `initialize`, `server/discover`, `ping`, notifications, `tools/list` (filtered to the allowed tools) and
    `tools/call` for an allowed tool.
  - On `initialize` it strips the client's capabilities (roots, sampling, elicitation) before forwarding.
  - Server-to-client requests from upstream are answered upstream with an error, never passed to the client.
  - Everything else from the client gets a JSON-RPC error: resources, prompts, other methods, and batches.
- **Output.**
  - It forwards parsed and re-encoded JSON, never raw bytes. Every frame to the client is checked for the access token,
    the refresh token and the client secret, and they are redacted if present.
  - SSE answers are streamed event by event, each parsed the same way.
  - An upstream 401 or 403 becomes a JSON-RPC error after the one refresh attempt, so the client never sees an HTTP 401
    to start its own OAuth (M3).
- **Its status.** The bridge writes `credential-<id>.status.json` in the run folder: initialized, tools listed, auth
  failures and whether each recovered, last error. It writes a request log with no secret: method, tool name, status,
  duration.
- **A broken bridge fails the job.** After the run, `llm.py` reads that status. A run whose bridge never initialized,
  or whose bridge saw an auth failure it didn't recover from, is a failed run (`job.failed`, the bridge's reason), even
  when the model reported success.
- **Why not the alternatives.**
  - A header from `${VAR}`: the token would be in Claude's environment, which the model's Bash inherits (M6).
  - A literal header in the run's config: `claude mcp get` prints it (M1).
  - A loopback proxy (M3): it works with a per-run nonce header and a tool filter, but its port can be reached by any
    local process, it needs its own lifetime, and the header syntax would be each adapter's.
- **Neutral by construction.** A stdio server is the one MCP shape every runtime has.
  - The adapter only writes what it already writes for `ToolServer` (`runtime_claude._server_config`, Codex's
    `[mcp_servers]` when its runs gain tool servers).
  - No header or OAuth syntax enters the platform.
  - A runtime without `run_server` makes these jobs `Unsupported`, with its reason.
- **The sandbox.** The bridge runs outside the project's OS sandbox, as the mail server does: Claude Code's sandbox
  covers Bash only (`confined-mail-tools.md` M4, measured on a real run).

### C6. Which jobs may use which credential

- A job lists `credentials: [<name>]` (owner-only). Such a job is **frozen whole** (`allow:`, prompt, `cwd`, schedule),
  as `connector_jobs()` freezes a `connectors: true` job (the earlier plan's B1). A letter can't change what runs with
  the grant.
- **`registry.py` refuses `credentials:`:**
  - on event jobs, mailbox points, the `project-agent` point (a project's own declared agent call), leases, and jobs
    without `allow:`;
  - with `connectors: true`;
  - in a sandboxed project (C1, C7);
  - when the job's rules name tools of `server.id` but the job doesn't list the credential, or the other way round;
  - when the job's `allow:` holds a rule that runs code or reads outside the job's folders (`Bash`, any command rule, a
    `Read` wider than its folders). Such a run could read the credential file or the run file directly.
- **Rule parsing stays in the adapter.** The registry and `llm.py` never parse `mcp__`, `Bash(` or `Read(` themselves
  (`tests/test_runtime_boundary.py`'s ratchet forbids it).
  - They ask through the adapter's ports: `servers_named`, `rule_problems` (extended with the code-running and
    wide-read refusal for credential jobs) and a new `tools_named(task, server_id)`.
  - `tools_named` strips the `mcp__<id>__` prefix of each rule naming that server, since a tool name may hold `__`
    itself (`upwork__get_messages`). It doesn't split on `__`.
- The bridge's tool list is computed from the registry at run time, never from the run's environment (C5).

### C7. Sandboxed projects and the network

- **v1 refuses sandboxed projects** (C1). Their confined runs may name only reading and editing files and WebFetch to
  their own domains: `rule_problems` refuses every `mcp__…` rule there (`SANDBOXED_TOOLS`), so a credentials job couldn't
  call its tools anyway. A carve-out would have to say which MCP tools a sandboxed project's unattended run may call,
  with prompts nobody else writes. That is owner question 5, not built here.
- **An egress path `network:` doesn't describe.** The bridge connects to `server.url` from outside any sandbox, so the
  project's `network:` list doesn't cover it. The registry's credential entry is its declaration. The status page and
  `registry.py check --secrets-report` list it beside `network:`.
- **Hardening, not required.** On macOS the bridge could run under the platform's own Seatbelt profile: network to the
  two endpoints only, reads of its credential file and the platform's code. That has to be measured first, because a
  second Seatbelt layer can't start inside one (`seatbelt.py`). It is a follow-up.

### C8. Status, doctor, check-secrets

- **`check-secrets`** gets a row per credential: granted or not, access expiry, last refresh and its result,
  refresh-token age, `needs_grant` (from the status file).
  - A missing credential is a problem, as a missing secret is.
  - It never reads the credential file beyond whether it exists and its mode.
- **The status page** gets the same rows, the jobs each credential serves, and its egress (C7).
- **Doctor** gets a feature row, "unattended jobs on the platform's own MCP grants", supported where the runtime has
  `run_server`.
- **`registry.py check --secrets-report`** lists which jobs use which credential.

### C9. What this replaces and what it leaves

- **DV5 is unchanged.** No platform run reads the Keychain.
  - A job that moves off `connectors: true` gains the headless token and the refusing `security`.
  - As an agent-shaped token run, it stays on the shared `~/.claude` (the run table's row).
- **`connectors: true` stays** for claude.ai connectors (the account's servers, whose OAuth Anthropic holds and which
  only the subscription login loads). A connector that is a standard remote MCP server can move to a platform grant if
  the owner wants (owner question 6).
- The earlier plan's `mcp_servers:` and its unsealed run are not built.

## Moving the daily job

1. The owner answers questions 1–3 (the client, the grant's breadth, backup).
2. The owner adds the credential entry and `credentials: [upwork]` to the job's registry entry (owner-only fields: a
   change in the config repo with the owner's yes).
   - The project is unsandboxed, as v1 needs.
   - The job's `allow:` stays as it is: it names only `mcp__upwork__…` tools, `ToolSearch`, `PushNotification` and its
     own folder, with no code-running rule, so C6 accepts it.
3. The owner runs `brunna credentials grant <project> upwork` and approves the consent page.
4. `schedulectl run <project>/daily-check`. The run is sealed, on the token, with the stub, and the bridge is its only
   server.
5. The owner's interactive Upwork server in Claude Code is untouched. It is a separate grant.

The project hears about it with a `brunna send --type info` once it works (its session needs nothing).

## Tests

- **Registry:**
  - `kind` entries are owner-only (a manifest.update that touches one is returned whole, through
    `credential_entries`);
  - the field rules of C1 and C6, with the refusals for sandboxed projects and the excluded points;
  - a credentials job is frozen;
  - the code-running and wide-read refusal comes through the adapter's `rule_problems`;
  - `tools_named` with a tool name that holds `__`.
- **`credentials.py` against a fake authorization server** (local HTTP; no real provider):
  - discovery, with a mismatched `resource` and a mismatched `issuer` both refused;
  - `iss` on the redirect;
  - each client route and its refusal, and a fixed redirect URI;
  - PKCE and state checked;
  - the paste-back path;
  - `fresh()`: no refresh with lifetime left, one refresh otherwise, a just-issued short token accepted;
  - two concurrent refreshers make one refresh;
  - a 401 refresh with a stale `--failed` hash doesn't refresh;
  - a crash between the answer and the rename, with a valid `.new` taken and an invalid or older one dropped;
  - a lost response: one more try, then `needs_grant`, no loop;
  - `invalid_grant` → `needs_grant`, an alert, and the run refused at launch;
  - revoke with a failing endpoint still deletes and alerts;
  - rename and purge.
- **The bridge against a dummy MCP server** (the measurement's, made a fixture):
  - the header is added;
  - only allowed tools are listed and called, and other methods and batches are refused;
  - client capabilities are stripped, and server-to-client requests are answered upstream with errors;
  - SSE answers are streamed;
  - a 401 starts one detached refresh, then one retry, then a JSON-RPC error and a status that fails the run;
  - **a 302 on a `POST` is an error**, and no request goes to another URL;
  - proxy variables are ignored;
  - the run file's checks (a link, another owner, a writable mode, a path outside `.settings`, a job not listing the
    credential);
  - a frame from upstream that echoes the token is redacted.
- **A canary token**, run through the fake, appears in none of:
  - the run folder, Claude's environment, arguments or config;
  - the bridge's stderr and log;
  - Claude's MCP log files (`mcp-logs-…` in the config dir);
  - the session transcript.
- **llm.py / adapter:**
  - the run's `--mcp-config` holds the bridge entry with no `$` and no token, appended beside the mail server;
  - an unsealed run is refused;
  - the run stays on the stub (`test_llm_golden` with a fake store);
  - a bridge status of "never initialized" fails the run.
- **Conformance (`conformance_claude`, a new check per version, no account and no model call).** It turns M1–M5 into a
  gate.
  - The check starts a `-p` run in a throwaway config dir with no login, `--strict-mcp-config` and the bridge in
    `--mcp-config`, with the stub. M5 showed the MCP connection and `tools/list` happen before the login is needed. The
    run then fails at the login, without calling a model.
  - The check reads the dummy upstream's log and the bridge's status file: `initialize` and `tools/list` arrived through
    the bridge, with the header.
  - The stub logged only the config dir's own login items and no `-i` call.
  - A version that fails it is held back.
- `check-secrets` rows come from status files only. `bin/linux-test` runs the store and refresh on Linux.

## Order of work

1. **Registry:** `kind`, `server`, `credentials:`, `credential_entries` owner-only and frozen, the C6 refusals through
   the adapter's ports (`tools_named`, the extended `rule_problems`). Plus `check-secrets` and status rows from status
   files. No credential exists yet; the rows say "not granted".
2. **`credentials.py`:** storage, status file, `fresh()`, the detached `refresh`, the lock, `.new` validation, the
   `brunna/credentials` job, alerts, rename and purge. Tested against the fake authorization server.
3. **`credential_bridge.py`**, its attachment in `llm.py` (appended, sealed runs only, `fresh()` before the launch, the
   bridge status read after it), and the conformance check.
   - Before merge it measures what M4 left open on a real run: a `tools/call` through the bridge, the filter, the `-p`
     path, SSE, and a run on the shared `~/.claude`.
   - If the bridge fails there, the loopback proxy (M3) takes its place, with a design note first.
4. **`brunna credentials grant|set|revoke`**, with the three client routes.
5. **The daily job's move** (above), by the owner.

Each step is merged on its own through `merge-branch`. Before step 3 is built, the design and then the code each get a
cold review.

## Open owner questions

1. **Which client the platform's grant uses.**
   - (a) A client ID metadata document at an https URL the owner controls: one for this deployment, or one brunna
     publishes for all installs (which makes brunna a named client at every provider).
   - (b) Dynamic registration, only if Upwork documents its `registration_endpoint` as RFC 7591. The advertised URL
     looks like the human sign-up page, so the platform doesn't post a registration there unasked.
   - (c) The owner's own Upwork developer app (`--client-id` and secret).

   Recommended: (a) if the owner has an https place for the document, else (c); (b) only after Upwork's developer docs
   confirm it.

   None of the three routes uses Claude Code's client. Whether Upwork lets a second grant for the same user stand beside
   the owner's interactive one is learned at the first grant. If the owner's Claude Code loses its Upwork connection
   then, the platform's grant is revoked and the owner is told.
2. **A full-rights grant.** Upwork publishes no scopes, so the platform's grant will likely carry everything the owner's
   account can do on the MCP (send messages, submit proposals). Only the bridge's tool filter would hold the job to its
   read tools. Does the owner accept that?
   Recommended: yes for this job's read-only tool list. Request narrower scopes at the grant if route (c)'s developer
   app offers them.
3. **Refresh tokens in the nightly cloud backup.** A restored refresh token is normally stale (rotated since), so the
   backup gives little, while an unrotated one is a live credential off the machine.
   Recommended: leave credential files out of the backup (the registry's `backup: false` with "re-grant after a
   restore"); `check-secrets` accepts it.
4. **A broken grant.** Alert only, or also an owner to-do line (an owner-tagged line in the project's STATE.md,
   registered with the assistant when the assistant module is on)?
   Recommended: both. Re-granting is something only the owner can do, which is what owner to-dos are for.
5. **Sandboxed projects.** v1 refuses them (C7). Should a carve-out (named MCP tools for a sandboxed project's
   unattended run) be designed?
   Recommended: not until a sandboxed project needs one.
6. **The other connectors jobs.** Should they move to platform grants where the connector is a standard remote MCP
   server, leaving `connectors: true` only for what only the account can load?
   Recommended: one at a time, after the daily job has run on its grant for a week.

Decided in r2 (were questions):
- the bridge, with the proxy as fallback (old Q2);
- tools only, no resources or prompts (old Q4);
- refresh on demand plus a keep-alive job (old Q5, C4);
- no code-running rules, now a registry refusal with sandboxed projects out of v1 (old Q3).

## Risks

- **The bridge as a confused deputy.** It holds a token with the owner's Upwork rights.
  - What bounds it: only the allowed tools, only the exact URL, only for a run `llm.py` started (the validated run
    file, rechecked against the registry).
  - The residual: a run whose rules could read the run file or the credential. C6 refuses those.
- **The guarantee is per confined run.** It covers the credentials job's own run. Other unsandboxed processes of the
  same project (its sessions, services, other jobs' shells) can read the credential file as the owner's user, as they
  can any platform secret.
- **Scope.** The grant has whatever the consent page grants (owner question 2). The bridge's tool filter is the
  boundary, not the token's scope.
- **The token at rest.** A file readable by the owner's user, like the headless token and the backup keys. This is
  narrower than the Keychain item it replaces (one server, not the login and every server).
- **Restoring a backup.** If credentials are backed up at all (owner question 3), a restored refresh token being stale is
  the normal case, not an edge case.
  - It fails as `invalid_grant`, and on providers with reuse detection it may revoke the whole grant family.
  - After a restore, expect `needs_grant` and re-grant. The restore notes say so; a stale refresh is never retried in a
    loop.
- **Provider policy.** A provider may limit grants per user and client, or rotate or expire refresh tokens on its own
  schedule. Both show as `needs_grant` with an alert, never as a silent fallback.
- **Protocol drift.** The bridge's method allowlist fails closed on a new MCP method that a newer client needs at start
  (like `server/discover`, seen in M1). The conformance check runs the bridge against each new version before it is
  promoted.
- **Revocation lag.** Revoking at the provider takes effect at its next check. `brunna credentials revoke` deletes the
  local copy at once, even when the provider's endpoint fails.

## r2 (11-15), after the blind review

One cold review of r1. Nothing blocked the architecture. All eight should-fix findings and the nits are taken, as
below.

- **S1, a broken bridge left the job green.** Taken in C4 and C5:
  - `llm.py` calls `fresh()` before the launch, and a failure is `job.failed` with the reason;
  - the bridge only reads the file, so nothing slow sits in Claude's 30-second MCP connect;
  - the bridge writes a status file in the run folder, and `llm.py` fails the run when the bridge never initialized or
    saw an unrecovered auth failure.
- **S2, refresh.** Taken in C4:
  - `fresh()` is defined (refresh under `need_s`, a just-issued token accepted whatever its lifetime);
  - refresh happens before the launch in `llm.py`;
  - a mid-run refresh happens in a short detached `credentials.py refresh`, never in Claude's child;
  - on a 401: re-read under the lock, refresh only if the stored token is the one that failed (by hash), at most once;
  - the hourly job only keeps refresh tokens alive and writes status;
  - a lost response gets one more try, then `needs_grant`;
  - a leftover `.new` is validated before it is taken;
  - revoke takes the lock, deletes `.new`, and deletes locally even when RFC 7009 fails, with an alert.
- **S3, sandboxed projects.** r1 was wrong here: `rule_problems` already refuses every `mcp__…` rule in a sandboxed
  project's confined run (`SANDBOXED_TOOLS`).
  - v1 is for unsandboxed projects only, and the registry refuses `credentials:` in a sandboxed project with that reason
    (C1, C6, C7).
  - The step-3 measurement on a sandboxed project is dropped.
  - The old Q3 is now a registry rule, and the carve-out is owner question 5.
  - The bridge is recorded as an egress path that `network:` doesn't describe (C7, C8).
- **S4, the run file.** Taken in C5: it names the job; the bridge validates it as the mail server does (`O_NOFOLLOW`,
  fstat owner and mode, inside `<STATE>/agent-jobs/.settings/`, read once), then rechecks the job and its tools against
  the registry.
- **S5, tool neutrality.** Taken in C6: all `mcp__`, `Bash(` and `Read(` parsing goes behind the adapter (`servers_named`,
  the extended `rule_problems`, the new `tools_named(task, server_id)`, which strips the prefix rather than splitting
  on `__`). r1's C6 would have put that parsing in `registry.py`, which the ratchet forbids.
- **S6, the bridge's network and output.** Taken in C5:
  - no redirects at all. Measured in r2: the standard library re-sent `Authorization` on a 302 from `POST` to a `GET` at
    another origin. The exact `server.url` only, with a 302-on-POST test;
  - `-I`, an explicit opener with an empty `ProxyHandler`, proxy and CA variables ignored, no process but the fixed
    refresh. r1's claim that the bridge's environment is only what the config names was wrong: it inherits Claude's
    whole environment, the headless token included;
  - re-encoded JSON with every frame redacted;
  - client capabilities stripped on `initialize`, and server-to-client requests answered upstream with errors;
  - the canary test widened to the bridge's stderr, Claude's `mcp-logs-` files and the transcript.
- **S7, the grant flow.** Taken in C2:
  - `resource` must equal `server.url`, and `issuer` must match;
  - `iss` is checked on the redirect where advertised (RFC 9207);
  - the endpoint hosts are printed for the owner to confirm;
  - no re-discovery at refresh;
  - a fixed or registered redirect URI is recorded with the client.
- **S8, rename and purge.** Taken in C3: credentials follow `rename-project.sh`, and `uninstall --purge` revokes them
  first.
- **Nits, all taken:**
  - sealed runs only (C5);
  - `attach_server` appends (C5);
  - `credential_entries` as an owner-only check of its own (C1);
  - the bridge outside the sandbox is cited, from `confined-mail-tools.md` M4 (the review said M2; the measured
    statement is in M4);
  - C9 keeps agent token runs on the shared `~/.claude`;
  - `project-agent` and leases are in C6's refusals;
  - the per-run scope of the guarantee is in Risks;
  - how the conformance gate sees the tool list without a model call is under Tests;
  - stale refresh tokens in a backup are the normal case (Risks).
- **Owner questions.**
  - Q2 (the bridge), Q4 (tools only) and Q5 (cadence, from S2) are decided in the design.
  - Q1's last sentence is rewritten (no route shares Claude Code's client), and `client_credentials` is dropped (it
    authenticates an app, not the owner, and nothing says Upwork's MCP accepts it).
  - Q3 is rewritten per S3.
  - Two questions are added: the full-rights grant, and refresh tokens in the cloud backup.
  - The list keeps only what is the owner's, each with a recommendation.
