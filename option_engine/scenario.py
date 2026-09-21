"""行情冲击情景：批量重估与风险汇总。"""

from __future__ import annotations

from dataclasses import dataclass

from .models import ItemError, PricingResult


@dataclass(frozen=True)
class Shock:
    """行情冲击：标的价格乘性偏移、波动率加性偏移、利率加性偏移。"""

    spot_shift: float = 0.0  # 相对偏移，如 0.1 表示 +10%
    vol_shift: float = 0.0  # 绝对偏移，如 0.05 表示 +5 个波动率点
    rate_shift: float = 0.0  # 绝对偏移


@dataclass(frozen=True)
class ScenarioResult:
    results: tuple[PricingResult, ...]
    errors: tuple[ItemError, ...]
    total_value: float
    total_delta: float
    total_gamma: float
    total_vega: float
