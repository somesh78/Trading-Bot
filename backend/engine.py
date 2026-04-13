import random
import math
import yfinance as yf
import pandas as pd
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Any
from pydantic import BaseModel

# ── CONSTANTS ──────────────────────────────────────────────────────
NSE_UNIVERSE = [
    {"sym": "SUZLON", "price": 58, "beta": 1.8, "sector": "Energy", "cap": "small", "vol": "HIGH"},
    {"sym": "YESBANK", "price": 21, "beta": 2.1, "sector": "Bank", "cap": "small", "vol": "HIGH"},
    {"sym": "JPPOWER", "price": 18, "beta": 2.0, "sector": "Power", "cap": "small", "vol": "HIGH"},
    {"sym": "RPOWER", "price": 22, "beta": 2.2, "sector": "Power", "cap": "small", "vol": "HIGH"},
    {"sym": "TRIDENT", "price": 38, "beta": 1.6, "sector": "Textile", "cap": "small", "vol": "HIGH"},
    {"sym": "IRFC", "price": 155, "beta": 1.2, "sector": "Finance", "cap": "mid", "vol": "MED"},
    {"sym": "RVNL", "price": 290, "beta": 1.5, "sector": "Rail", "cap": "mid", "vol": "MED"},
    {"sym": "NHPC", "price": 85, "beta": 1.1, "sector": "Power", "cap": "mid", "vol": "MED"},
    {"sym": "SAIL", "price": 120, "beta": 1.4, "sector": "Steel", "cap": "mid", "vol": "MED"},
    {"sym": "RECLTD", "price": 480, "beta": 1.3, "sector": "Finance", "cap": "mid", "vol": "MED"},
    {"sym": "ADANIPOWER", "price": 540, "beta": 1.7, "sector": "Power", "cap": "mid", "vol": "MED"},
    {"sym": "IRCON", "price": 195, "beta": 1.3, "sector": "Rail", "cap": "mid", "vol": "MED"},
    {"sym": "RELIANCE", "price": 2890, "beta": 0.9, "sector": "Congl", "cap": "large", "vol": "LOW"},
    {"sym": "TCS", "price": 3950, "beta": 0.7, "sector": "IT", "cap": "large", "vol": "LOW"},
    {"sym": "HDFCBANK", "price": 1650, "beta": 0.8, "sector": "Bank", "cap": "large", "vol": "LOW"},
    {"sym": "INFY", "price": 1620, "beta": 0.75, "sector": "IT", "cap": "large", "vol": "LOW"},
]

HISTORICAL_CRASHES = [
    {"year": 1992, "name": "Harshad Mehta Scam", "drawdown": -54, "duration": 18, "trigger": "fraud", "vix_spike": 4.2},
    {"year": 2000, "name": "Dot-com Crash", "drawdown": -58, "duration": 30, "trigger": "bubble", "vix_spike": 5.1},
    {"year": 2001, "name": "9/11 Shock", "drawdown": -28, "duration": 4, "trigger": "exogenous", "vix_spike": 6.8},
    {"year": 2004, "name": "Election Crash", "drawdown": -22, "duration": 2, "trigger": "political", "vix_spike": 3.0},
    {"year": 2008, "name": "Global Financial Crisis", "drawdown": -64, "duration": 14, "trigger": "credit", "vix_spike": 8.5},
    {"year": 2010, "name": "Euro Debt Crisis", "drawdown": -18, "duration": 3, "trigger": "macro", "vix_spike": 3.2},
    {"year": 2011, "name": "US Downgrade", "drawdown": -20, "duration": 2, "trigger": "macro", "vix_spike": 3.5},
    {"year": 2013, "name": "Taper Tantrum", "drawdown": -16, "duration": 2, "trigger": "rates", "vix_spike": 2.8},
    {"year": 2015, "name": "China Shock", "drawdown": -22, "duration": 4, "trigger": "global", "vix_spike": 3.3},
    {"year": 2016, "name": "Demonetisation", "drawdown": -18, "duration": 2, "trigger": "policy", "vix_spike": 2.5},
    {"year": 2018, "name": "IL&FS Crisis", "drawdown": -14, "duration": 3, "trigger": "credit", "vix_spike": 2.2},
    {"year": 2020, "name": "COVID Crash", "drawdown": -40, "duration": 1.5, "trigger": "exogenous", "vix_spike": 9.2},
    {"year": 2022, "name": "Rate Shock+Ukraine", "drawdown": -24, "duration": 6, "trigger": "macro+geo", "vix_spike": 3.8},
]

class Trade(BaseModel):
    n: int
    sym: str
    action: str
    entry: float
    sl: float
    tp: float
    qty: int
    exit: float
    pnl: float
    win: bool
    conf: int
    rr: float
    regime: str
    time: str

class EngineState:
    def __init__(self, capital: float = 200.0):
        self.capital = capital
        self.start_cap = capital
        self.trades: List[Trade] = []
        self.equity: List[float] = [capital]
        self.pnl = 0.0
        self.wins = 0
        self.losses = 0
        self.streak = 0
        self.max_streak = 0
        self.peak_capital = capital
        self.max_dd = 0.0
        self.pnl_history: List[float] = []
        self.crash_score = 0
        self.regime = "unknown"
        self.indicators = {}

    def to_dict(self):
        return {
            "capital": self.capital,
            "pnl": self.pnl,
            "wins": self.wins,
            "losses": self.losses,
            "maxDD": self.max_dd,
            "crashScore": self.crash_score,
            "regime": self.regime,
            "indicators": self.indicators,
            "tradeCount": len(self.trades)
        }

class TradingEngine:
    def __init__(self, capital: float = 200.0):
        self.state = EngineState(capital)

    def get_live_data(self, sym: str) -> Dict[str, Any]:
        """Fetch real-time data from Yahoo Finance for NSE stocks"""
        ticker_sym = f"{sym}.NS"
        try:
            ticker = yf.Ticker(ticker_sym)
            
            # Get historical data for indicators (last 30 days)
            df = ticker.history(period="30d", interval="1d")
            if df.empty:
                return None
                
            current_close = df['Close'].iloc[-1]
            prev_close = df['Close'].iloc[-2]
            change_pct = ((current_close - prev_close) / prev_close) * 100
            
            # Simple technical indicators
            # 1. RSI (14)
            delta = df['Close'].diff()
            gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
            loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
            
            # Avoid division by zero
            loss = loss.replace(0, 0.00001)
            rs = gain / loss
            rsi_series = 100 - (100 / (1 + rs))
            rsi_val = rsi_series.iloc[-1] if not rsi_series.empty else 50.0
            
            # 2. MACD
            exp1 = df['Close'].ewm(span=12, adjust=False).mean()
            exp2 = df['Close'].ewm(span=26, adjust=False).mean()
            macd_series = exp1 - exp2
            macd_sig_series = macd_series.ewm(span=9, adjust=False).mean()
            
            # 3. ATR (14)
            high_low = df['High'] - df['Low']
            high_cp = abs(df['High'] - df['Close'].shift())
            low_cp = abs(df['Low'] - df['Close'].shift())
            tr = pd.concat([high_low, high_cp, low_cp], axis=1).max(axis=1)
            atr_series = tr.rolling(window=14).mean()
            atr_val = atr_series.iloc[-1] if not atr_series.empty and not pd.isna(atr_series.iloc[-1]) else (current_close * 0.02)

            # 4. Bollinger Bands (20, 2)
            sma = df['Close'].rolling(window=20).mean()
            std = df['Close'].rolling(window=20).std()
            bb_upper = sma + (std * 2)
            bb_lower = sma - (std * 2)
            
            bb_range = bb_upper.iloc[-1] - bb_lower.iloc[-1]
            if not pd.isna(bb_range) and bb_range != 0:
                bb_pct = (current_close - bb_lower.iloc[-1]) / bb_range * 100
            else:
                bb_pct = 50.0

            # 5. ADX (Basic approximation)
            adx = 25.0
            
            # Find the base stock info from NSE_UNIVERSE
            base_info = next((s for s in NSE_UNIVERSE if s["sym"] == sym), {"beta": 1.0, "sector": "N/A", "cap": "N/A", "vol": "MED"})
            
            return {
                "sym": sym,
                "price": round(float(current_close), 2),
                "change": round(float(change_pct), 2),
                "rsi": round(float(rsi_val), 2) if not pd.isna(rsi_val) else 50.0,
                "macd": round(float(macd_series.iloc[-1]), 3) if not macd_series.empty and not pd.isna(macd_series.iloc[-1]) else 0.0,
                "macd_sig": round(float(macd_sig_series.iloc[-1]), 3) if not macd_sig_series.empty and not pd.isna(macd_sig_series.iloc[-1]) else 0.0,
                "bb_pct": round(float(bb_pct), 1),
                "volume": int(df['Volume'].iloc[-1]) if not pd.isna(df['Volume'].iloc[-1]) else 0,
                "vol_avg": int(df['Volume'].mean()) if not pd.isna(df['Volume'].mean()) else 1,
                "atr": round(float(atr_val), 2),
                "adx": float(adx),
                "obv_trend": "UP" if not df['Volume'].empty and len(df) > 1 and df['Volume'].iloc[-1] > df['Volume'].iloc[-2] else "DOWN",
                "mfi": round(random.random() * 100, 1),
                "stoch_k": round(random.random() * 100, 1),
                "beta": base_info["beta"],
                "sector": base_info["sector"],
                "cap": base_info["cap"],
                "vol": base_info["vol"]
            }
        except Exception as e:
            print(f"Error fetching live data for {sym}: {e}")
            return None

    def simulate_stock(self, stock: Dict[str, Any]) -> Dict[str, Any]:
        beta = stock.get("beta", 1.0)
        base_price = stock.get("price")

        regime_drift = {
            "bull": 0.0008,
            "bear": -0.0012,
            "sideways": 0.0001,
            "crash": -0.003,
            "recovery": 0.0015
        }
        drift = regime_drift.get(self.state.regime, 0) * beta
        noise = (random.random() - 0.5) * 0.022 * beta
        change = drift + noise

        price = round(base_price * (1 + change), 2)
        rsi = round(35 + random.random() * 40, 1)
        macd = round((random.random() - 0.5) * 0.9, 3)
        macd_sig = round((random.random() - 0.5) * 0.7, 3)
        bb_pct = round(random.random() * 100, 1)
        volume = int(80000 + random.random() * 600000 * (2 if stock.get("vol") == "HIGH" else 1))
        vol_avg = int(volume * (0.7 + random.random() * 0.6))
        atr = round(base_price * 0.018 * beta, 2)
        adx = round(15 + random.random() * 45, 1)
        obv_trend = "UP" if random.random() > 0.5 else "DOWN"
        mfi = round(30 + random.random() * 50, 1)
        stoch_k = round(20 + random.random() * 70, 1)
        change_pct = round(change * 100, 2)

        return {
            **stock,
            "price": price,
            "change": change_pct,
            "rsi": rsi,
            "macd": macd,
            "macd_sig": macd_sig,
            "bb_pct": bb_pct,
            "volume": volume,
            "vol_avg": vol_avg,
            "atr": atr,
            "adx": adx,
            "obv_trend": obv_trend,
            "mfi": mfi,
            "stoch_k": stoch_k
        }

    def compute_crash_score(self, stocks: List[Dict[str, Any]]) -> int:
        score = 0
        declining = len([s for s in stocks if s["change"] < 0])
        breadth_ratio = declining / len(stocks)

        if breadth_ratio > 0.8: score += 30
        elif breadth_ratio > 0.65: score += 18
        elif breadth_ratio > 0.5: score += 8

        avg_change = abs(sum([s["change"] for s in stocks]) / len(stocks))
        if avg_change > 3.0: score += 25
        elif avg_change > 2.0: score += 15
        elif avg_change > 1.5: score += 8

        low_rsi = len([s for s in stocks if s["rsi"] < 35])
        if low_rsi / len(stocks) > 0.7: score += 20
        elif low_rsi / len(stocks) > 0.5: score += 12

        dd = (self.state.peak_capital - self.state.capital) / self.state.peak_capital
        if dd > 0.12: score += 20
        elif dd > 0.07: score += 10

        recent_losses = len([t for t in self.state.trades[:5] if not t.win])
        if recent_losses >= 4: score += 15
        elif recent_losses >= 3: score += 8

        vol_spike = len([s for s in stocks if s["volume"] > s["vol_avg"] * 2.5])
        if vol_spike / len(stocks) > 0.5: score += 10

        return min(100, score)

    def detect_regime(self, stocks: List[Dict[str, Any]]) -> Dict[str, Any]:
        avg_change = sum([s["change"] for s in stocks]) / len(stocks)
        avg_rsi = sum([s["rsi"] for s in stocks]) / len(stocks)
        rising = len([s for s in stocks if s["change"] > 0]) / len(stocks)
        crash_score = self.compute_crash_score(stocks)

        self.state.crash_score = crash_score
        self.state.indicators = {"avgChange": avg_change, "avgRSI": avg_rsi, "rising": rising, "crashScore": crash_score}

        if crash_score >= 60: regime = "crash"
        elif avg_change < -1.0 and rising < 0.35: regime = "bear"
        elif avg_change > 0.4 and rising > 0.6: regime = "bull"
        elif avg_rsi > 60 and rising > 0.55 and len([t for t in self.state.trades if t.win]) > 0: regime = "recovery"
        else: regime = "sideways"

        self.state.regime = regime
        return {"regime": regime, "crashScore": crash_score, "avgChange": avg_change, "avgRSI": avg_rsi, "rising": rising}

    def execute_trade(self, sig: Dict[str, Any], mkt: Dict[str, Any], qty: int, time_str: str) -> Trade:
        action = sig["action"].upper()
        entry = float(sig.get("entry", mkt["price"]))
        sl = float(sig.get("stop_loss", entry * 0.97))
        tp = float(sig.get("take_profit", entry * 1.05))
        conf = int(sig.get("confidence", 50))
        rr = float(sig.get("risk_reward", 1.5))

        regime_mult = {"bull": 1.15, "sideways": 1.0, "recovery": 1.1, "bear": 0.8, "crash": 0.5}
        base_win_prob = 0.42 + (conf / 100) * 0.30
        win_prob = min(0.82, base_win_prob * regime_mult.get(self.state.regime, 1.0))
        win = random.random() < win_prob

        exit_price = round(entry + (tp - entry) * (0.55 + random.random() * 0.45), 2) if win \
            else round(entry - (entry - sl) * (0.45 + random.random() * 0.55), 2)

        raw_pnl = (exit_price - entry) * qty * (-1 if action == "SELL" else 1)
        pnl = round(raw_pnl, 2)

        self.state.capital = round(self.state.capital + pnl, 2)
        self.state.pnl = round(self.state.pnl + pnl, 2)
        if self.state.capital > self.state.peak_capital:
            self.state.peak_capital = self.state.capital
        
        dd = (self.state.peak_capital - self.state.capital) / self.state.peak_capital
        if dd > self.state.max_dd:
            self.state.max_dd = dd

        if win:
            self.state.wins += 1
            self.state.streak += 1
            if self.state.streak > self.state.max_streak:
                self.state.max_streak = self.state.streak
        else:
            self.state.losses += 1
            self.state.streak = 0

        self.state.equity.append(self.state.capital)
        self.state.pnl_history.append(pnl)

        trade = Trade(
            n=len(self.state.trades) + 1,
            sym=mkt["sym"],
            action=action,
            entry=entry,
            sl=sl,
            tp=tp,
            qty=qty,
            exit=exit_price,
            pnl=pnl,
            win=win,
            conf=conf,
            rr=rr,
            regime=self.state.regime,
            time=time_str
        )
        self.state.trades.insert(0, trade)
        return trade

    def get_sharpe(self) -> Optional[float]:
        if len(self.state.pnl_history) < 5: return None
        h = self.state.pnl_history
        mean = sum(h) / len(h)
        variance = sum([(v - mean) ** 2 for v in h]) / len(h)
        std = math.sqrt(variance)
        if std == 0: return None
        return round(mean / std * math.sqrt(252), 2)
