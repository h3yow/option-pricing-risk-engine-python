"""定价引擎：途径选择、交叉验证容差、批量编排与上限控制。

引擎无状态：不缓存合约级结果，每次调用独立计算，并发安全。
"""
from __future__ import annotations

from .config import Settings
from .errors import ErrorCode, PricingError
from .market import resolve_contract
from .models import (BatchItemResult, BatchPriceResponse, Contract, ItemError,
                     MarketPrecedence, MarketSnapshot, PriceResult,
                     PricingMethod)
from .pricing.black_scholes import bs_greeks, bs_price
from .pricing.monte_carlo import mc_price


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
                  tolerance: float | None = None,
                  market: MarketSnapshot | None = None,
                  precedence: MarketPrecedence = MarketPrecedence.CONTRACT_FIRST
                  ) -> PriceResult:
        """单笔定价。绑定行情时先按优先级合并，结果标记实际所用行情版本。"""
        resolved = resolve_contract(c, market, precedence)
        result = self._price_resolved(resolved, method, tolerance)
        if market is not None:
            result = result.model_copy(update={"market_version": market.version})
        return result

    def _price_resolved(self, c: Contract, method: PricingMethod,
                        tolerance: float | None = None) -> PriceResult:
        if method is PricingMethod.ANALYTIC:
            return self._analytic(c)
        if method is PricingMethod.MONTE_CARLO:
            return self._monte_carlo(c)
        if method is PricingMethod.CROSS_CHECK:
            analytic = self._analytic(c)
            mc = self._monte_carlo(c)
            # 请求方可显式收紧容差；缺省用配置默认值
            tol = tolerance if tolerance is not None else self.settings.cross_check_tolerance
            scale = max(abs(analytic.price), 1.0)
            rel_diff = abs(analytic.price - mc.price) / scale
            if rel_diff > tol:
                raise PricingError(
                    ErrorCode.METHOD_DIVERGENCE,
                    f"合约 {c.contract_id} 两种定价途径分歧: 解析={analytic.price:.6f} "
                    f"蒙特卡洛={mc.price:.6f} 相对差={rel_diff:.4%} "
                    f"超过容差 {tol:.4%}",
                    {"analytic": analytic.price, "monte_carlo": mc.price,
                     "rel_diff": rel_diff,
                     "tolerance": tol,
                     "tolerance_source": "request" if tolerance is not None else "default",
                     "criterion": "abs(analytic - monte_carlo) / max(abs(analytic), 1.0) > tolerance"})
            result = analytic.model_copy()
            result.method = PricingMethod.CROSS_CHECK.value
            return result
        raise PricingError(ErrorCode.UNKNOWN_METHOD, f"未知定价途径: {method!r}",
                           {"method": repr(method)})

    def price_batch(self, contracts: list[Contract], method: PricingMethod,
                    tolerance: float | None = None,
                    market: MarketSnapshot | None = None,
                    precedence: MarketPrecedence = MarketPrecedence.CONTRACT_FIRST
                    ) -> BatchPriceResponse:
        if len(contracts) > self.settings.max_batch_size:
            # 超限整体拒绝，不残留部分计算结果
            raise PricingError(
                ErrorCode.LIMIT_EXCEEDED,
                f"批量规模 {len(contracts)} 超过上限 {self.settings.max_batch_size}",
                {"size": len(contracts), "limit": self.settings.max_batch_size})
        results: list[BatchItemResult] = []
        for c in contracts:
            try:
                results.append(BatchItemResult(
                    contract_id=c.contract_id, ok=True,
                    result=self.price_one(c, method, tolerance, market, precedence)))
            except PricingError as e:
                # 单笔失败隔离：记录可区分错误，继续处理其余
                results.append(BatchItemResult(
                    contract_id=c.contract_id, ok=False,
                    error=ItemError(**e.to_dict())))
        succeeded = sum(1 for r in results if r.ok)
        return BatchPriceResponse(results=results, succeeded=succeeded,
                                  failed=len(results) - succeeded)
