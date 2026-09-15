"""Event-driven watch rules.

Deliberately deterministic. Knowing that a lockup tranche lands on a given date,
or that a position has grown past its cap, requires a calendar and arithmetic --
not a language model. The scheduled brief spends its search budget on what
actually needs looking up, and this module handles what can simply be known.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .analysis import concentration
from .models import Portfolio

SEVERITY_ORDER = {"critical": 0, "warn": 1, "info": 2}

# A dated event this close to a cash-need date is escalated: it can move the
# price of the very asset being sold to raise the cash.
COLLISION_WINDOW_DAYS = 45


@dataclass
class CalendarEvent:
    date: dt.date
    label: str
    tickers: list[str] = field(default_factory=list)
    severity: str = "warn"

    def days_away(self, asof: dt.date) -> int:
        return (self.date - asof).days


@dataclass
class WatchRule:
    ticker: str
    move_pct: float | None = None
    below: float | None = None
    above: float | None = None
    max_weight: float | None = None


@dataclass
class Alert:
    severity: str
    kind: str
    ticker: str
    message: str

    def __str__(self) -> str:
        tag = self.severity.upper()
        return f"[{tag:<8}] {self.kind:<13} {self.ticker:<8} {self.message}"


@dataclass
class WatchConfig:
    default_move_pct: float = 5.0
    lead_days: list[int] = field(default_factory=lambda: [30, 14, 7, 1])
    max_position_weight: float | None = None
    calendar: list[CalendarEvent] = field(default_factory=list)
    rules: list[WatchRule] = field(default_factory=list)

    def rule_for(self, ticker: str) -> WatchRule | None:
        return next((r for r in self.rules if r.ticker.upper() == ticker.upper()), None)


def load_watch(path: str | Path) -> WatchConfig:
    raw = yaml.safe_load(Path(path).read_text()) or {}
    watch = raw.get("watch") or {}

    calendar = []
    for entry in watch.get("calendar", []):
        date = entry["date"]
        calendar.append(
            CalendarEvent(
                date=date if isinstance(date, dt.date) else dt.date.fromisoformat(str(date)),
                label=entry["label"],
                tickers=[t.upper() for t in entry.get("tickers", [])],
                severity=entry.get("severity", "warn"),
            )
        )

    rules = [
        WatchRule(
            ticker=entry["ticker"].upper(),
            move_pct=entry.get("move_pct"),
            below=entry.get("below"),
            above=entry.get("above"),
            max_weight=entry.get("max_weight"),
        )
        for entry in watch.get("rules", [])
    ]

    return WatchConfig(
        default_move_pct=float(watch.get("default_move_pct", 5.0)),
        lead_days=list(watch.get("lead_days", [30, 14, 7, 1])),
        max_position_weight=watch.get("max_position_weight"),
        calendar=calendar,
        rules=rules,
    )


def _nearest_lead(days: int, lead_days: list[int]) -> int | None:
    """The lead threshold this event has just crossed, if any."""
    crossed = [d for d in sorted(lead_days) if days <= d]
    return crossed[0] if crossed else None


def evaluate(
    portfolio: Portfolio, watch: WatchConfig, asof: dt.date | None = None
) -> list[Alert]:
    """Everything worth flagging today, most severe first."""
    asof = asof or dt.date.today()
    alerts: list[Alert] = []
    held = {p.ticker.upper() for p in portfolio.positions}

    # --- Dated events on held positions ------------------------------------
    for event in watch.calendar:
        days = event.days_away(asof)
        if days < 0:
            continue
        relevant = [t for t in event.tickers if t in held] or (
            [] if event.tickers else ["<portfolio>"]
        )
        if not relevant:
            continue
        lead = _nearest_lead(days, watch.lead_days)
        if lead is None:
            continue

        for ticker in relevant:
            alerts.append(
                Alert(
                    severity=event.severity,
                    kind="calendar",
                    ticker=ticker,
                    message=f"{event.label} in {days}d ({event.date.isoformat()})",
                )
            )

        # Does this land near money that has to be raised? Only asset-specific
        # events can collide: the warning is "do not raise cash from THIS asset",
        # which is meaningless for a portfolio-level marker. Without this guard a
        # calendar entry for the cash need itself collides with itself at 0 days.
        if not event.tickers:
            continue

        for need in portfolio.cash_needs:
            gap = abs((event.date - need.date).days)
            if gap <= COLLISION_WINDOW_DAYS and event.severity in ("warn", "critical"):
                alerts.append(
                    Alert(
                        severity="critical",
                        kind="collision",
                        ticker=relevant[0],
                        message=(
                            f"{event.label} ({event.date.isoformat()}) lands {gap}d from "
                            f"'{need.label}' (${need.amount:,.0f} due "
                            f"{need.date.isoformat()}) -- do not plan to raise that cash "
                            f"from this asset"
                        ),
                    )
                )

    # --- Position-level rules ----------------------------------------------
    invested = portfolio.invested_value
    conc = concentration(portfolio)

    for position in portfolio.positions:
        rule = watch.rule_for(position.ticker)
        weight = position.value / invested if invested else 0.0

        cap = (rule.max_weight if rule and rule.max_weight else watch.max_position_weight)
        if cap and weight > cap:
            alerts.append(
                Alert(
                    severity="warn",
                    kind="concentration",
                    ticker=position.ticker,
                    message=f"{weight:.1%} of invested assets, above the {cap:.0%} cap",
                )
            )

        if rule:
            # Prices come from the config and are only as fresh as the last
            # edit, so these are prompts to check, not confirmed crossings.
            if rule.below is not None and position.price <= rule.below:
                alerts.append(
                    Alert(
                        severity="warn",
                        kind="level",
                        ticker=position.ticker,
                        message=(
                            f"config price ${position.price:,.2f} at or below "
                            f"${rule.below:,.2f} -- verify live"
                        ),
                    )
                )
            if rule.above is not None and position.price >= rule.above:
                alerts.append(
                    Alert(
                        severity="info",
                        kind="level",
                        ticker=position.ticker,
                        message=(
                            f"config price ${position.price:,.2f} at or above "
                            f"${rule.above:,.2f} -- verify live"
                        ),
                    )
                )

    if conc.get("top_weight", 0) and watch.max_position_weight:
        top = conc["top_ticker"]
        if conc["top_weight"] > watch.max_position_weight and not any(
            a.kind == "concentration" and a.ticker == top for a in alerts
        ):
            alerts.append(
                Alert(
                    severity="warn",
                    kind="concentration",
                    ticker=top,
                    message=f"largest position at {conc['top_weight']:.1%} of invested assets",
                )
            )

    # --- Unfunded near-term cash needs --------------------------------------
    for need in portfolio.cash_needs:
        months = need.months_away(asof)
        if 0 <= months <= 12 and portfolio.cash < need.amount:
            gap = need.amount - portfolio.cash
            alerts.append(
                Alert(
                    severity="critical" if months <= 3 else "warn",
                    kind="liquidity",
                    ticker="<cash>",
                    message=(
                        f"${gap:,.0f} of '{need.label}' still unfunded, "
                        f"{months:.1f} months out"
                    ),
                )
            )

    alerts.sort(key=lambda a: (SEVERITY_ORDER.get(a.severity, 3), a.kind, a.ticker))
    return alerts


def watchlist_prompt_section(portfolio: Portfolio, alerts: list[Alert]) -> str:
    """What the scheduled brief should actively go look up."""
    tickers = sorted({p.ticker for p in portfolio.positions})
    if not tickers:
        return (
            "No holdings supplied. Do not invent any. Report theme-level news only "
            "and state that position-level alerts are unavailable."
        )

    lines = [
        f"Held tickers to check individually: {', '.join(tickers)}",
        "",
        "For each, verify live: price and % move, any 8-K or Form 4 filed in the last "
        "24h, analyst actions, and whether a scheduled event (earnings, unlock tranche, "
        "index change) is imminent.",
    ]
    if alerts:
        lines += ["", "Deterministic alerts already triggered (confirm and expand):"]
        lines += [f"  {a}" for a in alerts]
    return "\n".join(lines)
