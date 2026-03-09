from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
from sklearn.ensemble import ExtraTreesClassifier, ExtraTreesRegressor, HistGradientBoostingClassifier, HistGradientBoostingRegressor
from sklearn.ensemble import RandomForestClassifier, RandomForestRegressor
from sklearn.linear_model import LogisticRegression, Ridge


@dataclass(frozen=True)
class HistoryRow:
    # UTC timestamp (seconds) for charting
    t: int
    # original date string YYYY-MM-DD (from NEPSE)
    date: str
    open: float
    high: float
    low: float
    close: float
    volume: float


def _next_day_ts(last_ts: int) -> int:
    # NEPSE trading calendar differs (Sun-Thu), but for visualization we use "next day".
    return last_ts + 86400


def _parse_ts(date_s: str) -> int:
    dt = datetime.strptime(date_s, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    return int(dt.timestamp())


def fetch_price_history(
    api: Any,
    security_id: int,
    *,
    max_pages: int = 10,
    page_size: int = 200,
) -> List[HistoryRow]:
    """
    Fetches and normalizes OHLCV rows using NepseAPI.get_price_volume_history() paging.
    Returns ascending-by-time rows, de-duplicated by timestamp.
    """
    by_t: Dict[int, HistoryRow] = {}
    for page in range(max_pages):
        resp = api.get_price_volume_history(security_id, page=page, size=page_size)
        content = resp.get("content") if isinstance(resp, dict) else None
        if not isinstance(content, list):
            break
        for r in content:
            if not isinstance(r, dict):
                continue
            d = r.get("businessDate")
            if not d:
                continue
            try:
                t = _parse_ts(str(d))
            except Exception:
                continue
            o, h, l, c = r.get("openPrice"), r.get("highPrice"), r.get("lowPrice"), r.get("closePrice")
            v = r.get("totalTradedQuantity")
            if o is None or h is None or l is None or c is None:
                continue
            row = HistoryRow(
                t=t,
                date=str(d),
                open=float(o),
                high=float(h),
                low=float(l),
                close=float(c),
                volume=float(v) if v is not None else 0.0,
            )
            by_t[t] = row

        if isinstance(resp, dict) and resp.get("last") is True:
            break

    rows = sorted(by_t.values(), key=lambda x: x.t)
    return rows


def _rolling_mean(x: np.ndarray, w: int) -> np.ndarray:
    out = np.full_like(x, fill_value=np.nan, dtype=float)
    if w <= 0 or len(x) < w:
        return out
    c = np.cumsum(np.insert(x.astype(float), 0, 0.0))
    out[w - 1 :] = (c[w:] - c[:-w]) / w
    return out


def _make_features(rows: List[HistoryRow]) -> Tuple[np.ndarray, np.ndarray]:
    """
    Build simple tree-friendly features from OHLCV history.
    Target is next-day close return (close[t+1]/close[t] - 1).
    """
    close = np.array([r.close for r in rows], dtype=float)
    open_ = np.array([r.open for r in rows], dtype=float)
    high = np.array([r.high for r in rows], dtype=float)
    low = np.array([r.low for r in rows], dtype=float)
    vol = np.array([r.volume for r in rows], dtype=float)

    ret1 = np.zeros_like(close)
    ret1[1:] = close[1:] / close[:-1] - 1.0

    rng = (high - low) / np.maximum(close, 1e-9)
    body = (close - open_) / np.maximum(close, 1e-9)
    vol_chg = np.zeros_like(vol)
    vol_chg[1:] = (vol[1:] + 1.0) / (vol[:-1] + 1.0) - 1.0

    ma5 = _rolling_mean(close, 5)
    ma10 = _rolling_mean(close, 10)
    ma20 = _rolling_mean(close, 20)
    ma5_ratio = close / np.maximum(ma5, 1e-9) - 1.0
    ma10_ratio = close / np.maximum(ma10, 1e-9) - 1.0
    ma20_ratio = close / np.maximum(ma20, 1e-9) - 1.0

    X = np.column_stack(
        [
            ret1,
            rng,
            body,
            vol_chg,
            np.nan_to_num(ma5_ratio, nan=0.0),
            np.nan_to_num(ma10_ratio, nan=0.0),
            np.nan_to_num(ma20_ratio, nan=0.0),
        ]
    )

    # next-day return target
    y = close[1:] / close[:-1] - 1.0
    X = X[:-1]
    return X, y


ALGOS: Dict[str, Dict[str, str]] = {
    "naive": {
        "label": "Naive (next close = last close)",
        "description": "Baseline: predicts 0% return.",
    },
    "momentum": {
        "label": "Momentum (use last return)",
        "description": "Baseline: predicts next return = last return (clipped).",
    },
    "ridge": {
        "label": "Ridge (linear)",
        "description": "Linear ridge regression on simple OHLCV features (+ logistic direction prob).",
    },
    "rf": {
        "label": "Random Forest",
        "description": "RandomForest regressor for return (+ RF classifier for P(up)).",
    },
    "extra_trees": {
        "label": "Extra Trees",
        "description": "ExtraTrees regressor for return (+ ExtraTrees classifier for P(up)).",
    },
    "hgb": {
        "label": "HistGradientBoosting",
        "description": "Gradient-boosted trees for return (+ classifier for P(up)).",
    },
}


def list_algorithms() -> List[Dict[str, str]]:
    return [{"id": k, **v} for k, v in ALGOS.items()]


def _train_test_split_time(X: np.ndarray, y: np.ndarray) -> Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    n = len(y)
    split = max(int(n * 0.8), n - 30)
    split = max(min(split, n - 5), 5)
    return X[:split], y[:split], X[split:], y[split:]


def _band_from_recent(rows: List[HistoryRow]) -> float:
    recent = rows[-20:] if len(rows) >= 20 else rows
    avg_range = float(np.mean([(r.high - r.low) / max(r.close, 1e-9) for r in recent]))
    return max(0.003, min(0.15, avg_range * 0.8))


def predict_next_candle(rows: List[HistoryRow], *, algo: str = "rf", random_state: int = 42) -> Dict[str, Any]:
    """
    Predict the next candle using one of the supported algorithms.
    Output is a single "predicted candle" for chart overlay.
    """
    if algo not in ALGOS:
        return {"ok": False, "error": f"Unknown algo '{algo}'. Try /api/algos."}
    if len(rows) < 40 and algo not in ("naive", "momentum"):
        return {"ok": False, "error": f"Not enough history to train (need >=40 rows, got {len(rows)})."}

    X, y = _make_features(rows)
    if len(y) < 20 and algo not in ("naive", "momentum"):
        return {"ok": False, "error": "Not enough feature rows after preprocessing."}

    last_row = rows[-1]
    next_t = _next_day_ts(last_row.t)
    last_close = last_row.close
    band = _band_from_recent(rows)

    # Baselines that do not require training.
    if algo == "naive":
        pred_ret = 0.0
        prob_up = 0.5
        pred_close = last_close
        pred_open = last_close
        pred_high = max(pred_open, pred_close) * (1.0 + band)
        pred_low = min(pred_open, pred_close) * (1.0 - band)
        return {
            "ok": True,
            "algo": algo,
            "meta": {"rows": len(rows), "label": ALGOS[algo]["label"]},
            "prediction": {
                "time": next_t,
                "open": float(pred_open),
                "high": float(pred_high),
                "low": float(pred_low),
                "close": float(pred_close),
                "pred_close_return": float(pred_ret),
                "prob_up": float(prob_up),
            },
            "last": {"time": int(last_row.t), "close": float(last_close), "date": last_row.date},
        }

    if algo == "momentum":
        # use last observed return (clip)
        if len(rows) < 2:
            return {"ok": False, "error": "Need at least 2 rows for momentum baseline."}
        last_ret = rows[-1].close / max(rows[-2].close, 1e-9) - 1.0
        pred_ret = float(np.clip(last_ret, -0.2, 0.2))
        prob_up = 1.0 if pred_ret > 0 else 0.0 if pred_ret < 0 else 0.5
        pred_close = max(0.0, last_close * (1.0 + pred_ret))
        pred_open = last_close
        pred_high = max(pred_open, pred_close) * (1.0 + band)
        pred_low = min(pred_open, pred_close) * (1.0 - band)
        return {
            "ok": True,
            "algo": algo,
            "meta": {"rows": len(rows), "label": ALGOS[algo]["label"]},
            "prediction": {
                "time": next_t,
                "open": float(pred_open),
                "high": float(pred_high),
                "low": float(pred_low),
                "close": float(pred_close),
                "pred_close_return": float(pred_ret),
                "prob_up": float(prob_up),
            },
            "last": {"time": int(last_row.t), "close": float(last_close), "date": last_row.date},
        }

    X_train, y_train, X_test, y_test = _train_test_split_time(X, y)
    x_last = X[-1].reshape(1, -1)

    reg = None
    clf = None

    if algo == "ridge":
        reg = Ridge(alpha=1.0, random_state=random_state)
        clf = LogisticRegression(max_iter=2000, n_jobs=1)
    elif algo == "rf":
        reg = RandomForestRegressor(
            n_estimators=400,
            random_state=random_state,
            n_jobs=-1,
            min_samples_leaf=2,
            bootstrap=True,
            oob_score=True,
        )
        clf = RandomForestClassifier(
            n_estimators=400,
            random_state=random_state,
            n_jobs=-1,
            min_samples_leaf=2,
        )
    elif algo == "extra_trees":
        reg = ExtraTreesRegressor(
            n_estimators=600,
            random_state=random_state,
            n_jobs=-1,
            min_samples_leaf=2,
        )
        clf = ExtraTreesClassifier(
            n_estimators=600,
            random_state=random_state,
            n_jobs=-1,
            min_samples_leaf=2,
        )
    elif algo == "hgb":
        reg = HistGradientBoostingRegressor(random_state=random_state, max_depth=3, learning_rate=0.05)
        clf = HistGradientBoostingClassifier(random_state=random_state, max_depth=3, learning_rate=0.05)

    assert reg is not None and clf is not None
    reg.fit(X_train, y_train)
    clf.fit(X_train, (y_train > 0).astype(int))

    pred_ret = float(reg.predict(x_last)[0])
    try:
        prob_up = float(clf.predict_proba(x_last)[0][1])
    except Exception:
        prob_up = float(clf.predict(x_last)[0])

    pred_close = max(0.0, last_close * (1.0 + pred_ret))

    pred_open = last_close
    pred_high = max(pred_open, pred_close) * (1.0 + band)
    pred_low = min(pred_open, pred_close) * (1.0 - band)

    # quick sanity metrics (not a trading backtest)
    if len(y_test) > 0:
        yhat = reg.predict(X_test)
        mae = float(np.mean(np.abs(yhat - y_test)))
        dir_acc = float(np.mean((yhat > 0) == (y_test > 0)))
    else:
        mae = None
        dir_acc = None

    oob_r2 = getattr(reg, "oob_score_", None)
    return {
        "ok": True,
        "algo": algo,
        "meta": {
            "rows": len(rows),
            "train_rows": int(len(y_train)),
            "test_rows": int(len(y_test)),
            "label": ALGOS[algo]["label"],
            "test_dir_acc": dir_acc,
            "oob_r2": float(oob_r2) if oob_r2 is not None else None,
            "test_mae_return": mae,
        },
        "prediction": {
            "time": next_t,
            "open": float(pred_open),
            "high": float(pred_high),
            "low": float(pred_low),
            "close": float(pred_close),
            "pred_close_return": float(pred_ret),
            "prob_up": float(prob_up),
        },
        "last": {
            "time": int(last_row.t),
            "close": float(last_close),
            "date": last_row.date,
        },
    }

