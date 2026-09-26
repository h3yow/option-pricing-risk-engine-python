"""组合级情景重估：汇总与逐笔严格一致、单笔失败隔离、超限整体拒绝。"""
import pytest

from app.config import Settings
from app.engine import PricingEngine
from app.errors import ErrorCode, PricingError
from app.models import Greeks, PricingMethod, Shock
from app.portfolio import run_portfolio_scenarios
from tests.conftest import make_contract

SHOCKS = [Shock(spot_pct=-0.1), Shock(spot_pct=0.05, vol_shift=0.1),
          Shock(rate_shift=0.01)]


def _portfolio(engine, contracts, shocks=SHOCKS, **kw):
    return run_portfolio_scenarios(engine, "pf", contracts, shocks,
                                   PricingMethod.ANALYTIC, **kw)


def test_portfolio_value_matches_leg_sum(engine):
    contracts = [make_contract(contract_id=f"c{i}", strike=90 + i * 5,
                               quantity=1.0 + i)
                 for i in range(4)]
    resp = _portfolio(engine, contracts)
    for shock in resp.shocks:
        leg_value = sum(leg.shocked_price for leg in shock.legs if leg.ok)
        leg_pnl = sum(leg.pnl for leg in shock.legs if leg.ok)
        assert abs(shock.portfolio_value - leg_value) < 1e-9, (
            f"组合价值 {shock.portfolio_value} 与逐笔合计 {leg_value} 不一致")
        assert abs(shock.total_pnl - leg_pnl) < 1e-9
        # portfolio_value = base_value + total_pnl（成功腿口径）
        ok_base = sum(leg.base_price for leg in shock.legs if leg.ok)
        assert abs(shock.portfolio_value - (ok_base + shock.total_pnl)) < 1e-9


def test_base_value_and_greeks_match_leg_sum(engine):
    contracts = [make_contract(contract_id=f"c{i}", strike=95 + i * 5,
                               quantity=2.0)
                 for i in range(3)]
    resp = _portfolio(engine, contracts)
    legs = resp.shocks[0].legs  # 基准希腊字母在每条成功腿上携带
    assert abs(resp.base_value - sum(leg.base_price for leg in legs if leg.ok)) < 1e-9
    for field in ("delta", "gamma", "vega", "theta", "rho"):
        leg_sum = sum(getattr(leg.greeks, field) for leg in legs if leg.ok)
        assert abs(getattr(resp.greeks, field) - leg_sum) < 1e-9, (
            f"组合 {field} 与逐笔合计不一致: {getattr(resp.greeks, field)} vs {leg_sum}")


def test_portfolio_greeks_are_quantity_weighted(engine):
    c = make_contract(contract_id="q", quantity=10.0)
    resp = _portfolio(engine, [c], [Shock()])
    unit = engine.price_one(make_contract(contract_id="u", quantity=1.0),
                            PricingMethod.ANALYTIC)
    for field in ("delta", "gamma", "vega", "theta", "rho"):
        assert abs(getattr(resp.greeks, field)
                   - 10.0 * getattr(unit.greeks, field)) < 1e-9


def test_single_leg_failure_isolated_and_aggregates_unaffected(engine):
    good1 = make_contract(contract_id="good1", quantity=2.0)
    good2 = make_contract(contract_id="good2", strike=110.0)
    bad = make_contract(contract_id="bad", volatility=-0.5)
    resp = _portfolio(engine, [good1, bad, good2])
    for shock in resp.shocks:
        assert (shock.succeeded, shock.failed) == (2, 1)
        bad_leg = [leg for leg in shock.legs if leg.contract_id == "bad"][0]
        assert not bad_leg.ok and bad_leg.error.code == "VALIDATION"
        assert "volatility" in str(bad_leg.error.detail), (
            f"失败原因应可区分到字段: {bad_leg.error.detail}")
        # 汇总只含成功腿，且与逐笔一致
        ok_legs = [leg for leg in shock.legs if leg.ok]
        assert abs(shock.portfolio_value
                   - sum(leg.shocked_price for leg in ok_legs)) < 1e-9
    # 组合基准同样只含成功腿
    ok_base = sum(leg.base_price for leg in resp.shocks[0].legs if leg.ok)
    assert abs(resp.base_value - ok_base) < 1e-9


def test_shock_induced_failure_isolated(engine):
    c = make_contract(contract_id="edge", volatility=0.01)
    resp = _portfolio(engine, [c], [Shock(vol_shift=-0.02)])
    shock = resp.shocks[0]
    assert shock.failed == 1 and shock.legs[0].error.code == "VALIDATION"
    assert shock.portfolio_value == 0.0 and shock.total_pnl == 0.0


def test_shock_count_limit_rejects_whole_request():
    engine = PricingEngine(Settings(max_scenarios=1))
    with pytest.raises(PricingError) as exc_info:
        _portfolio(engine, [make_contract()], [Shock(), Shock()])
    assert exc_info.value.code is ErrorCode.LIMIT_EXCEEDED


def test_contract_count_limit_rejects_whole_request():
    engine = PricingEngine(Settings(max_batch_size=1))
    contracts = [make_contract(contract_id="a"), make_contract(contract_id="b")]
    with pytest.raises(PricingError) as exc_info:
        _portfolio(engine, contracts, [Shock()])
    assert exc_info.value.code is ErrorCode.LIMIT_EXCEEDED


def test_portfolio_result_reproducible(engine):
    contracts = [make_contract(contract_id=f"c{i}", strike=90 + i * 10)
                 for i in range(3)]
    r1 = _portfolio(engine, contracts)
    r2 = _portfolio(engine, contracts)
    assert r1.model_dump() == r2.model_dump(), "同一请求重放结果应完全一致"


def test_portfolio_with_market_binding(engine):
    """组合请求绑定行情：全请求共享同一快照，冲击施加在解析后的取值上。"""
    from app.market import MarketStore
    from app.models import MarketPrecedence
    store = MarketStore()
    snap = store.publish(spot=100.0, risk_free_rate=0.05, dividend_yield=0.0)
    contracts = [make_contract(contract_id="m1", spot=None, risk_free_rate=None),
                 make_contract(contract_id="m2", spot=None, risk_free_rate=None,
                               strike=110.0, quantity=3.0)]
    resp = _portfolio(engine, contracts, market=snap,
                      precedence=MarketPrecedence.MARKET_FIRST)
    assert resp.market_version == snap.version
    for shock in resp.shocks:
        for leg in shock.legs:
            assert leg.market_version == snap.version
    # 与显式传入相同取值、无行情绑定的结果完全一致
    explicit = [make_contract(contract_id="m1"),
                make_contract(contract_id="m2", strike=110.0, quantity=3.0)]
    resp2 = _portfolio(engine, explicit)
    assert resp.base_value == resp2.base_value
    for s1, s2 in zip(resp.shocks, resp2.shocks):
        assert s1.portfolio_value == s2.portfolio_value
        assert s1.total_pnl == s2.total_pnl
