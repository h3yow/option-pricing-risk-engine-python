"""两种定价途径的一致性容差与分歧报出。"""

import pytest

from option_engine import (
    DivergenceError,
    EngineConfig,
    OptionContract,
    OptionType,
    PricingEngine,
    PricingMethod,
)
from option_engine import binomial, black_scholes


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


class TestMethodAgreement:
    @pytest.mark.parametrize(
        "contract",
        [
            make_contract(),
            make_contract(option_type=OptionType.PUT),
            make_contract(spot=80.0, strike=120.0, maturity=0.25, volatility=0.4),
            make_contract(option_type=OptionType.PUT, spot=150.0, strike=130.0,
                          maturity=2.0, volatility=0.15, rate=0.01, dividend_yield=0.02),
            make_contract(volatility=0.0),  # 零波动率退化
        ],
        ids=["atm_call", "atm_put", "otm_call", "itm_put_div", "zero_vol"],
    )
    def test_methods_agree_within_tolerance(self, contract):
        engine = PricingEngine()
        resp = engine.price_batch({"c": contract}, method=PricingMethod.CROSS_CHECK)
        assert not resp.errors, (
            f"contract {contract}: cross-check reported divergence: {resp.errors}"
        )
        r = resp.results[0]
        bn = binomial.price(contract, engine.config.binomial_steps)
        bs = black_scholes.price(contract)
        assert r.cross_check_diff is not None
        assert r.cross_check_diff <= engine.config.cross_check_tolerance, (
            f"contract {contract}: bs={bs}, binomial={bn}, "
            f"rel_diff={r.cross_check_diff} > tol={engine.config.cross_check_tolerance}"
        )

    def test_method_selection(self):
        engine = PricingEngine()
        c = make_contract()
        for method in PricingMethod:
            resp = engine.price_batch({"c": c}, method=method)
            assert resp.results[0].method is method


class TestDivergenceReported:
    def test_divergence_raises_not_silent(self):
        # 把二叉树步数压到极小使两途径必然分歧，验证分歧被明确报出
        engine = PricingEngine(
            EngineConfig(binomial_steps=2, cross_check_tolerance=1e-6)
        )
        resp = engine.price_batch({"c": make_contract()}, method=PricingMethod.CROSS_CHECK)
        assert not resp.results, "divergent pricers must not silently return a price"
        assert len(resp.errors) == 1
        err = resp.errors[0]
        assert err.reason == "pricing_divergence", (
            f"expected pricing_divergence, got {err.reason}: {err.message}"
        )
        assert "black_scholes=" in err.message and "binomial=" in err.message

    def test_divergence_error_type(self):
        engine = PricingEngine(
            EngineConfig(binomial_steps=2, cross_check_tolerance=1e-6)
        )
        with pytest.raises(DivergenceError):
            engine._price_one("c", make_contract(), PricingMethod.CROSS_CHECK)
