# 期权定价与风险情景引擎

本地可运行的欧式期权定价与风险情景引擎（FastAPI）。同一批合约在多种定价途径、
隐含波动率反解、曲面查询与压力情景下结论一致、可解释、可复现。

## 运行

```bash
uv run uvicorn main:app --reload   # 服务
uv run pytest                      # 测试（60 个用例）
```

## 定价约定

- 模型：Black-Scholes，连续复利，`dividend_yield` 为连续股息率，时间以年计。
- 希腊字母约定：`vega`/`rho` 为每 1 个百分点（0.01）变动，`theta` 为每日历日（1/365 年）。
- 零波动率为合法输入，价格退化为折现内在价值；到期时间必须为正，波动率不得为负。
- 蒙特卡洛途径：几何布朗运动风险中性模拟，期末一步精确离散化（无时间步误差），
  对偶变量降方差；每次调用使用独立 `random.Random(seed)`，无跨请求共享状态。

### 两种独立定价途径

| method | 说明 |
|---|---|
| `analytic` | Black-Scholes 闭式解 |
| `monte_carlo` | 蒙特卡洛模拟（固定种子，可复现） |
| `cross_check` | 默认。两种途径同时计算，相对差 ≤ 容差时返回解析解；超差抛 `METHOD_DIVERGENCE`，绝不静默选取其一 |

**容差设定**：默认相对容差 2%（`OPE_CROSS_CHECK_TOLERANCE`），按
`|a − m| / max(|a|, 1)` 计算。50,000 条对偶路径下蒙特卡洛标准误远小于该容差，
正常参数下不会误报；容差被人为收紧（如 1e-9）时必然明确报出分歧。

## 隐含波动率反解

流程：输入校验 → 无套利边界检查 → 二分迭代（波动率搜索区间 [1e-9, 10]）。

- 目标价超出无套利区间 `[max(S·e^{-qT} − K·e^{-rT}, 0), S·e^{-qT}]`（看涨，看跌对称）
  → `NO_ARBITRAGE_BOUNDS`。
- 目标价高于波动率上界对应价格 → `NO_SOLUTION`。
- 迭代次数耗尽仍未达到 `OPE_IV_TOLERANCE` → `NOT_CONVERGED`，附最优残差。
- 绝不返回负数或未收敛结果。

## 曲面查询

- 规则网格双线性插值，构造时校验轴严格递增、数值有限，构造后不可变。
- 查询点超出网格范围一律抛 `OUT_OF_SURFACE`，**不静默外推**。
- 插值随查询点连续变化；纯函数实现，重复/并发查询结果稳定。

## 情景重估

- 冲击（`spot_pct` 相对、`vol_shift`/`rate_shift` 绝对加点）生成新合约对象，不修改原合约。
- `total_pnl` 仅累加成功腿，与逐笔 `pnl` 严格一致（测试断言 < 1e-9）。
- 单笔失败（基准或冲击后非法）隔离为腿级错误，不影响其余结果。

## 错误码

所有领域错误以 HTTP 422 返回 `{"error": {"code", "message", "detail"}}`：

| code | 含义 |
|---|---|
| `VALIDATION` | 输入非法（缺失/非有限/到期非正/波动率为负等），`detail.field` 指明字段 |
| `NO_ARBITRAGE_BOUNDS` | 目标价超出无套利区间 |
| `NO_SOLUTION` | 波动率上界内无解 |
| `NOT_CONVERGED` | 迭代不收敛 |
| `OUT_OF_SURFACE` | 曲面范围外查询 |
| `METHOD_DIVERGENCE` | 两种定价途径分歧超容差 |
| `LIMIT_EXCEEDED` | 批量/情景超上限（整体拒绝，不残留部分结果） |
| `CONFIG` | 配置取值不支持（启动时拒绝） |
| `UNKNOWN_METHOD` | 未知定价途径 |

## 配置项（环境变量）

缺失时取下列确定默认值；非法取值（非数值/低于下限）在启动时抛 `CONFIG` 错误拒绝。

| 变量 | 默认值 | 下限 | 说明 |
|---|---|---|---|
| `OPE_MAX_BATCH_SIZE` | 256 | 1 | 批量定价/情景合约数上限 |
| `OPE_MAX_IV_ITERATIONS` | 200 | 1 | 隐含波动率二分迭代上限 |
| `OPE_IV_TOLERANCE` | 1e-8 | 1e-15 | 反解价格残差容差 |
| `OPE_MC_PATHS` | 50000 | 100 | 蒙特卡洛路径数 |
| `OPE_MC_SEED` | 42 | 0 | 蒙特卡洛固定种子 |
| `OPE_CROSS_CHECK_TOLERANCE` | 0.02 | 1e-9 | 双途径相对容差 |
| `OPE_MAX_SCENARIOS` | 64 | 1 | 单次情景冲击数上限 |

## 并发与状态

引擎无状态：不缓存合约级结果，每次调用独立计算；曲面构造后不可变；
蒙特卡洛每次调用使用独立 RNG。多线程并发定价/查询/反解结果与单线程逐位一致
（见 `tests/test_concurrency.py`）。

## 接口

- `GET /` — 兼容原有接口
- `POST /price` — 单笔定价（含希腊字母）
- `POST /price/batch` — 批量定价，逐笔隔离失败
- `POST /implied-vol` — 隐含波动率反解
- `GET /surface/{vol|rate}?maturity=&strike=` — 曲面查询
- `POST /scenarios` — 情景冲击批量重估与汇总

## 验证方法

```bash
uv run pytest -v
```

测试以边界与异常链路为主，断言失败时打印输入与判定依据：

- `test_validation.py` — 非法参数（到期非正、负波动率、NaN/Inf、零波动率合法退化）
- `test_implied_vol.py` — 往返一致、越界/无解/不收敛拒绝、结果非负
- `test_surface.py` — 范围外拒绝、插值连续、重复稳定、不可变
- `test_engine.py` — 双途径容差、分歧上报、批量上限、部分失败、平价关系
- `test_scenarios.py` — 汇总一致性、单笔失败隔离、冲击后非法、数量缩放
- `test_concurrency.py` — 多线程定价/查询/反解一致性
- `test_config.py` / `test_api.py` — 配置默认值与拒绝、HTTP 错误结构
