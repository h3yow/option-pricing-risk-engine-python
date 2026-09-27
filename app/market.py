"""行情版本与合约字段的统一解析（供批量定价、情景重估、组合重估复用）。

约定：
- snapshot 为 None（未绑定版本）：按合约自带取值，版本标注为 None，
  与既有行为完全一致。
- precedence=contract：字段以合约为准（兼容既有行为），仅标注版本。
- precedence=snapshot：版本行情覆盖 spot/risk_free_rate/dividend_yield/
  volatility；版本缺少 volatility 时该笔以 MARKET_DATA_MISSING 隔离失败。
"""
from __future__ import annotations

from .errors import ErrorCode
from .models import (Contract, ItemError, MarketPrecedence, MarketSnapshot)


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
