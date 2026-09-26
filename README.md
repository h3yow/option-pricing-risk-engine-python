# 期权定价与风险情景引擎

本地可运行的欧式期权定价与风险情景引擎（FastAPI）。同一批合约在多种定价途径、
隐含波动率反解、曲面查询与压力情景下结论一致、可解释、可复现。

## 运行

```bash
uv run uvicorn main:app --reload   # 服务
uv run pytest                      # 测试（88 个用例）
```

## 定价约定

- 模型：Black-Scholes，连续复利，`dividend_yield` 为连续股息率，时间以年计。
- 希腊字母约定：`vega`/`rho` 为每 1 个百分点（0.01）变动，`theta` 为每日历日（1/365 年）。
- 零波动率为合法输入，价格退化为折现内在价值；到期时间必须为正，波动率不得为负。
- 蒙特卡洛途径：几何布朗运动风险中性模拟，期末一步精确离散化（无时间步误差）；
  分层抽样 + 对偶变量 + 标的控制变量三重降方差；每次调用使用独立
  `random.Random(seed)`，无跨请求共享状态，结果可复现。

### 两种独立定价途径

| method | 说明 |
|---|---|
| `analytic` | Black-Scholes 闭式解 |
| `monte_carlo` | 蒙特卡洛模拟（固定种子，可复现） |
| `cross_check` | 默认。两种途径同时计算，相对差 ≤ 容差时返回解析解；超差抛 `METHOD_DIVERGENCE`，绝不静默选取其一 |

**容差设定**：默认相对容差 2%（`OPE_CROSS_CHECK_TOLERANCE`），按
`|a − m| / max(|a|, 1)` 计算。蒙特卡洛经分层抽样 + 对偶变量 + 控制变量降方差后，
覆盖深度实值/虚值（行权价 20–300，标的 100）、短到期（1 天）到长到期（10 年）、
波动率 5%–80% 的常见参数范围，实测最差相对差约 0.12%，**默认设置下正常合约
整体可直接定价，不会误报分歧**（见 `test_default_cross_check_succeeds_over_common_ranges`）。

**请求级容差**：`/price`、`/price/batch`、`/scenarios`、`/portfolio/scenarios`
均接受可选字段 `cross_check_tolerance`，显式收紧（如 1e-9）到无法满足时返回
`METHOD_DIVERGENCE`，`detail` 同时给出双方数值（`analytic`/`monte_carlo`）、
实际相对差、所用容差、容差来源（`tolerance_source`: `request`/`default`）与
判定依据（`criterion`），便于区分是参数问题还是途径问题。

## 行情版本绑定

- `POST /market/snapshots` 发布行情快照（标的价格、利率、股息率），服务端分配
  单调递增版本号（`v1`、`v2`…）；`GET /market/snapshots/{version}` 按版本查询。
- 快照发布后**不可变**；版本映射原子更新，任何请求都读不到被中途改动的半更新行情。
- 定价/批量/情景/组合请求均可带 `market_version`：同一请求内所有合约共享同一份
  快照；引用同一版本的多个请求（无论先后或并发）得到完全一致的结果。
- 优先级由 `market_precedence` 控制：
  - `contract_first`（默认）：合约自带取值优先，行情仅填补合约缺失的
    `spot`/`risk_free_rate`/`dividend_yield`；
  - `market_first`：行情状态覆盖合约的上述三个字段。
- 结果中 `market_version` 字段标明本笔记价实际使用的行情版本（未绑定为 `null`）。
- 引用不存在的版本 → `UNKNOWN_MARKET_VERSION`。

## 组合级情景重估

`POST /portfolio/scenarios`：一次提交多个合约与一组冲击，返回：

- `base_value`：基准组合价值（数量加权）；`greeks`：组合层面基准
  Delta/Gamma/Vega/Theta/Rho（数量加权逐腿求和）；
- 每个冲击下的 `portfolio_value`（冲击后组合价值）、`total_pnl` 与逐笔明细
  （含每条腿的数量加权希腊字母与所用行情版本）。

语义保证：

- 组合汇总仅累加成功腿，且按腿顺序逐项累加，**与逐笔结果相加严格一致**
  （测试断言 < 1e-9）。
- 单笔失败（基准或冲击后非法）隔离为腿级错误，附可区分错误码与字段级
  `detail`，不影响同一冲击下其余合约的计算与汇总。
- 合约数超 `OPE_MAX_BATCH_SIZE` 或冲击数超 `OPE_MAX_SCENARIOS` 时
  在任何计算之前整体拒绝（`LIMIT_EXCEEDED`），不残留部分结果。
- 冲击施加在行情解析后的取值上：绑定行情时先按优先级合并，再施加冲击。

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
| `UNKNOWN_MARKET_VERSION` | 引用的行情版本不存在 |

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
行情快照发布后不可变、版本原子发布；蒙特卡洛每次调用使用独立 RNG。
多线程并发定价/查询/反解/行情读取结果与单线程逐位一致
（见 `tests/test_concurrency.py`、`tests/test_market.py`）。

## 兼容范围

- 既有接口与请求/响应结构保持兼容：`GET /` 原样；既有字段语义不变。
- `Contract` 的 `spot`/`risk_free_rate`/`dividend_yield` 现为可选：绑定行情版本时
  由行情按优先级填充；未绑定行情时 `dividend_yield` 缺省仍为 0.0，
  `spot`/`risk_free_rate` 缺失在引擎层报 `VALIDATION`（原为模型层 422，均为 422）。
- 响应新增字段（`market_version` 等）均为可空附加字段，不影响既有消费方。

## 接口

- `GET /` — 兼容原有接口
- `POST /price` — 单笔定价（含希腊字母，可绑定行情版本）
- `POST /price/batch` — 批量定价，逐笔隔离失败
- `POST /implied-vol` — 隐含波动率反解
- `GET /surface/{vol|rate}?maturity=&strike=` — 曲面查询
- `POST /scenarios` — 情景冲击批量重估与汇总
- `POST /portfolio/scenarios` — 组合级情景重估：组合价值与敏感度汇总
- `POST /market/snapshots` — 发布行情快照，返回版本号
- `GET /market/snapshots/{version}` — 按版本查询行情快照

## 验证方法

```bash
uv run pytest -v
```

测试以边界与异常链路为主，断言失败时打印输入与判定依据：

- `test_validation.py` — 非法参数（到期非正、负波动率、NaN/Inf、零波动率合法退化）
- `test_implied_vol.py` — 往返一致、越界/无解/不收敛拒绝、结果非负
- `test_surface.py` — 范围外拒绝、插值连续、重复稳定、不可变
- `test_engine.py` — 双途径容差、默认设置下常见参数范围批量整体成功、
  请求级容差收紧报分歧（含双方数值/容差/判定依据）、批量上限、部分失败、平价关系
- `test_scenarios.py` — 汇总一致性、单笔失败隔离、冲击后非法、数量缩放
- `test_portfolio.py` — 组合价值/敏感度与逐笔严格一致、单笔失败隔离不影响汇总、
  超限整体拒绝、行情绑定下冲击施加于解析后取值
- `test_market.py` — 版本发布/查询、优先级、结果可溯源、并发下同版本结果一致、
  并发发布期间旧版本读取无半更新
- `test_concurrency.py` — 多线程定价/查询/反解一致性
- `test_config.py` / `test_api.py` — 配置默认值与拒绝、HTTP 错误结构、
  行情快照与组合端点
