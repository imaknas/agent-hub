# Security

agent-hub runs coding agents unattended, gives them sandboxes, and holds credentials for the services it schedules.
A flaw in any of that matters, so please report one privately.

## Reporting

Use GitHub's private vulnerability reporting on this repository ("Report a vulnerability" under the Security tab).
Please don't open a public issue for a security problem. Say what you found, how to reproduce it, and what it lets an
attacker do.

## Scope

In scope: anything that lets a project's agent, service or job act outside what its registry entry allows. That
includes escaping the sandbox, reading another project's files or memory, writing the platform's own files, sending
mail as another project, or reaching credentials. Bugs that leak an instance's private data into this repository's
code or into what the platform publishes also count. The sandbox's guarantees and their limits on each OS are in
[docs/design/capabilities.md](docs/design/capabilities.md).

Out of scope: a session the owner opens by hand. There the coding agent's own protections apply, and the platform
advises but does not enforce (see "Tool neutrality" in [AGENTS.md](AGENTS.md)).

## Supported versions

Fixes go into the latest release only.
