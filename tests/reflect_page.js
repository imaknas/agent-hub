// Renders the weekly report page (reflect/index.html, built by bin/weekly-reflect.py) the way a browser would, with a
// stub DOM: the page's own script runs in node's vm, then show() is called for every week it embeds.
// Prints the <title>, what the page shows on load, then each week's rendered HTML.
//   node tests/reflect_page.js <index.html>
"use strict";
const fs = require("fs");
const vm = require("vm");

const html = fs.readFileSync(process.argv[2], "utf8");
const script = html.match(/<script>([\s\S]*)<\/script>/)[1];
const title = (html.match(/<title>([\s\S]*?)<\/title>/) || [null, ""])[1];
const app = { innerHTML: "" };
const ctx = {
  document: { getElementById: id => (id === "app" ? app : null), documentElement: {}, title: "" },
  location: { hash: "" },
  history: { replaceState() {} },
};
vm.createContext(ctx);
vm.runInContext(script + "\n;globalThis.__weeks = WEEKS; globalThis.__show = show;", ctx);
const out = [`=== title\n${title}`, `=== on load\n${app.innerHTML}`];
for (const w of ctx.__weeks) {
  ctx.__show(w.week);
  out.push(`=== ${w.week}\n${app.innerHTML}`);
}
process.stdout.write(out.join("\n") + "\n");
