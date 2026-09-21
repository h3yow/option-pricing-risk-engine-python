"""非法参数与批量限额：必须拒绝并给出可区分原因，不得用默认值兜底。"""

import math

import pytest

from option_engine import (
    EngineConfig,
    LimitExceededError,
    OptionContract,
    OptionType,
    PricingEngine,
    PricingMethod,
    ValidationError,
)
from option_engine.service import validate_contract


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


class TestContractValidation:
    @pytest.mark.parametrize(
        "field,value,reason",
        [
            ("maturity", 0.0, "non_positive_maturity"),
            ("maturity", -1.0, "non_positive_maturity"),
            ("volatility", -0.1, "negative_volatility"),
            ("spot", 0.0, "non_positive_spot"),
            ("strike", -5.0, "non_positive_strike"),
            ("spot", float("nan"), "invalid_field_spot"),
            ("rate", float("inf"), "invalid_field_rate"),
            ("volatility", float("nan"), "invalid_field_volatility"),
            ("quantity", float("nan"), "invalid_field_quantity"),
        ],
    )
    def test_invalid_inputs_rejected_with_reason(self, field, value, reason):
        c = make_contract(**{field: value})
        with pytest.raises(ValidationError) as exc_info:
            validate_contract(c)
        assert exc_info.value.reason == reason, (
            f"input {field}={value}: expected reason {reason}, "
            f"got {exc_info.value.reason} ({exc_info.value})"
        )

    def test_zero_volatility_accepted(self):
        # 零波动率合法（退化情形），不应被拒绝
        validate_contract(make_contract(volatility=0.0))


class TestBatchLimits:
    def test_batch_over_limit_rejected_without_partial_results(self):
        engine = PricingEngine(EngineConfig(max_batch_size=3))
        contracts = {f"c{i}": make_contract() for i in range(4)}
        with pytest.raises(LimitExceededError) as exc_info:
            engine.price_batch(contracts)
        assert exc_info.value.reason == "limit_exceeded"

    def test_batch_at_limit_accepted(self):
        engine = PricingEngine(EngineConfig(max_batch_size=3))
        contracts = {f"c{i}": make_contract() for i in range(3)}
        resp = engine.price_batch(contracts)
        assert len(resp.results) == 3 and not resp.errors

    def test_partial_failure_isolates_bad_item(self):
        engine = PricingEngine()
        contracts = {
            "good": make_contract(),
            "bad_maturity": make_contract(maturity=-1.0),
            "bad_vol": make_contract(volatility=-0.5),
        }
        resp = engine.price_batch(contracts, method=PricingMethod.BLACK_SCHOLES)
        assert [r.contract_id for r in resp.results] == ["good"]
        reasons = {e.contract_id: e.reason for e in resp.errors}
        assert reasons == {
            "bad_maturity": "non_positive_maturity",
            "bad_vol": "negative_volatility",
        }, f"unexpected error mapping: {reasons}"
