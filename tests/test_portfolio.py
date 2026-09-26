"""组合级情景重估：汇总与逐笔严格一致、失败隔离、版本绑定/优先级、上限。"""
import pytest

from app.config import Settings
from app.engine import PricingEngine
from app.errors import ErrorCode, PricingError
from app.market_registry import MarketRegistry
from app.models import (MarketPrecedence, MarketSnapshot, OptionType,
                        PortfolioScenarioRequest, PricingMethod, Shock)
from app.portfolio import resolve_market, run_portfolio_scenarios
from app.pricing.black_scholes import bs_price
from tests.conftest import make_contract


GREEK_KEYS = ("delta", "gamma", "vega", "theta", "rho")


@pytest.fixture()
def registry():
    reg = MarketRegistry()
    reg.publish(MarketSnapshot(
        snapshot_id="v1", spot=105.0, risk_free_rate=0.03,
        dividend_yield=0.01, volatility=0.25))
    reg.publish(MarketSnapshot(
        snapshot_id="novol", spot=105.0, risk_free_rate=0.03))
    return reg


def _req(contracts, shocks, **kw):
    base = dict(name="p", contracts=contracts, shocks=shocks,
                method=PricingMethod.ANALYTIC)
    base.update(kw)
    return PortfolioScenarioRequest(**base)


def test_portfolio_totals_match_leg_sum(engine, registry):
    contracts = [make_contract(contract_id=f"c{i}", strike=90 + i * 4,
                               quantity=1.0 + 0.5 * i)
                 for i in range(6)]
    shocks = [Shock(spot_pct=-0.1), Shock(spot_pct=0.05, vol_shift=0.03),
             Shock(rate_shift=0.01)]
    resp = run_portfolio_scenarios(engine, registry,
                                   _req(contracts, shocks))
    assert resp.failed == 0
    for s in resp.shocks:
        good = [l for l in s.legs if l.ok]
        assert s.succeeded == len(good)
        assert abs(s.total_pnl - sum(l.pnl for l in good)) < 1e-9
        assert abs(s.total_value - sum(l.shocked_price for l in good)) < 1e-9
        assert abs(s.total_base_value - sum(l.base_price for l in good)) < 1e-9
        # 组合敏感度 == 逐笔加权敏感度之和
        for key in GREEK_KEYS:
            assert abs(getattr(s.greeks, key)
                       - sum(getattr(l.greeks, key) for l in good)) < 1e-12
        # 单腿 PnL 自身一致
        for l in good:
            assert abs(l.pnl - (l.shocked_price - l.base_price)) < 1e-12


def test_greeks_weighted_by_quantity(engine, registry):
    c1 = make_contract(contract_id="a", quantity=2.0)
    c2 = make_contract(contract_id="b", strike=110, quantity=-3.0)
    resp = run_portfolio_scenarios(engine, registry,
                                   _req([c1, c2], [Shock()]))
    s = resp.shocks[0]
    la, lb = s.legs
    for key in GREEK_KEYS:
        assert abs(getattr(s.greeks, key)
                   - (getattr(la.greeks, key) + getattr(lb.greeks, key))) < 1e-12
    # 直接与独立定价的加权希腊字母核对
    r1 = engine.price_one(c1, PricingMethod.ANALYTIC).greeks
    assert abs(la.greeks.delta - r1.delta * 2.0) < 1e-12
    r2 = engine.price_one(c2, PricingMethod.ANALYTIC).greeks
    assert abs(lb.greeks.delta - r2.delta * -3.0) < 1e-12


def test_base_failure_isolated_and_excluded_from_totals(engine, registry):
    good = make_contract(contract_id="good")
    bad = make_contract(contract_id="bad", volatility=-0.3)
    resp = run_portfolio_scenarios(engine, registry,
                                   _req([good, bad], [Shock(spot_pct=0.1)]))
    assert (resp.succeeded, resp.failed) == (1, 1)
    s = resp.shocks[0]
    assert (s.succeeded, s.failed) == (1, 1)
    assert s.legs[0].ok
    assert not s.legs[1].ok
    assert s.legs[1].error.code == "VALIDATION"
    # 汇总只含成功腿
    assert abs(s.total_value - s.legs[0].shocked_price) < 1e-12
    assert abs(s.total_pnl - s.legs[0].pnl) < 1e-12


def test_shock_induced_failure_isolated_per_shock(engine, registry):
    # 基准合法；其中一个冲击把波动率打到负值 → 仅该冲击下该腿失败
    edge = make_contract(contract_id="edge", volatility=0.01)
    other = make_contract(contract_id="other", strike=105)
    resp = run_portfolio_scenarios(
        engine, registry,
        _req([edge, other], [Shock(vol_shift=-0.02), Shock(spot_pct=0.05)]))
    s_bad, s_good = resp.shocks
    assert (s_bad.succeeded, s_bad.failed) == (1, 1)
    assert not s_bad.legs[0].ok and s_bad.legs[0].error.code == "VALIDATION"
    assert s_bad.legs[1].ok
    assert abs(s_bad.total_pnl - s_bad.legs[1].pnl) < 1e-12
    assert s_good.succeeded == 2
    assert abs(s_good.total_pnl - sum(l.pnl for l in s_good.legs)) < 1e-12


def test_snapshot_precedence_overrides_contract(engine, registry):
    c = make_contract(contract_id="a", spot=100.0, strike=100.0,
                      volatility=0.2, risk_free_rate=0.05,
                      dividend_yield=0.0)
    req = _req([c], [Shock()], market_version="v1",
               precedence=MarketPrecedence.SNAPSHOT)
    resp = run_portfolio_scenarios(engine, registry, req)
    leg = resp.shocks[0].legs[0]
    assert leg.ok and leg.market_version == "v1"
    # 用版本行情（spot=105,r=3%,q=1%,vol=25%）独立重算
    resolved = make_contract(contract_id="a", spot=105.0, strike=100.0,
                             volatility=0.25, risk_free_rate=0.03,
                             dividend_yield=0.01)
    assert abs(leg.shocked_price - bs_price(resolved)) < 1e-10


def test_contract_precedence_keeps_contract_values_but_marks_version(
        engine, registry):
    c = make_contract(contract_id="a", spot=100.0, volatility=0.2,
                      risk_free_rate=0.05)
    req = _req([c], [Shock()], market_version="v1",
               precedence=MarketPrecedence.CONTRACT)
    resp = run_portfolio_scenarios(engine, registry, req)
    leg = resp.shocks[0].legs[0]
    assert leg.market_version == "v1"
    assert abs(leg.shocked_price - bs_price(c)) < 1e-10


def test_missing_market_field_is_per_leg_distinct_error(engine, registry):
    # novol 版本不带 volatility；snapshot 优先级下逐笔失败且原因可区分
    c = make_contract(contract_id="a")
    req = _req([c], [Shock()], market_version="novol",
               precedence=MarketPrecedence.SNAPSHOT)
    resp = run_portfolio_scenarios(engine, registry, req)
    s = resp.shocks[0]
    assert s.failed == 1
    err = s.legs[0].error
    assert err.code == "MARKET_DATA_MISSING"
    assert err.detail["field"] == "volatility"
    assert s.legs[0].market_version == "novol"


def test_resolve_market_without_snapshot_uses_contract():
    c = make_contract()
    resolved, version, err = resolve_market(c, None, MarketPrecedence.SNAPSHOT)
    assert resolved is c and version is None and err is None


def test_unknown_version_rejects_entire_request(engine, registry):
    c = make_contract()
    req = _req([c], [Shock()], market_version="missing")
    with pytest.raises(PricingError) as exc:
        run_portfolio_scenarios(engine, registry, req)
    assert exc.value.code is ErrorCode.VERSION_NOT_FOUND


def test_contract_limit_rejects_whole_request(engine, registry):
    small_engine = PricingEngine(Settings(max_batch_size=2))
    contracts = [make_contract(contract_id=str(i)) for i in range(3)]
    with pytest.raises(PricingError) as exc:
        run_portfolio_scenarios(small_engine, registry,
                                _req(contracts, [Shock()]))
    assert exc.value.code is ErrorCode.LIMIT_EXCEEDED


def test_shock_limit_rejects_whole_request(engine, registry):
    small_engine = PricingEngine(Settings(max_scenarios=1))
    with pytest.raises(PricingError) as exc:
        run_portfolio_scenarios(
            small_engine, registry,
            _req([make_contract()], [Shock(), Shock()]))
    assert exc.value.code is ErrorCode.LIMIT_EXCEEDED


def test_no_partial_results_when_limits_exceeded(engine, registry):
    # 超限时响应对象不应被构造：异常即终态，无残留
    small_engine = PricingEngine(Settings(max_batch_size=1, max_scenarios=10))
    with pytest.raises(PricingError):
        run_portfolio_scenarios(
            small_engine, registry,
            _req([make_contract(contract_id="a"),
                  make_contract(contract_id="b")], [Shock()]))
    # 引擎/仓库仍可正常服务后续请求（无残留污染）
    resp = run_portfolio_scenarios(
        engine, registry,
        _req([make_contract(contract_id="a")], [Shock()]))
    assert resp.shocks[0].succeeded == 1
