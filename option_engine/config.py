"""引擎配置：缺失取有文档的默认值，非法取值在加载时拒绝。"""

from __future__ import annotations

import dataclasses
import math
from dataclasses import dataclass

from .errors import ConfigError

_ALLOWED_LIMIT_POLICIES = ("reject",)


@dataclass(frozen=True)
class EngineConfig:
    """引擎配置。

    默认值（均有文档说明）：
    - max_batch_size=1000: 单次批量定价请求的最大合约数，超限按 on_limit_exceeded 处理。
    - binomial_steps=200: 二叉树步数（>=2）。
    - cross_check_tolerance=0.02: 两种定价途径的相对容差（2%），超出报分歧。
    - iv_max_iterations=200: 隐含波动率反解最大迭代次数。
    - iv_tolerance=1e-8: 隐含波动率反解价格容差。
    - iv_max_vol=5.0: 隐含波动率搜索上界（500%）。
    - on_limit_exceeded="reject": 超限策略，当前仅支持 "reject"。
    """

    max_batch_size: int = 1000
    binomial_steps: int = 200
    cross_check_tolerance: float = 0.02
    iv_max_iterations: int = 200
    iv_tolerance: float = 1e-8
    iv_max_vol: float = 5.0
    on_limit_exceeded: str = "reject"

    @classmethod
    def from_dict(cls, data: dict | None) -> "EngineConfig":
        """从字典加载；缺失键取默认值，未知键与非法取值抛 ConfigError。"""
        if data is None:
            return cls()
        if not isinstance(data, dict):
            raise ConfigError(f"config must be a dict, got {type(data).__name__}")
        known = {f.name for f in dataclasses.fields(cls)}
        unknown = set(data) - known
        if unknown:
            raise ConfigError(f"unknown config keys: {sorted(unknown)}")
        cfg = cls(**data)
        cfg._validate()
        return cfg

    def _validate(self) -> None:
        def _check_int(name: str, minimum: int) -> None:
            v = getattr(self, name)
            if not isinstance(v, int) or isinstance(v, bool) or v < minimum:
                raise ConfigError(f"{name} must be an int >= {minimum}, got {v!r}")

        def _check_float(name: str, minimum: float, *, exclusive: bool = False) -> None:
            v = getattr(self, name)
            ok = (
                isinstance(v, (int, float))
                and not isinstance(v, bool)
                and math.isfinite(v)
                and (v > minimum if exclusive else v >= minimum)
            )
            if not ok:
                raise ConfigError(f"{name} must be a finite number > {minimum}, got {v!r}")

        _check_int("max_batch_size", 1)
        _check_int("binomial_steps", 2)
        _check_int("iv_max_iterations", 1)
        _check_float("cross_check_tolerance", 0.0, exclusive=True)
        _check_float("iv_tolerance", 0.0, exclusive=True)
        _check_float("iv_max_vol", 0.0, exclusive=True)
        if self.on_limit_exceeded not in _ALLOWED_LIMIT_POLICIES:
            raise ConfigError(
                f"on_limit_exceeded must be one of {_ALLOWED_LIMIT_POLICIES}, "
                f"got {self.on_limit_exceeded!r}"
            )
