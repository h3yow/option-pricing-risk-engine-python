"""隐含波动率反解：成功回读与失败链路（无解/越界/不收敛）。"""

import math

import pytest

from option_engine import (
    ImpliedVolError,
    OptionContract,
    OptionType,
    PricingEngine,
)
from option_engine import black_scholes
from option_engine.implied_vol import implied_volatility, no_arbitrage_bounds


def make_contract(**overrides):
    base = dict(
        option_type=OptionType.CALL,
        spot=100.0,
        strike=100.0,
        maturity=1.0,
        volatility=0.25,
        rate=0.03,
    )
    base.update(overrides)
    return OptionContract(**base)


class TestRoundTrip:
    @pytest.mark.parametrize("vol", [0.01, 0.1, 0.25, 0.8, 2.0])
    @pytest.mark.parametrize("option_type", [OptionType.CALL, OptionType.PUT])
    def test_round_trip(self, vol, option_type):
        c = make_contract(option_type=option_type, volatility=vol)
        price = black_scholes.price(c)
        iv = implied_volatility(c, price)
        assert iv >= 0.0, f"negative iv returned for vol={vol}: {iv}"
        assert abs(iv - vol) < 1e-5, (
            f"input vol={vol} ({option_type.value}), price={price}: "
            f"recovered iv={iv}, |diff|={abs(iv - vol)}"
        )

    def test_zero_vol_boundary(self):
        c = make_contract(volatility=0.0)
        price = black_scholes.price(c)
        assert implied_volatility(c, price) == 0.0


class TestFailurePaths:
    def test_below_no_arbitrage_bound(self):
        c = make_contract()
        lo, _ = no_arbitrage_bounds(c)
        with pytest.raises(ImpliedVolError) as exc_info:
            implied_volatility(c, lo - 0.5)
        assert exc_info.value.reason == "price_below_no_arbitrage_bound", (
            f"target={lo - 0.5}, lower bound={lo}: got {exc_info.value.reason}"
        )

    def test_above_no_arbitrage_bound(self):
        c = make_contract()
        _, hi = no_arbitrage_bounds(c)
        with pytest.raises(ImpliedVolError) as exc_info:
            implied_volatility(c, hi + 0.5)
        assert exc_info.value.reason == "price_above_no_arbitrage_bound"

    def test_not_converged(self):
        c = make_contract()
        price = black_scholes.price(c)
        with pytest.raises(ImpliedVolError) as exc_info:
            implied_volatility(c, price, max_iterations=1)
        assert exc_info.value.reason == "not_converged"

    def test_non_finite_target(self):
        c = make_contract()
        with pytest.raises(ImpliedVolError) as exc_info:
            implied_volatility(c, math.nan)
        assert exc_info.value.reason == "invalid_target_price"

    def test_engine_uses_config_limits(self):
        engine = PricingEngine()
        c = make_contract()
        price = black_scholes.price(c)
        # 配置上限为 1 次迭代 => 必须不收敛而拒绝
        from option_engine import EngineConfig

        strict = PricingEngine(EngineConfig(iv_max_iterations=1))
        with pytest.raises(ImpliedVolError):
            strict.implied_volatility(c, price)
        assert engine.implied_volatility(c, price) > 0
