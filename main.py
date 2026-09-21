"""期权定价与风险情景引擎 HTTP 接口。

既有接口兼容：GET / 保持原样。
"""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.config import load_settings
from app.engine import PricingEngine
from app.errors import PricingError
from app.implied_vol import implied_vol
from app.models import (BatchPriceRequest, ImpliedVolRequest, PriceRequest,
                        ScenarioRequest)
from app.scenarios import run_scenarios
from app.surface import Surface

@asynccontextmanager
async def lifespan(app: FastAPI):
    # 配置在启动时加载并校验；非法取值在此即拒绝
    settings = load_settings()
    app.state.settings = settings
    app.state.engine = PricingEngine(settings)
    # 默认示例曲面（不可变，供并发查询）；生产环境可由配置/行情源替换
    app.state.vol_surface = Surface(
        maturities=[0.25, 0.5, 1.0, 2.0],
        strikes=[80.0, 90.0, 100.0, 110.0, 120.0],
        values=[
            [0.24, 0.21, 0.19, 0.20, 0.23],
            [0.25, 0.22, 0.20, 0.21, 0.24],
            [0.27, 0.24, 0.22, 0.23, 0.25],
            [0.29, 0.26, 0.24, 0.25, 0.27],
        ],
        name="vol",
    )
    app.state.rate_surface = Surface(
        maturities=[0.25, 0.5, 1.0, 2.0],
        strikes=[80.0, 90.0, 100.0, 110.0, 120.0],
        values=[[0.03] * 5, [0.032] * 5, [0.035] * 5, [0.038] * 5],
        name="rate",
    )
    yield


app = FastAPI(title="Option Pricing & Risk Engine", lifespan=lifespan)


@app.exception_handler(PricingError)
async def pricing_error_handler(_: Request, exc: PricingError) -> JSONResponse:
    return JSONResponse(status_code=422, content={"error": exc.to_dict()})


@app.get("/")
def read_root():
    return {"Hello": "World"}


@app.post("/price")
def price(req: PriceRequest):
    engine: PricingEngine = app.state.engine
    return engine.price_one(req.contract, req.method)


@app.post("/price/batch")
def price_batch(req: BatchPriceRequest):
    engine: PricingEngine = app.state.engine
    return engine.price_batch(req.contracts, req.method)


@app.post("/implied-vol")
def solve_implied_vol(req: ImpliedVolRequest):
    settings = app.state.settings
    iv, iterations, residual = implied_vol(
        req.option_type, req.spot, req.strike, req.time_to_maturity,
        req.risk_free_rate, req.dividend_yield, req.target_price,
        settings.max_iv_iterations, settings.iv_tolerance)
    return {"implied_vol": iv, "iterations": iterations, "residual": residual}


@app.get("/surface/{kind}")
def query_surface(kind: str, maturity: float, strike: float):
    if kind == "vol":
        surface: Surface = app.state.vol_surface
    elif kind == "rate":
        surface = app.state.rate_surface
    else:
        from app.errors import ErrorCode
        raise PricingError(ErrorCode.VALIDATION, f"未知曲面类型: {kind!r}，支持 vol/rate",
                           {"kind": kind})
    return {"kind": kind, "maturity": maturity, "strike": strike,
            "value": surface.query(maturity, strike)}


@app.post("/scenarios")
def scenarios(req: ScenarioRequest):
    engine: PricingEngine = app.state.engine
    return run_scenarios(engine, req.name, req.contracts, req.shocks, req.method)
