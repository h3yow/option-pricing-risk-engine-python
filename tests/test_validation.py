"""非法参数不得用默认值兜底，须以可区分原因拒绝。"""
import math

import pytest

from app.errors import ErrorCode, PricingError
from app.models import OptionType, PricingMethod
from app.pricing.black_scholes import bs_price
from tests.conftest import make_contract


@pytest.mark.parametrize("field,value", [
    ("time_to_maturity", 0.0),
    ("time_to_maturity", -1.0),
    ("volatility", -0.1),
    ("spot", 0.0),
    ("spot", -5.0),
    ("strike", 0.0),
    ("spot", float("nan")),
    ("volatility", float("inf")),
    ("risk_free_rate", float("nan")),
])
def test_invalid_inputs_rejected_with_distinct_reason(field, value):
    c = make_contract(**{field: value})
    with pytest.raises(PricingError) as exc_info:
        bs_price(c)
    err = exc_info.value
    assert err.code is ErrorCode.VALIDATION, (
        f"输入 {field}={value} 应报 VALIDATION，实际 {err.code}: {err.message}")
    assert field in err.message or err.detail.get("field") == field, (
        f"错误信息应指明字段 {field}: {err.message} {err.detail}")


def test_quantity_nan_rejected():
    c = make_contract(quantity=float("nan"))
    with pytest.raises(PricingError, match="quantity"):
        bs_price(c)


def test_zero_volatility_is_valid_and_deterministic():
    # 波动率为 0 合法：价格退化为折现内在价值
    c = make_contract(volatility=0.0, spot=110.0, strike=100.0)
    price = bs_price(c)
    expected = 110.0 - 100.0 * math.exp(-0.05)
    assert abs(price - expected) < 1e-10, (
        f"零波动率价格应等于折现内在价值 {expected}，实际 {price}")


def test_unknown_method_rejected(engine, contract):
    with pytest.raises(PricingError) as exc_info:
        engine.price_one(contract, "bogus")  # 绕过枚举的非法取值
    assert exc_info.value.code is ErrorCode.UNKNOWN_METHOD
