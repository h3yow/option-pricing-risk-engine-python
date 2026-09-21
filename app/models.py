"""合约、行情快照与请求/响应模型。"""
from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field


class OptionType(str, Enum):
    CALL = "call"
    PUT = "put"


class PricingMethod(str, Enum):
    ANALYTIC = "analytic"      # Black-Scholes 闭式解
    MONTE_CARLO = "monte_carlo"  # 蒙特卡洛模拟
    CROSS_CHECK = "cross_check"  # 两种途径交叉验证


class Contract(BaseModel):
    """单个欧式期权合约。字段级校验在引擎层完成，以便给出可区分错误。"""

    contract_id: str
    option_type: OptionType
    spot: float
    strike: float
    time_to_maturity: float  # 年化
    volatility: float
    risk_free_rate: float
    dividend_yield: float = 0.0
    quantity: float = 1.0


class MarketSnapshot(BaseModel):
    """行情快照：不可变，供多请求并发共享。"""

    snapshot_id: str
    spot: float
    risk_free_rate: float
    dividend_yield: float = 0.0


class PriceRequest(BaseModel):
    contract: Contract
    method: PricingMethod = PricingMethod.CROSS_CHECK


class BatchPriceRequest(BaseModel):
    contracts: list[Contract] = Field(default_factory=list)
    method: PricingMethod = PricingMethod.CROSS_CHECK


class Greeks(BaseModel):
    delta: float
    gamma: float
    vega: float
    theta: float
    rho: float


class PriceResult(BaseModel):
    contract_id: str
    price: float
    greeks: Greeks
    method: str


class ItemError(BaseModel):
    code: str
    message: str
    detail: dict = Field(default_factory=dict)


class BatchItemResult(BaseModel):
    contract_id: str
    ok: bool
    result: PriceResult | None = None
    error: ItemError | None = None


class BatchPriceResponse(BaseModel):
    results: list[BatchItemResult]
    succeeded: int
    failed: int


class ImpliedVolRequest(BaseModel):
    option_type: OptionType
    spot: float
    strike: float
    time_to_maturity: float
    risk_free_rate: float
    dividend_yield: float = 0.0
    target_price: float


class ImpliedVolResponse(BaseModel):
    implied_vol: float
    iterations: int
    residual: float


class Shock(BaseModel):
    """行情冲击：相对/绝对变动。"""

    spot_pct: float = 0.0          # 标的价格百分比变动，如 0.05
    vol_shift: float = 0.0         # 波动率绝对加点，如 0.02
    rate_shift: float = 0.0        # 利率绝对加点


class ScenarioRequest(BaseModel):
    name: str
    shocks: list[Shock]
    contracts: list[Contract]
    method: PricingMethod = PricingMethod.ANALYTIC


class ScenarioLegResult(BaseModel):
    contract_id: str
    ok: bool
    base_price: float | None = None
    shocked_price: float | None = None
    pnl: float | None = None
    error: ItemError | None = None


class ScenarioShockResult(BaseModel):
    shock_index: int
    total_pnl: float
    legs: list[ScenarioLegResult]
    succeeded: int
    failed: int


class ScenarioResponse(BaseModel):
    name: str
    shocks: list[ScenarioShockResult]
