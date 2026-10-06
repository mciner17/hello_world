"""Tests for the deterministic portfolio math."""

from __future__ import annotations

import datetime as dt

import pytest

from portfolio_agent import analysis
from portfolio_agent.models import CashNeed, Lot, Portfolio, Position

TODAY = dt.date.today()


def make_portfolio(**overrides) -> Portfolio:
    positions = [
        Position(
            ticker="VOO",
            shares=100,
            price=500.0,
            sleeve="core",
            lots=[Lot(100, 400.0, TODAY - dt.timedelta(days=800))],
        ),
        Position(
            ticker="TSLA",
            shares=100,
            price=300.0,
            sleeve="tesla",
            lots=[Lot(100, 350.0, TODAY - dt.timedelta(days=100))],
        ),
    ]
    kwargs = {
        "positions": positions,
        "cash": 10_000.0,
        "targets": {"core": 0.7, "tesla": 0.3},
        "marginal_tax_rate": 0.30,
        "ltcg_rate": 0.15,
    }
    kwargs.update(overrides)
    return Portfolio(**kwargs)


def test_total_value_includes_cash():
    p = make_portfolio()
    assert p.invested_value == pytest.approx(80_000)
    assert p.total_value == pytest.approx(90_000)


def test_unrealized_gain_signs():
    p = make_portfolio()
    assert p.position("VOO").unrealized_gain == pytest.approx(10_000)
    assert p.position("TSLA").unrealized_gain == pytest.approx(-5_000)


def test_concentration_identifies_largest_position():
    conc = analysis.concentration(make_portfolio())
    assert conc["top_ticker"] == "VOO"
    assert conc["top_weight"] == pytest.approx(50_000 / 80_000)
    # HHI = 0.625^2 + 0.375^2 = 0.53125, so these two positions behave like
    # ~1.88 equally-weighted ones rather than a true 2.
    assert conc["hhi"] == pytest.approx(0.53125)
    assert conc["effective_positions"] == pytest.approx(1.88, abs=0.01)


def test_loss_lots_are_sold_before_gain_lots():
    """Selling the losing lot first is the point -- it defers tax."""
    position = Position(
        ticker="X",
        shares=200,
        price=100.0,
        lots=[
            Lot(100, 50.0, TODAY - dt.timedelta(days=800)),   # long-term gain
            Lot(100, 150.0, TODAY - dt.timedelta(days=30)),   # short-term loss
        ],
    )
    chosen = position.lots_covering(100)
    assert len(chosen) == 1
    assert chosen[0].cost_basis_per_share == 150.0  # the loss lot


def test_cash_covers_need_without_sales():
    p = make_portfolio(cash=30_000.0)
    need = CashNeed(25_000, TODAY + dt.timedelta(days=80), "October")
    plan = analysis.plan_liquidity(p, need)
    assert plan.sales == []
    assert plan.is_funded


def test_shortfall_triggers_sales_and_estimates_tax():
    p = make_portfolio(cash=5_000.0)
    need = CashNeed(25_000, TODAY + dt.timedelta(days=80), "October")
    plan = analysis.plan_liquidity(p, need)

    assert plan.is_funded
    assert sum(s.dollars for s in plan.sales) == pytest.approx(20_000)
    # TSLA is held at a loss, so selling it should not create a tax bill.
    assert plan.est_total_tax <= 0


def test_retirement_accounts_are_never_sold_for_cash_needs():
    p = make_portfolio(
        positions=[
            Position("VTI", 100, 300.0, account="ira", sleeve="core"),
            Position("VOO", 10, 500.0, account="taxable", sleeve="core"),
        ],
        cash=0.0,
    )
    plan = analysis.plan_liquidity(p, CashNeed(25_000, TODAY + dt.timedelta(days=80)))
    assert all(s.ticker != "VTI" for s in plan.sales)
    # Taxable holdings alone cannot cover it; the gap must be reported, not hidden.
    assert plan.shortfall > 0


def test_near_term_need_is_excluded_from_investable_base():
    """A cash need inside the horizon must not be allocated to growth sleeves."""
    p = make_portfolio(cash_needs=[CashNeed(25_000, TODAY + dt.timedelta(days=80))])
    assert analysis.reserved_for_cash_needs(p) == pytest.approx(25_000)

    trades = analysis.rebalance(p)
    # Investable is 90k - 25k = 65k, so the core target is 0.7 * 65k = 45.5k,
    # not 0.7 * 90k = 63k.
    core = next(t for t in trades if t.ticker == "VOO")
    assert core.action == "sell"
    assert core.dollars == pytest.approx(50_000 - 45_500)


def test_distant_need_is_not_reserved():
    p = make_portfolio(cash_needs=[CashNeed(25_000, TODAY + dt.timedelta(days=800))])
    assert analysis.reserved_for_cash_needs(p) == pytest.approx(0)


def test_rebalance_respects_no_trade_band():
    """Trivial drift should not generate churn."""
    p = make_portfolio(cash=0.0, targets={"core": 0.625, "tesla": 0.375})
    assert analysis.rebalance(p) == []


def test_summarize_runs_without_lots():
    p = make_portfolio(positions=[Position("VOO", 10, 500.0, sleeve="core")])
    assert "VOO" in analysis.summarize(p)


def test_cash_beyond_the_reserve_still_counts_as_investable():
    """Reserving for a cash need must not delete the surplus cash sleeve."""
    p = make_portfolio(
        cash=40_000.0,
        targets={"core": 0.6, "tesla": 0.2, "cash": 0.2},
        cash_needs=[CashNeed(25_000, TODAY + dt.timedelta(days=80))],
    )
    # Total 120k, reserve 25k, investable 95k. Cash sleeve currently holds
    # 40k - 25k = 15k, and targets 0.2 * 95k = 19k -- a small top-up, not a
    # from-zero purchase of the full 19k.
    trades = analysis.rebalance(p)
    cash_trade = next(t for t in trades if t.ticker == "<cash>")
    assert cash_trade.action == "buy"
    assert cash_trade.dollars == pytest.approx(19_000 - 15_000)


def test_household_view_sizes_risk_against_outside_accounts():
    """A big untouched 401k reframes what the managed account's risk means."""
    from portfolio_agent.models import ExternalAccount

    p = make_portfolio(
        cash=3_000.0,
        cash_needs=[CashNeed(25_000, TODAY + dt.timedelta(days=80))],
        external_accounts=[ExternalAccount("401k", 400_000.0, "target-date fund")],
    )
    view = analysis.household_view(p)

    assert view["household_value"] == pytest.approx(483_000)
    assert view["managed_value"] == pytest.approx(83_000)
    # Risk capital is 83k - 25k reserved = 58k, which is ~12% of the household
    # even though it is 70% of the account being managed.
    assert view["investable"] == pytest.approx(58_000)
    assert view["investable_share_of_household"] == pytest.approx(0.120, abs=0.001)


def test_household_view_is_inert_without_external_accounts():
    view = analysis.household_view(make_portfolio())
    assert view["external_value"] == 0
    assert view["managed_share"] == pytest.approx(1.0)


def test_placeholder_config_warns_loudly(tmp_path, capsys):
    """Placeholder holdings must never pass silently as real ones."""
    from portfolio_agent.models import load_portfolio

    config = tmp_path / "example.yaml"
    config.write_text(
        "placeholder_data: true\n"
        "cash: 1000\n"
        "positions:\n"
        "  - {ticker: VOO, shares: 1, price: 500.0, sleeve: core}\n"
    )
    load_portfolio(config)
    assert "PLACEHOLDER DATA" in capsys.readouterr().err


def test_real_config_does_not_warn(tmp_path, capsys):
    from portfolio_agent.models import load_portfolio

    config = tmp_path / "portfolio.yaml"
    config.write_text(
        "cash: 1000\n"
        "positions:\n"
        "  - {ticker: VOO, shares: 1, price: 500.0, sleeve: core}\n"
    )
    load_portfolio(config)
    assert capsys.readouterr().err == ""


def test_source_catalog_loads_with_bias_recorded():
    """Every catalogued source must declare a bias -- silence is not neutrality."""
    from portfolio_agent import monitor

    sources = monitor.load_sources("sources.yaml")
    assert len(sources) >= 15
    assert all(s.bias for s in sources), [s.name for s in sources if not s.bias]
    assert all(s.why for s in sources)

    primary = [s for s in sources if s.tier == "primary"]
    assert primary and all(s.weight == 1.0 for s in primary)


def test_theme_focus_only_references_real_sources():
    from portfolio_agent import monitor

    names = {s.name for s in monitor.load_sources("sources.yaml")}
    for theme, listed in monitor.theme_focus("sources.yaml").items():
        unknown = set(listed) - names
        assert not unknown, f"{theme} references unknown sources: {unknown}"


def test_brief_prompt_is_timestamped_and_scoped():
    from portfolio_agent import monitor

    sources = monitor.load_sources("sources.yaml")
    prompt = monitor.brief_prompt(
        checkpoint="preopen",
        portfolio_summary="<PORTFOLIO>",
        constraints="<CONSTRAINTS>",
        sources=sources,
        focus=monitor.theme_focus("sources.yaml"),
        themes=["semiconductor"],
    )
    assert "UTC" in prompt
    assert "Pre-open" in prompt
    assert "<PORTFOLIO>" in prompt
    # Focus should narrow the catalog, not dump all 17 sources every time.
    assert "Stratechery" not in prompt
    assert "SemiAnalysis" in prompt


def _watch_config(tmp_path, body: str):
    from portfolio_agent.alerts import load_watch

    path = tmp_path / "w.yaml"
    path.write_text(body)
    return load_watch(path)


def test_calendar_event_fires_only_inside_lead_window(tmp_path):
    from portfolio_agent import alerts

    p = make_portfolio(positions=[Position("SPCX", 100, 115.0, sleeve="spacex")])
    watch = _watch_config(
        tmp_path,
        "watch:\n"
        "  lead_days: [30, 7]\n"
        "  calendar:\n"
        "    - date: 2026-08-06\n"
        "      label: unlock\n"
        "      tickers: [SPCX]\n",
    )
    # 60 days out: silent. 20 days out: fires.
    far = alerts.evaluate(p, watch, asof=dt.date(2026, 6, 7))
    near = alerts.evaluate(p, watch, asof=dt.date(2026, 7, 17))
    assert not [a for a in far if a.kind == "calendar"]
    assert [a for a in near if a.kind == "calendar"]


def test_calendar_event_near_cash_need_escalates_to_collision(tmp_path):
    """An unlock landing beside the date cash must be raised is the whole point."""
    from portfolio_agent import alerts

    p = make_portfolio(
        positions=[Position("SPCX", 100, 115.0, sleeve="spacex")],
        cash_needs=[CashNeed(25_000, dt.date(2026, 10, 15), "October")],
    )
    watch = _watch_config(
        tmp_path,
        "watch:\n"
        "  calendar:\n"
        "    - date: 2026-09-20\n"
        "      label: unlock tranche\n"
        "      tickers: [SPCX]\n"
        "      severity: critical\n",
    )
    fired = alerts.evaluate(p, watch, asof=dt.date(2026, 9, 1))
    collisions = [a for a in fired if a.kind == "collision"]
    assert collisions
    assert collisions[0].severity == "critical"
    assert "do not plan to raise that cash" in collisions[0].message


def test_untickered_calendar_event_does_not_collide_with_itself(tmp_path):
    """A calendar entry marking the cash need is not a catalyst threatening it.

    "Do not raise that cash from this asset" only means something when the event
    names an asset. Without the guard, a reminder for the October need collides
    with the October need at zero days and buries the real alerts.
    """
    from portfolio_agent import alerts

    p = make_portfolio(
        positions=[Position("GEN", 100, 30.0, sleeve="employer_stock")],
        cash_needs=[CashNeed(25_000, dt.date(2026, 10, 15), "October")],
    )
    watch = _watch_config(
        tmp_path,
        "watch:\n"
        "  calendar:\n"
        "    - date: 2026-10-15\n"
        "      label: October cash requirement due\n"
        "      severity: critical\n",
    )
    fired = alerts.evaluate(p, watch, asof=dt.date(2026, 9, 15))

    assert not [a for a in fired if a.kind == "collision"]
    # The plain calendar reminder still fires.
    assert [a for a in fired if a.kind == "calendar"]


def test_stale_prices_escalate_by_age(tmp_path):
    """The engine has no price feed, so silence about age is the real hazard.

    A GEN position sat in the config at $30.65 for eleven days while the real
    stock fell 28% on an acquisition approach. No move_pct rule could fire,
    because the rule compares config prices to config prices. The only defense
    is to say out loud how old the file is.
    """
    from portfolio_agent import alerts

    watch = _watch_config(tmp_path, "watch:\n  default_move_pct: 5.0\n")
    asof = dt.date(2026, 10, 20)

    fresh = make_portfolio(prices_as_of=dt.date(2026, 10, 19))
    warn = make_portfolio(prices_as_of=dt.date(2026, 10, 14))
    crit = make_portfolio(prices_as_of=dt.date(2026, 10, 5))

    def staleness(p):
        return [a for a in alerts.evaluate(p, watch, asof=asof) if a.kind == "stale_prices"]

    assert not staleness(fresh)
    assert [a.severity for a in staleness(warn)] == ["warn"]

    critical = staleness(crit)
    assert [a.severity for a in critical] == ["critical"]
    assert "15d old" in critical[0].message


def test_missing_prices_as_of_is_critical(tmp_path):
    """Unknown vintage is worse than a known-old one -- it cannot be reasoned about."""
    from portfolio_agent import alerts

    watch = _watch_config(tmp_path, "watch:\n  default_move_pct: 5.0\n")
    p = make_portfolio(prices_as_of=None)

    fired = [a for a in alerts.evaluate(p, watch) if a.kind == "stale_prices"]
    assert [a.severity for a in fired] == ["critical"]
    assert "no 'prices_as_of'" in fired[0].message


def test_calendar_ignores_tickers_not_held(tmp_path):
    from portfolio_agent import alerts

    p = make_portfolio(positions=[Position("VOO", 10, 500.0, sleeve="core")])
    watch = _watch_config(
        tmp_path,
        "watch:\n"
        "  calendar:\n"
        "    - date: 2026-08-06\n"
        "      label: unlock\n"
        "      tickers: [SPCX]\n",
    )
    # Scoped to calendar alerts: an unrelated staleness alert fires here too,
    # because make_portfolio sets no prices_as_of.
    fired = alerts.evaluate(p, watch, asof=dt.date(2026, 8, 1))
    assert not [a for a in fired if a.kind == "calendar"]


def test_concentration_cap_flags_oversized_position(tmp_path):
    from portfolio_agent import alerts

    p = make_portfolio(cash=0.0)  # VOO 62.5%, TSLA 37.5%
    watch = _watch_config(tmp_path, "watch:\n  max_position_weight: 0.25\n")
    flagged = {a.ticker for a in alerts.evaluate(p, watch) if a.kind == "concentration"}
    assert flagged == {"VOO", "TSLA"}


def test_unfunded_near_term_need_is_critical(tmp_path):
    from portfolio_agent import alerts

    p = make_portfolio(
        cash=5_000.0,
        cash_needs=[CashNeed(25_000, TODAY + dt.timedelta(days=60), "October")],
    )
    watch = _watch_config(tmp_path, "watch: {}\n")
    liquidity = [a for a in alerts.evaluate(p, watch) if a.kind == "liquidity"]
    assert liquidity and liquidity[0].severity == "critical"
    assert "$20,000" in liquidity[0].message


def test_watchlist_section_refuses_to_invent_holdings():
    from portfolio_agent import alerts

    empty = Portfolio(positions=[], cash=0.0)
    section = alerts.watchlist_prompt_section(empty, [])
    assert "Do not invent" in section
