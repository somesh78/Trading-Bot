"""
SENTINEL QUANT — Global Data Layer
=====================================
Handles multi-asset, multi-market data across time zones.

- TimezoneManager: Routes Scout focus to the live exchange
- MarketDataProvider: Unified interface over FMP + yfinance fallback
- NewsMonitor: Geopolitical sentiment via free news APIs
- GlobalUniverse: Equities (NSE/NYSE/LSE), Forex, Commodities
"""

import asyncio
import logging
import httpx
import random
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Any, Tuple
from zoneinfo import ZoneInfo

logger = logging.getLogger("sentinel.data")


# ═══════════════════════════════════════════════════════════════
# GLOBAL ASSET UNIVERSE
# ═══════════════════════════════════════════════════════════════

GLOBAL_UNIVERSE: Dict[str, List[Dict]] = {
    "NSE": [
        {"sym": "RELIANCE",   "exchange": "NSE", "asset": "equity", "sector": "Congl",   "cap": "large", "vol": "LOW",  "beta": 0.9,  "base_price": 2890},
        {"sym": "TCS",        "exchange": "NSE", "asset": "equity", "sector": "IT",      "cap": "large", "vol": "LOW",  "beta": 0.7,  "base_price": 3950},
        {"sym": "HDFCBANK",   "exchange": "NSE", "asset": "equity", "sector": "Bank",    "cap": "large", "vol": "LOW",  "beta": 0.8,  "base_price": 1650},
        {"sym": "INFY",       "exchange": "NSE", "asset": "equity", "sector": "IT",      "cap": "large", "vol": "LOW",  "beta": 0.75, "base_price": 1620},
        {"sym": "SUZLON",     "exchange": "NSE", "asset": "equity", "sector": "Energy",  "cap": "small", "vol": "HIGH", "beta": 1.8,  "base_price": 58},
        {"sym": "RVNL",       "exchange": "NSE", "asset": "equity", "sector": "Rail",    "cap": "mid",   "vol": "MED",  "beta": 1.5,  "base_price": 290},
        {"sym": "ADANIPOWER", "exchange": "NSE", "asset": "equity", "sector": "Power",   "cap": "mid",   "vol": "MED",  "beta": 1.7,  "base_price": 540},
        {"sym": "IRFC",       "exchange": "NSE", "asset": "equity", "sector": "Finance", "cap": "mid",   "vol": "MED",  "beta": 1.2,  "base_price": 155},
    ],
    "NYSE": [
        {"sym": "AAPL",   "exchange": "NYSE", "asset": "equity", "sector": "Tech",     "cap": "mega",  "vol": "LOW",  "beta": 1.2,  "base_price": 213},
        {"sym": "NVDA",   "exchange": "NYSE", "asset": "equity", "sector": "Semicon",  "cap": "mega",  "vol": "MED",  "beta": 1.8,  "base_price": 875},
        {"sym": "TSLA",   "exchange": "NYSE", "asset": "equity", "sector": "EV",       "cap": "large", "vol": "HIGH", "beta": 2.1,  "base_price": 175},
        {"sym": "AMZN",   "exchange": "NYSE", "asset": "equity", "sector": "Ecomm",   "cap": "mega",  "vol": "LOW",  "beta": 1.3,  "base_price": 192},
        {"sym": "MSFT",   "exchange": "NYSE", "asset": "equity", "sector": "Tech",     "cap": "mega",  "vol": "LOW",  "beta": 0.9,  "base_price": 415},
        {"sym": "META",   "exchange": "NYSE", "asset": "equity", "sector": "Social",   "cap": "mega",  "vol": "MED",  "beta": 1.5,  "base_price": 503},
    ],
    "LSE": [
        {"sym": "GSK",    "exchange": "LSE", "asset": "equity", "sector": "Pharma",   "cap": "large", "vol": "LOW",  "beta": 0.6,  "base_price": 1650},
        {"sym": "HSBA",   "exchange": "LSE", "asset": "equity", "sector": "Bank",     "cap": "mega",  "vol": "LOW",  "beta": 0.75, "base_price": 768},
        {"sym": "RIO",    "exchange": "LSE", "asset": "equity", "sector": "Mining",   "cap": "large", "vol": "MED",  "beta": 1.2,  "base_price": 4980},
        {"sym": "BP",     "exchange": "LSE", "asset": "equity", "sector": "Energy",   "cap": "large", "vol": "MED",  "beta": 0.85, "base_price": 425},
    ],
    "FOREX": [
        {"sym": "EURUSD", "exchange": "FX",  "asset": "forex",  "sector": "Major",    "cap": "N/A",   "vol": "LOW",  "beta": 0.3,  "base_price": 1.075},
        {"sym": "GBPUSD", "exchange": "FX",  "asset": "forex",  "sector": "Major",    "cap": "N/A",   "vol": "MED",  "beta": 0.5,  "base_price": 1.265},
        {"sym": "USDINR", "exchange": "FX",  "asset": "forex",  "sector": "EM",       "cap": "N/A",   "vol": "LOW",  "beta": 0.2,  "base_price": 83.5},
        {"sym": "USDJPY", "exchange": "FX",  "asset": "forex",  "sector": "Major",    "cap": "N/A",   "vol": "LOW",  "beta": 0.25, "base_price": 150.2},
    ],
    "COMMODITIES": [
        {"sym": "XAUUSD", "exchange": "CMX", "asset": "commodity", "sector": "Metals",  "cap": "N/A",   "vol": "MED",  "beta": 0.4,  "base_price": 2340},
        {"sym": "XAGUSD", "exchange": "CMX", "asset": "commodity", "sector": "Metals",  "cap": "N/A",   "vol": "HIGH", "beta": 0.8,  "base_price": 27.5},
        {"sym": "USOIL",  "exchange": "CMX", "asset": "commodity", "sector": "Energy",  "cap": "N/A",   "vol": "HIGH", "beta": 0.9,  "base_price": 82.5},
        {"sym": "NATGAS", "exchange": "CMX", "asset": "commodity", "sector": "Energy",  "cap": "N/A",   "vol": "HIGH", "beta": 1.1,  "base_price": 2.15},
    ],
}

# Flat list for simple iteration
ALL_ASSETS = [a for assets in GLOBAL_UNIVERSE.values() for a in assets]


# ═══════════════════════════════════════════════════════════════
# TIMEZONE MANAGER — Routes Scout focus to live exchanges
# ═══════════════════════════════════════════════════════════════

class TimezoneManager:
    """
    Determines which global exchange is currently "live" and routes
    the Scout agent's focus accordingly. Uses actual market hours.
    """

    MARKET_HOURS = {
        # (open_hour_utc, close_hour_utc, tz_name)
        "NSE":  (3, 10, "Asia/Kolkata"),      # 09:15–15:30 IST = 03:45–10:00 UTC
        "HKEX": (1, 8,  "Asia/Hong_Kong"),    # 09:30–16:00 HKT = 01:30–08:00 UTC
        "LSE":  (8, 16, "Europe/London"),     # 08:00–16:30 GMT = 08:00–16:30 UTC
        "NYSE": (13, 20, "America/New_York"), # 09:30–16:00 EST = 13:30–20:00 UTC
        "CMX":  (0,  23, "UTC"),              # Commodities/Forex: near-24h
        "FX":   (0,  23, "UTC"),
    }

    def get_active_markets(self) -> List[str]:
        """Return list of markets currently open."""
        now_utc = datetime.now(timezone.utc)
        hour = now_utc.hour
        active = []
        for market, (open_h, close_h, _) in self.MARKET_HOURS.items():
            if open_h <= hour < close_h:
                active.append(market)
        # Always include commodities and forex as they're near-24h
        for always_on in ["CMX", "FX"]:
            if always_on not in active:
                active.append(always_on)
        return active

    def get_primary_exchange(self) -> str:
        """Return the single most active exchange right now."""
        now_utc = datetime.now(timezone.utc)
        hour = now_utc.hour
        # Priority: NYSE > NSE > LSE > HKEX > FX
        priority = [
            ("NYSE", 13, 20),
            ("NSE",  3,  10),
            ("LSE",  8,  16),
            ("HKEX", 1,  8),
        ]
        for market, open_h, close_h in priority:
            if open_h <= hour < close_h:
                return market
        return "FX"  # Default to forex when all equity markets closed

    def get_universe_for_active_markets(self) -> Tuple[List[Dict], str]:
        """Return the asset universe for currently active markets."""
        active = self.get_active_markets()
        primary = self.get_primary_exchange()
        universe = []
        for market in active:
            if market in GLOBAL_UNIVERSE:
                universe.extend(GLOBAL_UNIVERSE[market])
        # Always add commodities + forex
        universe.extend(GLOBAL_UNIVERSE["FOREX"])
        universe.extend(GLOBAL_UNIVERSE["COMMODITIES"])
        # Deduplicate
        seen = set()
        unique = []
        for a in universe:
            if a["sym"] not in seen:
                seen.add(a["sym"])
                unique.append(a)
        return unique, primary

    def get_status(self) -> dict:
        active = self.get_active_markets()
        primary = self.get_primary_exchange()
        now_utc = datetime.now(timezone.utc)
        return {
            "utc_time": now_utc.strftime("%H:%M UTC"),
            "primary": primary,
            "active_markets": active,
            "total_assets": sum(len(GLOBAL_UNIVERSE.get(m, [])) for m in active),
        }


# ═══════════════════════════════════════════════════════════════
# NEWS MONITOR — Geopolitical Sentiment (Black Swan Guard)
# ═══════════════════════════════════════════════════════════════

BLACK_SWAN_KEYWORDS = [
    "conflict", "war", "rate hike", "halt", "crisis", "default",
    "sanctions", "nuclear", "recession", "collapse", "emergency",
    "terror", "assassination", "coup", "invasion"
]

class NewsMonitor:
    """
    Monitors news headlines for Black Swan signals.
    Uses NewsData.io free tier (100 req/day) or falls back to simulation.
    """

    def __init__(self, api_key: Optional[str] = None, is_paper: bool = True):
        self.api_key = api_key
        self.is_paper = is_paper
        self.threat_level = "LOW"
        self.threat_score = 0
        self.triggered_keywords: List[str] = []
        self.last_headlines: List[str] = []

    async def scan(self) -> Dict[str, Any]:
        """
        Fetch and analyze global financial news.
        Returns threat assessment.
        """
        if self.api_key:
            try:
                return await self._fetch_live_news()
            except Exception as e:
                logger.warning(f"[NEWS] Live fetch failed: {e}, using simulation")

        return self._simulate_news()

    async def _fetch_live_news(self) -> Dict[str, Any]:
        """Fetch from NewsData.io (free tier: 100 requests/day)."""
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.get(
                "https://newsdata.io/api/1/news",
                params={
                    "apikey": self.api_key,
                    "q": "market stock finance economy",
                    "language": "en",
                    "category": "business"
                }
            )
            r.raise_for_status()
            data = r.json()
            headlines = [a["title"].lower() for a in data.get("results", [])[:20] if a.get("title")]
            return self._analyze_headlines(headlines)

    def _simulate_news(self) -> Dict[str, Any]:
        """Simulate news when API is unavailable."""
        if self.is_paper:
            logger.info("[NEWS] Paper mode: news monitor disabled, threat hardcoded to LOW")
            self.threat_level = "LOW"
            self.threat_score = 0
            self.triggered_keywords = []
            return {
                "threat_level": "LOW",
                "threat_score": 0,
                "triggered_keywords": [],
                "headline_count": 0
            }

        # Real simulation (non-paper)
        normal_headlines = [
            "fed signals steady rate path amid strong employment data",
            "tech stocks rally on ai optimism",
            "nse gains on positive global cues",
            "crude oil stabilizes after opec+ decision",
        ]
        headlines = normal_headlines
        return self._analyze_headlines(headlines)

    def _analyze_headlines(self, headlines: List[str]) -> Dict[str, Any]:
        """Score headlines for black swan keywords."""
        self.last_headlines = headlines
        self.triggered_keywords = []
        match_count = 0

        for headline in headlines:
            for kw in BLACK_SWAN_KEYWORDS:
                if kw in headline and kw not in self.triggered_keywords:
                    self.triggered_keywords.append(kw)
                    match_count += 1

        # Score: 0-100
        self.threat_score = min(100, match_count * 15)

        if self.threat_score >= 45:
            self.threat_level = "EXTREME"
        elif self.threat_score >= 30:
            self.threat_level = "HIGH"
        elif self.threat_score >= 15:
            self.threat_level = "MEDIUM"
        else:
            self.threat_level = "LOW"

        logger.info(f"[NEWS] Threat: {self.threat_level} ({self.threat_score}) | Keywords: {self.triggered_keywords}")
        return {
            "threat_level": self.threat_level,
            "threat_score": self.threat_score,
            "triggered_keywords": self.triggered_keywords,
            "headline_count": len(headlines)
        }

    def should_move_to_breakeven(self) -> bool:
        """Return True if open positions should immediately move SLs to break-even."""
        return self.threat_level in ("HIGH", "EXTREME")

    def get_status(self) -> dict:
        return {
            "threat_level": self.threat_level,
            "threat_score": self.threat_score,
            "keywords": self.triggered_keywords,
            "breakeven_mode": self.should_move_to_breakeven()
        }


# ═══════════════════════════════════════════════════════════════
# MARKET DATA PROVIDER — FMP + yfinance hybrid
# ═══════════════════════════════════════════════════════════════

class MarketDataProvider:
    """
    Unified market data interface.
    Priority: FMP API → yfinance → simulation fallback.
    Handles equities, forex, and commodities.
    """

    def __init__(self, fmp_key: Optional[str] = None):
        self.fmp_key = fmp_key
        self.cache: Dict[str, Dict] = {}
        self.cache_ttl = 30  # seconds
        self.cache_timestamps: Dict[str, float] = {}

    async def get_quote(self, sym: str, asset_meta: Dict) -> Optional[Dict]:
        """Fetch a quote for any asset type."""
        import time
        # Check cache
        if sym in self.cache:
            age = time.time() - self.cache_timestamps.get(sym, 0)
            if age < self.cache_ttl:
                return self.cache[sym]

        asset_type = asset_meta.get("asset", "equity")
        exchange = asset_meta.get("exchange", "NSE")

        # Try FMP for equities and commodities
        if self.fmp_key and asset_type in ("equity", "commodity"):
            result = await self._fetch_fmp(sym, asset_meta)
            if result:
                self.cache[sym] = result
                self.cache_timestamps[sym] = time.time()
                return result

        # Try yfinance for NSE equities
        if exchange == "NSE":
            result = await asyncio.get_event_loop().run_in_executor(
                None, self._fetch_yfinance_nse, sym, asset_meta
            )
            if result:
                self.cache[sym] = result
                self.cache_timestamps[sym] = time.time()
                return result

        # Always fall back to simulation
        return self._simulate_quote(asset_meta)

    async def _fetch_fmp(self, sym: str, meta: Dict) -> Optional[Dict]:
        """Fetch from Financial Modeling Prep API."""
        try:
            # FMP uses different ticker formats
            fmp_sym = sym
            if meta.get("exchange") == "LSE":
                fmp_sym = f"{sym}.L"

            async with httpx.AsyncClient(timeout=8.0) as client:
                r = await client.get(
                    f"https://financialmodelingprep.com/api/v3/quote/{fmp_sym}",
                    params={"apikey": self.fmp_key}
                )
                r.raise_for_status()
                data = r.json()
                if not data:
                    return None
                q = data[0]
                return self._normalize_fmp(q, meta)
        except Exception as e:
            logger.debug(f"[FMP] Failed for {sym}: {e}")
            return None

    def _normalize_fmp(self, q: Dict, meta: Dict) -> Dict:
        """Normalize FMP response to our internal format."""
        price = float(q.get("price", meta["base_price"]))
        change_pct = float(q.get("changesPercentage", 0))
        volume = int(q.get("volume", 100000))
        avg_volume = int(q.get("avgVolume", volume))

        return {
            "sym": meta["sym"],
            "exchange": meta["exchange"],
            "asset": meta["asset"],
            "price": round(price, 4),
            "change": round(change_pct, 3),
            "volume": volume,
            "vol_avg": avg_volume,
            "rsi": 50.0,   # FMP doesn't give indicators in basic quote
            "macd": 0.0,
            "macd_sig": 0.0,
            "bb_pct": 50.0,
            "atr": round(price * 0.015 * meta.get("beta", 1.0), 4),
            "adx": 25.0,
            "obv_trend": "UP" if change_pct > 0 else "DOWN",
            "mfi": 50.0,
            "stoch_k": 50.0,
            "beta": meta.get("beta", 1.0),
            "sector": meta.get("sector", "N/A"),
            "cap": meta.get("cap", "N/A"),
            "vol": meta.get("vol", "MED"),
            "source": "FMP"
        }

    def _fetch_yfinance_nse(self, sym: str, meta: Dict) -> Optional[Dict]:
        """Synchronous yfinance fetch (run in executor)."""
        try:
            import yfinance as yf
            import pandas as pd

            ticker = yf.Ticker(f"{sym}.NS")
            df = ticker.history(period="30d", interval="1d")
            if df.empty:
                return None

            price = float(df['Close'].iloc[-1])
            prev = float(df['Close'].iloc[-2])
            change_pct = ((price - prev) / prev) * 100

            # RSI
            delta = df['Close'].diff()
            gain = delta.where(delta > 0, 0).rolling(14).mean()
            loss = (-delta.where(delta < 0, 0)).rolling(14).mean().replace(0, 1e-5)
            rsi = (100 - (100 / (1 + gain / loss))).iloc[-1]

            # MACD
            exp1 = df['Close'].ewm(span=12, adjust=False).mean()
            exp2 = df['Close'].ewm(span=26, adjust=False).mean()
            macd = (exp1 - exp2).iloc[-1]
            macd_sig = ((exp1 - exp2).ewm(span=9, adjust=False).mean()).iloc[-1]

            # ATR
            high_low = df['High'] - df['Low']
            high_cp = abs(df['High'] - df['Close'].shift())
            low_cp = abs(df['Low'] - df['Close'].shift())
            tr = pd.concat([high_low, high_cp, low_cp], axis=1).max(axis=1)
            atr = tr.rolling(14).mean().iloc[-1]

            # Bollinger
            sma = df['Close'].rolling(20).mean().iloc[-1]
            std = df['Close'].rolling(20).std().iloc[-1]
            bb_up = sma + 2 * std
            bb_lo = sma - 2 * std
            bb_pct = ((price - bb_lo) / (bb_up - bb_lo) * 100) if (bb_up - bb_lo) != 0 else 50.0

            volume = int(df['Volume'].iloc[-1])
            vol_avg = int(df['Volume'].mean())

            return {
                "sym": sym,
                "exchange": "NSE",
                "asset": "equity",
                "price": round(price, 2),
                "change": round(change_pct, 3),
                "rsi": round(float(rsi), 2) if not pd.isna(rsi) else 50.0,
                "macd": round(float(macd), 4) if not pd.isna(macd) else 0.0,
                "macd_sig": round(float(macd_sig), 4) if not pd.isna(macd_sig) else 0.0,
                "bb_pct": round(float(bb_pct), 1),
                "volume": volume,
                "vol_avg": max(vol_avg, 1),
                "atr": round(float(atr), 3) if not pd.isna(atr) else price * 0.02,
                "adx": 25.0,
                "obv_trend": "UP" if volume > vol_avg else "DOWN",
                "mfi": round(random.random() * 100, 1),
                "stoch_k": round(random.random() * 100, 1),
                "beta": meta.get("beta", 1.0),
                "sector": meta.get("sector", "N/A"),
                "cap": meta.get("cap", "N/A"),
                "vol": meta.get("vol", "MED"),
                "source": "yfinance"
            }
        except Exception as e:
            logger.debug(f"[yfinance] Failed for {sym}: {e}")
            return None

    def _simulate_quote(self, meta: Dict) -> Dict:
        """Simulate a realistic price quote for any asset."""
        base = meta["base_price"]
        beta = meta.get("beta", 1.0)
        noise = (random.random() - 0.5) * 0.018 * beta
        price = round(base * (1 + noise), 4)
        change = round(noise * 100, 3)
        volume = int(100000 + random.random() * 500000)
        vol_avg = int(volume * (0.7 + random.random() * 0.5))

        return {
            "sym": meta["sym"],
            "exchange": meta["exchange"],
            "asset": meta.get("asset", "equity"),
            "price": price,
            "change": change,
            "rsi": round(35 + random.random() * 40, 1),
            "macd": round((random.random() - 0.5) * base * 0.001, 4),
            "macd_sig": round((random.random() - 0.5) * base * 0.0008, 4),
            "bb_pct": round(random.random() * 100, 1),
            "volume": volume,
            "vol_avg": vol_avg,
            "atr": round(base * 0.016 * beta, 4),
            "adx": round(15 + random.random() * 45, 1),
            "obv_trend": "UP" if random.random() > 0.5 else "DOWN",
            "mfi": round(30 + random.random() * 50, 1),
            "stoch_k": round(20 + random.random() * 70, 1),
            "beta": beta,
            "sector": meta.get("sector", "N/A"),
            "cap": meta.get("cap", "N/A"),
            "vol": meta.get("vol", "MED"),
            "source": "sim"
        }
