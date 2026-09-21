# 期权定价与风险情景引擎

本地可运行的期权定价与风险情景引擎：同一批合约在多种定价途径、隐含波动率反解、
曲面查询与压力情景下结论一致、可解释且可复现。

## 运行

```bash
uv sync                                # 安装依赖
uv run uvicorn main:app --reload       # 启动 HTTP 服务
uv run pytest tests/ -q                # 运行测试
```

## 定价约定

- 模型：Black-Scholes（含连续股息率 `dividend_yield`），欧式期权。
- 时间：年化（`maturity=1.0` 表示 1 年），必须 `> 0`。
- 波动率、利率、股息率均为小数形式（`0.2` = 20%），波动率允许为 0（退化为
  确定性漂移下的贴现内在价值）。
- 希腊字母：`vega`/`rho` 对应 1.00（100%）的波动率/利率变动，`theta` 为每年。
- 所有价格与希腊字母均乘以 `quantity`。

### 两种独立定价途径

| 途径 | 说明 |
|---|---|
| `black_scholes` | 解析解，希腊字母为解析公式 |
| `binomial` | CRR 二叉树；delta/gamma/theta 由树节点计算，vega/rho 为中心差分 |
| `cross_check`（默认） | 两种途径互相校验：相对差异 ≤ 容差时返回解析解并附 `cross_check_diff`；超差时**明确报出分歧**（`pricing_divergence`），绝不静默选取其一 |

### 容差设定

- `cross_check_tolerance = 0.02`（2% 相对容差）：欧式期权下 CRR 树以
  O(1/√steps) 收敛到解析解，200 步时典型差异 < 0.1%，2% 足以吸收树收敛
  误差同时能发现真实实现分歧。
- `iv_tolerance = 1e-8`：隐含波动率反解的价格收敛容差（二分法）。

## 输入校验

以下输入一律拒绝并返回可区分的 `reason`，**不用默认值兜底**：

| reason | 含义 |
|---|---|
| `non_positive_maturity` | 到期时间 ≤ 0 |
| `negative_volatility` | 波动性 < 0 |
| `non_positive_spot` / `non_positive_strike` | 标的价格/行权价 ≤ 0 |
| `invalid_field_<name>` | 字段缺失、布尔或 NaN/Inf |
| `invalid_option_type` / `invalid_pricing_method` | 未知枚举值 |
| `pricing_divergence` | 两种途径结果超差 |
| `price_below_no_arbitrage_bound` / `price_above_no_arbitrage_bound` | 目标价超出无套利边界，隐含波动率无解 |
| `not_converged` | 反解迭代次数用尽 |
| `surface_out_of_range` | 曲面查询点超出网格范围（不静默外推） |
| `limit_exceeded` | 批量规模超限（策略：整体拒绝，无部分结果残留） |
| `config_error` | 配置取值不支持（启动时拒绝） |

批量定价与情景重估中，单笔失败以 `errors[]` 返回（含 `contract_id`、`reason`、
`message`），不影响其余笔；情景汇总值严格等于成功逐笔结果之和。

## 曲面

`Surface(maturities, strikes, values)`：规则网格 + 双线性插值。网格必须严格
递增、至少 2×2、值有限；查询点在范围内（含边界）连续插值，范围外抛
`surface_out_of_range`。曲面构造后不可变，重复查询结果逐位稳定。

## 配置项

通过环境变量 `OPTION_ENGINE_CONFIG`（JSON）注入；未设置或键缺失时取以下
文档化默认值；未知键或非法取值在启动时抛 `config_error`：

| 键 | 默认值 | 说明 |
|---|---|---|
| `max_batch_size` | 1000 | 单次批量/情景请求最大合约数（≥1） |
| `binomial_steps` | 200 | 二叉树步数（≥2） |
| `cross_check_tolerance` | 0.02 | 两途径相对容差（>0） |
| `iv_max_iterations` | 200 | 反解最大迭代次数（≥1） |
| `iv_tolerance` | 1e-8 | 反解价格容差（>0） |
| `iv_max_vol` | 5.0 | 反解波动率搜索上界（>0） |
| `on_limit_exceeded` | `"reject"` | 超限策略，当前仅支持 `"reject"` |

## 并发与状态

`PricingEngine` 仅持有不可变配置，不保存任何跨请求可变状态；合约、快照、
曲面均为不可变对象。多请求并发访问同一引擎/快照/曲面结果逐位一致，
无半更新状态、无缓存漂移（引擎不引入缓存）。

## HTTP 接口

- `GET /`：兼容性根路径。
- `POST /price`：`{contracts: {id: {...}}, method?, snapshot?}` → 价格 + 希腊字母 + 部分失败信息。
- `POST /implied-vol`：`{contract, target_price}` → 隐含波动率或 400 + reason。
- `POST /surface/query`：`{maturities, strikes, values, maturity, strike}` → 插值或 400。
- `POST /scenario`：`{contracts, shocks: [{spot_shift?, vol_shift?, rate_shift?}], method?}`
  → 每个冲击组合的重估结果与汇总（total_value/delta/gamma/vega）。
  `spot_shift` 为相对偏移（0.1 = +10%），`vol_shift`/`rate_shift` 为绝对偏移。

## 验证方法

```bash
uv run pytest tests/ -q
```

测试以边界与异常链路为主：非法参数（`test_validation.py`）、双途径一致性与
分歧报出（`test_pricing.py`）、反解失败（`test_implied_vol.py`）、曲面外查询
与插值连续性（`test_surface.py`）、情景内单笔失败与汇总一致性
（`test_scenario.py`）、并发调用逐位一致（`test_concurrency.py`）、配置拒绝
（`test_config.py`）、HTTP 边界（`test_api.py`）。断言失败时打印输入与判定依据。
