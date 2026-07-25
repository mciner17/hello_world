"""Scheduled market checks against the source catalog."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from pathlib import Path

import yaml

# Windows are in US Eastern, the timezone the exchanges actually run on.
CHECKPOINTS = {
    "preopen": "Pre-open (~08:30 ET). Overnight news, futures, pre-market movers, "
    "anything that repriced while the US market was closed.",
    "midday": "Mid-day (~12:30 ET). What the tape is actually doing, whether the "
    "morning narrative held, intraday reversals.",
    "preclose": "Pre-close (~15:30 ET). Positioning into the close, late-day "
    "reversals, after-hours earnings due tonight.",
}


@dataclass
class Source:
    name: str
    tier: str
    weight: float
    url: str = ""
    why: str = ""
    bias: str = ""
    covers: str = ""


def load_sources(path: str | Path = "sources.yaml") -> list[Source]:
    raw = yaml.safe_load(Path(path).read_text())
    out: list[Source] = []
    for tier_name, tier in raw.get("tiers", {}).items():
        for entry in tier.get("sources", []):
            out.append(
                Source(
                    name=entry["name"],
                    tier=tier_name,
                    weight=float(tier.get("weight", 0.5)),
                    url=entry.get("url", ""),
                    why=entry.get("why", "").strip(),
                    bias=entry.get("bias", "").strip(),
                    covers=entry.get("covers", ""),
                )
            )
    return out


def theme_focus(path: str | Path = "sources.yaml") -> dict[str, list[str]]:
    raw = yaml.safe_load(Path(path).read_text())
    return raw.get("theme_focus", {})


def format_catalog(sources: list[Source]) -> str:
    lines = []
    current = None
    for source in sorted(sources, key=lambda s: (-s.weight, s.tier, s.name)):
        if source.tier != current:
            current = source.tier
            lines.append(f"\n## {current.upper()}  (weight {source.weight})")
        lines.append(f"\n  {source.name}")
        if source.url:
            lines.append(f"    {source.url}")
        if source.why:
            lines.append(f"    why:  {' '.join(source.why.split())}")
        if source.bias:
            lines.append(f"    bias: {' '.join(source.bias.split())}")
    return "\n".join(lines)


def brief_prompt(
    checkpoint: str,
    portfolio_summary: str,
    constraints: str,
    sources: list[Source],
    focus: dict[str, list[str]],
    themes: list[str],
) -> str:
    now = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    window = CHECKPOINTS.get(checkpoint, checkpoint)

    relevant = sorted({s for theme in themes for s in focus.get(theme, [])})
    catalog = format_catalog([s for s in sources if not relevant or s.name in relevant] or sources)

    return f"""\
The current date and time is **{now}**. This is a scheduled market check.

**Checkpoint:** {window}

Verify everything against live sources right now. Do not report any price,
status, or figure from memory -- this brief exists specifically to be current,
so a stale number in it is worse than no brief at all.

Portfolio data supplied by the investor (the only thing you know about their
holdings -- do not assume anything beyond it):

```
{portfolio_summary}
```

Constraints:
{constraints}

Themes being tracked: {', '.join(themes)}

## Source catalog

Weight claims by tier. Primary-tier sources can be stated as fact; everything
else needs corroboration before it drives a decision. Each source's documented
bias is listed -- apply it. A bullish supply-scarcity call from a source with a
known scarcity tilt is weaker evidence than the same call from a filing.
{catalog}

## Deliver, briefly

Keep this short. A scheduled brief that takes ten minutes to read will not be
read. Aim for under 400 words unless something genuinely material happened.

1. **Moves that matter** -- only positions and themes in the portfolio above.
   Price, magnitude, and the verified reason. Skip anything under ~2% without a
   news catalyst.
2. **New information** -- filings, guidance changes, insider transactions,
   unlock tranches, index events, policy. Label each FUNDAMENTAL, EXPECTATION,
   or SPECULATION and name the source.
3. **Thesis impact** -- does anything today change a held thesis? If nothing
   does, say "no thesis change" and stop. Most checks should end here.
4. **Action required** -- only if something is genuinely time-sensitive.
   Otherwise write "none". Do not manufacture urgency; a brief that always
   recommends a trade is a brief that will lose money.
5. **Cash-need watch** -- if a near-term cash need exists, flag anything
   affecting the assets that would fund it, including supply overhangs or
   catalysts landing near that date.

If nothing material happened, a three-line brief is the correct output.
"""
