"""Deep-research engine backed by the Claude API with live web search."""

from __future__ import annotations

import datetime as dt
import os
from dataclasses import dataclass

import anthropic

MODEL = "claude-opus-5"

# Server-side tools. The _20260209 variants do dynamic filtering internally --
# do NOT also declare code_execution, which would create a second execution
# environment and confuse the model.
TOOLS = [
    {"type": "web_search_20260209", "name": "web_search", "max_uses": 40},
    {"type": "web_fetch_20260209", "name": "web_fetch", "max_uses": 20},
]

SYSTEM_PROMPT = """\
You are an investment research analyst working for a single retail investor who \
is deliberately making speculative, thesis-driven bets. Your job is to make the \
best possible predictions about the future and size bets on them. You are not a \
registered investment adviser and the reader knows it; do not pad output with \
disclaimers beyond a single line at the end.

## Rule zero: nothing from memory

Your training data is stale and you do not know how stale. Every fact that can \
change MUST be re-verified by search before you write it down, at the time you \
write it. This includes, and is not limited to:

- Whether a company is public or private, and under what ticker.
- Prices, market caps, multiples, and 52-week ranges.
- Whether a company has IPO'd, been acquired, merged, split, renamed, delisted,
  or moved exchanges.
- Index membership, lockup schedules, and share-count changes.
- Earnings dates, guidance, and the most recent reported quarter.
- Management, policy, subsidy, and regulatory status.

Stating a stale fact confidently is the worst failure mode available to you. It \
is far worse than saying "I could not verify this." A single wrong fact about \
corporate status invalidates every recommendation built on top of it.

Every report must open with the UTC timestamp at which you verified its facts, \
and every figure must carry the date you observed it. If a number is more than \
a few days old, say so. If you cannot verify something, write "UNVERIFIED" next \
to it rather than filling the gap from memory.

Prefer primary sources: SEC filings (S-1, 10-K, 10-Q, 8-K), earnings call \
transcripts, investor-relations pages, exchange notices, regulator publications. \
Use financial press for what the market already expects, not for ground truth.

## Rule one: the portfolio is only what you were given

You know nothing about the investor's holdings beyond the portfolio data \
supplied in the prompt. Never infer, assume, or invent a position, a position \
size, a cost basis, or an account balance. If a recommendation depends on \
whether they hold something and the supplied data does not say, state the \
dependency and ask -- do not guess and do not use an example or a typical \
portfolio as a stand-in. Phrases like "your largest position" are forbidden \
unless the supplied data actually shows it.

## Speculation is the point

The investor wants calculated bets on things that have not happened yet. Do not \
talk them out of speculating, do not retreat to index funds as a default \
answer, and do not treat "this is speculative" as a reason to avoid a position. \
It is a reason to size it deliberately and define what would falsify it.

What you owe them instead of caution is *discipline about which kind of claim \
you are making*. Label every claim as one of:

- FUNDAMENTAL -- established from filings and reported numbers.
- EXPECTATION -- what the market currently prices in; show how you know.
- SPECULATION -- your forecast, with no current evidence to confirm it yet.

A speculation is a legitimate basis for a position. An unlabeled speculation \
dressed as a fundamental is not.

## Make actual predictions

Vague directional talk is useless. For every thesis, commit to:

- A specific, checkable claim with a date attached.
- A probability you would defend, and the base rate you started from.
- What the position is worth if you are right, and if you are wrong.
- The single piece of evidence that would most change your mind, and when it
  arrives.

Cover two horizons separately, because they imply different positions:

- **12-24 months**: earnings trajectory, product cycles, rate and policy path,
  supply and demand imbalances, catalysts with dates.
- **5-10 years**: structural change -- technology S-curves, capex cycles,
  regulatory regime shifts, who captures the value in a build-out and who just
  finances it.

Say explicitly when a name is attractive on one horizon and not the other. That \
is a common and important answer.

## Mechanical factors that swamp narrative

Check these before recommending anything, especially recently-listed names:

- Lockup and unlock schedules, tranche by tranche, with dates and share counts.
  Post-IPO supply can overwhelm good fundamentals for months.
- Dilution: at-the-market programs, convertibles, secondary offerings, and
  share-count growth over the last several quarters.
- Index inclusion or deletion, and forced flows around it.
- Short interest, days-to-cover, and options positioning.
- Cash runway against burn for anything unprofitable.

For each candidate also give: what it earns money from today in one sentence, \
valuation versus its own history and its peers, concrete dated catalysts, and \
the bear case argued as though you believed it.

## Two things that override conviction

1. Money needed within 12 months does not belong in equities. A near-term cash
   need is the first claim on the portfolio, before any new position. Note when
   a supply overhang or catalyst lands near the date cash must be raised.
2. Every recommendation includes a size. "Buy X" without a dollar or percentage
   figure is an incomplete answer.

Write clear prose with markdown structure. Lead with the answer, then evidence. \
Assume a numerate reader who wants a real opinion, not a hedge.
"""


@dataclass
class ResearchResult:
    markdown: str
    searches: int
    input_tokens: int
    output_tokens: int
    stopped_early: bool = False
    refused: bool = False


def _client() -> anthropic.Anthropic:
    if not os.environ.get("ANTHROPIC_API_KEY") and not os.environ.get("ANTHROPIC_AUTH_TOKEN"):
        raise SystemExit(
            "No Anthropic credentials found.\n"
            "Set ANTHROPIC_API_KEY, or run `ant auth login` to store a profile."
        )
    return anthropic.Anthropic()


def _count_searches(content) -> int:
    return sum(
        1
        for block in content
        if getattr(block, "type", "") == "server_tool_use"
        and getattr(block, "name", "") == "web_search"
    )


def _extract_text(content) -> str:
    return "\n".join(b.text for b in content if getattr(b, "type", "") == "text")


def run_research(
    prompt: str,
    *,
    effort: str = "xhigh",
    max_tokens: int = 32000,
    max_continuations: int = 6,
) -> ResearchResult:
    """Run one deep-research pass, resuming through server-tool pauses.

    A long server-tool turn stops with `stop_reason == "pause_turn"` when the
    server-side loop hits its iteration limit. Resuming means echoing the
    paused assistant turn back with no extra user message -- the API detects
    the trailing server_tool_use block and continues where it left off.
    """
    client = _client()
    messages = [{"role": "user", "content": prompt}]

    text_parts: list[str] = []
    searches = 0
    input_tokens = 0
    output_tokens = 0

    for attempt in range(max_continuations + 1):
        with client.beta.messages.stream(
            model=MODEL,
            max_tokens=max_tokens,
            system=[
                {
                    "type": "text",
                    "text": SYSTEM_PROMPT,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            thinking={"type": "adaptive", "display": "summarized"},
            output_config={"effort": effort},
            tools=TOOLS,
            messages=messages,
            # Opus 5 safety classifiers can decline a request outright; the
            # fallback re-serves it on Anthropic's recommended model instead
            # of handing us an empty response.
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        ) as stream:
            response = stream.get_final_message()

        searches += _count_searches(response.content)
        input_tokens += response.usage.input_tokens
        output_tokens += response.usage.output_tokens

        if response.stop_reason == "refusal":
            return ResearchResult(
                markdown=_extract_text(response.content),
                searches=searches,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                refused=True,
            )

        chunk = _extract_text(response.content)
        if chunk:
            text_parts.append(chunk)

        if response.stop_reason != "pause_turn":
            return ResearchResult(
                markdown="\n\n".join(text_parts),
                searches=searches,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                stopped_early=(response.stop_reason == "max_tokens"),
            )

        # Paused mid-turn: echo the assistant turn back to resume it.
        messages.append({"role": "assistant", "content": response.content})

    return ResearchResult(
        markdown="\n\n".join(text_parts),
        searches=searches,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        stopped_early=True,
    )


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def theme_research_prompt(theme: str, portfolio_summary: str, constraints: str) -> str:
    return f"""\
The current date and time is **{_now()}**. Verify every fact against sources no
older than that, and open your report with the timestamp at which you checked.

Research the **{theme}** theme as an investment opportunity.

Portfolio data supplied by the investor (this is the only thing you know about
their holdings -- do not assume anything beyond it):

```
{portfolio_summary}
```

Constraints and context:
{constraints}

Deliver:

1. **State of the theme today.** What is actually shipping and generating
   revenue versus what is still a research program. Be specific about the gap
   and how it has moved in the last two quarters.

2. **Five to eight investable names**, spanning established and profitable
   through pure speculation. For each: ticker (verify it is current and the
   company is still independently listed), what it earns money from today,
   the thesis, the falsifier, valuation with your observation date, dilution
   and share-count trend, any lockup or unlock schedule, and the bear case.

3. **Two separate verdicts per name**: one for 12-24 months, one for 5-10
   years. Say when these disagree and which one you would trade.

4. **Predictions.** Three to five specific, dated, checkable claims about this
   theme, each with a probability you would defend and the evidence that would
   settle it. These are the bets -- make them real.

5. **Cycle position.** Early, consensus, or crowded? Show evidence: multiple
   expansion, insider selling, issuance, short interest, fund flows.

6. **Correlation with the supplied portfolio.** If this theme is largely the
   same bet the investor already owns in a different wrapper, say so.

7. **Allocation range** as a percentage of the investable portion, with the
   reasoning for both floor and ceiling.

Search extensively before answering. Cite sources with dates.
"""


def synthesis_prompt(portfolio_summary: str, constraints: str, theme_reports: str) -> str:
    return f"""\
The current date and time is **{_now()}**. Re-verify any price or status you are
about to act on -- the theme reports below may be minutes or days old.

You have completed research on several themes. Now build the actual plan.

Portfolio data supplied by the investor (the only thing you know about their
holdings -- do not assume anything beyond it):

```
{portfolio_summary}
```

Constraints:
{constraints}

Your theme research:

{theme_reports}

---

Produce a decision document:

1. **Liquidity first.** Address the near-term cash need before anything else.
   Name exactly what to sell, when, and what the tax consequence is. If the
   answer is "sell nothing, it is already in cash," say that.

2. **Target allocation.** A specific percentage per sleeve, summing to 100% of
   the investable remainder, split into a lower-volatility base and the
   speculative sleeve. Justify each number. Estimate the drawdown in a 2022-like
   market and state it in dollars, not percentages.

3. **The bets.** For each speculative position: the prediction, the probability,
   the size, what it returns if right, what it costs if wrong, and the date by
   which you will know. Separate the 12-24 month bets from the 5-10 year ones --
   they are different positions and may be different instruments.

4. **The trade list.** Ordered, with dollar amounts. For each trade: what, how
   much, why, and what would make you not do it.

5. **Sequencing.** What to execute now versus what to stage, and why. Name any
   supply overhang, unlock date, earnings date, or policy event that argues for
   waiting -- and any that argues for moving before it. If a catalyst lands near
   the date cash must be raised, flag the collision explicitly.

6. **What would make this plan wrong.** The three most likely ways this
   underperforms a plain index fund over the next year, and the leading
   indicator for each.

7. **Review triggers.** Specific, checkable conditions that should prompt a
   revisit -- price levels, earnings dates, unlock tranches, policy events.

Be direct about tradeoffs. Where you recommend concentration, say what is being
given up. Where the investor's stated preference conflicts with evidence you
found, say so plainly rather than quietly optimizing around it.
"""
