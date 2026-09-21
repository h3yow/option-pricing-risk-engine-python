"""隐含波动率反解：往返一致、无解/越界/不收敛须明确拒绝。"""
import pytest

from app.errors import ErrorCode, PricingError
from app.implied_vol import implied_vol, no_arbitrage_bounds
from app.models import OptionType
from app.pricing.black_scholes import bs_price
from tests.conftest import make_contract

ARGS = dict(spot=100.0, strike=100.0, t=1.0, r=0.05, q=0.0)


def round_trip(option_type, vol):
    c = make_contract(option_type=option_type, volatility=vol)
    target = bs_price(c)
    iv, iterations, residual = implied_vol(
        option_type, ARGS["spot"], ARGS["strike"], ARGS["t"], ARGS["r"], ARGS["q"],
        target, max_iterations=200, tolerance=1e-8)
    assert abs(iv - vol) < 1e-6, (
        f"往返失败: 输入 vol={vol}, target={target}, 反解 iv={iv}, "
        f"iterations={iterations}, residual={residual}")
    assert iv >= 0.0


def test_round_trip_call_and_put():
    for ot in (OptionType.CALL, OptionType.PUT):
        for vol in (0.05, 0.2, 0.6, 1.5):
            round_trip(ot, vol)


def test_price_above_upper_bound_rejected():
    lower, upper = no_arbitrage_bounds(OptionType.CALL, **ARGS)
    with pytest.raises(PricingError) as exc_info:
        implied_vol(OptionType.CALL, **ARGS, target_price=upper + 1.0,
                    max_iterations=200, tolerance=1e-8)
    err = exc_info.value
    assert err.code is ErrorCode.NO_ARBITRAGE_BOUNDS, (
        f"目标价 {upper + 1.0} 超出上界 {upper}，应报 NO_ARBITRAGE_BOUNDS，实际 {err.code}")
    assert err.detail["upper"] == upper


def test_price_below_lower_bound_rejected():
    # 深度实值看涨：下界 = S - K·e^{-rt} > 0
    args = dict(spot=100.0, strike=80.0, t=1.0, r=0.05, q=0.0)
    lower, _ = no_arbitrage_bounds(OptionType.CALL, **args)
    assert lower > 0
    with pytest.raises(PricingError) as exc_info:
        implied_vol(OptionType.CALL, **args, target_price=lower - 0.5,
                    max_iterations=200, tolerance=1e-8)
    assert exc_info.value.code is ErrorCode.NO_ARBITRAGE_BOUNDS


def test_negative_target_rejected_as_validation():
    with pytest.raises(PricingError) as exc_info:
        implied_vol(OptionType.CALL, **ARGS, target_price=-1.0,
                    max_iterations=200, tolerance=1e-8)
    assert exc_info.value.code is ErrorCode.VALIDATION


def test_iteration_limit_reports_not_converged():
    c = make_contract(volatility=0.2)
    target = bs_price(c)
    with pytest.raises(PricingError) as exc_info:
        implied_vol(OptionType.CALL, **ARGS, target_price=target,
                    max_iterations=2, tolerance=1e-15)
    err = exc_info.value
    assert err.code is ErrorCode.NOT_CONVERGED, (
        f"2 次迭代无法达到 1e-15 精度，应报 NOT_CONVERGED，实际 {err.code}: {err.message}")
    assert "residual" in err.detail


def test_result_never_negative_or_divergent():
    # 深度实值目标价接近下界：应收敛到接近 0 的非负值或明确拒绝
    lower, _ = no_arbitrage_bounds(OptionType.CALL, **ARGS)
    iv, _, _ = implied_vol(OptionType.CALL, **ARGS, target_price=lower + 1e-9,
                           max_iterations=200, tolerance=1e-8)
    assert iv >= 0.0
