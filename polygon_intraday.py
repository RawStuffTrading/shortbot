# polygon_intraday.py
import os, math, requests, pandas as pd, pytz
from datetime import datetime, timedelta

BASE_URL = "https://api.polygon.io"
TZ_NY = pytz.timezone("America/New_York")

def _auth_headers():
    key = os.getenv("POLYGON_API_KEY")
    if not key:
        raise RuntimeError("POLYGON_API_KEY nicht gesetzt.")
    return {"Authorization": f"Bearer {key}"}

def _aggs_range_url(ticker: str, start_utc_ms: int, end_utc_ms: int) -> str:
    return f"{BASE_URL}/v2/aggs/ticker/{ticker}/range/1/minute/{start_utc_ms}/{end_utc_ms}"

def _fetch_range_1m(ticker: str, start_utc_ms: int, end_utc_ms: int) -> pd.DataFrame:
    r = requests.get(
        _aggs_range_url(ticker, start_utc_ms, end_utc_ms),
        headers=_auth_headers(),
        params={"adjusted": "true", "limit": 50000},
        timeout=20,
    )
    r.raise_for_status()
    rows = (r.json() or {}).get("results") or []
    if not rows:
        return pd.DataFrame(columns=["t","o","h","l","c","v","ts_utc","ts_ny","date","time_ny"])
    df = pd.DataFrame(rows)
    df["ts_utc"] = pd.to_datetime(df["t"], unit="ms", utc=True)
    df["ts_ny"] = df["ts_utc"].dt.tz_convert(TZ_NY)
    df["date"] = df["ts_ny"].dt.date
    df["time_ny"] = df["ts_ny"].dt.strftime("%H:%M")
    return df.sort_values("ts_utc").reset_index(drop=True)

def fetch_1m_intraday_for_ny_date(ticker: str, ny_date) -> pd.DataFrame:
    if isinstance(ny_date, str):
        ny_date = datetime.strptime(ny_date, "%Y-%m-%d").date()
    start_ny = TZ_NY.localize(datetime(ny_date.year, ny_date.month, ny_date.day, 0, 0))
    end_ny = start_ny + timedelta(days=1)
    start_utc_ms = int(start_ny.astimezone(pytz.utc).timestamp() * 1000)
    end_utc_ms = int(end_ny.astimezone(pytz.utc).timestamp() * 1000)
    return _fetch_range_1m(ticker, start_utc_ms, end_utc_ms)

def fetch_1m_intraday_today(ticker: str) -> pd.DataFrame:
    now_utc = datetime.utcnow()
    start_utc = datetime(now_utc.year, now_utc.month, now_utc.day)
    return _fetch_range_1m(
        ticker,
        int(start_utc.timestamp()*1000),
        int(now_utc.timestamp()*1000),
    )

def fetch_1m_intraday_latest_session(ticker: str, max_lookback_days: int = 10):
    """
    Liefert (df, session_date_ny) für die jüngste NY-Handelssession
    (geht rückwärts ab HEUTE bis max_lookback_days).
    """
    today_ny = datetime.now(TZ_NY).date()
    for i in range(max_lookback_days):
        d = today_ny - timedelta(days=i)
        df = fetch_1m_intraday_for_ny_date(ticker, d)
        if not df.empty:
            return df[df["date"] == d].reset_index(drop=True), d
    return pd.DataFrame(columns=["t","o","h","l","c","v","ts_utc","ts_ny","date","time_ny"]), None

def first_rth_bar_index(df: pd.DataFrame):
    if df.empty: return None
    idx = df.index[(df["time_ny"] == "09:30")].tolist()
    return idx[0] if idx else None

def compute_gap(prev_close: float, rth_open: float):
    return (rth_open/prev_close - 1.0) if (prev_close and rth_open) else None

def compute_change(prev_close: float, last_price: float):
    return (last_price/prev_close - 1.0) if (prev_close and last_price) else None

def compute_cum_volume(df: pd.DataFrame) -> pd.Series:
    return df["v"].cumsum() if ("v" in df.columns and not df.empty) else pd.Series(dtype="float64")

def compute_avwap_from_anchor(df: pd.DataFrame, anchor_idx: int) -> pd.Series:
    """AVWAP ab anchor_idx (inkl.)."""
    import numpy as np
    if df.empty or anchor_idx is None or anchor_idx >= len(df):
        return pd.Series([math.nan]*len(df), index=df.index, name="avwap")
    tp = (df["h"] + df["l"] + df["c"]) / 3.0
    vol = df["v"].astype(float)
    wprice = tp * vol
    avwap = np.full(len(df), np.nan, dtype=float)
    run_w = 0.0; run_v = 0.0
    for i in range(anchor_idx, len(df)):
        run_w += float(wprice.iloc[i]); run_v += float(vol.iloc[i])
        avwap[i] = (run_w/run_v) if run_v>0 else math.nan
    return pd.Series(avwap, index=df.index, name="avwap")

def find_pump_anchor_idx(df: pd.DataFrame, prev_close: float, pump_threshold: float = 0.20):
    if df.empty or not prev_close: return None
    target = prev_close * (1.0 + pump_threshold)
    cand = df.index[(df["h"] >= target)].tolist()
    return cand[0] if cand else None
