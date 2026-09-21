"""情景重估：汇总与逐笔一致、单笔失败隔离、冲击语义正确。"""

import dataclasses

import pytest

from option_engine import (
    LimitExceededError,
    EngineConfig,
    OptionContract,
    OptionType,
    PricingEngine,
    PricingMethod,
)
from option_engine.scenario import Shock


def make_contract(**overrides):
    base = dict(
        option_type=OptionType.CALL,
        spot=100.0,
        strike=100.0,
        maturity=1.0,
        volatility=0.2,
        rate=0.05,
    )
    base.update(overrides)
    return OptionContract(**base)


@pytest.fixture
def engine():
    return PricingEngine()


@pytest.fixture
def contracts():
    return {
        "call1": make_contract(),
        "put1": make_contract(option_type=OptionType.PUT, strike=95.0),
        "call2": make_contract(spot=50.0, strike=55.0, volatility=0.3, quantity=2.0),
    }


class TestAggregation:
    def test_totals_match_per_item_sum(self, engine, contracts):
        shock = Shock(spot_shift=-0.1, vol_shift=0.05, rate_shift=0.01)
        [sc] = engine.run_scenario(contracts, [shock])
        assert not sc.errors
        assert sc.total_value == pytest.approx(sum(r.price for r in sc.results))
        assert sc.total_delta == pytest.approx(sum(r.greeks.delta for r in sc.results))
        assert sc.total_gamma == pytest.approx(sum(r.greeks.gamma for r in sc.results))
        assert sc.total_vega == pytest.approx(sum(r.greeks.vega for r in sc.results))

    def test_shock_semantics(self, engine):
        # 标的上移 10% 的看涨价格应等于直接以 110 定价
        c = make_contract()
        [sc] = engine.run_scenario({"c": c}, [Shock(spot_shift=0.1)])
        resp = engine.price_batch(
            {"c": dataclasses.replace(c, spot=110.0)},
            method=PricingMethod.BLACK_SCHOLES,
        )
        assert sc.results[0].price == pytest.approx(resp.results[0].price), (
            f"shocked price {sc.results[0].price} != repriced {resp.results[0].price}"
        )

    def test_multiple_shocks_independent(self, engine, contracts):
        shocks = [Shock(spot_shift=0.1), Shock(spot_shift=-0.1)]
        results = engine.run_scenario(contracts, shocks)
        assert len(results) == 2
        assert results[0].total_value != results[1].total_value


class TestPartialFailure:
    def test_single_item_failure_isolated(self, engine):
        contracts = {
            "good": make_contract(),
            # 波动率冲击后为负 => 该笔必须失败
            "fragile": make_contract(volatility=0.02),
        }
        shock = Shock(vol_shift=-0.05)
        [sc] = engine.run_scenario(contracts, [shock])
        assert [r.contract_id for r in sc.results] == ["good"]
        assert len(sc.errors) == 1
        assert sc.errors[0].contract_id == "fragile"
        assert sc.errors[0].reason == "negative_volatility", (
            f"shock vol_shift=-0.05 on vol=0.02: got reason "
            f"{sc.errors[0].reason} ({sc.errors[0].message})"
        )
        # 汇总只包含成功笔
        assert sc.total_value == pytest.approx(sc.results[0].price)

    def test_batch_limit_applies_to_scenarios(self, contracts):
        engine = PricingEngine(EngineConfig(max_batch_size=2))
        with pytest.raises(LimitExceededError):
            engine.run_scenario(contracts, [Shock()])
