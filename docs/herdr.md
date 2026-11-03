# brunna with herdr, and in any status bar

## In herdr

[herdr](https://herdr.dev) keeps many coding agents running side by side. brunna keeps what they learn: each project's
notes and state, the nightly settling, the mailbox between projects, and what only you can decide. They fit together:

- **Nothing to set up for your agents.** Open a brunna project in a herdr pane and start Claude Code, Codex or another
  agent as usual: brunna's hooks, its rules and the MCP mailbox work as they do in any terminal.
- **The brunna panel.** A herdr mod shows, beside your agents, what needs you across all your projects, the mail your
  agents left, and whether brunna is healthy, with a toast when something new needs you. To add it, tell your agent in
  herdr:

  > Add the herdr mod from github.com/imaknas/brunna (herdr/brunna).

  It runs `brunna glance --json` once a minute and nothing else. If brunna runs on another computer (a hub machine you
  reach over SSH), set the mod's `machine` prop to that herdr machine; if `brunna` isn't on the app's PATH, set `cli` to
  its full path (install-cli puts it in `~/.local/bin/brunna`).

## In any status bar: `brunna glance`

```bash
brunna glance            # a few lines for a terminal
brunna glance --json     # the glance.v1 contract (contracts/glance.v1.schema.json)
```

One look at what needs you (the owner's open to-dos and questions), the open mail per project, the platform's health
(the status page's alerts) and whether the scheduler is running. It only reads and answers in a fraction of a second, so
a status bar can run it every minute: an iTerm2 status bar component, SwiftBar or xbar on macOS, a tmux status line.
The alerts and your open items are as of brunna's last scheduler tick (up to 15 minutes old, `health.as_of`); the mail
and the scheduler are live.

If you open the status page from another machine (over a VPN or tailnet), put its address in the settings file so
glance reports it:

```json
"status_page": {"url": "https://your-hub.example.ts.net/"}
```
