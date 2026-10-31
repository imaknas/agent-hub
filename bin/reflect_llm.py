"""The analysis section of weekly-reflect.py: hand the week's statistics to the model and get back a weekly analysis
with conclusions, evidence and recommendations (structured JSON, used directly by the HTML report). One call a week
(subscription quota, not API billing).

**Conversation text is never sent to a cloud model** (decided by the owner on 2026-10-21; the same rule as the
assistant's: only the hub machine's local model reads the original text). What is sent is the statistics, plus the
per-session summaries the assistant's local model writes (`<assistant.cli> digests --week … --json`: fixed fields, no
original text; the assistant itself leaves out sessions in the work domain and sessions whose domain it cannot tell,
giving only their count). EXCLUDED projects send not even summaries, only statistics. When the assistant lacks the
command, it fails, or there are no summaries for the week yet, only statistics are sent. Reports before 2026-W42 were
written from sampled messages; those json files are kept as they are. The fields are defined in the Muninn repo;
a change there must be made on both sides.
"""
from __future__ import annotations  # the system python3 (3.9) must be able to import it too

import json
import pathlib
import subprocess

import sys
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import hubconf  # noqa: E402
import i18n  # noqa: E402

_ = i18n.domain(__file__)   # only what the report page shows (the error, the model)
MUNINN_CLI = hubconf.p("assistant.cli")
# Projects whose registry privacy.reflection is not include: not even summaries are sent.
# Registry unreadable = None: then no summaries at all, statistics only
EXCLUDED = hubconf.reflection_private()

# Mask credential-like strings before sending to the model: one masking point for the whole platform
# (completion.py; llm.py goes through the same one before sending to any provider).
from completion import SECRET, redact  # noqa: E402,F401


# The prompt is templates/prompts/reflect-week.md (rendered by bin/prompts.py with the content language); the output
# schema is contracts/llm/reflect-week.schema.json (judgment point reflect-week, applied by bin/llm.py)

DIGEST_INTRO = ("and the summary of each session that the local model wrote (not the original text; a long session can "
                "have several rows, each covering only the new turns).{withheld}")
NO_DIGEST_INTRO = ("numbers only, with no conversation content or summaries. You can't see what the work was about: don't "
                   "guess which feature was being built or what was discussed.")
WITHHELD = " {n} more work-related sessions have no summary; they count only in the statistics."
UNDIGESTED = " {n} sessions were not summarized in time: the summaries are incomplete."


def muninn_digests(week: str) -> dict | None:
    """Session summaries written by the assistant's local model; None when they cannot be had (statistics only)."""
    exe = MUNINN_CLI
    if EXCLUDED is None or not exe or not exe.exists():
        return None
    try:
        r = subprocess.run([str(exe), "digests", "--week", week, "--json"], cwd=exe.parents[2],
                           capture_output=True, text=True, timeout=120)
        d = json.loads(r.stdout) if r.returncode == 0 else None
    except (subprocess.TimeoutExpired, ValueError):
        return None
    if not isinstance(d, dict) or d.get("week") != week:
        return None
    d["sessions"] = [x for x in d.get("sessions", []) if x.get("project") not in EXCLUDED]
    return d


def digest_rows(d: dict) -> str:
    lines = []
    for x in sorted(d["sessions"], key=lambda x: x.get("last_turn") or ""):
        parts = [f"{x.get('date', '')} [{x.get('project') or x.get('domain') or '?'}] {x.get('summary', '')}"]
        if x.get("themes"):
            parts.append("themes: " + ", ".join(x["themes"]))
        if x.get("mood"):
            parts.append(f"mood: {x['mood']}")
        if x.get("decisions"):
            parts.append("decisions: " + "; ".join(f"{y.get('what')} ({y.get('why')})" if y.get("why") else str(y.get("what"))
                                            for y in x["decisions"]))
        if x.get("commitments"):
            parts.append("commitments: " + "; ".join(str(y.get("text")) for y in x["commitments"]))
        if x.get("friction"):
            parts.append("stuck on: " + "; ".join(x["friction"]))
        parts.append(f"{x.get('user_turns', '?')} messages")
        lines.append("- " + " | ".join(parts))
    return redact("\n".join(lines))


def stats_brief(stats: dict) -> str:
    t = stats["totals"]
    lines = [f"messages {t['prompts']} (last week {t['prompts_prev']}), sessions {t['sessions']}, "
             f"active projects {t['projects']}, days worked {t['days']}/7"]
    for p in stats["projects"]:
        if p["prompts"] or p["prompts_prev"]:
            lines.append(f"- {p['name']}: messages {p['prompts']} (last week {p['prompts_prev']}), sessions {p['sessions']}, "
                         f"active {p['days']} days, commits {p['commits']}, memory notes {p['notes']}")
    r = stats["rhythm"]
    lines.append(f"late-night (00–06) messages {r['late']} ({r['late_share']:.0%}, last week {r['late_prev']}); "
                 f"longest continuous work {r['longest_hours']:.1f} hours; time work ended each day: "
                 + ", ".join(f"{d['day']} {d['time']}" for d in r["last_per_day"]))
    d = stats["delegation"]
    lines.append(f"subagents {d['agents']} (last week {d['agents_prev']}), messages between sessions {d['peer']} "
                 f"(last week {d['peer_prev']}), skills: {', '.join(f'{k}×{v}' for k, v in d['skills'].items()) or 'none'}")
    if stats["changes"]["new"] or stats["changes"]["quiet"]:
        lines.append(f"started: {', '.join(stats['changes']['new']) or 'none'}; "
                     f"stopped: {', '.join(stats['changes']['quiet']) or 'none'}")
    return "\n".join(lines)


def build_prompt(stats: dict, d: dict | None, lang: str | None = None, owner: str | None = None) -> str:
    """the prompt of judgment point reflect-week: the statistics, plus the session summaries when there are any (d: the
    assistant's summaries, already without the excluded projects)"""
    import prompts
    if d and d["sessions"]:
        withheld = WITHHELD.format(n=d.get("withheld", 0)) if d.get("withheld") else ""
        if not d.get("complete"):
            withheld += UNDIGESTED.format(n=d.get("undigested_sessions", 0))
        intro, digests = DIGEST_INTRO.format(withheld=withheld), "\n\n## Summary of each session\n" + digest_rows(d)
    else:
        intro, digests = NO_DIGEST_INTRO, ""
    return prompts.render("reflect-week", lang=lang, owner=owner, digest_intro=intro, stats=stats_brief(stats),
                          digests=digests)


def analyze(stats: dict) -> dict:
    import llm
    meta = {"model": llm.model_for("reflect-week") or _("the tool's default"), "source": "stats", "excluded": sorted(EXCLUDED or [])}
    if not stats["totals"]["prompts"]:
        return {"error": _("no activity to analyze this week"), "meta": meta}
    d = muninn_digests(stats["week"])
    if d and d["sessions"]:
        meta.update(source="stats+digests", digests=len(d["sessions"]), withheld=d.get("withheld", 0),
                    complete=d.get("complete"), undigested=d.get("undigested_sessions", 0))
        prompt = build_prompt(stats, d)
    else:
        prompt = build_prompt(stats, None)
    # judgment point reflect-week (all tools off, schema: contracts/llm-points.yaml)
    res = subprocess.run([sys.executable, str(pathlib.Path(__file__).resolve().parent / "llm.py"),
                          "run", "reflect-week"],
                         input=prompt, capture_output=True, text=True, timeout=1300)
    try:
        got = json.loads(res.stdout)
        out = got.get("structured_output")
        # the one actually used (hub.json llm may hand it to another provider)
        meta["model"] = got.get("llm_model") or meta["model"]
    except ValueError:
        out = None
    if not isinstance(out, dict):
        return {"error": _("the model call failed (exit {code}); the statistics are not affected", code=res.returncode),
                "meta": meta}
    out["meta"] = meta
    return out
