# Investment Research Agent

A deep-research agent for building and rebalancing an investment portfolio.

It has two halves that are deliberately kept apart:

- **Deterministic math** (`models.py`, `analysis.py`) — allocation, concentration,
  tax-lot selection, liquidity planning, rebalancing. No LLM involved. The
  numbers are reproducible and covered by tests.
- **Deep research** (`research.py`) — Claude Opus 5 with live web search and
  fetch, producing sourced reports on themes you name, then synthesizing them
  into a plan against your actual positions and constraints.

The split matters. Arithmetic should not be probabilistic, and the model should
not be asked to remember prices it cannot verify.

## Setup

```bash
pip install -r requirements.txt
export ANTHROPIC_API_KEY=sk-ant-...      # or run `ant auth login`
cp portfolio.example.yaml portfolio.yaml # then edit in your real positions
```

## Commands

```bash
python -m portfolio_agent.cli status      # allocation, concentration, drift
python -m portfolio_agent.cli liquidity   # how to fund upcoming cash needs
python -m portfolio_agent.cli rebalance   # trades to reach target weights
python -m portfolio_agent.cli research    # deep research + synthesized plan
```

`research` writes one markdown report per theme into `research/` plus a
`PLAN.md` synthesis. It runs at `xhigh` effort by default and will make dozens
of web searches; a full run takes several minutes and costs real tokens. Use
`--effort medium` for a cheaper pass, or `--themes quantum` to scope it.

## How the config works

`sleeve` groups positions for allocation targets, and doubles as the default
list of research themes. `targets` are weights on the **investable remainder**
— that is, total value minus anything reserved for a cash need due within 12
months. This is the one piece of opinionated behavior in the math, and it is
there because a near-term obligation is not risk capital.

`account` matters: only `taxable` positions are considered for funding cash
needs, and only taxable sales generate a tax estimate. Retirement accounts are
never proposed as a funding source.

`lots` are optional but worth entering. Without them the agent cannot prefer
loss lots when selling, and every tax estimate is zero — which will make sales
look cheaper than they are.

## Household context

`external_accounts` lists money that counts toward your risk picture but is
never traded by this tool — typically a 401k you are leaving alone. It changes
two things:

- `status` reports risk capital as a share of the *household*, not of the
  sub-account. A sleeve that looks aggressive at 30% of a small taxable account
  may be 4% of everything you own.
- The research agent is told about it and instructed to size against the
  household total.

Fill in `composition` honestly. If a large outside account is a target-date
fund, the managed account can reasonably carry concentrated bets. If it is
loaded with company stock or the same megacap tech that your themes track, the
managed account is doubling down rather than diversifying, and the correct
allocation is very different. Leaving it as `unknown` is flagged in the output
for exactly this reason.

## Things worth knowing before you trust the output

**Corporate status is re-verified, never remembered.** This tool was first
written asserting from training data that SpaceX was private and unbuyable. It
had IPO'd — NASDAQ:SPCX, June 12 2026 — weeks earlier. Every recommendation
built on that claim was worthless.

The lesson is encoded in the research prompt as rule zero: whether a company is
public, under what ticker, and whether it has been acquired, renamed, split, or
delisted must be checked by search at the moment of writing, alongside prices,
index membership, lockup schedules, and earnings dates. Reports open with the
UTC timestamp at which facts were verified, and unverifiable figures are marked
`UNVERIFIED` rather than filled in from memory. Any comment in this repo naming
a specific price, date, or corporate status is stale by the time you read it —
including the ones in `portfolio.example.yaml`.

**Tax estimates are approximations.** They apply a flat rate to realized gains
by lot. They do not model wash sales, net investment income tax, state tax,
AMT, or the interaction with your other income. Treat the output as a way to
compare two sale plans against each other, not as a number to put on a return.

**Prices are whatever you last typed into the config.** Nothing in this tool
fetches quotes. Stale prices produce confidently wrong allocations.

**Your holdings come only from your config.** The agent is instructed never to
infer, assume, or invent a position, and never to use the example file as a
stand-in for real holdings. `placeholder_data: true` in a config makes the tool
print a warning on every load; the example ships with it set. Delete it only
when the positions in the file are genuinely yours.

**The agent speculates on purpose.** It is built to make dated, probabilistic
predictions on two horizons — 12-24 months and 5-10 years — and to size bets on
them. What it will not do is blur the three kinds of claim: each is labeled
`FUNDAMENTAL` (from filings), `EXPECTATION` (what the market prices in), or
`SPECULATION` (a forecast with no confirming evidence yet). A speculation is a
legitimate basis for a position. An unlabeled one dressed as a fundamental is
not.

**The research agent searches the web and can still be wrong.** Verify anything
you are about to act on, particularly numbers that drive a large trade.

This is a research and modeling tool, not financial advice, and none of its
authors are your adviser.
