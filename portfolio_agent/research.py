"""Deep-research engine backed by the Claude API with live web search."""

from __future__ import annotations

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
You are an investment research analyst working for a single retail investor. \
You produce rigorous, source-backed research on public markets. You are not a \
registered investment adviser, and the person reading this knows that; do not \
pad your output with disclaimers beyond a single line at the end.

## How you work

Search the web before making any factual claim about prices, valuations, \
earnings, guidance, product timelines, or policy. Your training data is stale \
by definition; treat every number you remember as a hypothesis to verify. \
Cite the source and the date for every figure. If you cannot verify something, \
say so explicitly rather than reporting a remembered number as fact.

Prefer primary sources: SEC filings (10-K, 10-Q, 8-K), earnings call \
transcripts, company investor-relations pages, central bank and regulator \
publications. Use financial press for context and for what the market already \
expects, not as a source of truth for numbers.

## What good output looks like

For each investment candidate you cover:
- What the business actually earns money from today, in one sentence.
- The specific thesis: what has to be true for this to work.
- The falsifier: what would tell the investor the thesis is wrong, and by when.
- Valuation in context: current multiple vs. its own history and vs. peers.
- Concrete catalysts in the next 12 months, with dates where known.
- The bear case, argued as if you believed it.

Distinguish sharply between:
- Company fundamentals (revenue, margins, backlog, guidance).
- Narrative and positioning (what is priced in, sentiment, flows).
- Speculation (what could happen with no current evidence).

Label speculation as speculation. A thematic story that is years from revenue \
is a speculation, not a fundamental, regardless of how compelling it is.

## Rules that override enthusiasm

1. Money needed within 12 months does not belong in equities. If the investor \
   has a near-term cash need, funding it is the first claim on the portfolio, \
   before any new position.
2. Position sizing is part of the recommendation. "Buy X" without a size is an \
   incomplete answer. Size to what the investor can lose without changing plans.
3. A pre-revenue or pre-profit theme is sized as a speculation even when the \
   thesis is strong. Conviction is not a substitute for diversification.
4. When an asset is not directly purchasable (a private company, for example), \
   say so plainly and describe the actual available vehicles, including their \
   specific costs: expense ratios, premium or discount to NAV, lockups, \
   accreditation requirements, and how much of the exposure is really the \
   asset in question versus other holdings.
5. Never claim an entry point is uniquely good "right now" unless you can show \
   evidence for it. Timing claims require the same sourcing as any other claim.

Write in clear prose with markdown structure. Lead with the answer, then the \
evidence. Assume a numerate reader who does not want to be flattered.
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


def theme_research_prompt(theme: str, portfolio_summary: str, constraints: str) -> str:
    return f"""\
Research the **{theme}** theme as an investment opportunity for the next 12 months.

Here is the investor's current portfolio:

```
{portfolio_summary}
```

Constraints and context:
{constraints}

Deliver:
1. State of the theme today -- what is actually shipping and generating revenue
   versus what is still a research program. Be specific about the gap.
2. Four to six investable names, spanning the risk range from established
   profitable companies to pure speculations. For each: ticker, what it earns
   money from, the thesis, the falsifier, current valuation with the date you
   observed it, and the bear case.
3. Where the theme sits in the cycle. Is this early, consensus, or crowded?
   Show the evidence -- multiple expansion, insider selling, retail flows,
   short interest, recent issuance.
4. How this theme correlates with what the investor already owns. If it is
   largely the same bet in a different wrapper, say so.
5. A recommended allocation range as a percentage of the growth portion of the
   portfolio, with the reasoning for both the floor and the ceiling.

Search extensively before answering. Cite sources with dates.
"""


def synthesis_prompt(portfolio_summary: str, constraints: str, theme_reports: str) -> str:
    return f"""\
You have completed research on several themes. Now build the actual plan.

Current portfolio:

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
   the investable remainder. Justify each number. Explain what risk level this
   actually represents -- estimate the drawdown in a market like 2022 and say
   it in dollars, not percentages.

3. **The trade list.** Ordered, with dollar amounts. For each trade: what,
   how much, why, and what would make you not do it.

4. **Sequencing.** What to execute this week versus what to stage over months,
   and the reasoning. If you recommend staging, say what evidence would make
   you accelerate or abandon it.

5. **What would make this plan wrong.** The three most likely ways this
   underperforms a plain index fund over the next year, and the leading
   indicator for each.

6. **Review triggers.** Specific, checkable conditions that should prompt a
   revisit -- price levels, earnings dates, policy events.

Be direct about tradeoffs. Where you are recommending concentration, say what
is being given up. Where the investor's stated preference conflicts with the
evidence you found, say so plainly and explain the conflict rather than
quietly optimizing around it.
"""
