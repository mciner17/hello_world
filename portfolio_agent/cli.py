"""Command-line interface for the investment research agent."""

from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path

from . import analysis
from .models import Portfolio, load_portfolio

DEFAULT_CONFIG = "portfolio.yaml"


def _constraints_text(portfolio: Portfolio) -> str:
    lines = [
        f"- Risk tolerance: medium-to-high; the goal is faster growth than a "
        f"broad index, accepting larger drawdowns.",
        f"- Horizon for the growth portion: 12 months for evaluation, longer for holding.",
        f"- Marginal ordinary rate {portfolio.marginal_tax_rate:.0%}, "
        f"long-term capital gains rate {portfolio.ltcg_rate:.0%}.",
    ]
    for need in portfolio.cash_needs:
        plan = analysis.plan_liquidity(portfolio, need)
        lines.append(
            f"- HARD CONSTRAINT: ${need.amount:,.0f} must be available in cash by "
            f"{need.date.isoformat()} ({plan.months_away:.1f} months away) for "
            f"'{need.label}'. Currently ${plan.already_liquid:,.0f} is liquid."
        )

    if portfolio.external_accounts:
        view = analysis.household_view(portfolio)
        lines.append(
            f"- Household context: ${view['external_value']:,.0f} sits in accounts not "
            f"managed here and not being traded. The portfolio you are advising on is "
            f"${view['managed_value']:,.0f}, i.e. {view['managed_share']:.0%} of the "
            f"household. Size recommendations against the household, not the sub-account."
        )
        for account in portfolio.external_accounts:
            lines.append(
                f"  - {account.label}: ${account.value:,.0f}, holdings: {account.composition}"
            )
        lines.append(
            "  - If an outside account's composition is unknown or overlaps the themes "
            "below, say so explicitly and explain what it changes before recommending sizes."
        )
    return "\n".join(lines)


def cmd_status(args) -> int:
    portfolio = load_portfolio(args.config)
    print(analysis.summarize(portfolio))

    reserve = analysis.reserved_for_cash_needs(portfolio)
    if reserve:
        investable = portfolio.total_value - reserve
        print(
            f"\nReserved for near-term cash needs: ${reserve:,.0f}"
            f"\nInvestable for growth: ${investable:,.0f}"
        )

    if portfolio.external_accounts:
        view = analysis.household_view(portfolio)
        print("\nHousehold context (accounts not managed here):")
        for account in portfolio.external_accounts:
            print(
                f"  {account.label:<24} ${account.value:>12,.0f}   [{account.composition}]"
            )
        print(
            f"\n  Household total:      ${view['household_value']:,.0f}"
            f"\n  Managed here:         ${view['managed_value']:,.0f} "
            f"({view['managed_share']:.1%} of household)"
            f"\n  After near-term needs:${view['investable']:,.0f} "
            f"({view['investable_share_of_household']:.1%} of household)"
            "\n    -- this still includes cash and any concentrated holding;"
            "\n       it is not the amount free to put into new positions."
        )
        if any(a.composition == "unknown" for a in portfolio.external_accounts):
            print(
                "\n  NOTE: an outside account has composition 'unknown'. Its holdings"
                "\n  decide whether this portfolio is diversifying or doubling down."
            )
    return 0


def cmd_liquidity(args) -> int:
    portfolio = load_portfolio(args.config)
    if not portfolio.cash_needs:
        print("No cash needs configured. Add a `cash_needs:` entry to the config.")
        return 0

    for need in portfolio.cash_needs:
        plan = analysis.plan_liquidity(portfolio, need)
        print(f"\n=== ${need.amount:,.0f} for '{need.label}' on {need.date.isoformat()} ===")
        print(f"Months away:     {plan.months_away:.1f}")
        print(f"Already liquid:  ${plan.already_liquid:,.0f}")

        if plan.months_away <= analysis.DERISK_HORIZON_MONTHS:
            print(
                f"\nThis need is inside the {analysis.DERISK_HORIZON_MONTHS:.0f}-month "
                "de-risking horizon. It should be held in cash or T-bills, not equities."
            )

        if not plan.sales:
            print("\nNo sales required -- existing cash covers this need.")
        else:
            print("\nSuggested sales (loss lots first, then long-term gains):")
            for trade in plan.sales:
                print(
                    f"  SELL {trade.shares:>10,.2f} sh {trade.ticker:<8} "
                    f"= ${trade.dollars:>11,.0f}   est. tax ${trade.est_tax:>9,.0f}"
                )
            print(f"\nEstimated total tax on these sales: ${plan.est_total_tax:,.0f}")
            print(
                f"Gross to raise ${need.amount:,.0f} net of tax: "
                f"${need.amount + plan.est_total_tax:,.0f}"
            )

        if plan.shortfall > 0.01:
            print(f"\nWARNING: ${plan.shortfall:,.0f} of this need is still unfunded.")

    if any(p.account == "taxable" for p in portfolio.positions):
        print(
            "\nAt execution: set the sale to SPECIFIC LOT identification and choose lots"
            "\nby the basis the broker shows, not by age. Vest or purchase date does not"
            "\ntell you which lot is cheapest to sell -- that assumes the price only rose,"
            "\nand a stock below an earlier peak will have older lots sitting at a LOSS."
            "\nSell losses first (they offset gains elsewhere), then the smallest gains."
            "\nBroker defaults are usually FIFO, which ignores all of this."
        )
    return 0


def cmd_rebalance(args) -> int:
    portfolio = load_portfolio(args.config)
    trades = analysis.rebalance(portfolio)
    if not trades:
        print("No rebalancing trades needed (or no `targets:` set in the config).")
        return 0

    reserve = analysis.reserved_for_cash_needs(portfolio)
    print(
        f"Rebalancing ${portfolio.total_value - reserve:,.0f} investable "
        f"(${reserve:,.0f} reserved for near-term cash needs)\n"
    )
    for trade in trades:
        print(f"  {trade.action.upper():<5} ${trade.dollars:>11,.0f}  {trade.ticker:<8}")
        print(f"        {trade.rationale}")
    return 0


def cmd_research(args) -> int:
    from . import research  # imported lazily so offline commands need no SDK

    portfolio = load_portfolio(args.config)
    summary = analysis.summarize(portfolio)
    constraints = _constraints_text(portfolio)

    themes = args.themes or list(portfolio.targets.keys())
    themes = [t for t in themes if t not in ("cash", "core", "reserve")]
    if not themes:
        print("No themes to research. Pass --themes or set `targets:` in the config.")
        return 1

    outdir = Path(args.out)
    outdir.mkdir(parents=True, exist_ok=True)
    stamp = dt.date.today().isoformat()

    reports = []
    for theme in themes:
        print(f"[research] {theme} ...", file=sys.stderr, flush=True)
        result = research.run_research(
            research.theme_research_prompt(theme, summary, constraints),
            effort=args.effort,
        )
        if result.refused:
            print(f"[research] {theme}: request was declined; skipping.", file=sys.stderr)
            continue

        path = outdir / f"{stamp}-{theme.replace(' ', '-')}.md"
        path.write_text(result.markdown)
        print(
            f"[research] {theme}: {result.searches} searches, "
            f"{result.output_tokens:,} output tokens -> {path}",
            file=sys.stderr,
        )
        if result.stopped_early:
            print(f"[research] {theme}: hit the token ceiling; report may be partial.",
                  file=sys.stderr)
        reports.append(f"## Research: {theme}\n\n{result.markdown}")

    if not reports:
        print("No theme reports were produced; skipping synthesis.", file=sys.stderr)
        return 1

    if args.no_synthesis:
        return 0

    print("[research] synthesizing plan ...", file=sys.stderr, flush=True)
    plan = research.run_research(
        research.synthesis_prompt(summary, constraints, "\n\n---\n\n".join(reports)),
        effort=args.effort,
    )
    plan_path = outdir / f"{stamp}-PLAN.md"
    plan_path.write_text(plan.markdown)
    print(f"[research] plan -> {plan_path}", file=sys.stderr)
    print(plan.markdown)
    return 0


def cmd_watch(args) -> int:
    from . import alerts as alerts_mod

    portfolio = load_portfolio(args.config)
    watch = alerts_mod.load_watch(args.config)
    triggered = alerts_mod.evaluate(portfolio, watch)

    if not triggered:
        print("No alerts. Nothing in the watchlist is triggered today.")
        return 0

    print(f"{len(triggered)} alert(s):\n")
    for alert in triggered:
        print(f"  {alert}")

    critical = sum(1 for a in triggered if a.severity == "critical")
    if critical:
        print(f"\n{critical} critical. These need a decision, not a note.")
    return 0


def cmd_sources(args) -> int:
    from . import monitor

    sources = monitor.load_sources(args.sources)
    print(f"{len(sources)} sources, ordered by how much weight a claim deserves:")
    print(monitor.format_catalog(sources))
    print(
        "\nTiers are ranked by verifiability, not reputation. Auditable track"
        "\nrecords for market pundits mostly do not exist -- survivorship bias"
        "\nalone overstates median active fund alpha by ~0.60%/yr."
    )
    return 0


def cmd_brief(args) -> int:
    from . import alerts as alerts_mod
    from . import monitor, research

    portfolio = load_portfolio(args.config)
    sources = monitor.load_sources(args.sources)
    themes = [t for t in portfolio.targets if t not in ("cash",)]

    watch = alerts_mod.load_watch(args.config)
    triggered = alerts_mod.evaluate(portfolio, watch)
    if triggered:
        print("Deterministic alerts before searching:", file=sys.stderr)
        for alert in triggered:
            print(f"  {alert}", file=sys.stderr)

    prompt = monitor.brief_prompt(
        checkpoint=args.checkpoint,
        portfolio_summary=analysis.summarize(portfolio),
        constraints=_constraints_text(portfolio),
        sources=sources,
        focus=monitor.theme_focus(args.sources),
        themes=themes,
        watchlist=alerts_mod.watchlist_prompt_section(portfolio, triggered),
    )

    result = research.run_research(prompt, effort=args.effort, max_tokens=8000)
    if result.refused:
        print("Brief request was declined.", file=sys.stderr)
        return 1

    stamp = dt.datetime.now(dt.timezone.utc)
    outdir = Path(args.out)
    outdir.mkdir(parents=True, exist_ok=True)
    path = outdir / f"{stamp:%Y-%m-%d}-{args.checkpoint}.md"
    path.write_text(f"# {args.checkpoint} brief -- {stamp:%Y-%m-%d %H:%M UTC}\n\n{result.markdown}")

    print(result.markdown)
    print(f"\n[saved to {path}; {result.searches} searches]", file=sys.stderr)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="portfolio-agent",
        description="Deep-research agent for building and rebalancing an investment portfolio.",
    )
    parser.add_argument(
        "-c", "--config", default=DEFAULT_CONFIG, help=f"portfolio YAML (default: {DEFAULT_CONFIG})"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("status", help="show current allocation and concentration").set_defaults(
        func=cmd_status
    )
    sub.add_parser("liquidity", help="plan for upcoming cash needs").set_defaults(
        func=cmd_liquidity
    )
    sub.add_parser("rebalance", help="trades to reach target allocation").set_defaults(
        func=cmd_rebalance
    )

    research_parser = sub.add_parser(
        "research", help="run deep research on themes and synthesize a plan"
    )
    research_parser.add_argument(
        "--themes", nargs="*", help="themes to research (default: sleeves from targets)"
    )
    research_parser.add_argument("--out", default="research", help="output directory")
    research_parser.add_argument(
        "--effort",
        default="xhigh",
        choices=["low", "medium", "high", "xhigh", "max"],
        help="reasoning effort (default: xhigh)",
    )
    research_parser.add_argument(
        "--no-synthesis", action="store_true", help="write theme reports only"
    )
    research_parser.set_defaults(func=cmd_research)

    sub.add_parser("watch", help="evaluate event-driven watch rules").set_defaults(
        func=cmd_watch
    )

    sources_parser = sub.add_parser("sources", help="print the source catalog with biases")
    sources_parser.add_argument("--sources", default="sources.yaml")
    sources_parser.set_defaults(func=cmd_sources)

    brief_parser = sub.add_parser("brief", help="scheduled market check against the catalog")
    brief_parser.add_argument(
        "checkpoint", choices=["preopen", "midday", "preclose"], help="which scheduled window"
    )
    brief_parser.add_argument("--sources", default="sources.yaml")
    brief_parser.add_argument("--out", default="briefs", help="output directory")
    brief_parser.add_argument(
        "--effort", default="medium", choices=["low", "medium", "high", "xhigh", "max"]
    )
    brief_parser.set_defaults(func=cmd_brief)

    args = parser.parse_args(argv)
    if args.command == "sources":
        return args.func(args)
    if not Path(args.config).exists():
        parser.error(
            f"config not found: {args.config}\n"
            "Copy portfolio.example.yaml to portfolio.yaml and fill in your positions."
        )
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
