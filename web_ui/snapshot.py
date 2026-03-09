from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Callable, Dict, Optional, Tuple


@dataclass(frozen=True)
class CallResult:
    ok: bool
    data: Any = None
    error: Optional[str] = None
    seconds: Optional[float] = None


def _summarize_value(v: Any) -> Dict[str, Any]:
    if isinstance(v, list):
        return {"type": "list", "len": len(v)}
    if isinstance(v, dict):
        return {"type": "dict", "keys": list(v.keys())[:50], "keys_truncated": len(v.keys()) > 50}
    if v is None:
        return {"type": "null"}
    return {"type": type(v).__name__}


def _try_extract_security_id(*values: Any) -> Optional[int]:
    """
    Attempts to extract a security/stock id from common NEPSE response shapes.
    Works with floorsheet-like dicts and list-of-dicts (live market, today price, etc).
    """
    candidates = ("securityId", "stockId", "id", "security_id", "stock_id")

    def scan_obj(obj: Any) -> Optional[int]:
        if isinstance(obj, dict):
            for k in candidates:
                if k in obj and obj[k] is not None:
                    try:
                        return int(obj[k])
                    except Exception:
                        pass
            # floorsheet schema: {"floorsheets": {"content": [ ... ]}}
            for nested_key in ("floorsheets", "content", "data", "items", "result"):
                if nested_key in obj:
                    got = scan_obj(obj[nested_key])
                    if got is not None:
                        return got
            return None
        if isinstance(obj, list):
            for item in obj[:50]:
                got = scan_obj(item)
                if got is not None:
                    return got
            return None
        return None

    for v in values:
        got = scan_obj(v)
        if got is not None:
            return got
    return None


def _safe_call(name: str, fn: Callable[[], Any]) -> Tuple[str, CallResult]:
    started = datetime.now()
    try:
        data = fn()
        return (
            name,
            CallResult(ok=True, data=data, seconds=(datetime.now() - started).total_seconds()),
        )
    except Exception as e:
        return (
            name,
            CallResult(ok=False, error=f"{type(e).__name__}: {e}", seconds=(datetime.now() - started).total_seconds()),
        )


def fetch_all(api: Any, *, index_id: int = 58, days_back: int = 30) -> Dict[str, Any]:
    """
    Calls every public NepseAPI method once and returns a single JSON-serializable snapshot.
    """
    meta: Dict[str, Any] = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "index_id": index_id,
        "days_back": days_back,
    }

    results: Dict[str, Dict[str, Any]] = {}

    # Order matters a bit: floorsheet/today_price/live_market help us pick a security_id for other calls.
    ordered_calls = [
        ("get_market_status", lambda: api.get_market_status()),
        ("get_market_summary", lambda: api.get_market_summary()),
        ("get_nepse_index", lambda: api.get_nepse_index()),
        ("get_indices", lambda: api.get_indices()),
        ("get_sector_wise", lambda: api.get_sector_wise()),
        ("get_supply_demand", lambda: api.get_supply_demand()),
        ("get_market_cap_by_date", lambda: api.get_market_cap_by_date()),
        ("get_trading_average", lambda: api.get_trading_average(120)),
        ("get_market_history", lambda: api.get_market_history()),
        ("get_news", lambda: api.get_news()),
        ("get_company_list", lambda: api.get_company_list()),
        ("get_security_classification", lambda: api.get_security_classification()),
        ("get_top_gainers", lambda: api.get_top_gainers()),
        ("get_top_losers", lambda: api.get_top_losers()),
        ("get_top_turnover", lambda: api.get_top_turnover()),
        ("get_top_volume", lambda: api.get_top_volume()),
        ("get_top_transaction", lambda: api.get_top_transaction()),
        ("get_price_volume", lambda: api.get_price_volume()),
        ("get_today_price", lambda: api.get_today_price(page=0, size=500)),
        ("get_floorsheet", lambda: api.get_floorsheet(page=0, size=500)),
        ("get_live_market", lambda: api.get_live_market()),
        ("get_sector_live_indices", lambda: api.get_sector_live_indices()),
        ("get_index_history", lambda: api.get_index_history(index_id)),
        ("get_index_graph", lambda: api.get_index_graph(index_id)),
        ("get_broker_list", lambda: api.get_broker_list()),
        ("get_stock_dealers", lambda: api.get_stock_dealers()),
        ("get_promoter_share", lambda: api.get_promoter_share()),
    ]

    raw_data_for_id: Dict[str, Any] = {}
    for name, thunk in ordered_calls:
        k, cr = _safe_call(name, thunk)
        payload: Dict[str, Any] = {"ok": cr.ok, "seconds": cr.seconds}
        if cr.ok:
            payload["data"] = cr.data
            payload["summary"] = _summarize_value(cr.data)
            raw_data_for_id[name] = cr.data
        else:
            payload["error"] = cr.error
        results[k] = payload

    # Pick a sample security id for security-specific methods.
    sample_security_id = _try_extract_security_id(
        raw_data_for_id.get("get_floorsheet"),
        raw_data_for_id.get("get_today_price"),
        raw_data_for_id.get("get_live_market"),
        raw_data_for_id.get("get_company_list"),
    )
    meta["sample_security_id"] = sample_security_id

    # Security-specific calls (depend on selected security_id)
    if sample_security_id is not None:
        end = date.today()
        start = end - timedelta(days=days_back)
        start_s = start.isoformat()
        end_s = end.isoformat()
        meta["trading_history_range"] = {"start": start_s, "end": end_s}

        security_calls = [
            ("get_security_details", lambda: api.get_security_details(sample_security_id)),
            ("get_price_volume_history", lambda: api.get_price_volume_history(sample_security_id)),
            ("get_trading_history", lambda: api.get_trading_history(sample_security_id, start_s, end_s)),
            ("get_market_graph_data", lambda: api.get_market_graph_data(sample_security_id)),
        ]
        for name, thunk in security_calls:
            k, cr = _safe_call(name, thunk)
            payload = {"ok": cr.ok, "seconds": cr.seconds}
            if cr.ok:
                payload["data"] = cr.data
                payload["summary"] = _summarize_value(cr.data)
            else:
                payload["error"] = cr.error
            results[k] = payload
    else:
        for name in ("get_security_details", "get_price_volume_history", "get_trading_history", "get_market_graph_data"):
            results[name] = {
                "ok": False,
                "skipped": True,
                "error": "Skipped because a sample security id could not be extracted from other responses.",
            }

    return {"meta": meta, "results": results}

