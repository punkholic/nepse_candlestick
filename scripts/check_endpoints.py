from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from nepse_core.nepse_api import NepseAPI  # noqa: E402
from web_ui.snapshot import fetch_all  # noqa: E402


OUT_DIR = ROOT / "data" / "snapshots"
OUT_DIR.mkdir(parents=True, exist_ok=True)


def _short(s: str, n: int = 160) -> str:
    s = " ".join(str(s).split())
    return s if len(s) <= n else s[: n - 1] + "…"


def _format_summary(summary: Dict[str, Any]) -> str:
    if not isinstance(summary, dict):
        return ""
    t = summary.get("type")
    if t == "list":
        return f"list(len={summary.get('len')})"
    if t == "dict":
        keys = summary.get("keys") or []
        keys_s = ",".join(keys[:8])
        if summary.get("keys_truncated"):
            keys_s += ",…"
        return f"dict(keys={keys_s})"
    return str(t or "")


def main() -> None:
    api = NepseAPI(verify_ssl=False)
    snap = fetch_all(api, index_id=58, days_back=30)

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = OUT_DIR / f"nepse_snapshot_{stamp}.json"
    out_path.write_text(json.dumps(snap, ensure_ascii=False, indent=2), encoding="utf-8")

    meta = snap.get("meta", {})
    print(f"Saved: {out_path}")
    print(f"Meta: timestamp={meta.get('timestamp')} index_id={meta.get('index_id')} days_back={meta.get('days_back')}")
    print(f"Meta: sample_security_id={meta.get('sample_security_id')}")
    if meta.get("trading_history_range"):
        rng = meta["trading_history_range"]
        print(f"Meta: trading_history_range={rng.get('start')}..{rng.get('end')}")

    print("\nPer-endpoint status:")
    results: Dict[str, Any] = snap.get("results", {})
    for name in sorted(results.keys()):
        r = results[name] or {}
        ok = r.get("ok")
        secs = r.get("seconds")
        summary = _format_summary(r.get("summary"))
        if ok:
            print(f"- {name:24} ok   {secs:>6.2f}s  {summary}")
        else:
            skipped = r.get("skipped")
            tag = "skip" if skipped else "fail"
            err = _short(r.get("error", ""))
            if secs is None:
                print(f"- {name:24} {tag:4}          {err}")
            else:
                print(f"- {name:24} {tag:4} {secs:>6.2f}s  {err}")


if __name__ == "__main__":
    main()

