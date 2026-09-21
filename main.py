"""期权定价与风险情景引擎 HTTP 入口。

配置通过环境变量 OPTION_ENGINE_CONFIG（JSON 字符串）注入；
未设置时使用 EngineConfig 的文档化默认值。非法配置在启动时拒绝。
"""

from __future__ import annotations

import json
import os
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from option_engine import (
    EngineConfig,
    EngineError,
    MarketSnapshot,
    OptionContract,
    OptionType,
    PricingEngine,
    PricingMethod,
    ValidationError,
)
from option_engine.scenario import Shock
from option_engine.surface import Surface


def _load_config() -> EngineConfig:
    raw = os.environ.get("OPTION_ENGINE_CONFIG")
    if raw is None:
        return EngineConfig()
    return EngineConfig.from_dict(json.loads(raw))


engine = PricingEngine(_load_config())
app = FastAPI(title="option-pricing-risk-engine")


@app.get("/")
def read_root():
    return {"Hello": "World"}


class ContractIn(BaseModel):
    option_type: str
    spot: float
    strike: float
    maturity: float
    volatility: float
    rate: float
    dividend_yield: float = 0.0
    quantity: float = 1.0


class PriceRequest(BaseModel):
    contracts: dict[str, ContractIn]
    method: str = "cross_check"
    snapshot: dict[str, Any] | None = None


class ImpliedVolRequest(BaseModel):
    contract: ContractIn
    target_price: float


class SurfaceQueryRequest(BaseModel):
    maturities: list[float]
    strikes: list[float]
    values: list[list[float]]
    maturity: float
    strike: float


class ScenarioRequest(BaseModel):
    contracts: dict[str, ContractIn]
    shocks: list[dict[str, float]]
    method: str = "black_scholes"


def _to_contract(cid: str, c: ContractIn) -> OptionContract:
    try:
        option_type = OptionType(c.option_type)
    except ValueError:
        raise ValidationError(
            f"contract {cid!r}: unknown option_type {c.option_type!r}",
            reason="invalid_option_type",
        )
    return OptionContract(
        option_type=option_type,
        spot=c.spot,
        strike=c.strike,
        maturity=c.maturity,
        volatility=c.volatility,
        rate=c.rate,
        dividend_yield=c.dividend_yield,
        quantity=c.quantity,
    )


def _to_method(name: str) -> PricingMethod:
    try:
        return PricingMethod(name)
    except ValueError:
        raise ValidationError(
            f"unknown pricing method {name!r}", reason="invalid_pricing_method"
        )


def _to_snapshot(data: dict[str, Any] | None) -> MarketSnapshot | None:
    if data is None:
        return None
    return MarketSnapshot(
        snapshot_id=str(data.get("snapshot_id", "")),
        spots={k: float(v) for k, v in data.get("spots", {}).items()},
        vol_overrides={k: float(v) for k, v in data.get("vol_overrides", {}).items()},
        rate_overrides={k: float(v) for k, v in data.get("rate_overrides", {}).items()},
    )


@app.post("/price")
def price(req: PriceRequest):
    try:
        contracts = {cid: _to_contract(cid, c) for cid, c in req.contracts.items()}
        resp = engine.price_batch(contracts, _to_snapshot(req.snapshot), _to_method(req.method))
    except EngineError as e:
        raise HTTPException(status_code=400, detail={"reason": e.reason, "message": str(e)})
    return {
        "results": [
            {
                "contract_id": r.contract_id,
                "price": r.price,
                "method": r.method.value,
                "cross_check_diff": r.cross_check_diff,
                "greeks": vars(r.greeks),
            }
            for r in resp.results
        ],
        "errors": [vars(e) for e in resp.errors],
    }


@app.post("/implied-vol")
def implied_vol(req: ImpliedVolRequest):
    try:
        contract = _to_contract("contract", req.contract)
        iv = engine.implied_volatility(contract, req.target_price)
    except EngineError as e:
        raise HTTPException(status_code=400, detail={"reason": e.reason, "message": str(e)})
    return {"implied_volatility": iv}


@app.post("/surface/query")
def surface_query(req: SurfaceQueryRequest):
    try:
        surface = Surface(
            tuple(req.maturities),
            tuple(req.strikes),
            tuple(tuple(row) for row in req.values),
        )
        value = engine.query_vol_surface(surface, req.maturity, req.strike)
    except EngineError as e:
        raise HTTPException(status_code=400, detail={"reason": e.reason, "message": str(e)})
    return {"value": value}


@app.post("/scenario")
def scenario(req: ScenarioRequest):
    try:
        contracts = {cid: _to_contract(cid, c) for cid, c in req.contracts.items()}
        shocks = [Shock(**s) for s in req.shocks]
        results = engine.run_scenario(contracts, shocks, _to_method(req.method))
    except EngineError as e:
        raise HTTPException(status_code=400, detail={"reason": e.reason, "message": str(e)})
    return {
        "scenarios": [
            {
                "total_value": sc.total_value,
                "total_delta": sc.total_delta,
                "total_gamma": sc.total_gamma,
                "total_vega": sc.total_vega,
                "results": [
                    {"contract_id": r.contract_id, "price": r.price, "greeks": vars(r.greeks)}
                    for r in sc.results
                ],
                "errors": [vars(e) for e in sc.errors],
            }
            for sc in results
        ]
    }
