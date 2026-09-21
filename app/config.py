"""配置读取：缺失取确定默认值，非法取值在加载时拒绝。"""
from __future__ import annotations

import os
from dataclasses import dataclass

from .errors import ErrorCode, PricingError

# 文档化的默认值（见 README）
DEFAULT_MAX_BATCH_SIZE = 256
DEFAULT_MAX_IV_ITERATIONS = 200
DEFAULT_IV_TOLERANCE = 1e-8
DEFAULT_MC_PATHS = 50_000
DEFAULT_MC_SEED = 42
DEFAULT_CROSS_CHECK_TOLERANCE = 0.02  # 两种途径允许的相对容差
DEFAULT_MAX_SCENARIOS = 64


@dataclass(frozen=True)
class Settings:
    max_batch_size: int = DEFAULT_MAX_BATCH_SIZE
    max_iv_iterations: int = DEFAULT_MAX_IV_ITERATIONS
    iv_tolerance: float = DEFAULT_IV_TOLERANCE
    mc_paths: int = DEFAULT_MC_PATHS
    mc_seed: int = DEFAULT_MC_SEED
    cross_check_tolerance: float = DEFAULT_CROSS_CHECK_TOLERANCE
    max_scenarios: int = DEFAULT_MAX_SCENARIOS


def _parse_int(name: str, raw: str, minimum: int) -> int:
    try:
        value = int(raw)
    except ValueError:
        raise PricingError(ErrorCode.CONFIG, f"配置 {name} 不是整数: {raw!r}")
    if value < minimum:
        raise PricingError(ErrorCode.CONFIG, f"配置 {name}={value} 小于下限 {minimum}")
    return value


def _parse_float(name: str, raw: str, minimum: float) -> float:
    try:
        value = float(raw)
    except ValueError:
        raise PricingError(ErrorCode.CONFIG, f"配置 {name} 不是数值: {raw!r}")
    if value < minimum:
        raise PricingError(ErrorCode.CONFIG, f"配置 {name}={value} 小于下限 {minimum}")
    return value


def load_settings(env: dict[str, str] | None = None) -> Settings:
    """从环境变量读取配置；缺失项取默认值，非法取值立即拒绝。"""
    env = os.environ if env is None else env
    s = Settings()
    return Settings(
        max_batch_size=_parse_int("OPE_MAX_BATCH_SIZE", env.get("OPE_MAX_BATCH_SIZE", str(s.max_batch_size)), 1),
        max_iv_iterations=_parse_int("OPE_MAX_IV_ITERATIONS", env.get("OPE_MAX_IV_ITERATIONS", str(s.max_iv_iterations)), 1),
        iv_tolerance=_parse_float("OPE_IV_TOLERANCE", env.get("OPE_IV_TOLERANCE", str(s.iv_tolerance)), 1e-15),
        mc_paths=_parse_int("OPE_MC_PATHS", env.get("OPE_MC_PATHS", str(s.mc_paths)), 100),
        mc_seed=_parse_int("OPE_MC_SEED", env.get("OPE_MC_SEED", str(s.mc_seed)), 0),
        cross_check_tolerance=_parse_float("OPE_CROSS_CHECK_TOLERANCE", env.get("OPE_CROSS_CHECK_TOLERANCE", str(s.cross_check_tolerance)), 1e-9),
        max_scenarios=_parse_int("OPE_MAX_SCENARIOS", env.get("OPE_MAX_SCENARIOS", str(s.max_scenarios)), 1),
    )
