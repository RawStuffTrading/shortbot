import os
import requests

BASE_URL = "https://api.polygon.io"

class PolygonClient:
    def __init__(self, api_key: str = None):
        self.api_key = api_key or os.getenv("POLYGON_API_KEY")
        if not self.api_key:
            raise RuntimeError("POLYGON_API_KEY nicht gesetzt.")

    def fetch_ref_data(self, ticker: str) -> dict:
        headers = {"Authorization": f"Bearer {self.api_key}"}

        # 1) Prev Close
        prev_close_url = f"{BASE_URL}/v2/aggs/ticker/{ticker}/prev"
        r_prev = requests.get(prev_close_url, headers=headers, timeout=15)
        prev_close = None
        if r_prev.ok:
            j = r_prev.json()
            if isinstance(j, dict) and "results" in j and j["results"]:
                prev_close = j["results"][0].get("c")

        # 2) Ticker Details (Market Cap, Shares Out)
        details_url = f"{BASE_URL}/v3/reference/tickers/{ticker}"
        r_details = requests.get(details_url, headers=headers, timeout=15)
        shares = mcap = name = exchange = currency = None
        if r_details.ok:
            j = r_details.json()
            res = j.get("results") or {}
            shares = res.get("share_class_shares_outstanding")
            mcap = res.get("market_cap")
            name = res.get("name")
            exchange = res.get("primary_exchange")
            currency = res.get("currency_name") or res.get("currency")

        return {
            "ticker": ticker.upper(),
            "name": name,
            "prev_close": prev_close,
            "shares_out": shares,
            "market_cap": mcap,
            "primary_exchange": exchange,
            "currency": currency,
        }
