from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from .market_data import get_history
from .strategy import analyze, backtest
from .universe import get_universe

app = FastAPI(title="Stock Research Lab API", version="0.1.0")
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173","http://127.0.0.1:5173"],
    allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

@app.get("/api/health")
def health(): return {"status":"ok"}

@app.get("/api/analysis/{ticker}")
def analysis(ticker: str, period: str=Query("2y", pattern="^(6mo|1y|2y|5y|10y|max)$")):
    try: return analyze(ticker.upper(), get_history(ticker, period))
    except ValueError as e: raise HTTPException(404, str(e))
    except Exception as e: raise HTTPException(502, f"Market data/analysis error: {e}")

@app.post("/api/backtest/{ticker}")
def run_backtest(ticker: str, period: str=Query("5y", pattern="^(1y|2y|5y|10y|max)$"),
    initial_cash: float=Query(20000, gt=0, le=1000000),
    risk_fraction: float=Query(.005, gt=0, le=.02),
    fee_bps: float=Query(2, ge=0, le=100), slippage_bps: float=Query(5, ge=0, le=200)):
    try: return backtest(ticker.upper(), get_history(ticker, period), initial_cash, risk_fraction, fee_bps, slippage_bps)
    except ValueError as e: raise HTTPException(404, str(e))
    except Exception as e: raise HTTPException(502, f"Backtest error: {e}")


@app.get("/api/universe")
def stock_universe(
    asset_type: str = Query("stock", pattern="^(stock|etf|all)$"),
    search: str = Query("", max_length=100),
    limit: int = Query(500, ge=1, le=10000),
    offset: int = Query(0, ge=0),
    refresh: bool = Query(False),
):
    """List active US-listed common stocks and ETFs from Nasdaq Trader directories."""
    try:
        rows = get_universe(force_refresh=refresh)
        if asset_type != "all":
            rows = [r for r in rows if r["asset_type"] == asset_type]
        query = search.strip().casefold()
        if query:
            rows = [r for r in rows if query in r["symbol"].casefold() or query in r["name"].casefold()]
        return {"count": len(rows), "offset": offset, "limit": limit,
                "source": "Nasdaq Trader symbol directories",
                "cache_hours": 24, "results": rows[offset:offset + limit]}
    except Exception as e:
        raise HTTPException(502, f"Could not load US symbol directory: {e}")
