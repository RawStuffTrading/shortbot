# polygon_universe.py
import os, time, requests, pandas as pd
from datetime import datetime

BASE_URL = "https://api.polygon.io"

def _hdr():
    key = os.getenv("POLYGON_API_KEY")
    if not key:
        raise RuntimeError("POLYGON_API_KEY nicht gesetzt.")
    return {"Authorization": f"Bearer {key}"}

def fetch_us_tickers_active(exclude_otc: bool = True, limit: int = 1000) -> pd.DataFrame:
    """
    Holt aktive US-Aktien (v3/reference/tickers) mit Pagination (next_url).
    exclude_otc=True filtert über 'type'/'market'/'primary_exchange' heuristisch OTC raus.
    """
    url = f"{BASE_URL}/v3/reference/tickers"
    params = {"active": "true", "market": "stocks", "limit": limit}
    rows = []
    while True:
        r = requests.get(url, headers=_hdr(), params=params, timeout=20)
        r.raise_for_status()
        data = r.json()
        for res in data.get("results", []):
            # Heuristik: OTC raus (Polygon markiert OTC oft über primary_exchange='OTC' oder 'market':'otc')
            pe = res.get("primary_exchange") or ""
            market = res.get("market") or ""
            if exclude_otc and (market.lower() == "otc" or pe.upper() == "OTC"):
                continue
            rows.append({
                "ticker": res.get("ticker"),
                "name": res.get("name"),
                "primary_exchange": res.get("primary_exchange"),
                "locale": res.get("locale"),
                "currency": res.get("currency_name") or res.get("currency"),
                "market": res.get("market"),
                "type": res.get("type"),
            })
        next_url = data.get("next_url")
        if not next_url: break
        # next_url ist vollqualifiziert inkl. api.polygon.io, daher ohne params weiter:
        url = next_url
        params = None
        time.sleep(0.2)  # sanftes Rate-Limit
    return pd.DataFrame(rows)

def cache_today(out_dir: str = "out") -> str:
    os.makedirs(out_dir, exist_ok=True)
    df = fetch_us_tickers_active(exclude_otc=True, limit=1000)
    out = os.path.join(out_dir, f"universe_us_{datetime.now().strftime('%Y-%m-%d')}.csv")
    df.to_csv(out, index=False)
    print(f"✅ Universe cached: {out} ({len(df)} Ticker, OTC entfernt)")
    return out

if __name__ == "__main__":
    cache_today()
