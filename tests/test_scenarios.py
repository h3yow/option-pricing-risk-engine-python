"""情景：冲击重估、汇总与逐笔一致、单笔失败隔离、超限拒绝。"""
import pytest

from app.config import Settings
from app.engine import PricingEngine
from app.errors import ErrorCode, PricingError
from app.models import PricingMethod, Shock
from app.scenarios import apply_shock, run_scenarios
from tests.conftest import make_contract


def test_apply_shock_does_not_mutate_original():
    c = make_contract(spot=100.0, volatility=0.2, risk_free_rate=0.05)
    shocked = apply_shock(c, Shock(spot_pct=0.1, vol_shift=0.05, rate_shift=0.01))
    assert (c.spot, c.volatility, c.risk_free_rate) == (100.0, 0.2, 0.05)
    assert shocked.spot == pytest.approx(110.0)
    assert shocked.volatility == pytest.approx(0.25)
    assert shocked.risk_free_rate == pytest.approx(0.06)


def test_aggregation_matches_leg_sum(engine):
    contracts = [make_contract(contract_id=f"c{i}", strike=90 + i * 5) for i in range(4)]
    resp = run_scenarios(engine, "s", contracts,
                         [Shock(spot_pct=-0.1), Shock(spot_pct=0.05, vol_shift=0.1)],
                         PricingMethod.ANALYTIC)
    for shock_result in resp.shocks:
        leg_sum = sum(leg.pnl for leg in shock_result.legs if leg.ok)
        assert abs(shock_result.total_pnl - leg_sum) < 1e-9, (
            f"汇总 {shock_result.total_pnl} 与逐笔合计 {leg_sum} 不一致")


def test_single_leg_failure_isolated(engine):
    good = make_contract(contract_id="good")
    bad = make_contract(contract_id="bad", volatility=-0.5)
    resp = run_scenarios(engine, "s", [good, bad], [Shock(spot_pct=0.1)],
                         PricingMethod.ANALYTIC)
    s = resp.shocks[0]
    assert (s.succeeded, s.failed) == (1, 1)
    assert s.legs[0].ok and s.legs[0].pnl is not None
    assert not s.legs[1].ok and s.legs[1].error.code == "VALIDATION"


def test_shock_induced_failure_isolated(engine):
    # 基准合法，但冲击后波动率变负 → 该腿在冲击情景中失败
    c = make_contract(contract_id="edge", volatility=0.01)
    resp = run_scenarios(engine, "s", [c], [Shock(vol_shift=-0.02)],
                         PricingMethod.ANALYTIC)
    s = resp.shocks[0]
    assert s.failed == 1 and s.legs[0].error.code == "VALIDATION", (
        f"冲击后波动率为负应失败: {s.legs[0]}")


def test_scenario_count_limit():
    engine = PricingEngine(Settings(max_scenarios=1))
    with pytest.raises(PricingError) as exc_info:
        run_scenarios(engine, "s", [make_contract()],
                      [Shock(), Shock()], PricingMethod.ANALYTIC)
    assert exc_info.value.code is ErrorCode.LIMIT_EXCEEDED


def test_quantity_scales_pnl(engine):
    c = make_contract(quantity=10.0)
    resp = run_scenarios(engine, "s", [c], [Shock(spot_pct=0.1)], PricingMethod.ANALYTIC)
    leg = resp.shocks[0].legs[0]
    assert abs(leg.pnl - (leg.shocked_price - leg.base_price)) < 1e-12
