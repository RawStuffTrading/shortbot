# signals_short.py
from __future__ import annotations
import math
import pandas as pd
from typing import Dict, Any
from datetime import datetime

from polygon_client import PolygonClient
from polygon_intraday import (
    fetch_1m_intraday_latest_session,
    first_rth_bar_index,
    compute_gap,
    compute_change,
    compute_cum_volume,
    find_pump_anchor_idx,
)
from short_pnl import find_short_entry_after_pump, compute_outcomes_short

def enrich_live_metrics(ticker: str) -> Dict[str, Any]:
    """
    Nutzt die jüngste verfügbare Handelssession (Weekend-Fallback) und berechnet zusätzlich
    PnL-Tags (Entry nach AVWAP-Cross, Targets/Stop, MFE/MAE, ret_close_pct).
    """
    poly = PolygonClient()
    ref = poly.fetch_ref_data(ticker)
    prev_close = ref.get("prev_close")
    shares_out = ref.get("shares_out")
    float_proxy = shares_out * 0.8 if shares_out else None  # simple proxy

    df, session_date = fetch_1m_intraday_latest_session(ticker, max_lookback_days=10)

    base = {
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "session_date": str(session_date) if session_date else None,
        "ticker": ticker,
        "prev_close": prev_close,
        "shares_out": shares_out,
        "float_proxy": float_proxy,
    }

    if df.empty or session_date is None:
        return {
            **base,
            "gap": None,
            "change": None,
            "cum_volume": 0,
            "last": None,
            "rth_open": None,
            "pump20_anchor_idx": None,
            "avwap_last": None,
            "signal": "NO_DATA",
            # PnL-Felder
            "entry_idx": None,
            "entry_price": None,
            "mfe_pct": None,
            "mae_pct": None,
            "ret_close_pct": None,
            "hit_t3": None,
            "hit_t5": None,
            "hit_t10": None,
            "stopped": None,
        }

    rth_idx = first_rth_bar_index(df)
    rth_open = float(df.loc[rth_idx, "o"]) if rth_idx is not None else None
    last = float(df.iloc[-1]["c"])

    gap = compute_gap(prev_close, rth_open) if prev_close and rth_open else None
    change = compute_change(prev_close, last) if prev_close and last else None
    cumv = float(compute_cum_volume(df).iloc[-1]) if not df.empty else 0.0

    pump_anchor = find_pump_anchor_idx(df, prev_close, pump_threshold=0.20) if prev_close else None

    # PnL-Entry & Outcomes
    entry_idx = find_short_entry_after_pump(df, prev_close, pump_anchor)
    pnl = compute_outcomes_short(df, entry_idx) if entry_idx is not None else {}

    # Simple Signal-Heuristik
    cond_pump = (change is not None) and (change >= 0.20)
    cond_flow = (float_proxy and (cumv / float_proxy >= 0.05)) or (cumv >= 5_000_000)
    # Wenn Entry gefunden, ist Kurs per Definition unter AVWAP. Falls nicht, Bodenregel:
    cond_avwap = True if entry_idx is not None else False

    if cond_pump and cond_flow and cond_avwap:
        signal = "SHORT_CANDIDATE"
    elif cond_pump and cond_flow:
        signal = "WATCHLIST"
    else:
        signal = "NEUTRAL"

    return {
        **base,
        "gap": gap,
        "change": change,
        "cum_volume": cumv,
        "last": last,
        "rth_open": rth_open,
        "pump20_anchor_idx": pump_anchor,
        "avwap_last": None,  # optional: könntest du separat berechnen, wird für Signal hier nicht gebraucht
        "signal": signal,
        # PnL-Felder
        "entry_idx": pnl.get("entry_idx"),
        "entry_price": pnl.get("entry_price"),
        "mfe_pct": pnl.get("mfe_pct"),
        "mae_pct": pnl.get("mae_pct"),
        "ret_close_pct": pnl.get("ret_close_pct"),
        "hit_t3": pnl.get("hit_t3"),
        "hit_t5": pnl.get("hit_t5"),
        "hit_t10": pnl.get("hit_t10"),
        "stopped": pnl.get("stopped"),
    }
