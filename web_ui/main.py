from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from fastapi import FastAPI, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from nepse_core.nepse_api import NepseAPI
from web_ui.snapshot import fetch_all
from web_ui.predict import fetch_price_history, list_algorithms, predict_next_candle


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data" / "snapshots"
DATA_DIR.mkdir(parents=True, exist_ok=True)

_companies_cache: Optional[Tuple[datetime, List[Dict[str, Any]]]] = None
_companies_cache_ttl = timedelta(hours=6)

app = FastAPI(title="NEPSE Live Dashboard", version="0.1.0")

templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
app.mount("/static", StaticFiles(directory=str(Path(__file__).parent / "static")), name="static")


@app.get("/", response_class=HTMLResponse)
def index(request: Request) -> Any:
    return templates.TemplateResponse("index.html", {"request": request})


@app.get("/api/health")
def health() -> Dict[str, Any]:
    return {"ok": True, "time": datetime.now().isoformat(timespec="seconds")}


@app.get("/api/companies")
def companies(verify_ssl: bool = Query(False)) -> Dict[str, Any]:
    """
    Lightweight company/security list for UI selection.
    """
    global _companies_cache
    now = datetime.now()
    if _companies_cache is not None:
        ts, cached = _companies_cache
        if now - ts <= _companies_cache_ttl:
            return {"ok": True, "cached": True, "count": len(cached), "data": cached}

    api = NepseAPI(verify_ssl=verify_ssl)
    raw = api.get_company_list()
    # Reduce payload size: keep only the fields the UI needs.
    data: List[Dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        data.append(
            {
                "id": item.get("id") or item.get("securityId") or item.get("stockId"),
                "symbol": item.get("symbol") or item.get("stockSymbol") or item.get("companyShortName"),
                "name": item.get("securityName") or item.get("companyName") or item.get("name"),
                "sector": item.get("sectorName") or item.get("sector") or item.get("sectorMaster", {}).get("sectorDescription"),
            }
        )

    # Remove rows without an id or symbol.
    data = [d for d in data if d.get("id") and d.get("symbol")]
    _companies_cache = (now, data)
    return {"ok": True, "cached": False, "count": len(data), "data": data}


@app.get("/api/candles")
def candles(
    security_id: int = Query(..., description="NEPSE security id (e.g. 8122)."),
    page: int = Query(0, ge=0, description="Page number for NEPSE price history (0=newest page)."),
    size: int = Query(200, ge=1, le=500, description="Page size for NEPSE price history."),
    verify_ssl: bool = Query(False),
) -> Dict[str, Any]:
    """
    Candlestick-ready data from `get_price_volume_history(security_id)`.

    Output format matches TradingView Lightweight Charts:
    - candles: [{time:UTCTimestamp, open, high, low, close}]
    - volume:  [{time:UTCTimestamp, value}]
    """
    api = NepseAPI(verify_ssl=verify_ssl)
    resp = api.get_price_volume_history(security_id, page=page, size=size)
    content = (resp or {}).get("content") if isinstance(resp, dict) else None
    if not isinstance(content, list):
        return {"ok": False, "error": "Unexpected response shape (missing `content` list).", "raw": resp}

    def to_utc_ts(date_s: str) -> int:
        # businessDate is YYYY-MM-DD; convert to UTC midnight timestamp (seconds).
        dt = datetime.strptime(date_s, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        return int(dt.timestamp())

    candles_out: List[Dict[str, Any]] = []
    volume_out: List[Dict[str, Any]] = []
    for row in content:
        if not isinstance(row, dict):
            continue
        dt = row.get("businessDate")
        if not dt:
            continue
        ts = to_utc_ts(str(dt))
        o = row.get("openPrice")
        h = row.get("highPrice")
        l = row.get("lowPrice")
        c = row.get("closePrice")
        v = row.get("totalTradedQuantity")
        if o is None or h is None or l is None or c is None:
            continue
        candles_out.append({"time": ts, "open": float(o), "high": float(h), "low": float(l), "close": float(c)})
        if v is not None:
            volume_out.append({"time": ts, "value": float(v)})

    # API seems to return newest-first; chart wants ascending.
    candles_out.sort(key=lambda x: x["time"])
    volume_out.sort(key=lambda x: x["time"])

    meta = {
        "security_id": security_id,
        "page": page,
        "size": size,
        "last": (resp or {}).get("last") if isinstance(resp, dict) else None,
        "total_pages": (resp or {}).get("totalPages") if isinstance(resp, dict) else None,
        "total_elements": (resp or {}).get("totalElements") if isinstance(resp, dict) else None,
        "points": len(candles_out),
        "ts_min": candles_out[0]["time"] if candles_out else None,
        "ts_max": candles_out[-1]["time"] if candles_out else None,
    }
    return {"ok": True, "meta": meta, "candles": candles_out, "volume": volume_out}


@app.get("/api/snapshot")
def snapshot(
    download: bool = Query(False, description="If true, returns as a downloadable JSON file."),
    verify_ssl: bool = Query(False, description="Verify SSL certs (nepalstock.com can be flaky)."),
    index_id: int = Query(58, description="Index id for index history/graph calls."),
    days_back: int = Query(30, ge=1, le=365, description="Days back for get_trading_history()."),
) -> Response:
    api = NepseAPI(verify_ssl=verify_ssl)
    data = fetch_all(api, index_id=index_id, days_back=days_back)

    if download:
        content = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
        filename = f"nepse_snapshot_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        return Response(
            content,
            media_type="application/json",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
    return JSONResponse(data)


@app.post("/api/snapshot/save")
def snapshot_save(
    verify_ssl: bool = Query(False),
    index_id: int = Query(58),
    days_back: int = Query(30, ge=1, le=365),
) -> Dict[str, Any]:
    api = NepseAPI(verify_ssl=verify_ssl)
    data = fetch_all(api, index_id=index_id, days_back=days_back)
    fname = f"nepse_snapshot_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    out_path = DATA_DIR / fname
    out_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"ok": True, "path": str(out_path), "meta": data.get("meta", {})}


@app.get("/api/snapshots")
def list_snapshots(limit: int = Query(50, ge=1, le=500)) -> Dict[str, Any]:
    files = sorted(DATA_DIR.glob("nepse_snapshot_*.json"), key=os.path.getmtime, reverse=True)[:limit]
    return {
        "ok": True,
        "count": len(files),
        "files": [{"name": f.name, "path": str(f), "bytes": f.stat().st_size} for f in files],
    }


@app.get("/api/predict")
def predict(
    security_id: int = Query(..., description="NEPSE security id."),
    algo: str = Query("rf", description="Algorithm id. See /api/algos."),
    max_pages: int = Query(10, ge=1, le=60, description="How many pages of history to fetch for training."),
    page_size: int = Query(200, ge=10, le=500, description="NEPSE page size for history fetch."),
    verify_ssl: bool = Query(False),
) -> Dict[str, Any]:
    api = NepseAPI(verify_ssl=verify_ssl)
    rows = fetch_price_history(api, security_id, max_pages=max_pages, page_size=page_size)
    out = predict_next_candle(rows, algo=algo)
    out["security_id"] = security_id
    out["history_range"] = {
        "min_time": rows[0].t if rows else None,
        "max_time": rows[-1].t if rows else None,
        "rows": len(rows),
    }
    return out


@app.get("/api/algos")
def algos() -> Dict[str, Any]:
    return {"ok": True, "data": list_algorithms()}

