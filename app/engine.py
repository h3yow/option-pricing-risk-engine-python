"""定价引擎：途径选择、交叉验证容差、批量编排与上限控制。

引擎无状态：不缓存合约级结果，每次调用独立计算，并发安全。
"""
from __future__ import annotations

from .config import Settings
from .errors import ErrorCode, PricingError
from .models import (BatchItemResult, BatchPriceResponse, Contract, ItemError,
                     MarketPrecedence, MarketSnapshot, PriceResult,
                     PricingMethod, validate_cross_check_tolerance)
from .pricing.black_scholes import bs_greeks, bs_price
from .pricing.monte_carlo import mc_price


def resolve_market(c: Contract, snapshot: MarketSnapshot | None,
                   precedence: MarketPrecedence
                   ) -> tuple[Contract | None, str | None, ItemError | None]:
    """按优先级把合约字段与行情版本解析为实际记价合约。

    返回 (解析后合约, 实际行情版本, 错误)。snapshot 为 None 时不绑定版本。
    """
    if snapshot is None:
        return c, None, None
    version = snapshot.snapshot_id
    if precedence is MarketPrecedence.CONTRACT:
        # 合约级取值优先：绑定仅标记版本，字段以合约为准（兼容既有行为）
        return c, version, None
    # SNAPSHOT：行情版本覆盖标的/利率/股息；波动率在版本缺失时逐笔失败
    if snapshot.volatility is None:
        return None, version, ItemError(
            code=ErrorCode.MARKET_DATA_MISSING.value,
            message=(f"行情版本 {version!r} 未提供 volatility，"
                     f"precedence=snapshot 下合约 {c.contract_id} 无法记价"),
            detail={"snapshot_id": version, "field": "volatility",
                    "contract_id": c.contract_id})
    resolved = c.model_copy(update={
        "spot": snapshot.spot,
        "risk_free_rate": snapshot.risk_free_rate,
        "dividend_yield": snapshot.dividend_yield,
        "volatility": snapshot.volatility,
    })
    return resolved, version, None


class PricingEngine:
    def __init__(self, settings: Settings):
        self.settings = settings

    def _analytic(self, c: Contract) -> PriceResult:
        return PriceResult(contract_id=c.contract_id, price=bs_price(c),
                           greeks=bs_greeks(c), method=PricingMethod.ANALYTIC.value)

    def _monte_carlo(self, c: Contract) -> PriceResult:
        price = mc_price(c, self.settings.mc_paths, self.settings.mc_seed)
        # 希腊字母由解析式给出（同一模型假设下精确且稳定）
        return PriceResult(contract_id=c.contract_id, price=price,
                           greeks=bs_greeks(c), method=PricingMethod.MONTE_CARLO.value)

    def price_one(self, c: Contract, method: PricingMethod,
                  cross_check_tolerance: float | None = None) -> PriceResult:
        # 请求级容差：None 时沿用配置默认；显式传入则参与本次判定
        tolerance = self.settings.cross_check_tolerance
        if cross_check_tolerance is not None:
            tolerance = validate_cross_check_tolerance(cross_check_tolerance)
        if method is PricingMethod.ANALYTIC:
            return self._analytic(c)
        if method is PricingMethod.MONTE_CARLO:
            return self._monte_carlo(c)
        if method is PricingMethod.CROSS_CHECK:
            analytic = self._analytic(c)
            mc = self._monte_carlo(c)
            scale = max(abs(analytic.price), 1.0)
            rel_diff = abs(analytic.price - mc.price) / scale
            if rel_diff > tolerance:
                raise PricingError(
                    ErrorCode.METHOD_DIVERGENCE,
                    f"合约 {c.contract_id} 两种定价途径分歧: 解析={analytic.price:.6f} "
                    f"蒙特卡洛={mc.price:.6f} 相对差={rel_diff:.4%} "
                    f"超过容差 {tolerance:.4%}",
                    {"analytic": analytic.price, "monte_carlo": mc.price,
                     "rel_diff": rel_diff, "tolerance": tolerance,
                     "criterion": "rel_diff > tolerance",
                     "rel_diff_formula": "|analytic - monte_carlo| / max(|analytic|, 1)"})
            result = analytic.model_copy()
            result.method = PricingMethod.CROSS_CHECK.value
            return result
        raise PricingError(ErrorCode.UNKNOWN_METHOD, f"未知定价途径: {method!r}",
                           {"method": repr(method)})

    def price_batch(self, contracts: list[Contract], method: PricingMethod,
                    cross_check_tolerance: float | None = None,
                    snapshot: MarketSnapshot | None = None,
                    precedence: MarketPrecedence = MarketPrecedence.CONTRACT
                    ) -> BatchPriceResponse:
        # 非法容差在任何计算之前拒绝（不残留部分结果）
        validate_cross_check_tolerance(cross_check_tolerance)
        if len(contracts) > self.settings.max_batch_size:
            # 超限整体拒绝，不残留部分计算结果
            raise PricingError(
                ErrorCode.LIMIT_EXCEEDED,
                f"批量规模 {len(contracts)} 超过上限 {self.settings.max_batch_size}",
                {"size": len(contracts), "limit": self.settings.max_batch_size})
        results: list[BatchItemResult] = []
        for c in contracts:
            # 版本绑定在请求级完成：同一批量共享同一份冻结快照
            resolved, version, mkt_err = resolve_market(c, snapshot, precedence)
            if mkt_err is not None:
                results.append(BatchItemResult(
                    contract_id=c.contract_id, ok=False,
                    market_version=version, error=mkt_err))
                continue
            try:
                results.append(BatchItemResult(
                    contract_id=c.contract_id, ok=True,
                    result=self.price_one(resolved, method,
                                          cross_check_tolerance),
                    market_version=version))
            except PricingError as e:
                # 单笔失败隔离：记录可区分错误，继续处理其余
                results.append(BatchItemResult(
                    contract_id=c.contract_id, ok=False,
                    market_version=version,
                    error=ItemError(**e.to_dict())))
        succeeded = sum(1 for r in results if r.ok)
        return BatchPriceResponse(
            results=results, succeeded=succeeded,
            failed=len(results) - succeeded,
            market_version=snapshot.snapshot_id if snapshot else None,
            precedence=precedence.value)
