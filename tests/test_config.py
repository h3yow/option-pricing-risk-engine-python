"""配置：默认值确定性、非法取值拒绝、未知键拒绝。"""

import pytest

from option_engine import ConfigError, EngineConfig


class TestDefaults:
    def test_defaults_documented_and_deterministic(self):
        a, b = EngineConfig.from_dict(None), EngineConfig.from_dict({})
        assert a == b
        assert a.max_batch_size == 1000
        assert a.binomial_steps == 200
        assert a.cross_check_tolerance == 0.02
        assert a.iv_max_iterations == 200
        assert a.iv_tolerance == 1e-8
        assert a.iv_max_vol == 5.0
        assert a.on_limit_exceeded == "reject"

    def test_partial_override_keeps_defaults(self):
        c = EngineConfig.from_dict({"max_batch_size": 10})
        assert c.max_batch_size == 10
        assert c.binomial_steps == 200


class TestInvalidValues:
    @pytest.mark.parametrize(
        "data",
        [
            {"max_batch_size": 0},
            {"max_batch_size": -5},
            {"max_batch_size": 1.5},
            {"binomial_steps": 1},
            {"cross_check_tolerance": 0.0},
            {"cross_check_tolerance": -0.1},
            {"iv_max_iterations": 0},
            {"iv_tolerance": 0.0},
            {"iv_max_vol": -1.0},
            {"iv_max_vol": float("nan")},
            {"iv_max_vol": float("inf")},
            {"on_limit_exceeded": "ignore"},
            {"on_limit_exceeded": "degrade"},
            {"unknown_key": 1},
        ],
    )
    def test_rejected(self, data):
        with pytest.raises(ConfigError) as exc_info:
            EngineConfig.from_dict(data)
        assert exc_info.value.reason == "config_error", (
            f"config {data}: expected config_error, got {exc_info.value.reason}"
        )

    def test_non_dict_rejected(self):
        with pytest.raises(ConfigError):
            EngineConfig.from_dict([1, 2, 3])
