"""核心数据模型（不可变，线程安全共享）。"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field


class OptionType(enum.Enum):
    CALL = "call"
    PUT = "put"


class PricingMethod(enum.Enum):
    BLACK_SCHOLES = "black_scholes"
    BINOMIAL = "binomial"
    CROSS_CHECK = "cross_check"  # 两种途径互相校验


@dataclass(frozen=True)
class OptionContract:
    option_type: OptionType
    spot: float
    strike: float
    maturity: float  # 年化到期时间，必须 > 0
    volatility: float  # 必须 >= 0
    rate: float
    dividend_yield: float = 0.0
    quantity: float = 1.0


@dataclass(frozen=True)
class MarketSnapshot:
    """行情快照：按合约 id 提供标的价格，可选覆盖波动率/利率。"""

    snapshot_id: str
    spots: dict[str, float] = field(default_factory=dict)
    vol_overrides: dict[str, float] = field(default_factory=dict)
    rate_overrides: dict[str, float] = field(default_factory=dict)


@dataclass(frozen=True)
class Greeks:
    delta: float
    gamma: float
    vega: float  # 每 1.00 波动率
    theta: float  # 每年
    rho: float  # 每 1.00 利率


@dataclass(frozen=True)
class PricingResult:
    contract_id: str
    price: float
    greeks: Greeks
    method: PricingMethod
    cross_check_diff: float | None = None  # 两途径差异（仅 cross_check）


@dataclass(frozen=True)
class ItemError:
    contract_id: str
    reason: str
    message: str


@dataclass(frozen=True)
class PricingResponse:
    results: tuple[PricingResult, ...]
    errors: tuple[ItemError, ...]
