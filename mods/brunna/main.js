// brunna for herdr: what needs you across your projects, the mail your agents left, and whether brunna is
// healthy, in a side panel. It reads `brunna glance --json` (the glance.v1 contract) once a minute and nothing else.
// https://github.com/imaknas/brunna, docs/herdr.md

import { View, div, svg } from "gpui-kit";
import { v_flex, h_flex } from "gpui-base";
import { exec, notify, props, theme, nextCommand, run } from "herdr";

const EVERY = 60000;          // glance is cheap, but what it shows changes slowly
const SHOW = 8;               // items listed under "Needs you" (newest first)
const TOAST_GAP = 60000;      // at most one toast a minute
const MARK = "mods/brunna/mark.svg";

// How long ago, the way herdr's agents list says it.
function ago(iso, now = Date.now()) {
  const t = Date.parse(iso ?? "");
  if (Number.isNaN(t)) return "";
  const m = Math.max(0, Math.floor((now - t) / 60000));
  if (m < 1) return "now";
  if (m < 60) return `${m}m`;
  const h = Math.floor(m / 60);
  return h < 24 ? `${h}h` : `${Math.floor(h / 24)}d`;
}

// Each project keeps one of the theme's terminal colors, so its items and its mail line up at a glance.
function hue(t, project) {
  let h = 0;
  for (const c of project ?? "") h = (h * 31 + c.codePointAt(0)) >>> 0;
  return t[`ansi${[1, 2, 3, 4, 5, 6][h % 6]}`] ?? t.accent;
}

const dot = (color, size = 7) => div().w(size).h(size).flex_none().rounded(size / 2).bg(color);

export default class Brunna extends View {
  init(_props, cx) {
    this.g = null;
    this.error = null;
    this.seen = null;           // ids of the owner's items in the last glance (null: none yet, so no toast at start)
    this.lastToast = 0;
    this.at = null;
    this.open = new Set();      // items shown in full
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

  // A page local to the hub machine opens only when the hub is this computer.
  page() {
    const page = this.g?.status_page;
    return page?.url && (!page.local_to || !(props() ?? {}).machine) ? page.url : null;
  }

  header(t) {
    const h = this.g?.health ?? { state: "unknown", alerts: [] };
    const bad = (h.alerts ?? []).filter((a) => a.level === "bad").length;
    const [color, label] = !this.g ? [t.unknown, "…"]
      : h.state === "ok" ? [t.ansi2 ?? t.done, "Healthy"]
      : h.state === "problem" ? [t.blocked, bad === 1 ? "1 problem" : `${bad || "Some"} problems`]
      : [t.unknown, "Not known yet"];
    return h_flex().px(14).pt(14).pb(12).flex_none().items_center().gap(8)
      .child(svg(MARK).size(18).flex_none().text_color(t.accent))
      .child(div().text_size(14).font_semibold().child("brunna"))
      .child(div().flex_1())
      .child(h_flex().h(22).px(8).gap(6).items_center().rounded(11).bg(t.raised).border(1).border_color(t.border)
        .child(dot(color)).child(div().text_size(11.5).text_color(t.muted).child(label)))
      .child(h_flex().id("brunna-refresh").w(22).h(22).flex_none().rounded(6).items_center().justify_center()
        .text_color(t.dim).cursor_pointer().hover((s) => s.bg(t.hover).text_color(t.foreground))
        .tooltip("Refresh now").on_click(() => run("brunna.refresh")).child("↻"));
  }

  tiles(t) {
    const g = this.g;
    const need = (g.needs_you ?? []).length;
    const mail = g.mail?.total ?? 0;
    const bad = (g.health?.alerts ?? []).filter((a) => a.level === "bad").length;
    const tile = (n, label, hot) => v_flex().flex_1().min_w(0).h(64).px(10).py(8).gap(1).justify_center().rounded(10)
      .bg(t.raised).border(1).border_color(t.border)
      .child(div().text_size(22).font_semibold().line_height("28px").text_color(hot ? t.blocked : t.foreground).child(String(n)))
      .child(div().text_size(11).text_color(t.dim).truncate().child(label));
    return h_flex().px(14).h(64).gap(8).flex_none().items_start()
      .child(tile(need, "need you", need > 0))
      .child(tile(mail, mail === 1 ? "letter open" : "letters open", false))
      .child(tile(bad, bad === 1 ? "problem" : "problems", bad > 0));
  }

  section(t, label, extra) {
    return h_flex().px(8).pt(14).pb(4).flex_none().items_center().gap(6).text_size(11).font_medium()
      .child(div().text_color(t.dim).child(label))
      .when(Boolean(extra), (el) => el.child(div().text_color(t.dim).child(extra)));
  }

  item(t, x, now) {
    const key = `${x.project}/${x.id}`;
    const full = this.open.has(key);
    const question = x.kind === "question";
    const text = String(x.text ?? "").replace(/\s+/g, " ");
    const mark = question
      ? h_flex().w(16).h(16).mt(1).flex_none().rounded(8).border(1).border_color(t.blocked).items_center().justify_center()
        .text_size(10).font_semibold().text_color(t.blocked).child("?")
      : div().w(14).h(14).mt(2).ml(1).mr(1).flex_none().rounded(4).border(1).border_color(t.dim);
    return h_flex().id(`brunna-${key}`).px(8).py(7).flex_none().gap(10).items_start().rounded(8)
      .cursor_pointer().hover((s) => s.bg(t.hover))
      .tooltip(full ? "Show less" : "Show all")
      .on_click((_e, cx) => {
        full ? this.open.delete(key) : this.open.add(key);
        cx.notify();
      })
      .child(mark)
      .child(v_flex().flex_1().min_w(0).gap(2)
        .child(h_flex().gap(6).items_center()
          .child(dot(hue(t, x.project), 6))
          .child(div().flex_1().min_w(0).truncate().text_size(11.5).font_medium().text_color(t.muted).child(x.project))
          .child(div().flex_none().text_size(11).text_color(t.dim).child(ago(x.since, now))))
        .child(full ? div().text_size(12.5).line_height("18px").child(text)
          : div().text_size(12.5).line_height("18px").truncate().child(text)));
  }

  body(t) {
    const g = this.g;
    const now = Date.now();
    const need = [...(g.needs_you ?? [])].sort((a, b) => (b.since ?? "").localeCompare(a.since ?? ""));
    const rows = [];
    if (need.length) {
      rows.push(this.section(t, "Needs you", "newest first"));
      for (const x of need.slice(0, SHOW)) rows.push(this.item(t, x, now));
      if (need.length > SHOW) rows.push(div().px(8).py(6).text_size(11.5).text_color(t.dim)
        .child(`${need.length - SHOW} more · brunna owner list`));
    } else {
      rows.push(h_flex().mx(8).mt(14).px(10).py(10).gap(8).items_center().rounded(8).bg(t.raised)
        .child(dot(t.ansi2 ?? t.done)).child(div().text_size(12.5).text_color(t.muted).child("Nothing needs you right now.")));
    }
    const mail = g.mail?.projects ?? [];
    if (mail.length) {
      rows.push(this.section(t, "Mail"));
      for (const p of mail) rows.push(h_flex().px(8).h(28).flex_none().gap(8).items_center()
        .child(dot(hue(t, p.project), 6))
        .child(div().flex_1().min_w(0).truncate().text_size(12.5).child(p.project))
        .child(div().text_size(11).text_color(t.dim).child(ago(p.oldest, now)))
        .child(h_flex().h(18).px(7).rounded(9).items_center().bg(t.raised).border(1).border_color(t.border)
          .text_size(11).font_medium().child(String(p.open))));
    }
    const alerts = (g.health?.alerts ?? []).filter((a) => a.level === "bad");
    if (alerts.length) {
      rows.push(this.section(t, "Problems"));
      for (const a of alerts.slice(0, 5)) rows.push(h_flex().px(8).py(5).flex_none().gap(8).items_start()
        .child(div().mt(6).child(dot(t.blocked, 6)))
        .child(div().flex_1().min_w(0).text_size(12).line_height("17px").text_color(t.muted).child(String(a.text))));
    }
    return v_flex().id("brunna-list").flex_1().min_h(0).px(6).pb(8).overflow_y_scrollbar().children(rows);
  }

  footer(t) {
    const s = this.g?.scheduler;
    const page = this.page();
    const at = this.at ? this.at.toTimeString().slice(0, 5) : "";
    return h_flex().px(14).h(36).flex_none().gap(8).items_center().border_t(1).border_color(t.border)
      .text_size(11).text_color(t.dim)
      .when(Boolean(s), (el) => el
        .child(dot(s.alive ? t.ansi2 ?? t.done : t.blocked, 6))
        .child(div().truncate().tooltip("brunna's scheduler").child(s.alive ? `tick ${ago(s.last_tick)} ago` : `stopped ${ago(s.last_tick)} ago`)))
      .child(div().flex_1())
      .when(Boolean(this.error), (el) => el.child(div().text_color(t.blocked).tooltip(this.error).child("refresh failed")))
      .when(!this.error, (el) => el.child(div().child(at)))
      .when(Boolean(page), (el) => el.child(div().id("brunna-page").text_color(t.accent).cursor_pointer()
        .hover((s) => s.text_color(t.foreground)).tooltip(page)
        .on_click((_e, cx) => cx.open_url(page)).child("Status page ↗")));
  }

  // Before the first answer, without brunna, or before `brunna init`: the mark and one line of what to do.
  notice(t, title, line) {
    return v_flex().size_full().bg(t.surface).items_center().justify_center().gap(10).px(24)
      .child(svg(MARK).size(32).text_color(t.accent))
      .child(div().text_size(14).font_semibold().child(title))
      .when(Boolean(line), (el) => el.child(div().text_size(12).line_height("18px").text_color(t.muted).text_center().child(line)));
  }

  render(cx) {
    const t = theme();
    if (!this.g && !this.error) return this.notice(t, "brunna", null);
    if (!this.g) return this.notice(t, "Can't reach brunna",
      `${this.error}. Install it from github.com/imaknas/brunna, or set the mod's cli or machine props.`);
    if (!this.g.set_up) return this.notice(t, "brunna isn't set up yet", "Run brunna init.");
    return v_flex().size_full().min_h(0).bg(t.surface).text_size(13).text_color(t.foreground)
      .child(this.header(t))
      .child(this.tiles(t))
      .child(this.body(t))
      .child(this.footer(t));
  }
}
