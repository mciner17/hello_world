"""Deterministic portfolio math: concentration, liquidity, rebalancing.

Nothing in this module calls an LLM. It is the arithmetic the research agent
reasons *about*, kept separate so the numbers are reproducible and testable.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from .models import CashNeed, Portfolio, Position

# A cash need inside this horizon should not be funded from equities.
DERISK_HORIZON_MONTHS = 12.0


@dataclass
class Trade:
    ticker: str
    action: str  # "buy" | "sell"
    dollars: float
    shares: float
    rationale: str
    est_tax: float = 0.0


@dataclass
class LiquidityPlan:
    need: CashNeed
    months_away: float
    already_liquid: float
    shortfall: float
    sales: list[Trade]
    est_total_tax: float

    @property
    def is_funded(self) -> bool:
        return self.shortfall <= 0.01


def concentration(portfolio: Portfolio) -> dict:
    """Herfindahl index and top-position stats over the invested sleeve."""
    total = portfolio.invested_value
    if total <= 0:
        return {"hhi": 0.0, "top_weight": 0.0, "top_ticker": None, "effective_positions": 0.0}

    weights = sorted(
        ((p.ticker, p.value / total) for p in portfolio.positions),
        key=lambda kv: kv[1],
        reverse=True,
    )
    hhi = sum(w * w for _, w in weights)
    return {
        "hhi": hhi,
        "top_ticker": weights[0][0],
        "top_weight": weights[0][1],
        # 1/HHI is the "effective number of independent positions".
        "effective_positions": 1.0 / hhi if hhi else 0.0,
        "weights": weights,
    }


def allocation(portfolio: Portfolio) -> dict[str, float]:
    """Current sleeve weights as fractions of total value (cash included)."""
    total = portfolio.total_value
    if total <= 0:
        return {}
    return {sleeve: value / total for sleeve, value in portfolio.by_sleeve().items()}


def _estimate_tax(position: Position, shares: float, portfolio: Portfolio) -> float:
    """Approximate tax owed on selling `shares` of `position`."""
    if position.account != "taxable" or not position.lots:
        return 0.0
    tax = 0.0
    today = dt.date.today()
    for lot in position.lots_covering(shares):
        gain = lot.shares * lot.gain_per_share(position.price)
        rate = portfolio.ltcg_rate if lot.is_long_term(today) else portfolio.marginal_tax_rate
        tax += gain * rate
    return tax


def plan_liquidity(portfolio: Portfolio, need: CashNeed) -> LiquidityPlan:
    """Decide what to sell to cover a near-term cash need.

    Sells are drawn from taxable accounts only, cheapest-tax-first, and skip
    positions the target allocation wants to *grow*. Retirement accounts are
    never touched -- withdrawing early triggers penalties this model does not
    attempt to price.
    """
    months = need.months_away()
    liquid = portfolio.cash
    shortfall = max(0.0, need.amount - liquid)

    sales: list[Trade] = []
    if shortfall > 0:
        candidates = [p for p in portfolio.positions if p.account == "taxable" and p.value > 0]
        # Prefer selling what we are overweight in, and what costs least in tax.
        targets = portfolio.targets
        total = portfolio.total_value

        def cost_rank(p: Position) -> tuple[float, float]:
            target_w = targets.get(p.sleeve, 0.0)
            current_w = p.value / total if total else 0.0
            overweight = current_w - target_w
            tax_per_dollar = (
                _estimate_tax(p, p.shares, portfolio) / p.value if p.value else 0.0
            )
            # Most overweight first; ties broken by lowest tax drag.
            return (-overweight, tax_per_dollar)

        remaining = shortfall
        for p in sorted(candidates, key=cost_rank):
            if remaining <= 0.01:
                break
            take_dollars = min(p.value, remaining)
            take_shares = take_dollars / p.price if p.price else 0.0
            tax = _estimate_tax(p, take_shares, portfolio)
            sales.append(
                Trade(
                    ticker=p.ticker,
                    action="sell",
                    dollars=take_dollars,
                    shares=take_shares,
                    est_tax=tax,
                    rationale=(
                        f"fund '{need.label}' due {need.date.isoformat()} "
                        f"({months:.1f} months out)"
                    ),
                )
            )
            remaining -= take_dollars
        shortfall = remaining

    return LiquidityPlan(
        need=need,
        months_away=months,
        already_liquid=liquid,
        shortfall=shortfall,
        sales=sales,
        est_total_tax=sum(s.est_tax for s in sales),
    )


def reserved_for_cash_needs(portfolio: Portfolio) -> float:
    """Total cash that must be carved out before anything is invested for growth."""
    return sum(
        n.amount for n in portfolio.cash_needs if n.months_away() <= DERISK_HORIZON_MONTHS
    )


def rebalance(portfolio: Portfolio) -> list[Trade]:
    """Trades to move the *investable* remainder toward target sleeve weights.

    The near-term cash need is subtracted from total value first, so growth
    targets are computed on money that can actually stay invested.
    """
    if not portfolio.targets:
        return []

    reserve = reserved_for_cash_needs(portfolio)
    investable = max(0.0, portfolio.total_value - reserve)

    current_by_sleeve = portfolio.by_sleeve()
    # The reserve is drawn from cash first, so only cash beyond the reserve
    # counts toward the investable cash sleeve. Dropping the key entirely
    # would understate cash whenever the investor holds more than the need.
    current_by_sleeve["cash"] = max(0.0, portfolio.cash - reserve)

    trades: list[Trade] = []
    for sleeve, target_weight in sorted(portfolio.targets.items()):
        target_dollars = investable * target_weight
        current_dollars = current_by_sleeve.get(sleeve, 0.0)
        delta = target_dollars - current_dollars
        if abs(delta) < max(250.0, investable * 0.005):
            continue  # inside the no-trade band; not worth the friction

        holdings = [p for p in portfolio.positions if p.sleeve == sleeve]
        anchor = max(holdings, key=lambda p: p.value).ticker if holdings else f"<{sleeve}>"
        trades.append(
            Trade(
                ticker=anchor,
                action="buy" if delta > 0 else "sell",
                dollars=abs(delta),
                shares=0.0,
                rationale=(
                    f"sleeve '{sleeve}': "
                    f"{current_dollars / investable:.1%} -> {target_weight:.1%} of investable"
                ),
            )
        )
    return trades


def household_view(portfolio: Portfolio) -> dict:
    """Risk capital as a share of everything, including untouched accounts.

    The managed portfolio's risk budget is a household question. A 5% position
    in a small taxable account can be a rounding error at household level --
    which is an argument for taking *more* risk there, not less, provided the
    outside account is genuinely diversified.
    """
    household = portfolio.household_value
    reserve = reserved_for_cash_needs(portfolio)
    investable = max(0.0, portfolio.total_value - reserve)

    return {
        "household_value": household,
        "managed_value": portfolio.total_value,
        "external_value": portfolio.external_value,
        "managed_share": portfolio.total_value / household if household else 0.0,
        "investable": investable,
        "investable_share_of_household": investable / household if household else 0.0,
    }


def summarize(portfolio: Portfolio) -> str:
    """Human-readable snapshot; also fed to the research agent as context."""
    total = portfolio.total_value
    lines = [f"Total portfolio value: ${total:,.0f} (cash ${portfolio.cash:,.0f})", ""]

    invested = portfolio.invested_value
    lines.append("Positions (weights as % of invested assets, excluding cash):")
    for p in sorted(portfolio.positions, key=lambda x: x.value, reverse=True):
        weight = p.value / invested if invested else 0.0
        gain = f", unrealized ${p.unrealized_gain:,.0f}" if p.lots else ""
        lines.append(
            f"  {p.ticker:<8} ${p.value:>12,.0f}  {weight:>6.1%}  "
            f"[{p.sleeve}/{p.account}]{gain}"
        )

    conc = concentration(portfolio)
    lines += [
        "",
        f"Concentration: top position {conc['top_ticker']} at {conc['top_weight']:.1%} "
        f"of invested assets; effective positions {conc['effective_positions']:.1f} "
        f"(HHI {conc['hhi']:.3f})",
        "",
        "Sleeve allocation (% of total value, including cash):",
    ]
    for sleeve, weight in sorted(allocation(portfolio).items(), key=lambda kv: -kv[1]):
        target = portfolio.targets.get(sleeve)
        target_str = f"  (target {target:.1%})" if target is not None else ""
        lines.append(f"  {sleeve:<16} {weight:>6.1%}{target_str}")

    if portfolio.cash_needs:
        lines.append("")
        lines.append("Cash needs:")
        for need in portfolio.cash_needs:
            plan = plan_liquidity(portfolio, need)
            status = "covered by cash" if plan.is_funded and not plan.sales else "requires sales"
            lines.append(
                f"  ${need.amount:,.0f} on {need.date.isoformat()} "
                f"({plan.months_away:.1f} months) - {status}"
            )
    return "\n".join(lines)
