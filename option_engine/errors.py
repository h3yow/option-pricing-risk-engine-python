"""可区分的错误类型层级。"""


class EngineError(Exception):
    """引擎错误基类，携带机器可读的 reason 代码。"""

    reason: str = "engine_error"

    def __init__(self, message: str = "", *, reason: str | None = None):
        super().__init__(message or self.reason)
        if reason is not None:
            self.reason = reason


class ValidationError(EngineError):
    """输入非法：到期非正、波动率为负、字段缺失或数值非有限。"""

    reason = "validation_error"


class DivergenceError(EngineError):
    """两种定价途径结果超出容差。"""

    reason = "pricing_divergence"


class ImpliedVolError(EngineError):
    """隐含波动率反解失败：无解、越界或不收敛。"""

    reason = "implied_vol_failure"


class SurfaceOutOfRangeError(EngineError):
    """曲面查询点超出定义范围。"""

    reason = "surface_out_of_range"


class ConfigError(EngineError):
    """配置取值不支持。"""

    reason = "config_error"


class LimitExceededError(EngineError):
    """批量规模或迭代次数超限。"""

    reason = "limit_exceeded"
