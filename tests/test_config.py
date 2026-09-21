"""配置：缺失取默认值，非法取值明确拒绝。"""
import pytest

from app.config import (DEFAULT_MAX_BATCH_SIZE, Settings, load_settings)
from app.errors import ErrorCode, PricingError


def test_defaults_when_missing():
    s = load_settings({})
    assert s.max_batch_size == DEFAULT_MAX_BATCH_SIZE
    assert s.mc_seed == 42


def test_env_override():
    s = load_settings({"OPE_MAX_BATCH_SIZE": "10", "OPE_MC_PATHS": "1000"})
    assert (s.max_batch_size, s.mc_paths) == (10, 1000)


@pytest.mark.parametrize("env", [
    {"OPE_MAX_BATCH_SIZE": "abc"},
    {"OPE_MAX_BATCH_SIZE": "0"},
    {"OPE_IV_TOLERANCE": "-1"},
    {"OPE_MC_PATHS": "10"},          # 低于下限 100
    {"OPE_CROSS_CHECK_TOLERANCE": "xyz"},
])
def test_invalid_config_rejected(env):
    with pytest.raises(PricingError) as exc_info:
        load_settings(env)
    assert exc_info.value.code is ErrorCode.CONFIG, (
        f"非法配置 {env} 应报 CONFIG 错误，实际: {exc_info.value}")
