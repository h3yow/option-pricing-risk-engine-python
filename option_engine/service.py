"""引擎编排：批量定价、隐含波动率、曲面查询、情景重估。

线程安全约定：PricingEngine 仅持有不可变配置，不保存任何跨请求可变状态；
所有中间结果均为局部变量，快照与合约使用不可变数据类，
因此多请求并发访问同一引擎/快照结果一致，无半更新状态。
"""

from __future__ import annotations

import dataclasses
import math

from . import binomial, black_scholes
from .config import EngineConfig
from .errors import (
    DivergenceError,
    EngineError,
    LimitExceededError,
    ValidationError,
)
from .implied_vol import implied_volatility as _implied_volatility
from .models import (
    Greeks,
    ItemError,
    MarketSnapshot,
    OptionContract,
    PricingMethod,
    PricingResponse,
    PricingResult,
)
from .scenario import ScenarioResult, Shock
from .surface import Surface

_FINITE_FIELDS = ("spot", "strike", "maturity", "volatility", "rate", "dividend_yield", "quantity")


def validate_contract(contract: OptionContract) -> None:
    """入口校验：任何非法输入抛 ValidationError，不用默认值兜底。"""
    for name in _FINITE_FIELDS:
        v = getattr(contract, name)
        if not isinstance(v, (int, float)) or isinstance(v, bool) or not math.isfinite(v):
            raise ValidationError(
                f"field {name!r} is missing or not finite: {v!r}",
                reason=f"invalid_field_{name}",
            )
    if contract.maturity <= 0:
        raise ValidationError(
            f"maturity must be > 0, got {contract.maturity}",
            reason="non_positive_maturity",
        )
    if contract.volatility < 0:
        raise ValidationError(
            f"volatility must be >= 0, got {contract.volatility}",
            reason="negative_volatility",
        )
    if contract.spot <= 0:
        raise ValidationError(
            f"spot must be > 0, got {contract.spot}", reason="non_positive_spot"
        )
    if contract.strike <= 0:
        raise ValidationError(
            f"strike must be > 0, got {contract.strike}", reason="non_positive_strike"
        )


class PricingEngine:
    """无共享可变状态的定价引擎。"""

    def __init__(self, config: EngineConfig | None = None) -> None:
        self._config = config if config is not None else EngineConfig()

    @property
    def config(self) -> EngineConfig:
        return self._config

    # ---- 单笔定价 ----

    def _price_one(
        self,
        contract_id: str,
        contract: OptionContract,
        method: PricingMethod,
    ) -> PricingResult:
        validate_contract(contract)
        cfg = self._config
        if method is PricingMethod.BLACK_SCHOLES:
            return PricingResult(
                contract_id=contract_id,
                price=black_scholes.price(contract),
                greeks=black_scholes.greeks(contract),
                method=method,
            )
        if method is PricingMethod.BINOMIAL:
            return PricingResult(
                contract_id=contract_id,
                price=binomial.price(contract, cfg.binomial_steps),
                greeks=binomial.greeks(contract, cfg.binomial_steps),
                method=method,
            )
        # CROSS_CHECK：两种途径互相校验，分歧必须报出而非静默选取
        bs_price = black_scholes.price(contract)
        bn_price = binomial.price(contract, cfg.binomial_steps)
        scale = max(abs(bs_price), abs(bn_price), 1e-12)
        rel_diff = abs(bs_price - bn_price) / scale
        if rel_diff > cfg.cross_check_tolerance:
            raise DivergenceError(
                f"pricers diverge for {contract_id!r}: "
                f"black_scholes={bs_price}, binomial={bn_price}, "
                f"rel_diff={rel_diff:.6f} > tolerance={cfg.cross_check_tolerance}"
            )
        return PricingResult(
            contract_id=contract_id,
            price=bs_price,
            greeks=black_scholes.greeks(contract),
            method=method,
            cross_check_diff=rel_diff,
        )

    # ---- 批量定价 ----

    def price_batch(
        self,
        contracts: dict[str, OptionContract],
        snapshot: MarketSnapshot | None = None,
        method: PricingMethod = PricingMethod.CROSS_CHECK,
    ) -> PricingResponse:
        """批量定价；超限整体拒绝，单笔失败不影响其余（部分失败信息可区分）。"""
        if len(contracts) > self._config.max_batch_size:
            raise LimitExceededError(
                f"batch size {len(contracts)} exceeds limit "
                f"{self._config.max_batch_size} (policy: reject)"
            )
        results: list[PricingResult] = []
        errors: list[ItemError] = []
        for cid, contract in contracts.items():
            try:
                effective = self._apply_snapshot(cid, contract, snapshot)
                results.append(self._price_one(cid, effective, method))
            except EngineError as e:
                errors.append(ItemError(contract_id=cid, reason=e.reason, message=str(e)))
        return PricingResponse(results=tuple(results), errors=tuple(errors))

    @staticmethod
    def _apply_snapshot(
        contract_id: str,
        contract: OptionContract,
        snapshot: MarketSnapshot | None,
    ) -> OptionContract:
        if snapshot is None:
            return contract
        updates: dict[str, float] = {}
        if contract_id in snapshot.spots:
            updates["spot"] = snapshot.spots[contract_id]
        if contract_id in snapshot.vol_overrides:
            updates["volatility"] = snapshot.vol_overrides[contract_id]
        if contract_id in snapshot.rate_overrides:
            updates["rate"] = snapshot.rate_overrides[contract_id]
        if not updates:
            return contract
        return dataclasses.replace(contract, **updates)

    # ---- 隐含波动率 ----

    def implied_volatility(
        self, contract: OptionContract, target_price: float
    ) -> float:
        validate_contract(contract)
        cfg = self._config
        return _implied_volatility(
            contract,
            target_price,
            max_iterations=cfg.iv_max_iterations,
            tolerance=cfg.iv_tolerance,
            max_vol=cfg.iv_max_vol,
        )

    # ---- 曲面查询 ----

    def query_vol_surface(
        self, surface: Surface, maturity: float, strike: float
    ) -> float:
        return surface.query(maturity, strike)

    # ---- 情景重估 ----

    def run_scenario(
        self,
        contracts: dict[str, OptionContract],
        shocks: list[Shock],
        method: PricingMethod = PricingMethod.BLACK_SCHOLES,
    ) -> list[ScenarioResult]:
        """按冲击组合批量重估并汇总；汇总值由逐笔结果累加，保证一致。"""
        if len(contracts) > self._config.max_batch_size:
            raise LimitExceededError(
                f"batch size {len(contracts)} exceeds limit "
                f"{self._config.max_batch_size} (policy: reject)"
            )
        return [
            self._run_single_scenario(contracts, shock, method) for shock in shocks
        ]

    def _run_single_scenario(
        self,
        contracts: dict[str, OptionContract],
        shock: Shock,
        method: PricingMethod,
    ) -> ScenarioResult:
        results: list[PricingResult] = []
        errors: list[ItemError] = []
        for cid, contract in contracts.items():
            try:
                shocked = dataclasses.replace(
                    contract,
                    spot=contract.spot * (1.0 + shock.spot_shift),
                    volatility=contract.volatility + shock.vol_shift,
                    rate=contract.rate + shock.rate_shift,
                )
                results.append(self._price_one(cid, shocked, method))
            except EngineError as e:
                errors.append(ItemError(contract_id=cid, reason=e.reason, message=str(e)))
        return ScenarioResult(
            results=tuple(results),
            errors=tuple(errors),
            total_value=sum(r.price for r in results),
            total_delta=sum(r.greeks.delta for r in results),
            total_gamma=sum(r.greeks.gamma for r in results),
            total_vega=sum(r.greeks.vega for r in results),
        )
