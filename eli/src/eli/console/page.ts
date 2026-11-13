import { formatUsd } from "../stats.js";
import { type Dashboard, RANGES, type Totals } from "./data.js";

export const esc = (s: string): string =>
  s.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]!);

const STYLE = `
:root{--bg:#f6f6f4;--surface:#fcfcfb;--ink:#0b0b0b;--ink2:#52514e;--grid:#e1e0d9;--line:#e1e0d9;--bar:#2a78d6;--warn:#b54708}
@media (prefers-color-scheme:dark){:root{--bg:#121211;--surface:#1a1a19;--ink:#f0efec;--ink2:#c3c2b7;--grid:#2c2c2a;--line:#2c2c2a;--bar:#3987e5;--warn:#f79009}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.45 system-ui,-apple-system,"Segoe UI",sans-serif}
main{max-width:1040px;margin:0 auto;padding:24px 16px 48px}
header{display:flex;flex-wrap:wrap;gap:8px 16px;align-items:baseline;justify-content:space-between;margin-bottom:16px}
h1{font-size:22px;margin:0}h2{font-size:15px;margin:0 0 12px}.muted{color:var(--ink2)}
.who{display:flex;gap:12px;align-items:center;color:var(--ink2);font-size:14px}
button{font:inherit;font-size:14px;background:none;border:1px solid var(--line);color:var(--ink);border-radius:6px;padding:4px 10px;cursor:pointer}
nav{display:flex;gap:6px;flex-wrap:wrap;margin-bottom:16px}
nav a{color:var(--ink);text-decoration:none;border:1px solid var(--line);border-radius:999px;padding:4px 12px;font-size:14px}
nav a[aria-current]{background:var(--ink);color:var(--surface);border-color:var(--ink)}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:12px;margin-bottom:12px}
.card{background:var(--surface);border:1px solid var(--line);border-radius:10px;padding:16px;min-width:0}
.tile .label{color:var(--ink2);font-size:13px}.tile .value{font-size:28px;font-weight:600;font-variant-numeric:tabular-nums;margin:2px 0}
.tile .note{color:var(--ink2);font-size:13px}.warn{color:var(--warn)}
.grid2{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:12px;margin-top:12px}
svg{display:block;width:100%;height:auto}svg text{fill:var(--ink2);font-size:11px}
.hbar{display:grid;grid-template-columns:minmax(110px,40%) 1fr auto;gap:8px;align-items:center;margin:6px 0;font-size:14px}
.hbar .track{height:10px}.hbar .fill{height:10px;background:var(--bar);border-radius:0 4px 4px 0;min-width:2px}
.hbar .n{font-variant-numeric:tabular-nums;color:var(--ink2)}
table{width:100%;border-collapse:collapse;font-size:14px}th,td{text-align:left;padding:8px 6px;border-bottom:1px solid var(--line)}
th{color:var(--ink2);font-weight:500}td.num,th.num{text-align:right;font-variant-numeric:tabular-nums}
.scroll{overflow-x:auto}details{margin-top:8px;font-size:14px}summary{cursor:pointer;color:var(--ink2)}
footer{margin-top:24px;color:var(--ink2);font-size:13px}
`;

function page(title: string, body: string): string {
  return `<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex"><title>${esc(title)}</title><style>${STYLE}</style></head><body><main>${body}</main></body></html>`;
}

export function signedOutPage(message?: string): string {
  return page(
    "Eli console",
    `<h1>Eli console</h1>
<div class="card" style="margin-top:16px;max-width:560px">
${message ? `<p class="warn">${esc(message)}</p>` : ""}
<p>To sign in, open Slack and send Eli the word <strong>console</strong> in a DM (or use <strong>/eli-console</strong>). Eli will reply with a private sign-in link that works once, for 10 minutes.</p>
<p class="muted">Only people on Eli's board or admin list can open this page.</p>
</div>`,
  );
}

function change(cur: number, prev: number | undefined, days: number, kind: "count" | "usd"): string {
  if (prev === undefined) return "";
  if (prev === 0) return cur === 0 ? `Same as the previous ${days} days` : `None in the previous ${days} days`;
  const pct = Math.round(((cur - prev) / prev) * 100);
  const was = kind === "usd" ? formatUsd(prev) : prev.toLocaleString("en-US");
  return `${pct === 0 ? "No change" : `${pct > 0 ? "↑" : "↓"} ${Math.abs(pct)}%`} vs previous ${days} days (${was})`;
}

function tile(label: string, value: string, note: string, warn = false): string {
  return `<div class="card tile"><div class="label">${esc(label)}</div><div class="value${warn ? " warn" : ""}">${esc(value)}</div><div class="note">${esc(note)}</div></div>`;
}

function tiles(t: Totals, prev: Totals | undefined, days: number): string {
  const perQ = t.requests ? `About ${formatUsd(t.costUsd / t.requests)} per question` : "No questions yet";
  return `<section class="tiles">
${tile("Questions answered", t.requests.toLocaleString("en-US"), change(t.requests, prev?.requests, days, "count") || "All time")}
${tile("People using Eli", t.people.toLocaleString("en-US"), change(t.people, prev?.people, days, "count") || "All time")}
${tile("Estimated cost", formatUsd(t.costUsd), `${perQ}${t.webSearches ? `, incl. ${t.webSearches} web searches` : ""}`)}
${tile("Failed requests", t.failures.toLocaleString("en-US"), t.failures ? "⚠ Eli replied with an apology; details are in its log" : "None", t.failures > 0)}
</section>`;
}

const shortDate = (iso: string) => {
  const [y, m, d] = iso.split("-").map(Number) as [number, number, number];
  return new Date(Date.UTC(y, m - 1, d)).toLocaleDateString("en-US", { month: "short", day: "numeric", timeZone: "UTC" });
};

function dailyChart(daily: Dashboard["daily"]): string {
  const W = 960, H = 220, L = 34, B = 24, T = 10;
  const max = Math.max(1, ...daily.map((d) => d.requests));
  const step = Math.max(1, Math.ceil(max / 4));
  const top = step * Math.ceil(max / step);
  const n = daily.length;
  const slot = (W - L) / n;
  const bw = Math.max(2, Math.min(28, slot - 2));
  const y = (v: number) => T + (H - T - B) * (1 - v / top);
  const ticks = [];
  for (let v = 0; v <= top; v += step) {
    ticks.push(`<line x1="${L}" x2="${W}" y1="${y(v)}" y2="${y(v)}" stroke="var(--grid)" stroke-width="1"/><text x="${L - 6}" y="${y(v) + 4}" text-anchor="end">${v}</text>`);
  }
  const every = Math.ceil(n / 8);
  const bars = daily
    .map((d, i) => {
      const x = L + i * slot + (slot - bw) / 2;
      const label = i % every === 0 || i === n - 1 ? `<text x="${x + bw / 2}" y="${H - 6}" text-anchor="middle">${esc(shortDate(d.date))}</text>` : "";
      const tip = `<title>${esc(shortDate(d.date))}: ${d.requests} question${d.requests === 1 ? "" : "s"}, ${esc(formatUsd(d.costUsd))}</title>`;
      if (!d.requests) return `${label}<rect x="${L + i * slot}" y="${T}" width="${slot}" height="${H - T - B}" fill="transparent">${tip}</rect>`;
      const y0 = y(0), y1 = y(d.requests), r = Math.min(4, bw / 2, y0 - y1);
      const path = `M${x},${y0}V${y1 + r}Q${x},${y1} ${x + r},${y1}H${x + bw - r}Q${x + bw},${y1} ${x + bw},${y1 + r}V${y0}Z`;
      // Hit target spans the whole day column, not just the bar.
      return `${label}<g><rect x="${L + i * slot}" y="${T}" width="${slot}" height="${H - T - B}" fill="transparent"/><path d="${path}" fill="var(--bar)"/>${tip}</g>`;
    })
    .join("");
  const rows = daily.map((d) => `<tr><td>${esc(shortDate(d.date))}</td><td class="num">${d.requests}</td><td class="num">${esc(formatUsd(d.costUsd))}</td></tr>`).join("");
  return `<section class="card"><h2>Questions per day</h2>
<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Bar chart of questions answered per day">${ticks.join("")}${bars}</svg>
<details><summary>Show as a table</summary><div class="scroll"><table><thead><tr><th>Day</th><th class="num">Questions</th><th class="num">Est. cost</th></tr></thead><tbody>${rows}</tbody></table></div></details>
</section>`;
}

function barList(title: string, items: Array<{ name: string; count: number }>, empty: string, note?: string): string {
  const max = Math.max(1, ...items.map((i) => i.count));
  const rows = items.length
    ? items
        .map(
          (i) =>
            `<div class="hbar"><span>${esc(i.name)}</span><div class="track"><div class="fill" style="width:${((i.count / max) * 100).toFixed(1)}%"></div></div><span class="n">${i.count}</span></div>`,
        )
        .join("")
    : `<p class="muted">${esc(empty)}</p>`;
  return `<section class="card"><h2>${esc(title)}</h2>${rows}${note ? `<p class="muted" style="font-size:13px;margin:8px 0 0">${esc(note)}</p>` : ""}</section>`;
}

export interface DashboardView {
  viewerName: string;
  names: ReadonlyMap<string, string>;
  timeZone: string;
  limits: { userPerHour: number; workspacePerDay: number };
}

export function dashboardPage(d: Dashboard, v: DashboardView): string {
  const when = (iso: string) =>
    new Date(iso).toLocaleString("en-US", { timeZone: v.timeZone, month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
  const rangeLabel = d.days ? `Last ${d.days} days` : "All time";
  const nav = [...RANGES.map((n) => [String(n), `${n} days`] as const), ["all", "All time"] as const]
    .map(([q, label]) => `<a href="/?days=${q}"${(d.days ? String(d.days) : "all") === q ? ' aria-current="page"' : ""}>${label}</a>`)
    .join("");
  const people = d.people.length
    ? `<div class="scroll"><table><thead><tr><th>Person</th><th class="num">Questions</th><th class="num">Est. cost</th><th>Most common topic</th><th>Last used</th></tr></thead><tbody>${d.people
        .map(
          (p) =>
            `<tr><td>${esc(v.names.get(p.user) ?? p.user)}</td><td class="num">${p.requests}</td><td class="num">${esc(formatUsd(p.costUsd))}</td><td>${esc(p.topTopic)}</td><td>${esc(when(p.lastAt))}</td></tr>`,
        )
        .join("")}</tbody></table></div>`
    : `<p class="muted">Nobody has used Eli in this period.</p>`;

  return page(
    "Eli console",
    `<header><div><h1>Eli usage</h1><div class="muted">${esc(rangeLabel)}${d.from ? ` · since ${esc(when(d.from))}` : ""}</div></div>
<div class="who"><span>Signed in as ${esc(v.viewerName)}</span><form method="post" action="/logout"><button type="submit">Sign out</button></form></div></header>
<nav aria-label="Time range">${nav}</nav>
${tiles(d.totals, d.previous, d.days)}
${dailyChart(d.daily)}
<div class="grid2">
${barList("What people ask about", d.topics, "No questions yet.", "Topics are worked out automatically from each question. The questions themselves are never stored.")}
${barList("Features used", d.features, "No files, links, web searches or reminders used yet.", "Counted once per question.")}
</div>
<section class="card" style="margin-top:12px"><h2>Who is using Eli</h2>${people}</section>
<footer>Costs are estimates from token counts; the provider's billing page has the real numbers. Limits: ${v.limits.userPerHour} questions per person per hour, ${v.limits.workspacePerDay} per workspace per day. Updated ${esc(when(d.to))} (${esc(v.timeZone)}).</footer>`,
  );
}
