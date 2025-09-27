# short_pnl.py
from __future__ import annotations
import math
from typing import Optional, Dict, Any, List
import pandas as pd

from polygon_intraday import compute_avwap_from_anchor

def find_short_entry_after_pump(df: pd.DataFrame, prev_close: float, pump_anchor_idx: Optional[int]) -> Optional[int]:
    """
    Entry-Definition: erste Kerze mit Close < AVWAP nach dem 20%-Pump-Anker.
    """
    if df is None or df.empty or pump_anchor_idx is None or prev_close is None:
        return None
    avwap = compute_avwap_from_anchor(df, pump_anchor_idx)
    for i in range(pump_anchor_idx, len(df)):
        try:
            c = float(df.iloc[i]["c"])
            a = float(avwap.iloc[i])
            if not math.isnan(a) and c < a:
                return i
        except Exception:
            continue
    return None

def compute_outcomes_short(
    df: pd.DataFrame,
    entry_idx: int,
    target_pcts: List[float] = [0.03, 0.05, 0.10],
    stop_pct: float = 0.03,
) -> Dict[str, Any]:
    """
    Berechnet MFE/MAE, ob Targets erreicht wurden (3/5/10%) und ob Stop getroffen wurde.
    Short-Logik:
      - Target erreicht, wenn Low <= entry_price * (1 - target_pct)
      - Stop getroffen, wenn High >= entry_price * (1 + stop_pct)
    """
    if df is None or df.empty or entry_idx is None or entry_idx >= len(df):
        return {}

    entry_price = float(df.iloc[entry_idx]["c"])
    lows = df.loc[entry_idx + 1 :, "l"].astype(float)
    highs = df.loc[entry_idx + 1 :, "h"].astype(float)
    close_last = float(df.iloc[-1]["c"])

    min_low_after = float(lows.min()) if not lows.empty else entry_price
    max_high_after = float(highs.max()) if not highs.empty else entry_price

    # MFE/MAE fürs Short:
    mfe_pct = (entry_price - min_low_after) / entry_price if entry_price else None
    mae_pct = (max_high_after - entry_price) / entry_price if entry_price else None
    ret_close_pct = (entry_price - close_last) / entry_price if entry_price else None

    # Targets/Stop (Zeitpunkt des ersten Treffers)
    def first_index_target(target_pct: float) -> Optional[int]:
        tgt = entry_price * (1.0 - target_pct)
        idxs = lows.index[lows <= tgt].tolist()
        return idxs[0] if idxs else None

    def first_index_stop(stop_pct: float) -> Optional[int]:
        stp = entry_price * (1.0 + stop_pct)
        idxs = highs.index[highs >= stp].tolist()
        return idxs[0] if idxs else None

    stop_idx = first_index_stop(stop_pct)
    hits = {}
    for p in target_pcts:
        idx_hit = first_index_target(p)
        hits[f"hit_t{int(p*100)}"] = bool(idx_hit is not None and (stop_idx is None or idx_hit <= stop_idx))

    stopped = stop_idx is not None and all(not v for v in hits.values())

    return {
        "entry_idx": entry_idx,
        "entry_price": entry_price,
        "mfe_pct": mfe_pct,
        "mae_pct": mae_pct,
        "ret_close_pct": ret_close_pct,
        "stopped": stopped,
        **hits,
    }
