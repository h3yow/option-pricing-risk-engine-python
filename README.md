# 期权定价与风险情景引擎

本地可运行的欧式期权定价与风险情景引擎（FastAPI）。同一批合约在多种定价途径、
隐含波动率反解、曲面查询与压力情景下结论一致、可解释、可复现。

## 运行

```bash
uv run uvicorn main:app --reload   # 服务
uv run pytest                      # 测试（128 个用例）
```

## 定价约定

- 模型：Black-Scholes，连续复利，`dividend_yield` 为连续股息率，时间以年计。
- 希腊字母约定：`vega`/`rho` 为每 1 个百分点（0.01）变动，`theta` 为每日历日（1/365 年）。
- 零波动率为合法输入，价格退化为折现内在价值；到期时间必须为正，波动率不得为负。
- 蒙特卡洛途径：几何布朗运动风险中性模拟，期末一步精确离散化（无时间步误差），
  **分层抽样（stratified sampling）降方差**：把标准正态划分为 N 个等概率层，
  每层一个统一随机偏移样本；每次调用使用独立 `random.Random(seed)`（仅取一个
  均匀偏移），无跨请求共享状态，结果可复现。深度实值/深度虚值、短到期等尾部
  主导情形下标准误远小于旧的对偶变量抽样。

### 两种独立定价途径

| method | 说明 |
|---|---|
| `analytic` | Black-Scholes 闭式解 |
| `monte_carlo` | 蒙特卡洛模拟（固定种子，可复现） |
| `cross_check` | 默认。两种途径同时计算，相对差 ≤ 容差时返回解析解；超差抛 `METHOD_DIVERGENCE`，绝不静默选取其一 |

**容差设定**：默认相对容差 2%（`OPE_CROSS_CHECK_TOLERANCE`），按 `|a − m| / max(|a|, 1)` 计算。分层抽样的
50,000 条路径在**常见参数范围**（看涨/看跌，K/S 从 0.5 深度实值到 2.0 深度
虚值，T 从 1 天到 3 年，σ ∈ [5%, 80%]，r ∈ {0, 5%}，共 1,120 个参数点）
内相对误差全部远低于容差（实测最大 < 0.1%），正常合约不会误报；**默认设置
保证该范围内批量 `cross_check` 整体成功**。仅当请求方显式把容差收紧到确实
无法满足（如 1e-9）时才返回 `METHOD_DIVERGENCE`，并在 `detail` 中一并给出
`analytic`/`monte_carlo` 双方数值、实际 `rel_diff`、所用 `tolerance` 及判定
依据（`criterion="rel_diff > tolerance"`，另附 `rel_diff_formula` 说明相对差
算式），方便区分是参数问题还是途径问题。解析价在
深度虚值相消产生约 1e-16 的负值时按经济下界钳为 0，不影响平价等数值关系。

### 请求级一致性容差（`cross_check_tolerance`）

三个定价/重估入口的请求体都接受可选字段 `cross_check_tolerance`（正的有限
数值）：`POST /price`、`POST /price/batch`、`POST /scenarios`，以及
`POST /scenarios/portfolio`。

- **不传（默认）**：沿用环境变量 `OPE_CROSS_CHECK_TOLERANCE` 的默认 2%，
  行为与历史完全一致。
- **显式传入**：本次请求（批量为每一笔、情景为基准与每个冲击后的重估）
  一律以该值参与判定，与全局配置无关；实际所用值会原样出现在分歧错误的
  `detail.tolerance` 中。收紧到确实无法满足时如实返回 `METHOD_DIVERGENCE`，
  并附双方数值、实际相对差、容差与判定依据；批量/情景下该失败按既有规则
  逐笔（腿级）隔离，不影响其余合约。
- **非法取值明确拒绝**：非数值（字符串/布尔/对象等）、NaN/Inf、零或负数
  在请求解析阶段即以 `VALIDATION`（`detail.field=cross_check_tolerance`）
  拒绝，**绝不当作没传悄悄按默认处理**。
- 仅在 `method=cross_check` 时参与判定；对 `analytic`/`monte_carlo` 传该
  字段合法但不产生作用。

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

## 组合级情景重估（`POST /scenarios/portfolio`）

在单笔/合约列表情景之上新增组合编排层，面向日常组合风控流程：

- 一次提交多笔合约（含 `quantity`，可为负，如空头/对冲腿）与一组行情冲击。
- 每个冲击返回：
  - 逐笔 `legs[]`：基准价值、冲击后价值、PnL、按持仓量加权的希腊字母，以及
    该笔实际使用的行情版本 `market_version`；
  - 组合汇总 `total_base_value` / `total_value` / `total_pnl` 与组合
    `greeks`（delta、gamma、vega、theta、rho）。
- **严格一致性**：汇总由同一份成功腿列表直接求和构造（同一套数据，不是二次
  计算），测试逐字段断言汇总 = Σ 成功腿（价值/PnL 容差 1e-9，希腊字母
  1e-12）。基准组合价值在响应顶层 `base_total_value` 给出。
- **单笔失败隔离**：基准就算不出来的腿在每个冲击下带同一原因失败；仅在某个
  冲击后才非法的腿只在该冲击失败。失败腿不参与汇总，且带可区分错误码
  （如 `VALIDATION` / `MARKET_DATA_MISSING`），同一冲击下其余合约照常计价。
- **超限整体拒绝**：合约数超过 `OPE_MAX_BATCH_SIZE` 或冲击数超过
  `OPE_MAX_SCENARIOS` 时，在任何计算之前抛 `LIMIT_EXCEEDED`，不残留部分
  计算结果，引擎/仓库可继续服务后续请求。

## 行情版本绑定与复用（`POST /market/versions`）

- `MarketSnapshot`（`snapshot_id, spot, risk_free_rate, dividend_yield,
  volatility?`）是 **pydantic 冻结模型**；经 `POST /market/versions` 发布后
  存入内存版本仓库，只增不改。
- 重复发布：内容相同幂等成功；版本相同但内容不同 → `VERSION_CONFLICT`，
  绝不覆盖。引用未发布版本 → `VERSION_NOT_FOUND`（请求级失败）。
- **支持绑定的入口**：`POST /scenarios/portfolio`、`POST /price/batch`、
  `POST /scenarios` 均接受请求级 `market_version` 与 `precedence`。三个入口
  在开始任何计算之前先从仓库取出（不可变）快照：同一请求内所有合约、
  基准与全部冲击共享同一份行情状态。`POST /price` 为单笔无状态接口，
  不绑定版本。
- **每笔结果标注实际版本**：
  - 批量：响应顶层与每个 `results[]` 元素都回带 `market_version`；
  - 情景（`POST /scenarios`）：响应顶层与每条 `legs[]` 回带
    `market_version`（基准与冲击后的腿标注相同版本）；
  - 组合：每条 `legs[]` 回带 `market_version`（原有行为）。
  - 未绑定版本时该字段为 `null`，顶层 `precedence` 为 `"contract"`。
- 取值优先级由请求级 `precedence` 控制（三个入口语义一致）：
  - `contract`（**默认，兼容既有行为**）：合约自带字段优先，绑定仅作为版本
    标注并保证该请求共享同一版本；
  - `snapshot`：版本行情覆盖 `spot / risk_free_rate / dividend_yield /
    volatility`，情景冲击在**解析后的版本行情**上叠加。版本缺少
    `volatility` 时该笔以 `MARKET_DATA_MISSING` 逐笔（腿级）失败
    （`detail.field=volatility`，失败结果仍标注所引用的版本），不影响同一
    请求内其余合约。
- **引用不存在版本整体拒绝**：在任何逐笔计算之前抛 `VERSION_NOT_FOUND`，
  HTTP 422，响应中不含任何逐笔/逐冲击结果，不残留部分计算结果；仓库与引擎
  可继续服务后续请求。
- **一致性与无半更新**：仓库用锁保护字典读写，读路径在锁内取出不可变快照
  引用后立即释放；定价全程面对冻结对象。无论先发还是并发发出的多个请求，
  只要引用同一版本，结果（含浮点值）完全一致（测试逐位比较
  `model_dump()`，覆盖批量、情景与组合三个入口）；任何请求都不会读到被
  中途改动的半更新行情。

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
| `LIMIT_EXCEEDED` | 批量/情景/组合规模超上限（整体拒绝，不残留部分结果） |
| `CONFIG` | 配置取值不支持（启动时拒绝） |
| `UNKNOWN_METHOD` | 未知定价途径 |
| `VERSION_NOT_FOUND` | 引用的行情版本不存在（请求级失败） |
| `VERSION_CONFLICT` | 同名行情版本以不同内容重复发布（版本不可变，拒绝覆盖） |
| `MARKET_DATA_MISSING` | `snapshot` 优先级下版本缺少记价字段（腿级隔离失败） |

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

引擎对定价无状态：不缓存合约级结果，每次调用独立计算；曲面构造后不可变；
蒙特卡洛每次调用使用独立 RNG（分层抽样）。行情版本仓库以锁保护字典、存的是
冻结快照，发布只增不改；各入口在任何计算之前一次性取出不可变快照引用，
定价全程无锁。多线程并发定价/查询/反解/引用同一行情版本的批量、情景与组合
重估结果与单线程逐位一致（见 `tests/test_concurrency.py`）。

> 行情版本保存在进程内存中，服务重启后需重新发布；当前版本面向单进程部署，
> 多实例部署需在外部统一行情源/版本仓库（兼容范围说明）。

## 接口

- `GET /` — 兼容原有接口
- `POST /price` — 单笔定价（含希腊字母），支持请求级 `cross_check_tolerance`
- `POST /price/batch` — 批量定价，逐笔隔离失败；支持请求级
  `cross_check_tolerance` 与 `market_version`/`precedence` 版本绑定
- `POST /implied-vol` — 隐含波动率反解
- `GET /surface/{vol|rate}?maturity=&strike=` — 曲面查询
- `POST /scenarios` — 情景冲击批量重估与汇总；支持请求级
  `cross_check_tolerance` 与 `market_version`/`precedence` 版本绑定
- `POST /market/versions` — 发布（冻结）一个行情版本；同内容幂等、异内容冲突
- `POST /scenarios/portfolio` — 组合级情景重估：逐笔 + 组合价值/敏感度汇总 +
  行情版本绑定与优先级（同样支持请求级 `cross_check_tolerance`）

## 兼容性

- 既有 `POST /scenarios`、`POST /price*`、`POST /implied-vol`、
  `GET /surface/*`、`GET /` 的请求/响应结构不变：新增字段全部为**可选**，
  新增响应字段（批量/情景响应及逐笔结果上的 `market_version`、顶层
  `precedence`）在未绑定时取确定默认值（`null` / `"contract"`），老客户端
  忽略新字段即可，行为与既有完全一致。
- `MarketSnapshot` 的 `volatility` 为可选字段；`precedence` 默认 `contract`，
  不绑定版本时批量/情景/组合均与既有合约列表情景一致。
- 蒙特卡洛由对偶变量改为分层抽样，属于同一独立途径的内部方差缩减，固定种子
  仍逐位可复现；默认 2% 容差不变，但正常参数范围内的可用性显著增强。

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
- `test_portfolio.py` — 组合价值/敏感度汇总与逐笔严格一致、加权希腊字母、
  基准失败与冲击失败分别隔离、版本优先级、字段缺失逐笔失败、未知版本与
  超限整体拒绝且无残留
- `test_request_tolerance.py` — 请求级容差默认/收紧两条路径：单笔/批量/
  情景/组合显式收紧后如实分歧并附双方数值、实际容差与判定依据；非法取值
  （字符串/布尔/0/负/NaN/Inf）在请求层以 `VALIDATION` 拒绝、不静默默认
- `test_market_binding.py` / `test_market_binding_api.py` — 批量定价与情景
  重估在服务层与 HTTP 层的版本绑定、两种优先级、逐笔版本标注、未绑定默认
  不变、缺 `volatility` 腿级隔离失败、引用不存在版本整体拒绝（422 且无
  逐笔结果、无残留）
- `test_portfolio_api.py` — 版本发布冻结/幂等/冲突、组合端点、默认优先级、
  错误结构、既有接口不回归
- `test_concurrency.py` — 多线程定价/查询/反解一致；同一行情版本在批量、
  情景、组合三个入口的先发与并发请求下逐位一致；并发覆盖发布全部被拒、
  既有读取不漂移、无半更新
- `test_defaults.py` — 默认设置下常见参数范围（深度实值/虚值 × 短/长到期等
  1,120 点及 256 笔批量）`cross_check` 整体成功；仅显式收紧容差才报分歧，
  且分歧结果附双方数值、容差与判定依据
- `test_config.py` / `test_api.py` — 配置默认值与拒绝、HTTP 错误结构
