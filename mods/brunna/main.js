// brunna for herdr: what needs you across your projects, the mail your agents left, and whether brunna is
// healthy, in a side panel. It reads `brunna glance --json` (the glance.v1 contract) once a minute and nothing else.
// https://github.com/imaknas/brunna, docs/herdr.md

import { View, div } from "gpui-kit";
import { v_flex, TextView } from "gpui-base";
import { exec, notify, props, theme, nextCommand } from "herdr";

const EVERY = 60000;          // glance is cheap, but what it shows changes slowly
const SHOW = 8;               // items listed under "Needs you" (newest first)
const TOAST_GAP = 60000;      // at most one toast a minute

// Item texts are the projects' own words, shown as plain text: a `~` or `*` in them is not markdown.
const esc = (s) => String(s).replace(/[\\`*_~\[\]<>#|]/g, "\\$&");

export default class Brunna extends View {
  init(_props, cx) {
    this.g = null;
    this.error = null;
    this.seen = null;           // ids of the owner's items in the last glance (null: none yet, so no toast at start)
    this.lastToast = 0;
    this.at = null;
    this.refresh(cx);
    cx.timer.every(EVERY, (cx) => this.refresh(cx));
    cx.spawn(async (cx) => {
      for (;;) {
        const c = await nextCommand();
        if (c === "refresh") this.refresh(cx);
      }
    });
  }

  // brunna on the machine given in props (the hub may be another computer), found on PATH first (exec runs with the
  // user's real environment), then where install-cli puts it.
  async run(on) {
    const p = props() ?? {};
    const tries = p.cli ? [[p.cli, ["glance", "--json"]]]
      : [["brunna", ["glance", "--json"]], ["sh", ["-c", 'exec "$HOME/.local/bin/brunna" glance --json']]];
    let last = null;
    for (const [cmd, args] of tries) {
      try {
        const r = await exec(cmd, args, { on });
        if (r.code === 0) return JSON.parse(r.stdout);
        last = new Error(r.stderr.trim() || `${cmd} exited ${r.code}`);
      } catch (e) {
        last = e;
      }
    }
    throw last;
  }

  refresh(cx) {
    const on = (props() ?? {}).machine ?? null;
    cx.spawn(async (cx) => {
      try {
        const g = await this.run(on);
        this.toast(g);
        this.g = g;
        this.error = null;
      } catch (e) {
        this.error = String(e?.message ?? e);
      }
      this.at = new Date();
      cx.notify();
    });
  }

  toast(g) {
    const items = g.needs_you ?? [];
    const ids = new Set(items.map((x) => `${x.project}/${x.id}`));
    const fresh = this.seen ? items.filter((x) => !this.seen.has(`${x.project}/${x.id}`)) : [];
    this.seen = ids;
    if (!fresh.length || Date.now() - this.lastToast < TOAST_GAP) return;
    this.lastToast = Date.now();
    const first = fresh[0];
    notify({
      title: fresh.length === 1 ? `brunna: ${first.project} needs you` : `brunna: ${fresh.length} new things need you`,
      body: first.text.slice(0, 140),
      level: "warning",
    });
  }

  markdown() {
    if (this.error && !this.g) {
      return `### brunna\n\nCan't reach brunna: ${esc(this.error)}\n\nInstall it from [github.com/imaknas/brunna](https://github.com/imaknas/brunna), or set the mod's \`cli\` / \`machine\` props.`;
    }
    const g = this.g;
    if (!g.set_up) return "### brunna\n\nInstalled but not set up yet: run `brunna init`.";
    const out = [];
    const need = [...(g.needs_you ?? [])].sort((a, b) => (b.since ?? "").localeCompare(a.since ?? ""));
    out.push(`### Needs you · ${need.length}`);
    for (const x of need.slice(0, SHOW)) out.push(`- **${esc(x.project)}** · ${esc(x.text.replace(/\s+/g, " ").slice(0, 120))}`);
    if (need.length > SHOW) out.push(`- … ${need.length - SHOW} more (\`brunna owner list\`)`);
    const mail = g.mail ?? { total: 0, projects: [] };
    out.push("", `### Mail · ${mail.total} open`);
    for (const p of mail.projects) out.push(`- ${esc(p.project)} · ${p.open}`);
    const h = g.health ?? { state: "unknown", alerts: [] };
    const head = { ok: "✅ brunna is healthy", problem: "⚠️ brunna has problems", unknown: "brunna: status not known yet" };
    out.push("", `### ${head[h.state] ?? head.unknown}`);
    for (const a of (h.alerts ?? []).filter((a) => a.level === "bad").slice(0, 5)) out.push(`- ${esc(a.text.slice(0, 160))}`);
    if (g.scheduler && !g.scheduler.alive) out.push(`- the scheduler has not ticked since ${g.scheduler.last_tick ?? "-"}`);
    const page = g.status_page;
    // a page local to the hub machine opens only when the hub is this computer
    if (page && (!page.local_to || !(props() ?? {}).machine)) out.push("", `[Status page](${page.url})`);
    const at = this.at ? this.at.toTimeString().slice(0, 5) : "";
    out.push("", `_updated ${at}${this.error ? ` · last try failed: ${esc(this.error)}` : ""}_`);
    return out.join("\n");
  }

  render(cx) {
    const t = theme();
    return v_flex().size_full().bg(t.surface).p(12).text_size(13).text_color(t.foreground)
      .child(this.g || this.error ? TextView.markdown("brunna", this.markdown()).scrollable(true)
        : div().text_color(t.muted).child("brunna…"));
  }
}
