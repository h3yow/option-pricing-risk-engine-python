"""合约、行情快照与请求/响应模型。"""
from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class OptionType(str, Enum):
    CALL = "call"
    PUT = "put"


class PricingMethod(str, Enum):
    ANALYTIC = "analytic"      # Black-Scholes 闭式解
    MONTE_CARLO = "monte_carlo"  # 蒙特卡洛模拟
    CROSS_CHECK = "cross_check"  # 两种途径交叉验证


class MarketPrecedence(str, Enum):
    """合约级取值与行情状态冲突时的优先级。"""

    CONTRACT_FIRST = "contract_first"  # 合约自带取值优先，行情补缺
    MARKET_FIRST = "market_first"      # 行情状态优先，覆盖合约取值


class Contract(BaseModel):
    """单个欧式期权合约。字段级校验在引擎层完成，以便给出可区分错误。

    spot/risk_free_rate/dividend_yield 可为空：请求绑定行情版本时
    由行情状态按优先级填充；未绑定行情且缺失时在引擎层报 VALIDATION。
    """

    contract_id: str
    option_type: OptionType
    spot: float | None = None
    strike: float
    time_to_maturity: float  # 年化
    volatility: float
    risk_free_rate: float | None = None
    dividend_yield: float | None = None
    quantity: float = 1.0


class MarketSnapshot(BaseModel):
    """行情快照：不可变，按版本存取，供多请求并发共享。"""

    model_config = ConfigDict(frozen=True)

    version: str
    spot: float
    risk_free_rate: float
    dividend_yield: float = 0.0


class MarketSnapshotPublish(BaseModel):
    """发布行情快照的请求体；版本号由服务端分配。"""

    spot: float
    risk_free_rate: float
    dividend_yield: float = 0.0


class PriceRequest(BaseModel):
    contract: Contract
    method: PricingMethod = PricingMethod.CROSS_CHECK
    market_version: str | None = None
    market_precedence: MarketPrecedence = MarketPrecedence.CONTRACT_FIRST
    cross_check_tolerance: float | None = Field(default=None, gt=0.0)


class BatchPriceRequest(BaseModel):
    contracts: list[Contract] = Field(default_factory=list)
    method: PricingMethod = PricingMethod.CROSS_CHECK
    market_version: str | None = None
    market_precedence: MarketPrecedence = MarketPrecedence.CONTRACT_FIRST
    cross_check_tolerance: float | None = Field(default=None, gt=0.0)


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
    market_version: str | None = None  # 本笔记价实际使用的行情版本


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
    market_version: str | None = None
    market_precedence: MarketPrecedence = MarketPrecedence.CONTRACT_FIRST
    cross_check_tolerance: float | None = Field(default=None, gt=0.0)


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


# ---------- 组合级情景重估 ----------


class PortfolioScenarioRequest(BaseModel):
    """组合情景重估请求：一次提交多个合约与一组行情冲击。"""

    name: str
    shocks: list[Shock]
    contracts: list[Contract]
    method: PricingMethod = PricingMethod.ANALYTIC
    market_version: str | None = None
    market_precedence: MarketPrecedence = MarketPrecedence.CONTRACT_FIRST
    cross_check_tolerance: float | None = Field(default=None, gt=0.0)


class PortfolioLegResult(BaseModel):
    """组合中单笔合约在某一冲击下的结果（价格类字段均为数量加权）。"""

    contract_id: str
    ok: bool
    base_price: float | None = None
    shocked_price: float | None = None
    pnl: float | None = None
    greeks: Greeks | None = None          # 基准状态、数量加权
    market_version: str | None = None
    error: ItemError | None = None


class PortfolioShockResult(BaseModel):
    """某一冲击下的组合汇总与逐笔明细。

    portfolio_value/total_pnl 仅累加成功腿，与逐笔结果相加严格一致。
    """

    shock_index: int
    portfolio_value: float
    total_pnl: float
    legs: list[PortfolioLegResult]
    succeeded: int
    failed: int


class PortfolioScenarioResponse(BaseModel):
    """组合级情景重估响应：基准组合价值与敏感度 + 各冲击汇总。"""

    name: str
    base_value: float                     # 基准组合价值 = 成功腿 base_price 之和
    greeks: Greeks                        # 组合层面基准敏感度 = 成功腿 greeks 之和
    market_version: str | None = None     # 本请求实际使用的行情版本
    shocks: list[PortfolioShockResult]
