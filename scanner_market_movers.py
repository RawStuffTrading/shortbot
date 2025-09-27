# scanner_market_movers.py
import os, pandas as pd, requests
from datetime import datetime
from typing import List
from signals_short import enrich_live_metrics

BASE_URL = "https://api.polygon.io"

def _hdr():
    key = os.getenv("POLYGON_API_KEY")
    if not key:
        raise RuntimeError("POLYGON_API_KEY nicht gesetzt.")
    return {"Authorization": f"Bearer {key}"}

def _get(path: str, params=None):
    r = requests.get(f"{BASE_URL}{path}", headers=_hdr(), params=params or {}, timeout=15)
    r.raise_for_status()
    return r.json()

def fetch_top_gainers(limit: int = 50) -> List[str]:
    data = _get("/v2/snapshot/locale/us/markets/stocks/gainers")
    res = data.get("tickers") or []
    return [x.get("ticker") for x in res[:limit] if x.get("ticker")]

def fetch_top_losers(limit: int = 50) -> List[str]:
    data = _get("/v2/snapshot/locale/us/markets/stocks/losers")
    res = data.get("tickers") or []
    return [x.get("ticker") for x in res[:limit] if x.get("ticker")]

def scan_market(limit_each: int = 50, include_losers: bool = False) -> pd.DataFrame:
    tickers = fetch_top_gainers(limit_each)
    if include_losers:
        tickers += fetch_top_losers(limit_each)
    tickers = sorted(set([t for t in tickers if t]))

    rows = []
    for t in tickers:
        try:
            rows.append(enrich_live_metrics(t))
        except Exception as e:
            rows.append({"timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                         "session_date": None,
                         "ticker": t, "signal": "ERROR", "error": str(e)})
    return pd.DataFrame(rows)

# --- History Logging ---
def append_scan_history(df: pd.DataFrame, out_dir: str = "out") -> str:
    """
    Hängt die aktuelle Scan-Runde an out/short_scans_YYYY-MM-DD.csv an.
    Jetzt inkl. PnL-Feldern.
    """
    if df is None or df.empty:
        return ""
    os.makedirs(out_dir, exist_ok=True)
    today = datetime.now().strftime("%Y-%m-%d")
    path = os.path.join(out_dir, f"short_scans_{today}.csv")
    df2 = df.copy()
    # Spalten-Order inkl. PnL-Felder
    cols = [c for c in [
        "timestamp","session_date","ticker","signal",
        "last","change","gap","cum_volume","prev_close","rth_open",
        "float_proxy","shares_out",
        # PnL:
        "entry_price","entry_idx","mfe_pct","mae_pct","ret_close_pct",
        "hit_t3","hit_t5","hit_t10","stopped",
        "error"
    ] if c in df2.columns]
    if cols:
        df2 = df2[cols]
    header = not os.path.exists(path)
    df2.to_csv(path, mode="a", header=header, index=False)
    return path
