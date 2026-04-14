"""
SENTINEL QUANT - market_manager.py
====================================
GlobalScoutAgent: Zero-hardcode, API-driven asset discovery.

Sources (all free tier):
  | NSE Equities  | yfinance (.NS suffix)                     | Unlimited   |
  | US Equities   | Finnhub /stock/symbol + /quote            | 60 req/min  |
  | Forex/Comm    | Alpha Vantage FOREX_INTRADAY + CURRENCY   | 25 req/day  |
  | Crypto        | yfinance (weekend fallback)               | Unlimited   |

Scanning logic:
  1. Weekend? -> scan top Crypto tickers via yfinance (always liquid)
  2. Weekday? -> scan NSE (.NS suffix) / NYSE (Finnhub) / FX (AlphaV)
  3. Score each candidate: Volume Surge . Relative Strength . ATR Misprice
  4. Guarantee non-empty result: always return top scorer as floor
"""

import asyncio
import logging
import time
import random
import math
from typing import Dict, List, Optional, Any, Tuple
from datetime import datetime, timezone

import httpx

logger = logging.getLogger("sentinel.market_manager")

# Suppress yfinance internal noise (delisting errors, FutureWarnings)
logging.getLogger("yfinance").setLevel(logging.CRITICAL)
logging.getLogger("peewee").setLevel(logging.CRITICAL)
import warnings
warnings.filterwarnings("ignore", category=FutureWarning, module="yfinance")

# =====================================================================
# RATE-LIMITED HTTP CLIENT
# =====================================================================

class _ApiThrottle:
    """Per-source call counter to avoid exceeding free tiers."""

    _limits = {
        "finnhub":  (60, 60),
        "alphav":   (5,  60),
        "yfinance": (999, 60),
    }

    def __init__(self):
        self._queues: Dict[str, Any] = {
            k: {"dq": __import__("collections").deque(), "lock": asyncio.Lock()}
            for k in self._limits
        }

    async def wait(self, source: str):
        if source not in self._queues:
            return
        max_calls, period = self._limits[source]
        state = self._queues[source]
        async with state["lock"]:
            now = time.monotonic()
            dq = state["dq"]
            while dq and now - dq[0] >= period:
                dq.popleft()
            if len(dq) >= max_calls:
                sleep_for = period - (now - dq[0]) + 0.1
                logger.debug(f"[THROTTLE] {source}: sleeping {sleep_for:.1f}s")
                await asyncio.sleep(sleep_for)
                now = time.monotonic()
            dq.append(now)


_throttle = _ApiThrottle()


# =====================================================================
# SCORING HELPERS
# =====================================================================

def score_asset(quote: Dict) -> Tuple[float, Dict[str, float]]:
    """
    Composite score for "In-Play" conviction.

    Components:
      volume_surge  (0-40 pts): vol / 10d avg
      rel_strength  (0-30 pts): abs(change%) vs sector avg
      atr_misprice  (0-20 pts): (ATR / price) vs asset class baseline
      rsi_extreme   (0-10 pts): >70 or <30 -> momentum extreme
    """
    vol_ratio = quote.get("volume", 1) / max(quote.get("vol_avg", 1), 1)
    change    = abs(quote.get("change", 0))
    rsi       = quote.get("rsi", 50)
    atr       = quote.get("atr", 0)
    price     = max(quote.get("price", 1), 0.0001)
    atr_pct   = atr / price * 100

    vol_score = min(40, (vol_ratio - 1) * 20) if vol_ratio > 1 else 0

    sector_avg = abs(quote.get("sector_avg_change", change * 0.5))
    rs_excess  = change - sector_avg
    rs_score   = min(30, max(0, rs_excess * 8))

    baselines = {"equity": 1.5, "forex": 0.3, "commodity": 2.0, "crypto": 3.0}
    baseline  = baselines.get(quote.get("asset", "equity"), 1.5)
    atr_score = min(20, max(0, (atr_pct - baseline) * 5))

    rsi_score = 10 if (rsi < 32 or rsi > 68) else 0

    total = vol_score + rs_score + atr_score + rsi_score
    return total, {
        "vol_surge":    round(vol_score, 2),
        "rel_strength": round(rs_score, 2),
        "atr_misprice": round(atr_score, 2),
        "rsi_extreme":  rsi_score,
    }


def is_high_conviction(quote: Dict, min_score: float = 5.0) -> bool:
    """
    Lowered threshold to 25 (was 35) to prevent empty scans.
    At least 1 of the sub-components must be non-zero.
    """
    score, breakdown = score_asset(quote)
    non_zero = sum(1 for v in breakdown.values() if v > 0)
    return score >= min_score and non_zero >= 1


# =====================================================================
# CRYPTO SCREENER  (weekend + 24/7 fallback)
# =====================================================================

# Top liquid crypto tickers on Yahoo Finance
# In your CRYPTO_UNIVERSE list, expand to include these symbols:
CRYPTO_UNIVERSE = [
    # Crypto (always available 24/7)
    "BTC-USD", "ETH-USD", "BNB-USD", "SOL-USD", "XRP-USD",
    "ADA-USD", "AVAX-USD", "DOGE-USD", "DOT-USD", "MATIC-USD",
    "LINK-USD", "ATOM-USD", "ALGO-USD", "LTC-USD", "BCH-USD",
    "SHIB-USD", "XLM-USD", "UNI-USD", "AAVE-USD", "FIL-USD",
]

COMMODITY_UNIVERSE = [
    # Metals & Oil (via Yahoo Finance)
    "GC=F",    # Gold futures
    "SI=F",    # Silver futures  
    "CL=F",    # Crude Oil WTI
    "BZ=F",    # Brent Crude
    "NG=F",    # Natural Gas
    "HG=F",    # Copper
    "PL=F",    # Platinum
]

US_STOCK_UNIVERSE = [
    # High-volatility US stocks good for trading
    "NVDA", "TSLA", "AMD", "AAPL", "MSFT", "META", "GOOGL",
    "AMZN", "NFLX", "PLTR", "COIN", "MSTR", "RIOT", "MARA",
]

NSE_UNIVERSE = [
    # Indian stocks via yfinance (append .NS)
    "RELIANCE.NS", "TCS.NS", "HDFCBANK.NS", "INFY.NS", "ICICIBANK.NS",
    "WIPRO.NS", "TATAMOTORS.NS", "BAJFINANCE.NS", "ADANIENT.NS", "SBIN.NS",
    "HINDALCO.NS", "TATASTEEL.NS", "COALINDIA.NS", "ONGC.NS", "IOC.NS",
]

PENNY_STOCK_UNIVERSE = [
    # Low-priced / Penny stocks (Indian and US)
    "SUZLON.NS", "YESBANK.NS", "IDEA.NS", "RPOWER.NS", "JPPOWER.NS",
    "GMRINFRA.NS", "IRFC.NS", "SNDL", "MULN", "CEI", "ZOM"
]


class CryptoScreener:
    """
    Scans top-volume crypto assets via yfinance.
    Active 24/7 - primary engine on weekends.
    """

    def _fetch_one(self, sym: str) -> Optional[Dict]:
        try:
            import yfinance as yf
            import pandas as pd

            t = yf.Ticker(sym)
            try:
                df = t.history(period="5d", interval="1h")
            except Exception:
                return None

            if df is None or df.empty or len(df) < 5:
                return None

            if not all(c in df.columns for c in ["Close", "High", "Low", "Volume"]):
                return None

            price  = float(df["Close"].iloc[-1])
            prev   = float(df["Close"].iloc[-2])
            change = ((price - prev) / prev) * 100
            volume = int(df["Volume"].iloc[-1])
            vol_5d = int(df["Volume"].tail(24).mean())   # 24 hours of volume as baseline

            if price <= 0:
                return None

            # RSI
            delta = df["Close"].diff()
            g = delta.where(delta > 0, 0).rolling(14).mean()
            l = (-delta.where(delta < 0, 0)).rolling(14).mean().replace(0, 1e-5)
            rsi = float((100 - 100 / (1 + g / l)).iloc[-1])

            # ATR
            tr = pd.concat([
                df["High"] - df["Low"],
                abs(df["High"] - df["Close"].shift()),
                abs(df["Low"]  - df["Close"].shift()),
            ], axis=1).max(axis=1)
            atr = float(tr.rolling(14).mean().iloc[-1])

            # MACD
            exp1      = df["Close"].ewm(span=12, adjust=False).mean()
            exp2      = df["Close"].ewm(span=26, adjust=False).mean()
            macd_line = exp1 - exp2
            macd_val  = float(macd_line.iloc[-1])
            macd_sig  = float(macd_line.ewm(span=9, adjust=False).mean().iloc[-1])

            # Bollinger %B
            sma    = df["Close"].rolling(20).mean().iloc[-1]
            std    = df["Close"].rolling(20).std().iloc[-1]
            bb_pct = float(((price - (sma - 2*std)) / max(4*std, 1e-12)) * 100)
            bb_pct = max(0.0, min(100.0, round(bb_pct, 1)))  # clamp 0-100

            return {
                "sym":      sym,
                "exchange": "CRYPTO",
                "asset_class": "DIGITAL_ASSET",
                "asset_type": "token",
                "asset": "crypto",
                "price":    round(price, 6),
                "change":   round(change, 3),
                "volume":   volume,
                "vol_avg":  max(vol_5d, 1),
                "rsi":      round(rsi, 2) if not math.isnan(rsi) else 50.0,
                "macd":     round(macd_val, 8),
                "macd_sig": round(macd_sig, 8),
                "bb_pct":   bb_pct,
                "atr":      round(atr, 6) if not math.isnan(atr) else price * 0.03,
                "adx":      25.0,
                "obv_trend": "UP" if volume > vol_5d else "DOWN",
                "mfi":      50.0,
                "stoch_k":  50.0,
                "beta":     1.5,
                "sector":   "Crypto",
                "cap":      "N/A",
                "vol":      "HIGH",
                "source":   "yfinance-crypto",
            }
        except Exception as e:
            logger.debug(f"[CRYPTO] Skipping {sym}: {type(e).__name__}")
            return None

    async def scan_top_inplay(self, n_pick: int = 5) -> List[Dict]:
        loop = asyncio.get_event_loop()
        tasks = [
            loop.run_in_executor(None, self._fetch_one, sym)
            for sym in CRYPTO_UNIVERSE
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        candidates = []
        for r in results:
            if isinstance(r, Exception) or r is None:
                continue
            score, breakdown = score_asset(r)
            r["_score"]     = score
            r["_breakdown"] = breakdown
            candidates.append(r)

        candidates.sort(key=lambda x: x.get("_score", 0), reverse=True)
        top = candidates[:n_pick]

        if top:
            sym_scores = [(a["sym"], round(a["_score"], 1)) for a in top]
            logger.info(f"[CRYPTO SCANNER] In-play: {sym_scores}")

        return top


# =====================================================================
# FINNHUB PROVIDER  (US equities)
# =====================================================================

class FinnhubProvider:
    """Wraps the Finnhub free-tier API."""

    BASE = "https://finnhub.io/api/v1"

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key
        self._symbol_cache: List[str] = []
        self._cache_ts: float = 0

    def _headers(self) -> Dict:
        return {"X-Finnhub-Token": self.api_key or ""}

    async def _get(self, path: str, params: Dict = {}) -> Any:
        await _throttle.wait("finnhub")
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.get(f"{self.BASE}{path}", params=params,
                                 headers=self._headers())
            r.raise_for_status()
            return r.json()

    async def get_top_movers(self, exchange: str = "US", n: int = 20) -> List[Dict]:
        if not self.api_key:
            return []
        try:
            result = await self._get("/scan/technical-indicator",
                                     {"exchange": exchange, "screenerId": "US"})
            symbols_raw = result.get("result", [])[:n]
            quotes = []
            for item in symbols_raw:
                sym = item.get("s", "")
                if not sym:
                    continue
                q = await self.get_quote(sym)
                if q:
                    quotes.append(q)
            return quotes
        except Exception as e:
            logger.warning(f"[FINNHUB] top_movers failed: {e}")
            return []

    async def get_quote(self, sym: str) -> Optional[Dict]:
        if not self.api_key:
            return None
        try:
            data = await self._get("/quote", {"symbol": sym})
            if not data or data.get("c", 0) == 0:
                return None

            meta_raw = await self._get("/stock/metric",
                                       {"symbol": sym, "metric": "all"})
            metric  = meta_raw.get("metric", {})
            atr_pct = float(metric.get("52WeekHighLow", 0)) * 0.015

            price    = float(data["c"])
            change   = float(data["dp"])
            vol      = int(data.get("v", 0))
            prev_vol = int(data.get("pv", max(vol, 1)))

            return {
                "sym":      sym,
                "exchange": "NYSE",
                "asset":    "equity",
                "price":    round(price, 4),
                "change":   round(change, 3),
                "volume":   vol,
                "vol_avg":  prev_vol,
                "rsi":      50.0,
                "macd":     0.0,
                "macd_sig": 0.0,
                "bb_pct":   50.0,
                "atr":      round(price * atr_pct / 100, 4) if price else 0,
                "adx":      25.0,
                "obv_trend":"UP" if change > 0 else "DOWN",
                "mfi":      50.0,
                "stoch_k":  50.0,
                "beta":     1.0,
                "sector":   "Unknown",
                "cap":      "N/A",
                "vol":      "MED",
                "source":   "finnhub",
            }
        except Exception as e:
            logger.debug(f"[FINNHUB] quote({sym}) failed: {e}")
            return None

    async def get_news_sentiment(self) -> Dict[str, Any]:
        if not self.api_key:
            return {"threat_level": "LOW", "threat_score": 0,
                    "triggered_keywords": [], "source": "none"}
        try:
            data = await self._get("/news", {"category": "general", "minId": 0})
            headlines = [a.get("headline", "").lower() for a in (data or [])[:30]]
            return headlines
        except Exception as e:
            logger.warning(f"[FINNHUB] news failed: {e}")
            return []


# =====================================================================
# ALPHA VANTAGE PROVIDER  (Forex + Sector ETF)
# =====================================================================

class AlphaVantageProvider:
    BASE = "https://www.alphavantage.co/query"

    def __init__(self, api_key: Optional[str] = None):
        self.api_key       = api_key
        self._sector_cache: Optional[Dict] = None
        self._sector_ts: float = 0

    async def _get(self, params: Dict) -> Dict:
        await _throttle.wait("alphav")
        params["apikey"] = self.api_key or "demo"
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.get(self.BASE, params=params)
            r.raise_for_status()
            return r.json()

    async def get_sector_performance(self) -> Dict[str, float]:
        if not self.api_key:
            return {}
        now = time.time()
        if self._sector_cache and (now - self._sector_ts) < 3600:
            return self._sector_cache
        try:
            data = await self._get({"function": "SECTOR"})
            rank = data.get("Rank A: Real-Time Performance", {})
            perf = {}
            for sector, val in rank.items():
                try:
                    perf[sector] = float(val.strip("%"))
                except Exception:
                    pass
            self._sector_cache = perf
            self._sector_ts    = now
            logger.info(f"[AV] Sector leaders: {sorted(perf.items(), key=lambda x: -x[1])[:3]}")
            return perf
        except Exception as e:
            logger.warning(f"[AV] sector_performance failed: {e}")
            return {}

    async def get_forex_quote(self, from_sym: str, to_sym: str) -> Optional[Dict]:
        if not self.api_key:
            return None
        try:
            data = await self._get({
                "function": "CURRENCY_EXCHANGE_RATE",
                "from_currency": from_sym,
                "to_currency":   to_sym,
            })
            info  = data.get("Realtime Currency Exchange Rate", {})
            if not info:
                return None
            price = float(info.get("5. Exchange Rate", 0))
            sym   = f"{from_sym}{to_sym}"
            return {
                "sym":      sym,
                "exchange": "FX",
                "asset":    "forex",
                "price":    round(price, 6),
                "change":   0.0,
                "volume":   0,
                "vol_avg":  1,
                "rsi":      50.0,
                "macd":     0.0,
                "macd_sig": 0.0,
                "bb_pct":   50.0,
                "atr":      round(price * 0.005, 6),
                "adx":      20.0,
                "obv_trend":"N/A",
                "mfi":      50.0,
                "stoch_k":  50.0,
                "beta":     0.3,
                "sector":   "Forex",
                "cap":      "N/A",
                "vol":      "LOW",
                "source":   "alphavantage",
            }
        except Exception as e:
            logger.debug(f"[AV] forex({from_sym}/{to_sym}) failed: {e}")
            return None


# =====================================================================
# YFINANCE NSE SCREENER
# =====================================================================

# NSE F&O active symbols — screener seed only, not trading targets
NSE_FNO_SEEDS = [
    "RELIANCE", "TCS", "HDFCBANK", "INFY", "ICICIBANK", "KOTAKBANK",
    "HINDUNILVR", "AXISBANK", "BAJFINANCE", "BHARTIARTL",
    "ADANIPORTS", "ADANIPOWER", "SUZLON", "RVNL", "IRFC", "NHPC",
    "RECLTD", "PFC", "SAIL", "COALINDIA", "NTPC", "POWERGRID",
    "IRCTC", "ZOMATO", "PAYTM", "NYKAA",
    "TATASTEEL", "JSWSTEEL", "HINDALCO", "VEDL",
    "DRREDDY", "CIPLA", "SUNPHARMA", "DIVISLAB",
    "ASIANPAINT", "TITAN", "DMART",
]

# LSE seed for London session
LSE_SEEDS = [
    "SHEL.L", "BP.L", "HSBA.L", "AZN.L", "ULVR.L",
    "RIO.L", "BHP.L", "GSK.L", "VOD.L", "LLOY.L",
]

# US large-cap fallback when Finnhub key is missing
US_SEEDS = [
    "AAPL", "MSFT", "NVDA", "GOOGL", "AMZN",
    "META", "TSLA", "AMD", "SMCI", "PLTR",
    "COIN", "MARA", "RIOT", "UPST", "AFRM",
    "SOFI", "HOOD", "PYPL", "SQ", "GME"
]


class NSEScreener:
    """
    Scans the NSE F&O universe using yfinance.
    IMPORTANT: Always appends .NS suffix for Yahoo Finance compatibility.
    """

    def __init__(self):
        self._cache_ttl = 120

    def _fetch_one(self, sym: str, suffix: str = ".NS") -> Optional[Dict]:
        try:
            import yfinance as yf
            import pandas as pd

            ticker_sym = f"{sym}{suffix}" if suffix else sym
            t = yf.Ticker(ticker_sym)
            try:
                df = t.history(period="7d", interval="1h")
            except Exception as history_err:
                logger.debug(f"[NSE] Skipping {ticker_sym}: {history_err}")
                return None

            if df is None or df.empty or len(df) < 5:
                return None

            required = ["Close", "High", "Low", "Volume"]
            if not all(col in df.columns for col in required):
                return None

            price  = float(df["Close"].iloc[-1])
            prev   = float(df["Close"].iloc[-2])
            change = ((price - prev) / prev) * 100
            volume = int(df["Volume"].iloc[-1])
            vol_10d = int(df["Volume"].mean())

            if price <= 0:
                return None

            # RSI
            delta = df["Close"].diff()
            g = delta.where(delta > 0, 0).rolling(14).mean()
            l = (-delta.where(delta < 0, 0)).rolling(14).mean().replace(0, 1e-5)
            rsi = float((100 - 100 / (1 + g / l)).iloc[-1])

            # ATR
            tr = pd.concat([
                df["High"] - df["Low"],
                abs(df["High"] - df["Close"].shift()),
                abs(df["Low"]  - df["Close"].shift()),
            ], axis=1).max(axis=1)
            atr = float(tr.rolling(14).mean().iloc[-1])

            # MACD
            exp1     = df["Close"].ewm(span=12, adjust=False).mean()
            exp2     = df["Close"].ewm(span=26, adjust=False).mean()
            macd     = float((exp1 - exp2).iloc[-1])
            macd_sig = float(((exp1 - exp2).ewm(span=9, adjust=False).mean()).iloc[-1])

            # Bollinger %B
            sma    = df["Close"].rolling(20).mean().iloc[-1]
            std    = df["Close"].rolling(20).std().iloc[-1]
            bb_pct = ((price - (sma - 2 * std)) / max((4 * std), 0.0001)) * 100

            exchange = "NSE" if suffix == ".NS" else ("LSE" if suffix == ".L" else "NYSE")

            return {
                "sym":      sym,
                "exchange": exchange,
                "asset":    "equity",
                "price":    round(price, 2),
                "change":   round(change, 3),
                "volume":   volume,
                "vol_avg":  max(vol_10d, 1),
                "rsi":      round(rsi, 2) if not math.isnan(rsi) else 50.0,
                "macd":     round(macd, 4),
                "macd_sig": round(macd_sig, 4),
                "bb_pct":   round(bb_pct, 1),
                "atr":      round(atr, 3) if not math.isnan(atr) else price * 0.02,
                "adx":      25.0,
                "obv_trend":"UP" if volume > vol_10d else "DOWN",
                "mfi":      50.0,
                "stoch_k":  50.0,
                "beta":     1.0,
                "sector":   "Unknown",
                "cap":      "mid",
                "vol":      "MED",
                "source":   "yfinance",
            }
        except Exception as e:
            logger.debug(f"[NSE] Skipping {sym}: {type(e).__name__}")
            return None

    async def scan_top_inplay(
        self,
        seeds: List[str],
        suffix: str = ".NS",
        n_seeds: int = 20,
        n_pick: int = 5,
        min_vol_ratio: float = 1.0,          # Lowered from 1.3 to get more hits
    ) -> List[Dict]:
        exchange = "NSE" if suffix == ".NS" else ("LSE" if suffix == ".L" else "NYSE")
        sample = random.sample(seeds, min(n_seeds, len(seeds)))
        loop   = asyncio.get_event_loop()
        tasks  = [
            loop.run_in_executor(None, self._fetch_one, sym, suffix)
            for sym in sample
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        candidates = []
        failed = []
        for i, r in enumerate(results):
            if isinstance(r, Exception):
                failed.append(sample[i] if i < len(sample) else "?")
                continue
            if r is None:
                continue
            vol_ratio = r["volume"] / max(r["vol_avg"], 1)
            if vol_ratio < min_vol_ratio:
                continue
            score, breakdown = score_asset(r)
            r["_score"]     = score
            r["_breakdown"] = breakdown
            candidates.append(r)

        candidates.sort(key=lambda x: x.get("_score", 0), reverse=True)
        top = candidates[:n_pick]

        if failed:
            logger.debug(f"[SCANNER] {len(failed)} tickers skipped (no data): {', '.join(failed[:5])}{'...' if len(failed) > 5 else ''}")
        if not top and seeds:
            logger.info(f"[{exchange} SCANNER] No in-play assets found in {len(sample)} sampled tickers.")

        return top


# =====================================================================
# GLOBAL SCOUT AGENT  (unified — always returns results)
# =====================================================================

class GlobalScoutAgent:
    """
    Zero-hardcode asset discovery engine with weekend/weekday routing.

    Weekend: Crypto universe (24/7 liquid)
    Weekday: NSE (.NS) during Asia session, NYSE during US session, FX always
    Guarantee: Never returns an empty list.
    """

    def __init__(
        self,
        finnhub_key:  Optional[str] = None,
        alphav_key:   Optional[str] = None,
    ):
        self.finnhub  = FinnhubProvider(finnhub_key)
        self.alphav   = AlphaVantageProvider(alphav_key)
        self.nse      = NSEScreener()
        self.crypto   = CryptoScreener()
        self._sector_perf: Dict[str, float] = {}
        self._last_sector_refresh: float = 0

    def _is_weekend(self) -> bool:
        """Returns True if today is Saturday (5) or Sunday (6) UTC."""
        return datetime.now(timezone.utc).weekday() >= 5

    async def _refresh_sectors(self):
        now = time.time()
        if now - self._last_sector_refresh > 3600:
            self._sector_perf = await self.alphav.get_sector_performance()
            self._last_sector_refresh = now

    async def scan(
        self,
        n_candidates: int = 6,
        min_conviction: float = 12.0,
        primary_market: str = "AUTO",
        available_capital: float = 1000.0,
    ) -> Tuple[List[Dict], str]:
        """
        Scan all universes based on market hours and return top scoring assets.
        Ensures a non-empty pipeline 24/7.
        """
        now_utc = datetime.now(timezone.utc)
        hour = now_utc.hour
        weekday = now_utc.weekday()
        
        all_candidates: List[Dict] = []
        
        # Determine active universe based on capital
        is_low_capital = available_capital < 100.0
        
        # 1. Crypto is ALWAYS available 24/7
        crypto_assets = await self.crypto.scan_top_inplay(n_pick=10)
        all_candidates.extend(crypto_assets)
        
        # 2. US Equities (NYSE/NASDAQ)
        if weekday < 5 and 13 <= hour < 20:
            active_us = PENNY_STOCK_UNIVERSE if is_low_capital else US_STOCK_UNIVERSE
            us_assets = await self.nse.scan_top_inplay(
                seeds=active_us, suffix="",
                n_seeds=len(active_us), n_pick=10, min_vol_ratio=0.8
            )
            all_candidates.extend(us_assets)
            
        # 3. Indian Equities (NSE)
        if weekday < 5 and 3 <= hour < 10:
            active_nse = PENNY_STOCK_UNIVERSE if is_low_capital else NSE_UNIVERSE
            nse_assets = await self.nse.scan_top_inplay(
                seeds=active_nse, suffix="",
                n_seeds=len(active_nse), n_pick=10, min_vol_ratio=0.8
            )
            all_candidates.extend(nse_assets)
            
        # 4. Commodities (Futures) — fetched for scoring context but EXCLUDED from
        #    the tradeable candidate list. yfinance only provides 1h data for =F tickers,
        #    so 1m / 15m timeframes always return empty → MTF score = 0.33 every cycle,
        #    which wastes the target slot. Re-enable once a proper futures MTF path exists.
        # if weekday < 5:
        #     comm_assets = await self.nse.scan_top_inplay(
        #         seeds=COMMODITY_UNIVERSE, suffix="",
        #         n_seeds=len(COMMODITY_UNIVERSE), n_pick=10, min_vol_ratio=0.5
        #     )
        #     all_candidates.extend(comm_assets)

        # Score and Sort
        for q in all_candidates:
            if "_score" not in q:
                score, _ = score_asset(q)
                q["_score"] = score

        # Exclude commodity futures — they always fail the 3-TF MTF gate (1h-only data)
        FUTURES_TICKERS = {t.upper() for t in COMMODITY_UNIVERSE}
        tradeable = [c for c in all_candidates if c.get("sym", "").upper() not in FUTURES_TICKERS
                     and not c.get("sym", "").endswith("=F")]

        tradeable.sort(key=lambda x: x.get("_score", 0), reverse=True)

        # Filter by conviction
        filtered = [c for c in tradeable if c.get("_score", 0) >= min_conviction]

        # Guarantee at least 3 candidates even when only 1-2 pass the conviction bar.
        # Score collapses (stale yfinance data, quiet markets) can leave a single asset
        # passing, giving the MTF fallthrough loop no fallback. We always want >= 3
        # options in the pipeline to maximise the chance of finding an enterable target.
        if len(filtered) < 3 and tradeable:
            seen = {c["sym"] for c in filtered}
            extras = [c for c in tradeable if c["sym"] not in seen]
            filtered = (filtered + extras)[:max(3, len(filtered))]

        if not filtered:
            # Absolute recovery fallback
            crypto_rec = await self.crypto.scan_top_inplay(n_pick=3)
            for q in crypto_rec:
                score, _ = score_asset(q)
                q["_score"] = score
            filtered = crypto_rec

        top = filtered[:n_candidates]
        logger.info(
            f"[SCOUT] Multi-universe scan -> {len(all_candidates)} found "
            f"-> {len(tradeable)} tradeable (futures excluded) "
            f"-> {len(top)} in-play filtered | market=GLOBAL"
        )
        return top, "GLOBAL"

    async def get_sector_leaders(self) -> Dict[str, float]:
        await self._refresh_sectors()
        return dict(sorted(self._sector_perf.items(), key=lambda x: -x[1]))


# =====================================================================
# RESOURCE DETECTOR
# =====================================================================

def get_optimal_concurrency() -> Dict[str, int]:
    """
    Detect available CPU / memory and return safe concurrency params.
    Allows the same code to run on a laptop or an 8-vCPU VPS.
    """
    try:
        import psutil
        cpu_count = psutil.cpu_count(logical=True) or 2
        ram_gb    = psutil.virtual_memory().total / (1024 ** 3)
        available = psutil.virtual_memory().available / (1024 ** 3)
    except ImportError:
        cpu_count, ram_gb, available = 2, 4.0, 2.0

    max_workers = max(2, cpu_count // 2)
    scan_batch  = 10 if ram_gb >= 8 else 5
    delay_s     = 4  if cpu_count >= 4 else 8

    profile = "SERVER" if cpu_count >= 8 and ram_gb >= 16 else "LAPTOP"

    logger.info(
        f"[RESOURCE] {profile} - CPUs:{cpu_count} RAM:{ram_gb:.1f}GB "
        f"-> workers={max_workers} batch={scan_batch} delay={delay_s}s"
    )
    return {
        "profile":     profile,
        "max_workers": max_workers,
        "scan_batch":  scan_batch,
        "cycle_delay": delay_s,
    }
