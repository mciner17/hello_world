"""Portfolio data model and config loading."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass
class Lot:
    """A tax lot within a position."""

    shares: float
    cost_basis_per_share: float
    acquired: dt.date

    def is_long_term(self, asof: dt.date) -> bool:
        return (asof - self.acquired).days > 365

    def gain_per_share(self, price: float) -> float:
        return price - self.cost_basis_per_share


@dataclass
class Position:
    ticker: str
    shares: float
    price: float
    account: str = "taxable"
    sleeve: str = "unassigned"
    lots: list[Lot] = field(default_factory=list)

    @property
    def value(self) -> float:
        return self.shares * self.price

    @property
    def cost_basis(self) -> float:
        if not self.lots:
            return 0.0
        return sum(lot.shares * lot.cost_basis_per_share for lot in self.lots)

    @property
    def unrealized_gain(self) -> float:
        if not self.lots:
            return 0.0
        return self.value - self.cost_basis

    def lots_covering(self, shares: float) -> list[Lot]:
        """Lots to sell for `shares`, preferring losses, then long-term gains.

        Ordering rationale: harvest losses first (they offset gains), then
        long-term gains (taxed at preferential rates), then short-term gains
        (taxed as ordinary income) last.
        """

        def sort_key(lot: Lot) -> tuple[int, float]:
            gain = lot.gain_per_share(self.price)
            if gain < 0:
                return (0, gain)  # biggest loss first
            if lot.is_long_term(dt.date.today()):
                return (1, gain)
            return (2, gain)

        remaining = shares
        chosen: list[Lot] = []
        for lot in sorted(self.lots, key=sort_key):
            if remaining <= 0:
                break
            take = min(lot.shares, remaining)
            chosen.append(Lot(take, lot.cost_basis_per_share, lot.acquired))
            remaining -= take
        return chosen


@dataclass
class CashNeed:
    """A known future liquidity requirement."""

    amount: float
    date: dt.date
    label: str = "planned withdrawal"

    def months_away(self, asof: dt.date | None = None) -> float:
        asof = asof or dt.date.today()
        return (self.date - asof).days / 30.44


@dataclass
class Portfolio:
    positions: list[Position]
    cash: float = 0.0
    cash_needs: list[CashNeed] = field(default_factory=list)
    targets: dict[str, float] = field(default_factory=dict)
    marginal_tax_rate: float = 0.24
    ltcg_rate: float = 0.15

    @property
    def invested_value(self) -> float:
        return sum(p.value for p in self.positions)

    @property
    def total_value(self) -> float:
        return self.invested_value + self.cash

    def by_sleeve(self) -> dict[str, float]:
        out: dict[str, float] = {}
        for p in self.positions:
            out[p.sleeve] = out.get(p.sleeve, 0.0) + p.value
        if self.cash:
            out["cash"] = out.get("cash", 0.0) + self.cash
        return out

    def position(self, ticker: str) -> Position | None:
        return next((p for p in self.positions if p.ticker.upper() == ticker.upper()), None)


def _parse_date(value) -> dt.date:
    if isinstance(value, dt.date):
        return value
    return dt.date.fromisoformat(str(value))


def load_portfolio(path: str | Path) -> Portfolio:
    """Load a portfolio from a YAML config file."""
    raw = yaml.safe_load(Path(path).read_text())

    positions = []
    for entry in raw.get("positions", []):
        lots = [
            Lot(
                shares=float(lot["shares"]),
                cost_basis_per_share=float(lot["cost_basis_per_share"]),
                acquired=_parse_date(lot["acquired"]),
            )
            for lot in entry.get("lots", [])
        ]
        positions.append(
            Position(
                ticker=entry["ticker"].upper(),
                shares=float(entry["shares"]),
                price=float(entry["price"]),
                account=entry.get("account", "taxable"),
                sleeve=entry.get("sleeve", "unassigned"),
                lots=lots,
            )
        )

    cash_needs = [
        CashNeed(
            amount=float(n["amount"]),
            date=_parse_date(n["date"]),
            label=n.get("label", "planned withdrawal"),
        )
        for n in raw.get("cash_needs", [])
    ]

    return Portfolio(
        positions=positions,
        cash=float(raw.get("cash", 0.0)),
        cash_needs=cash_needs,
        targets=dict(raw.get("targets", {})),
        marginal_tax_rate=float(raw.get("marginal_tax_rate", 0.24)),
        ltcg_rate=float(raw.get("ltcg_rate", 0.15)),
    )
