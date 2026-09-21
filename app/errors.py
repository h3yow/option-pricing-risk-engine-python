"""可区分的错误类型与错误码。"""
from __future__ import annotations

from enum import Enum


class ErrorCode(str, Enum):
    VALIDATION = "VALIDATION"                # 输入非法（缺失/非有限/范围错误）
    NO_ARBITRAGE_BOUNDS = "NO_ARBITRAGE_BOUNDS"  # 目标价格超出无套利边界
    NO_SOLUTION = "NO_SOLUTION"              # 反解无解
    NOT_CONVERGED = "NOT_CONVERGED"          # 迭代不收敛
    OUT_OF_SURFACE = "OUT_OF_SURFACE"        # 曲面范围之外查询
    METHOD_DIVERGENCE = "METHOD_DIVERGENCE"  # 两种定价途径分歧超容差
    LIMIT_EXCEEDED = "LIMIT_EXCEEDED"        # 批量/迭代超上限
    CONFIG = "CONFIG"                        # 配置取值不支持
    UNKNOWN_METHOD = "UNKNOWN_METHOD"        # 未知定价途径


class PricingError(Exception):
    """携带错误码与判定依据的领域异常。"""

    def __init__(self, code: ErrorCode, message: str, detail: dict | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.detail = detail or {}

    def to_dict(self) -> dict:
        return {"code": self.code.value, "message": self.message, "detail": self.detail}
