"""Render the morning dashboard: holdings first, then theme scouting.

Holdings and prices come from the portfolio config. Everything that changes
day to day and needs judgment -- last-session moves, per-position notes, theme
scouting, sources -- comes from a separate JSON file written each morning. The
renderer itself makes no claims; it only lays out what those two files say.
"""

from __future__ import annotations

import datetime as dt
import json
from html import escape
from pathlib import Path

import yaml

from .models import Portfolio, load_portfolio

# Severity chip text for engine alerts. Concentration is left out: the GEN
# meter already shows it, and saying it twice is how briefs turn into noise.
_ALERT_LABELS = {"critical": "Needs an answer", "warn": "Heads up", "info": "Note"}
_SKIP_ALERT_KINDS = {"concentration"}


def load_baseline(path: str | Path) -> tuple[dt.date | None, dict[str, float]]:
    raw = yaml.safe_load(Path(path).read_text()) or {}
    base = raw.get("baseline") or {}
    date = base.get("date")
    if isinstance(date, str):
        date = dt.date.fromisoformat(date)
    prices = {k.upper(): float(v) for k, v in (base.get("prices") or {}).items()}
    return date, prices


def _money(x: float, cents: bool = False) -> str:
    sign = "−" if x < 0 else ""
    return f"{sign}${abs(x):,.2f}" if cents else f"{sign}${abs(x):,.0f}"


def _signed_money(x: float) -> str:
    return ("+" if x > 0 else "") + _money(x)


def _pct(x: float, digits: int = 1) -> str:
    sign = "+" if x > 0 else ("−" if x < 0 else "")
    return f"{sign}{abs(x):.{digits}f}%"


def _move_chip(pct: float | None) -> str:
    if pct is None:
        return '<span class="chip flat">—</span>'
    cls = "up" if pct > 0 else ("down" if pct < 0 else "flat")
    arrow = "▲" if pct > 0 else ("▼" if pct < 0 else "•")
    return f'<span class="chip {cls}"><i aria-hidden="true">{arrow}</i>{_pct(pct, 2)}</span>'


def _price(p: float) -> str:
    return f"${p:,.2f}" if p < 1000 else f"${p:,.0f}"


def _rows(portfolio: Portfolio, baseline: dict[str, float], data: dict) -> tuple[list[dict], float]:
    invested = portfolio.invested_value
    rows = []
    for pos in portfolio.positions:
        base_px = baseline.get(pos.ticker)
        base_val = pos.shares * base_px if base_px else None
        rows.append(
            {
                "ticker": pos.ticker,
                "name": data.get("names", {}).get(pos.ticker, ""),
                "shares": pos.shares,
                "price": pos.price,
                "value": pos.value,
                "weight": pos.value / invested if invested else 0.0,
                "since": (pos.value - base_val) if base_val else None,
                "since_pct": ((pos.price / base_px) - 1) * 100 if base_px else None,
                "day": data.get("day_moves", {}).get(pos.ticker),
                "note": data.get("notes", {}).get(pos.ticker, ""),
                "sleeve": pos.sleeve,
            }
        )
    rows.sort(key=lambda r: -r["value"])
    return rows, invested


def _summary(portfolio: Portfolio, rows: list[dict], invested: float, base_date, data: dict) -> str:
    base_total = sum(r["value"] - r["since"] for r in rows if r["since"] is not None)
    delta = sum(r["since"] for r in rows if r["since"] is not None)
    gen = next((r for r in rows if r["ticker"] == "GEN"), None)
    ex_gen_delta = delta - (gen["since"] if gen and gen["since"] is not None else 0.0)
    ex_gen_base = base_total - ((gen["value"] - gen["since"]) if gen and gen["since"] is not None else 0.0)

    gen_w = gen["weight"] if gen else 0.0
    cap = 0.25
    since_label = base_date.strftime("%b %-d") if base_date else "baseline"

    owned_tag = '<span class="tag">you own</span>'
    week = "".join(
        f'<li><span class="when">{escape(item["when"])}</span>'
        f'<span class="what">{escape(item["what"])}</span>'
        f'{owned_tag if item.get("owned") else ""}</li>'
        for item in data.get("this_week", [])
    )

    return f"""
<section class="summary" aria-label="Summary">
  <div class="cell">
    <div class="label">Stocks &amp; crypto</div>
    <div class="big">{_money(invested)}</div>
    <div class="sub">plus {_money(portfolio.cash)} cash</div>
  </div>
  <div class="cell">
    <div class="label">Since {since_label}</div>
    <div class="big">{_signed_money(delta)}</div>
    <div class="sub">{_pct(delta / base_total * 100 if base_total else 0)} overall ·
      everything except GEN {_pct(ex_gen_delta / ex_gen_base * 100 if ex_gen_base else 0)}</div>
  </div>
  <div class="cell">
    <div class="label">GEN share of your stocks</div>
    <div class="big">{gen_w:.0%}</div>
    <div class="meter" role="img" aria-label="GEN is {gen_w:.0%} of invested money; your cap is {cap:.0%}">
      <span class="fill" style="width:{min(gen_w, 1) * 100:.1f}%"></span>
      <span class="cap" style="left:{cap * 100:.0f}%"></span>
    </div>
    <div class="sub">tick marks your {cap:.0%} cap</div>
  </div>
  <div class="cell week">
    <div class="label">This week</div>
    <ul>{week}</ul>
  </div>
</section>"""


def _open_items(alerts, flags: list[dict], alert_text: dict[str, str]) -> str:
    items = []
    for a in alerts:
        if a.kind in _SKIP_ALERT_KINDS:
            continue
        # Engine messages are written for whoever maintains the config; the
        # daily data can restate one for the reader. The alert still shows.
        items.append((a.severity, alert_text.get(a.kind, a.message)))
    for f in flags:
        items.append((f.get("level", "info"), f["text"]))
    if not items:
        return ""
    lis = "".join(
        f'<li class="{escape(sev)}"><span class="sev">{escape(_ALERT_LABELS.get(sev, "Note"))}</span>'
        f"<span>{escape(text)}</span></li>"
        for sev, text in items
    )
    return f'<section class="open" aria-label="Open items"><ul>{lis}</ul></section>'


def _book(rows: list[dict], invested: float, portfolio: Portfolio, base_date, data: dict) -> str:
    since_label = base_date.strftime("%b %-d") if base_date else "baseline"
    trs = []
    for r in rows:
        since = (
            f'<span class="num">{_signed_money(r["since"])}</span>'
            f'<span class="pct">{_pct(r["since_pct"])}</span>'
            if r["since"] is not None
            else '<span class="pct">—</span>'
        )
        trs.append(
            f"""<tr>
  <th scope="row"><span class="tk">{escape(r["ticker"])}</span><span class="nm">{escape(r["name"])}</span></th>
  <td class="r"><span class="num">{_money(r["value"])}</span><span class="pct">{_price(r["price"])}</span></td>
  <td class="w"><span class="bar"><span style="width:{r["weight"] * 100:.2f}%"></span></span><span class="pct">{r["weight"] * 100:.1f}%</span></td>
  <td class="r">{_move_chip(r["day"])}</td>
  <td class="r since">{since}</td>
  <td class="note">{escape(r["note"])}</td>
</tr>"""
        )

    ext = "".join(
        f"<li>{escape(a.label)}: {_money(a.value)}</li>" for a in portfolio.external_accounts
    )
    last = escape(data.get("last_session_label", "Last session"))
    return f"""
<section id="book">
  <div class="sechead">
    <h2>Your positions</h2>
    <p>Sorted by size. “Since {since_label}” compares to the prices in your {since_label} screenshots; there's no cost basis on file, so it isn't your tax gain.</p>
  </div>
  <div class="tablewrap">
    <table>
      <thead><tr>
        <th scope="col">Holding</th><th scope="col" class="r">Value · price</th>
        <th scope="col" class="w">Weight</th><th scope="col" class="r">{last}</th>
        <th scope="col" class="r">Since {since_label}</th><th scope="col">What to know</th>
      </tr></thead>
      <tbody>{"".join(trs)}</tbody>
      <tfoot><tr>
        <th scope="row">Total</th><td class="r"><span class="num">{_money(invested)}</span></td>
        <td class="w"></td><td></td>
        <td class="r since"><span class="num">{_signed_money(sum(r["since"] or 0 for r in rows))}</span></td><td></td>
      </tr></tfoot>
    </table>
  </div>
  <ul class="ext"><li>Not shown, not traded here:</li>{ext}</ul>
</section>"""


def _theme_card(theme: dict, rows_by_ticker: dict[str, dict]) -> str:
    owned = [rows_by_ticker[t] for t in theme.get("owned", []) if t in rows_by_ticker]
    own_val = sum(r["value"] for r in owned)
    own_chip = (
        f'<span class="own yes">You own {_money(own_val)}</span>'
        if owned
        else '<span class="own">You own none</span>'
    )
    bullets = "".join(f"<li>{escape(b)}</li>" for b in theme.get("this_week", []))
    names = "".join(
        f"""<tr>
  <th scope="row" class="tk">{escape(n["ticker"])}</th>
  <td class="r num">{_price(n["price"]) if n.get("price") is not None else "—"}</td>
  <td class="r">{_move_chip(n.get("move"))}</td>
  <td class="nn">{escape(n.get("note", ""))}</td>
</tr>"""
        for n in theme.get("names", [])
    )
    return f"""
<article class="theme">
  <header><h3>{escape(theme["name"])}</h3>{own_chip}</header>
  <p class="headline">{escape(theme.get("headline", ""))}</p>
  <ul class="bullets">{bullets}</ul>
  <div class="namewrap"><table class="names"><tbody>{names}</tbody></table></div>
  <p class="watch"><span>Watching</span>{escape(theme.get("watching", ""))}</p>
</article>"""


_CSS = """
/* Layout: a pre-open desk sheet. Stamp and verdict up top, the book as one dense
   table, then theme scouting two-up. Numbers in mono so columns line up. */
:root {
  --paper: #f2f5f6; --sheet: #ffffff; --ink: #12202a; --ink-2: #44545f; --ink-3: #6b7b86;
  --rule: #dce3e7; --track: #e8edf0; --accent: #2a5db0; --accent-soft: #e5edf9;
  --up: #17703f; --up-bg: #e2f2e8; --down: #b0362a; --down-bg: #fae6e3;
  --warn: #855a00; --warn-bg: #f9efd4;
  --f-display: "Archivo", "Arial Narrow", "Helvetica Neue", Arial, sans-serif;
  --f-body: "IBM Plex Sans", "Segoe UI", system-ui, -apple-system, sans-serif;
  --f-mono: "IBM Plex Mono", ui-monospace, "SFMono-Regular", Menlo, Consolas, monospace;
}
@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) {
  --paper: #0d1418; --sheet: #141d23; --ink: #e4ebf0; --ink-2: #a7b5bf; --ink-3: #7f8e99;
  --rule: #24323b; --track: #1f2b33; --accent: #7ea9ee; --accent-soft: #1a2a40;
  --up: #55c38a; --up-bg: #12291e; --down: #f08a78; --down-bg: #33181a;
  --warn: #e2b54a; --warn-bg: #2e2412; color-scheme: dark; } }
:root[data-theme="dark"] {
  --paper: #0d1418; --sheet: #141d23; --ink: #e4ebf0; --ink-2: #a7b5bf; --ink-3: #7f8e99;
  --rule: #24323b; --track: #1f2b33; --accent: #7ea9ee; --accent-soft: #1a2a40;
  --up: #55c38a; --up-bg: #12291e; --down: #f08a78; --down-bg: #33181a;
  --warn: #e2b54a; --warn-bg: #2e2412; color-scheme: dark; }

body { background: var(--paper); color: var(--ink); font: 14px/1.5 var(--f-body); }
.page { max-width: 1120px; margin: 0 auto; padding-block: 28px 56px;
  padding-left: max(16px, env(safe-area-inset-left, 0px));
  padding-right: max(16px, env(safe-area-inset-right, 0px));
  display: flex; flex-direction: column; gap: 28px; }

.top { display: flex; flex-wrap: wrap; align-items: end; justify-content: space-between; gap: 8px 24px;
  border-bottom: 2px solid var(--ink); padding-bottom: 12px; }
.stamp { font: 500 11px/1 var(--f-mono); letter-spacing: .1em; text-transform: uppercase; color: var(--ink-3); }
h1 { margin: 6px 0 0; font: 800 34px/1 var(--f-display); font-stretch: 75%; letter-spacing: -.01em; }
.asof { margin: 0; font: 12px/1.4 var(--f-mono); color: var(--ink-2); text-align: right; }
.verdict { margin: 0; font: 600 18px/1.4 var(--f-body); max-width: 70ch; text-wrap: balance; }

.summary { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
  background: var(--sheet); border: 1px solid var(--rule); border-radius: 6px; }
.summary .cell { padding: 14px 16px; border-right: 1px solid var(--rule); min-width: 0;
  display: flex; flex-direction: column; gap: 4px; }
.summary .cell:last-child { border-right: 0; }
.label { font: 500 11px/1.2 var(--f-mono); letter-spacing: .08em; text-transform: uppercase; color: var(--ink-3); }
.big { font: 700 28px/1.1 var(--f-display); font-stretch: 85%; font-variant-numeric: tabular-nums; }
.sub { font-size: 12px; color: var(--ink-2); }
.meter { position: relative; height: 8px; background: var(--track); border-radius: 4px; margin-top: 6px; }
.meter .fill { position: absolute; inset: 0 auto 0 0; background: var(--accent); border-radius: 4px; }
.meter .cap { position: absolute; top: -4px; bottom: -4px; width: 2px; background: var(--ink); }
.week ul { list-style: none; margin: 2px 0 0; padding: 0; display: flex; flex-direction: column; gap: 4px; }
.week li { display: flex; flex-wrap: wrap; gap: 2px 8px; font-size: 12.5px; align-items: baseline; }
.when { font: 500 11.5px var(--f-mono); color: var(--ink-2); min-width: 62px; }
.tag { font: 500 10px var(--f-mono); text-transform: uppercase; letter-spacing: .06em; color: var(--accent);
  background: var(--accent-soft); padding: 1px 5px; border-radius: 3px; }

.open ul { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 6px; }
.open li { display: flex; gap: 10px; align-items: baseline; padding: 8px 12px; border-radius: 4px;
  background: var(--sheet); border: 1px solid var(--rule); border-left: 3px solid var(--ink-3); font-size: 13px; }
.open li.critical { border-left-color: var(--down); }
.open li.warn { border-left-color: var(--warn); }
.sev { font: 600 10.5px var(--f-mono); text-transform: uppercase; letter-spacing: .06em; white-space: nowrap; color: var(--ink-2); }
.open li.critical .sev { color: var(--down); } .open li.warn .sev { color: var(--warn); }

.sechead { display: flex; flex-wrap: wrap; align-items: baseline; gap: 4px 16px; margin-bottom: 10px; }
h2 { margin: 0; font: 700 22px/1.1 var(--f-display); font-stretch: 80%; }
.sechead p { margin: 0; color: var(--ink-2); font-size: 12.5px; max-width: 75ch; }

.tablewrap { overflow-x: auto; background: var(--sheet); border: 1px solid var(--rule); border-radius: 6px; }
table { border-collapse: collapse; width: 100%; font-variant-numeric: tabular-nums; }
#book table { min-width: 760px; }
th, td { text-align: left; padding: 8px 12px; border-bottom: 1px solid var(--rule); vertical-align: top; }
thead th { font: 500 10.5px var(--f-mono); letter-spacing: .07em; text-transform: uppercase; color: var(--ink-3);
  background: var(--sheet); position: sticky; top: 0; }
tbody tr:hover { background: var(--accent-soft); }
tbody tr:last-child th, tbody tr:last-child td { border-bottom: 0; }
tfoot th, tfoot td { border-top: 2px solid var(--ink); border-bottom: 0; font-weight: 600; }
.r { text-align: right; }
.tk { font: 600 13px var(--f-mono); display: block; }
.nm { display: block; font-size: 11.5px; color: var(--ink-3); font-weight: 400; }
.num { font: 500 13px var(--f-mono); display: block; white-space: nowrap; }
.pct { display: block; font: 11.5px var(--f-mono); color: var(--ink-3); white-space: nowrap; }
td.w { width: 150px; }
.bar { display: block; height: 6px; background: var(--track); border-radius: 3px; margin-top: 6px; }
.bar span { display: block; height: 100%; background: var(--accent); border-radius: 3px; min-width: 2px; }
.chip { display: inline-flex; gap: 4px; align-items: center; font: 500 12px var(--f-mono); padding: 2px 6px;
  border-radius: 3px; white-space: nowrap; color: var(--ink); }
.chip i { font-style: normal; font-size: 9px; }
.chip.up { background: var(--up-bg); } .chip.up i { color: var(--up); }
.chip.down { background: var(--down-bg); } .chip.down i { color: var(--down); }
.chip.flat { color: var(--ink-3); }
.note { font-size: 12.5px; color: var(--ink-2); min-width: 220px; max-width: 360px; }
.ext { list-style: none; display: flex; flex-wrap: wrap; gap: 4px 16px; margin: 8px 0 0; padding: 0;
  font-size: 12px; color: var(--ink-3); }

.themes { display: grid; grid-template-columns: repeat(auto-fit, minmax(min(100%, 440px), 1fr)); gap: 14px; }
.theme { background: var(--sheet); border: 1px solid var(--rule); border-radius: 6px; padding: 16px;
  display: flex; flex-direction: column; gap: 10px; min-width: 0; }
.theme header { display: flex; justify-content: space-between; align-items: baseline; gap: 8px; }
h3 { margin: 0; font: 700 18px/1.15 var(--f-display); font-stretch: 85%; }
.own { font: 500 10.5px var(--f-mono); text-transform: uppercase; letter-spacing: .06em; color: var(--ink-3);
  border: 1px solid var(--rule); padding: 2px 6px; border-radius: 3px; white-space: nowrap; }
.own.yes { color: var(--accent); border-color: var(--accent); }
.headline { margin: 0; font-weight: 600; text-wrap: balance; }
.bullets { margin: 0; padding-left: 18px; display: flex; flex-direction: column; gap: 4px; color: var(--ink-2); font-size: 13px; }
.namewrap { overflow-x: auto; border-top: 1px solid var(--rule); }
.names th, .names td { padding: 6px 8px 6px 0; }
.names .tk { display: table-cell; width: 54px; }
.names .num { display: table-cell; }
.nn { font-size: 12px; color: var(--ink-2); }
.watch { margin: 0; font-size: 13px; padding: 8px 10px; background: var(--paper); border-radius: 4px; }
.watch span { font: 600 10.5px var(--f-mono); text-transform: uppercase; letter-spacing: .07em; color: var(--accent);
  margin-right: 8px; }

details { font-size: 12.5px; color: var(--ink-2); }
summary { cursor: pointer; font: 500 11px var(--f-mono); letter-spacing: .08em; text-transform: uppercase; color: var(--ink-3); }
summary:focus-visible, a:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
details ol { columns: 2 320px; padding-left: 20px; }
a { color: var(--accent); }
.method { max-width: 80ch; }

@media (max-width: 640px) {
  /* The book stacks per row on phones so the notes stay on screen. */
  #book table { min-width: 0; }
  #book thead { display: none; }
  #book tbody tr, #book tfoot tr { display: grid; grid-template-columns: minmax(0, 1fr) auto auto auto;
    column-gap: 10px; align-items: start; padding: 10px 12px; border-bottom: 1px solid var(--rule); }
  #book tfoot tr { border-top: 2px solid var(--ink); border-bottom: 0; }
  #book th, #book td { padding: 0; border: 0; }
  #book td.w, #book td:empty { display: none; }
  #book td.note { grid-column: 1 / -1; min-width: 0; max-width: none; padding-top: 6px; }
  .summary .cell { border-right: 0; border-bottom: 1px solid var(--rule); }
  .summary .cell:last-child { border-bottom: 0; }
  .asof { text-align: left; }
  h1 { font-size: 30px; }
}
"""

_FONTS = (
    '<link rel="preconnect" href="https://fonts.googleapis.com">'
    '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>'
    '<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Archivo:wdth,wght@75..100,600..800'
    '&family=IBM+Plex+Mono:wght@400;500;600&family=IBM+Plex+Sans:wght@400;500;600&display=swap">'
)


def render(portfolio: Portfolio, base_date, baseline: dict[str, float], alerts, data: dict) -> str:
    rows, invested = _rows(portfolio, baseline, data)
    by_ticker = {r["ticker"]: r for r in rows}
    themes = "".join(_theme_card(t, by_ticker) for t in data.get("themes", []))
    sources = "".join(
        f'<li><a href="{escape(s["url"])}" target="_blank" rel="noopener">{escape(s["label"])}</a></li>'
        for s in data.get("sources", [])
    )
    stamp = escape(data.get("stamp", ""))
    asof = escape(data.get("asof", "")).replace("\n", "<br>")
    verdict = escape(data.get("verdict", ""))
    method = escape(data.get("method", ""))

    return f"""<title>Pre-Open Book</title>
<meta name="claude:viewport-fit-sides" content="cover">
{_FONTS}
<style>{_CSS}</style>
<div class="page">
  <header class="top">
    <div><div class="stamp">{stamp}</div><h1>Pre-Open Book</h1></div>
    <p class="asof">{asof}</p>
  </header>
  <p class="verdict">{verdict}</p>
  {_summary(portfolio, rows, invested, base_date, data)}
  {_open_items(alerts, data.get("flags", []), data.get("alert_text", {}))}
  {_book(rows, invested, portfolio, base_date, data)}
  <section id="themes">
    <div class="sechead"><h2>Theme scouting</h2>
      <p>Your themes: what changed, names worth a look, and the next piece of evidence. Prices are the same close as above unless marked.</p></div>
    <div class="themes">{themes}</div>
  </section>
  <footer>
    <details><summary>Sources and method</summary>
      <p class="method">{method}</p>
      <ol>{sources}</ol>
    </details>
  </footer>
</div>
"""


def build(config: str | Path, data_path: str | Path, asof: dt.date | None = None) -> str:
    from . import alerts as alerts_mod

    portfolio = load_portfolio(config)
    base_date, baseline = load_baseline(config)
    watch = alerts_mod.load_watch(config)
    triggered = alerts_mod.evaluate(portfolio, watch, asof=asof)
    data = json.loads(Path(data_path).read_text())
    return render(portfolio, base_date, baseline, triggered, data)
