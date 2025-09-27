# dash_short_live.py
import os
import glob
import math
import time
from datetime import datetime

import pandas as pd
import streamlit as st
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle
import pytz

from scanner_market_movers import scan_market, append_scan_history
from signals_short import enrich_live_metrics
from polygon_client import PolygonClient
from polygon_intraday import (
    fetch_1m_intraday_latest_session,
    first_rth_bar_index,
    find_pump_anchor_idx,
    compute_avwap_from_anchor,
)

st.set_page_config(page_title="SHORTBOT – Live Short Scanner", layout="wide")


# =====================================================
# Helpers
# =====================================================
def fmt_num(val, digits=2):
    """Sichere Zahlformatierung (— bei None/NaN)."""
    try:
        if val is None or (isinstance(val, float) and math.isnan(val)):
            return "—"
        return f"{val:.{digits}f}"
    except Exception:
        return "—"


def plot_candles_with_avwap(df, avwap_series=None, prev=None, rth_open_=None, title=""):
    """Einfaches 1m-Candle-Chart mit AVWAP-Overlay + Referenz-Linien."""
    fig, ax = plt.subplots(figsize=(11, 4))

    for i, row in df.iterrows():
        o, h, l, c = float(row["o"]), float(row["h"]), float(row["l"]), float(row["c"])
        ax.vlines(i, l, h, linewidth=1)
        rect_y = min(o, c)
        rect_h = abs(c - o)
        color = "#2ca02c" if c >= o else "#d62728"  # grün/rot
        ax.add_patch(Rectangle((i - 0.35, rect_y), 0.7, rect_h, edgecolor=color, facecolor=color, alpha=0.6))

    if avwap_series is not None:
        ax.plot(range(len(df)), avwap_series, linewidth=1.6, label="AVWAP (20%-Anker)")

    if prev:
        ax.axhline(prev, linestyle="--", linewidth=1, label="Prev Close")
    if rth_open_:
        ax.axhline(rth_open_, linestyle=":", linewidth=1, label="RTH Open")

    ticks = list(range(0, len(df), 30))
    labels = df.loc[ticks, "time_ny"] if len(df) else []
    ax.set_xticks(ticks)
    ax.set_xticklabels(labels, rotation=0, fontsize=8)

    ax.set_title(title)
    ax.set_xlabel("NY Time")
    ax.set_ylabel("Price")
    ax.legend(loc="best")
    ax.grid(True, linewidth=0.3, alpha=0.5)
    st.pyplot(fig)


# =====================================================
# Sidebar
# =====================================================
st.title("⚡ SHORTBOT – Live Short Scanner (Polygon)")

source = st.sidebar.selectbox(
    "Quelle",
    ["Top Gainers (Market-wide)", "Top Gainers + Losers", "Manuelle Tickerliste"],
    index=0,
)
refresh_sec = st.sidebar.number_input("Auto-Refresh (Sekunden)", min_value=15, max_value=180, value=30, step=5)

manual_tickers = []
if source == "Manuelle Tickerliste":
    path = st.sidebar.text_input("Tickerliste (.txt, 1 pro Zeile)", value="tickers_watchlist.txt")
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            manual_tickers = [ln.strip().upper() for ln in f if ln.strip()]
    else:
        st.sidebar.error(f"Datei nicht gefunden: {path}")

st.caption(f"Zeit: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}  |  Quelle: {source}")


# =====================================================
# Tabs: Scanner / History
# =====================================================
tab_scan, tab_hist = st.tabs(["🔎 Scanner", "📜 History"])

with tab_scan:
    # ----------------------------
    # Daten laden (df bauen)
    # ----------------------------
    if source == "Manuelle Tickerliste":
        rows = []
        for t in manual_tickers[:100]:
            try:
                rows.append(enrich_live_metrics(t))
            except Exception as e:
                rows.append({
                    "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "session_date": None,
                    "ticker": t, "signal": "ERROR", "error": str(e)
                })
        df = pd.DataFrame(rows)
    else:
        include_losers = (source == "Top Gainers + Losers")
        df = scan_market(limit_each=50, include_losers=include_losers)

    # ----------------------------
    # History-Logging
    # ----------------------------
    try:
        log_path = append_scan_history(df)
        if log_path:
            st.caption(f"📒 History appended → {log_path}")
    except Exception as e:
        st.caption(f"⚠️ History-Logging fehlgeschlagen: {e}")

    # Prozent hübsch
    for c in ("gap", "change"):
        if c in df.columns:
            df[c] = (df[c] * 100.0).round(2)

    # ----------------------------
    # KPIs
    # ----------------------------
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Symbole", len(df))
    col2.metric("Short Candidates", int((df["signal"] == "SHORT_CANDIDATE").sum()) if "signal" in df.columns else 0)
    col3.metric("Watchlist", int((df["signal"] == "WATCHLIST").sum()) if "signal" in df.columns else 0)
    col4.metric("Errors", int((df["signal"] == "ERROR").sum()) if "signal" in df.columns else 0)

    # ----------------------------
    # Tabelle
    # ----------------------------
    if not df.empty:
        dfv = df.copy()
        sort_cols = [c for c in ["signal", "change", "cum_volume"] if c in dfv.columns]
        if sort_cols:
            dfv = dfv.sort_values(sort_cols, ascending=[True, False, False][:len(sort_cols)], na_position="last")
        view_cols = [c for c in [
            "ticker","signal","last","change","gap","cum_volume",
            "prev_close","avwap_last","rth_open","float_proxy","shares_out","timestamp","session_date"
        ] if c in dfv.columns]
        st.dataframe(dfv[view_cols], use_container_width=True, hide_index=True)
    else:
        st.info("Keine Daten geladen – prüfe Quelle/Verbindung.")
        dfv = pd.DataFrame(columns=["ticker"])

    st.divider()

    # ----------------------------
    # Detail-Chart (Weekend-Fallback aktiv)
    # ----------------------------
    st.subheader("🔍 Detail-Chart")
    ticker_options = dfv["ticker"].dropna().unique().tolist()
    if not ticker_options:
        st.warning("Kein Ticker verfügbar.")
    else:
        sel = st.selectbox("Ticker auswählen", ticker_options, index=0, key="detail_ticker")

        # Refdaten (für Prev Close)
        poly = PolygonClient()
        ref = poly.fetch_ref_data(sel)
        prev_close = ref.get("prev_close")

        # Jüngste verfügbare Handelssession (Weekend-Fallback)
        idf, session_date = fetch_1m_intraday_latest_session(sel, max_lookback_days=10)
        if idf.empty or session_date is None:
            st.warning("Keine Intraday-Daten verfügbar.")
        else:
            idf = idf.reset_index(drop=True)
            st.caption(f"Session: {session_date}")

            # RTH Open / Anker / AVWAP
            rth_idx = first_rth_bar_index(idf)
            rth_open = float(idf.loc[rth_idx, "o"]) if rth_idx is not None else None
            anchor_idx = find_pump_anchor_idx(idf, prev_close, pump_threshold=0.20) if prev_close else None
            avwap = compute_avwap_from_anchor(idf, anchor_idx) if anchor_idx is not None else None

            last = None
            if not idf.empty and not pd.isna(idf.iloc[-1]["c"]):
                try:
                    last = float(idf.iloc[-1]["c"])
                except Exception:
                    last = None
            change_pct = ((last / prev_close) - 1.0) * 100 if (last is not None and prev_close) else None
            title = f"{sel} • Last: {fmt_num(last)}  • Change vs Prev: {fmt_num(change_pct)}%"

            plot_candles_with_avwap(
                idf,
                avwap_series=avwap.values if avwap is not None else None,
                prev=prev_close,
                rth_open_=rth_open,
                title=title
            )

            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Last", fmt_num(last))
            c2.metric("Prev Close", fmt_num(prev_close) if prev_close else "—")
            c3.metric("Change %", f"{fmt_num(change_pct)}%")
            avwap_last_val = None
            if avwap is not None:
                try:
                    avwap_last_val = float(avwap.iloc[-1])
                except Exception:
                    avwap_last_val = None
            c4.metric("AVWAP", fmt_num(avwap_last_val))


with tab_hist:
    st.subheader("📜 History & Aggregate-Analyse")

    # Dateien wählen (letzte N Tage)
    out_dir = os.path.join(os.getcwd(), "out")
    paths = sorted(glob.glob(os.path.join(out_dir, "short_scans_*.csv")))
    if not paths:
        st.info("Es wurden noch keine History-Dateien geschrieben.")
    else:
        days_available = len(paths)
        last_n = st.number_input(
            "Anzahl Tage laden",
            min_value=1,
            max_value=days_available,
            value=min(3, days_available),
            step=1,
        )
        use_paths = paths[-last_n:]

        # Laden & vereinen
        dfs = []
        for p in use_paths:
            try:
                dfp = pd.read_csv(p)
                dfp["__source_file"] = os.path.basename(p)
                dfs.append(dfp)
            except Exception as e:
                st.warning(f"Problem beim Laden von {os.path.basename(p)}: {e}")

        if not dfs:
            st.info("Keine nutzbaren History-Daten gefunden.")
        else:
            hist = pd.concat(dfs, ignore_index=True)

            # Timestamps parsen
            if "timestamp" in hist.columns:
                hist["ts"] = pd.to_datetime(hist["timestamp"], errors="coerce")
                hist = hist.dropna(subset=["ts"])
                hist = hist.sort_values("ts")
            else:
                hist["ts"] = pd.NaT

            # Filter UI
            colA, colB, colC = st.columns([1, 1, 2])
            sigs_all = sorted(hist.get("signal", pd.Series(dtype=str)).dropna().unique().tolist())
            sigs_sel = colA.multiselect("Signale", options=sigs_all, default=sigs_all)
            tick_query = colB.text_input("Ticker enthält …", value="").strip().lower()
            show_only_candidates = colC.checkbox("Nur SHORT_CANDIDATE zeigen", value=False)

            fh = hist.copy()
            if sigs_sel:
                fh = fh[fh["signal"].isin(sigs_sel)]
            if tick_query:
                fh = fh[fh["ticker"].str.lower().str.contains(tick_query, na=False)]
            if show_only_candidates:
                fh = fh[fh["signal"] == "SHORT_CANDIDATE"]

            # Kennzahlen Aggregat
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Scan-Zeilen", f"{len(fh):,}")
            c2.metric("Unique Ticker", f"{fh['ticker'].nunique():,}" if "ticker" in fh.columns else "0")
            c3.metric("Candidates", int((fh["signal"] == "SHORT_CANDIDATE").sum()) if "signal" in fh.columns else 0)
            c4.metric("Watchlist", int((fh["signal"] == "WATCHLIST").sum()) if "signal" in fh.columns else 0)

            st.markdown("**Top-Listen**")
            colL, colR = st.columns(2)

            # Links: Repeater (häufigste SHORT_CANDIDATE)
            if not fh.empty:
                top_rep = (
                    fh[fh["signal"] == "SHORT_CANDIDATE"]
                    .groupby("ticker")
                    .agg(
                        hits=("signal", "count"),
                        last_seen=("ts", "max"),
                        max_change=("change", "max"),
                        max_cumv=("cum_volume", "max"),
                    )
                    .sort_values(["hits", "max_change", "max_cumv"], ascending=[False, False, False])
                    .head(20)
                    .copy()
                )
                if "max_change" in top_rep.columns:
                    top_rep["max_change_%"] = (top_rep["max_change"] * 100.0).round(2)
                    top_rep = top_rep.drop(columns=["max_change"])
                colL.dataframe(top_rep, use_container_width=True)
            else:
                colL.info("Keine Daten für Repeater.")

            # Rechts: auswählbare Rangliste (Change / MFE / Close Return)
            metric = colR.selectbox("Ranking", ["Max Change", "Top MFE (post-entry)", "Best Close Return"], index=0)
            colR.caption("MFE = Maximum Favorable Excursion (bestmöglicher Gewinn nach Entry).  ret_close_pct = Ergebnis bis Session-Close.")

            if metric == "Max Change":
                if not fh.empty and "change" in fh.columns:
                    top_chg = (
                        fh.dropna(subset=["change"])
                        .sort_values("change", ascending=False)
                        .loc[:, ["ts", "session_date", "ticker", "signal", "change", "cum_volume"]]
                        .head(20)
                        .copy()
                    )
                    top_chg["change_%"] = (top_chg["change"] * 100.0).round(2)
                    top_chg = top_chg.drop(columns=["change"])
                    colR.dataframe(top_chg, use_container_width=True)
                else:
                    colR.info("Keine Change-Daten vorhanden.")
            elif metric == "Top MFE (post-entry)":
                if not fh.empty and "mfe_pct" in fh.columns:
                    top_mfe = (
                        fh.dropna(subset=["mfe_pct"])
                        .sort_values("mfe_pct", ascending=False)
                        .loc[:, ["ts", "session_date", "ticker", "signal", "mfe_pct", "ret_close_pct"]]
                        .head(20)
                        .copy()
                    )
                    top_mfe["mfe_%"] = (top_mfe["mfe_pct"] * 100.0).round(2)
                    if "ret_close_pct" in top_mfe.columns:
                        top_mfe["ret_close_%"] = (top_mfe["ret_close_pct"] * 100.0).round(2)
                    top_mfe = top_mfe.drop(columns=["mfe_pct", "ret_close_pct"], errors="ignore")
                    colR.dataframe(top_mfe, use_container_width=True)
                else:
                    colR.info("Keine MFE-Daten vorhanden (PnL-Tagging aktiv?).")
            else:  # Best Close Return
                if not fh.empty and "ret_close_pct" in fh.columns:
                    top_rc = (
                        fh.dropna(subset=["ret_close_pct"])
                        .sort_values("ret_close_pct", ascending=False)
                        .loc[:, ["ts", "session_date", "ticker", "signal", "ret_close_pct", "mfe_pct"]]
                        .head(20)
                        .copy()
                    )
                    top_rc["ret_close_%"] = (top_rc["ret_close_pct"] * 100.0).round(2)
                    if "mfe_pct" in top_rc.columns:
                        top_rc["mfe_%"] = (top_rc["mfe_pct"] * 100.0).round(2)
                    top_rc = top_rc.drop(columns=["ret_close_pct", "mfe_pct"], errors="ignore")
                    colR.dataframe(top_rc, use_container_width=True)
                else:
                    colR.info("Keine ret_close_pct-Daten vorhanden (PnL-Tagging aktiv?).")

            st.markdown("**Timeline (Scans pro 10 Minuten, nach Signal)**")
            if not fh.empty and "ts" in fh.columns and fh["ts"].notna().any():
                fh_idx = fh.set_index("ts")
                counts = fh_idx.groupby([pd.Grouper(freq="10min"), "signal"]).size().unstack(fill_value=0)
                st.line_chart(counts)
            else:
                st.info("Keine Zeitachsen-Daten verfügbar.")

            st.markdown("**Letzte 300 Einträge**")
            st.dataframe(fh.tail(300), use_container_width=True, hide_index=True)

            # Download gefilterte History
            csv_dl = fh.to_csv(index=False).encode("utf-8")
            st.download_button(
                "⬇️ Gefilterte History als CSV",
                data=csv_dl,
                file_name="short_history_filtered.csv",
                mime="text/csv",
            )


# =====================================================
# Auto-Refresh
# =====================================================
st.write(f"🔄 Aktualisiert um {datetime.now().strftime('%H:%M:%S')} – nächste Aktualisierung in {int(refresh_sec)}s …")
time.sleep(int(refresh_sec))
st.rerun()
