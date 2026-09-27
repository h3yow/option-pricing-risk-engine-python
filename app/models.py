"""合约、行情快照与请求/响应模型。"""
from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


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
    """行情快照：冻结模型，发布后不可变，供多请求并发共享并按版本复用。

    版本一经发布即冻结；volatility 可选（曲面/逐笔场景下波动率仍由
    合约携带）。
    """

    model_config = ConfigDict(frozen=True)

    snapshot_id: str
    spot: float
    risk_free_rate: float
    dividend_yield: float = 0.0
    volatility: float | None = None


class MarketPrecedence(str, Enum):
    """合约级取值与行情版本之间的优先级。"""

    CONTRACT = "contract"  # 合约自带取值优先（默认，兼容既有行为）
    SNAPSHOT = "snapshot"  # 所绑定行情版本的取值优先


class PriceRequest(BaseModel):
    contract: Contract
    method: PricingMethod = PricingMethod.CROSS_CHECK
    # 请求级一致性容差：None 表示沿用服务默认（OPE_CROSS_CHECK_TOLERANCE）；
    # 显式传入时必须为 >= 1e-9 的有限正数，否则请求被拒绝
    cross_check_tolerance: float | None = None


class BatchPriceRequest(BaseModel):
    contracts: list[Contract] = Field(default_factory=list)
    method: PricingMethod = PricingMethod.CROSS_CHECK
    # 请求级一致性容差（同上）
    cross_check_tolerance: float | None = None
    # 行情版本绑定：None 表示不绑定，按合约自带取值（兼容既有行为）
    market_version: str | None = None
    # 合约级取值与行情版本之间的优先级
    precedence: MarketPrecedence = MarketPrecedence.CONTRACT


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
    # 该笔记价实际使用的行情版本（未绑定为 None）；失败腿同样回带
    market_version: str | None = None
    error: ItemError | None = None


class BatchPriceResponse(BaseModel):
    results: list[BatchItemResult]
    succeeded: int
    failed: int
    # 本次请求实际绑定的行情版本（未绑定为 None）
    market_version: str | None = None


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
    # 请求级一致性容差（同 PriceRequest）
    cross_check_tolerance: float | None = None
    # 行情版本绑定（同 BatchPriceRequest）
    market_version: str | None = None
    precedence: MarketPrecedence = MarketPrecedence.CONTRACT


class ScenarioLegResult(BaseModel):
    contract_id: str
    ok: bool
    base_price: float | None = None
    shocked_price: float | None = None
    pnl: float | None = None
    # 该笔记价实际使用的行情版本（未绑定为 None）
    market_version: str | None = None
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
    # 本次请求实际绑定的行情版本（未绑定为 None）
    market_version: str | None = None


# ---- 组合级情景重估 ----

class PortfolioScenarioRequest(BaseModel):
    """组合情景重估请求：一次提交多笔合约与一组行情冲击。"""

    name: str
    contracts: list[Contract]
    shocks: list[Shock]
    method: PricingMethod = PricingMethod.ANALYTIC
    # 请求级一致性容差（同 PriceRequest）
    cross_check_tolerance: float | None = None
    # 行情版本绑定：同一请求内所有合约共享同一版本；None 表示不绑定
    market_version: str | None = None
    # 合约级取值与行情版本的优先级
    precedence: MarketPrecedence = MarketPrecedence.CONTRACT


class PortfolioGreeks(BaseModel):
    """组合层面风险敏感度（均按持仓量加权后求和）。"""

    delta: float
    gamma: float
    vega: float
    theta: float
    rho: float


class PortfolioLegResult(BaseModel):
    """组合中单笔合约在某个冲击下的重估结果。

    组合汇总（total_value/总 PnL/希腊字母）严格等于成功腿对应字段之和；
    失败腿单独标出原因，不参与汇总。
    """

    contract_id: str
    shock_index: int
    ok: bool
    base_price: float | None = None       # 基准价值（单价 × 持仓量）
    shocked_price: float | None = None    # 冲击后价值（单价 × 持仓量）
    pnl: float | None = None
    greeks: PortfolioGreeks | None = None  # 冲击后敏感度（已按持仓量加权）
    market_version: str | None = None      # 该笔记价实际使用的行情版本
    error: ItemError | None = None


class PortfolioShockResult(BaseModel):
    """单个冲击下的组合重估与汇总。"""

    shock_index: int
    total_base_value: float               # 成功腿基准价值之和
    total_value: float                    # 成功腿冲击后价值之和
    total_pnl: float                      # 成功腿 pnl 之和，与逐笔严格一致
    greeks: PortfolioGreeks               # 成功腿加权敏感度之和
    legs: list[PortfolioLegResult]
    succeeded: int
    failed: int


class PortfolioScenarioResponse(BaseModel):
    name: str
    market_version: str | None
    precedence: str
    base_total_value: float               # 基准组合价值（成功腿基准之和）
    shocks: list[PortfolioShockResult]
    succeeded: int                        # 基准价计算成功的合约数
    failed: int                           # 基准价计算失败的合约数


class PublishSnapshotRequest(BaseModel):
    """发布（冻结）一个行情版本。"""

    snapshot: MarketSnapshot

