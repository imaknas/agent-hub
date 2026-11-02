"""The runtime with no coding agent: none of runtime_ports' interfaces exist (docs/design/tool-agnostic.md).

For people with only an API key and no coding agent installed ({settings} `runtime.default: "none"`).
`runtime_ports.load()` raises `Unsupported` for every interface here, and callers treat it as "unsupported": mailbox
requests stay in the mailbox for a person, memory-gaps doesn't look at conversations, the weekly report has no
daily-rhythm section, hooks aren't installed: switched off explicitly with the reason given, never run on empty data.
Judgment points will go through the completion interface (llm-providers.md) and need nothing here.

Empty on purpose: any function added here would make it "implement part of an interface", which is rejected as a bug at
load time.
"""
