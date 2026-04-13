"""
SENTINEL QUANT — Multi-Agent Swarm v2 (Global)
================================================
Upgraded with:
- LangGraph-style StateGraph orchestration
- VATS (Volatility-Adjusted Trailing Stop) formula
- Multi-timeframe confluence validation
- Black Swan breakeven mode
- Analyst pattern memory feedback loop
"""

import math
import random
import logging
from datetime import datetime
from typing import Dict, List, Optional, Any, Callable, Tuple
from dataclasses import dataclass, field, asdict
from enum import Enum

logger = logging.getLogger("sentinel.agents")


# ═══════════════════════════════════════════════════════════════
# STATE GRAPH (LangGraph-style without the dependency)
# ═══════════════════════════════════════════════════════════════

@dataclass
class TradeState:
    """
    The shared state object passed between all agent nodes.
    This is the "memory" of a single analysis cycle.
    Inspired by LangGraph's StateGraph concept.
    """
    # Market context
    regime: str = "unknown"
    crash_score: int = 0
    market: Optional[Dict] = None
    all_stocks: List[Dict] = field(default_factory=list)
    sector_scans: List[Any] = field(default_factory=list)
    active_exchange: str = "NSE"
    news_threat: str = "LOW"
    news_score: int = 0

    # Signal
    signal: Optional[Dict] = None
    scout_context: str = ""
    analyst_advisory: str = ""

    # Decision
    action_taken: str = "NONE"   # OPEN|SKIP|HOLD|CIRCUIT_BREAK
    mission_id: Optional[str] = None
    filter_passes: int = 0

    # Multi-timeframe
    tf_confluence: float = 0.0   # 0.0 to 1.0: fraction of timeframes agreeing

    # Cycle metadata
    cycle: int = 0
    timestamp: str = ""

    def to_log(self) -> str:
        return (f"[STATE] Regime={self.regime} | Market={self.market.get('sym') if self.market else 'N/A'} "
                f"| Action={self.action_taken} | News={self.news_threat} | TF={self.tf_confluence:.2f}")


class GraphNode:
    """A single node in the StateGraph."""
    def __init__(self, name: str, fn: Callable):
        self.name = name
        self.fn = fn

    async def run(self, state: TradeState) -> TradeState:
        logger.debug(f"[GRAPH] Running node: {self.name}")
        return await self.fn(state)


class StateGraph:
    """
    A minimal LangGraph-inspired stateful graph.
    Nodes are async functions that receive and return TradeState.
    Edges define the routing logic between nodes.
    """

    def __init__(self):
        self.nodes: Dict[str, GraphNode] = {}
        self.edges: Dict[str, str] = {}   # node_name → next_node_name
        self.conditional_edges: Dict[str, Callable] = {}

    def add_node(self, name: str, fn: Callable):
        self.nodes[name] = GraphNode(name, fn)

    def add_edge(self, from_node: str, to_node: str):
        self.edges[from_node] = to_node

    def add_conditional_edge(self, from_node: str, router: Callable):
        """Router is a function(state) -> str (next node name or 'END')."""
        self.conditional_edges[from_node] = router

    async def run(self, initial_state: TradeState, entry: str = "scout") -> TradeState:
        state = initial_state
        current = entry

        while current and current != "END":
            if current not in self.nodes:
                logger.error(f"[GRAPH] Unknown node: {current}")
                break

            state = await self.nodes[current].run(state)

            # Conditional routing takes priority
            if current in self.conditional_edges:
                router = self.conditional_edges[current]
                current = router(state)
            elif current in self.edges:
                current = self.edges[current]
            else:
                current = "END"

        return state


# ═══════════════════════════════════════════════════════════════
# DATA MODELS
# ═══════════════════════════════════════════════════════════════

class MissionStatus(str, Enum):
    ACTIVE = "ACTIVE"
    TRIMMED = "TRIMMED"
    BREAKEVEN = "BREAKEVEN"   # New: Black Swan mode
    EXITED = "EXITED"
    STOPPED = "STOPPED"


@dataclass
class Mission:
    id: str
    sym: str
    action: str
    entry_price: float
    current_price: float
    qty: int
    stop_loss: float
    take_profit: float
    trailing_stop: float
    status: MissionStatus = MissionStatus.ACTIVE
    regime_at_entry: str = "unknown"
    exchange: str = "NSE"
    asset_type: str = "equity"
    atr_at_entry: float = 1.0
    confidence: int = 75
    unrealized_pnl: float = 0.0
    peak_price: float = 0.0      # For VATS calculation
    peak_pnl: float = 0.0
    check_count: int = 0
    vats_multiplier_k: float = 2.0
    created_at: str = ""
    last_check: str = ""
    guardian_verdicts: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["status"] = self.status.value
        return d


@dataclass
class SectorScan:
    sector: str
    avg_change: float
    momentum_score: float
    volume_surge: float
    divergence: str
    top_pick: Optional[str] = None
    confidence: int = 0
    exchange: str = "MIXED"


@dataclass
class TradeMemory:
    sym: str
    action: str
    regime: str
    entry_price: float
    exit_price: float
    pnl: float
    win: bool
    confidence: int
    lesson: str
    pattern_tag: str
    exchange: str = "NSE"
    timestamp: str = ""


# ═══════════════════════════════════════════════════════════════
# PORTFOLIO MANAGER
# ═══════════════════════════════════════════════════════════════

class PortfolioManager:
    def __init__(self, capital: float = 500.0):
        self.capital = capital
        self.reserved_capital = 0.0
        self.missions: Dict[str, Mission] = {}
        self.completed_missions: List[Mission] = []
        self.max_concurrent = 5
        self.total_realized_pnl = 0.0
        self.wins = 0
        self.losses = 0
        self.peak_capital = capital
        self.max_dd = 0.0

    @property
    def available_capital(self) -> float:
        return max(0.0, self.capital - self.reserved_capital)

    @property
    def active_count(self) -> int:
        return sum(1 for m in self.missions.values()
                   if m.status in (MissionStatus.ACTIVE, MissionStatus.TRIMMED, MissionStatus.BREAKEVEN))

    @property
    def total_unrealized_pnl(self) -> float:
        return sum(m.unrealized_pnl for m in self.missions.values()
                   if m.status in (MissionStatus.ACTIVE, MissionStatus.TRIMMED, MissionStatus.BREAKEVEN))

    def can_open_mission(self) -> bool:
        return self.active_count < self.max_concurrent and self.available_capital > 20

    def open_mission(self, mission: Mission, capital_reserved: float):
        self.missions[mission.id] = mission
        self.reserved_capital += capital_reserved
        mission.peak_price = mission.entry_price

    def close_mission(self, mission_id: str, exit_price: float) -> Optional[Mission]:
        if mission_id not in self.missions:
            return None
        m = self.missions[mission_id]
        mult = 1 if m.action == "BUY" else -1
        pnl = round((exit_price - m.entry_price) * m.qty * mult, 2)
        m.current_price = exit_price
        m.unrealized_pnl = pnl
        m.status = MissionStatus.EXITED

        self.capital += pnl
        self.total_realized_pnl += pnl
        self.reserved_capital = max(0, self.reserved_capital - m.entry_price * m.qty)

        if self.capital > self.peak_capital:
            self.peak_capital = self.capital
        dd = (self.peak_capital - self.capital) / self.peak_capital
        if dd > self.max_dd:
            self.max_dd = dd

        if pnl > 0:
            self.wins += 1
        else:
            self.losses += 1

        self.completed_missions.append(m)
        del self.missions[mission_id]
        return m

    def trim_mission(self, mission_id: str, trim_pct: float = 0.5) -> Optional[float]:
        if mission_id not in self.missions:
            return None
        m = self.missions[mission_id]
        trim_qty = max(1, int(m.qty * trim_pct))
        if trim_qty >= m.qty:
            return None
        mult = 1 if m.action == "BUY" else -1
        pnl = round((m.current_price - m.entry_price) * trim_qty * mult, 2)
        m.qty -= trim_qty
        m.status = MissionStatus.TRIMMED
        self.capital += pnl
        self.total_realized_pnl += pnl
        self.reserved_capital = max(0, self.reserved_capital - m.entry_price * trim_qty)
        if pnl > 0:
            self.wins += 1
        return pnl

    def move_all_to_breakeven(self):
        """Black Swan mode: immediately set all SLs to entry price."""
        moved = []
        for m in self.missions.values():
            if m.status in (MissionStatus.ACTIVE, MissionStatus.TRIMMED):
                m.stop_loss = m.entry_price
                m.trailing_stop = m.entry_price
                m.status = MissionStatus.BREAKEVEN
                moved.append(m.sym)
        return moved

    def get_state(self) -> dict:
        return {
            "capital": round(self.capital, 2),
            "reserved": round(self.reserved_capital, 2),
            "available": round(self.available_capital, 2),
            "active_missions": self.active_count,
            "total_realized_pnl": round(self.total_realized_pnl, 2),
            "wins": self.wins,
            "losses": self.losses,
            "max_dd": round(self.max_dd * 100, 2),
            "peak_capital": round(self.peak_capital, 2),
            "missions": [m.to_dict() for m in self.missions.values()],
        }


# ═══════════════════════════════════════════════════════════════
# SCOUT AGENT — Global Sector Scanning
# ═══════════════════════════════════════════════════════════════

class ScoutAgent:
    def __init__(self):
        self.last_scan: Dict[str, SectorScan] = {}
        self.scan_history: List[Dict] = []

    def scan_sectors(self, all_stocks: List[Dict], regime: Dict) -> List[SectorScan]:
        sectors: Dict[str, List[Dict]] = {}
        for s in all_stocks:
            key = f"{s.get('sector', 'Misc')}/{s.get('exchange', 'X')}"
            sectors.setdefault(key, []).append(s)

        results = []
        for sector_key, stocks in sectors.items():
            avg_change = sum(s["change"] for s in stocks) / len(stocks)
            avg_rsi = sum(s["rsi"] for s in stocks) / len(stocks)
            vol_ratio = sum(s["volume"] / max(s["vol_avg"], 1) for s in stocks) / len(stocks)

            price_down = avg_change < -0.3
            vol_surge = vol_ratio > 1.5
            price_up = avg_change > 0.3
            vol_dry = vol_ratio < 0.7

            if price_down and vol_surge:
                divergence = "BULLISH_DIV"
            elif price_up and vol_dry:
                divergence = "BEARISH_DIV"
            else:
                divergence = "NEUTRAL"

            momentum = (avg_change * 0.4) + ((avg_rsi - 50) * 0.02) + ((vol_ratio - 1) * 10 * 0.3)
            best = max(stocks, key=lambda s: s["change"] if regime.get("regime") == "bull" else -s["change"])
            exchange = best.get("exchange", "X")

            scan = SectorScan(
                sector=sector_key,
                avg_change=round(avg_change, 3),
                momentum_score=round(momentum, 2),
                volume_surge=round(vol_ratio, 2),
                divergence=divergence,
                top_pick=best["sym"],
                confidence=min(95, max(10, int(50 + momentum * 15))),
                exchange=exchange
            )
            results.append(scan)
            self.last_scan[sector_key] = scan

        results.sort(key=lambda x: abs(x.momentum_score), reverse=True)
        return results

    def get_top_targets(self, scans: List[SectorScan], regime: str, n: int = 3) -> List[str]:
        if regime == "bull":
            ranked = sorted(scans, key=lambda s: (s.divergence == "BULLISH_DIV", s.momentum_score), reverse=True)
        elif regime == "bear":
            ranked = sorted(scans, key=lambda s: (s.divergence == "BEARISH_DIV", -s.momentum_score), reverse=True)
        else:
            ranked = sorted(scans, key=lambda s: abs(s.momentum_score), reverse=True)
        return [s.top_pick for s in ranked[:n] if s.top_pick]


# ═══════════════════════════════════════════════════════════════
# MULTI-TIMEFRAME VALIDATOR
# ═══════════════════════════════════════════════════════════════

class MultiTimeframeValidator:
    """
    Validates trade direction across 3 timeframes (1m, 15m, 1h).
    In simulation mode, uses a stochastic model based on RSI alignment.
    In live mode, fetches multiple timeframe data from yfinance.
    """

    def validate(self, mkt: Dict, action: str, live: bool = False) -> Tuple[float, str]:
        """
        Returns (confluence_score 0-1, description).
        Score of 0.67+ means 2/3 timeframes agree.
        """
        if live:
            return self._validate_live(mkt, action)
        return self._validate_simulated(mkt, action)

    def _validate_simulated(self, mkt: Dict, action: str) -> Tuple[float, str]:
        """Stochastic simulation of multi-timeframe alignment."""
        rsi = mkt.get("rsi", 50)
        macd = mkt.get("macd", 0)
        macd_sig = mkt.get("macd_sig", 0)
        adx = mkt.get("adx", 25)

        votes = []

        # 1m timeframe: MACD crossover
        tf1_bull = macd > macd_sig
        votes.append(tf1_bull if action == "BUY" else not tf1_bull)

        # 15m timeframe: RSI momentum
        tf15_bull = 45 < rsi < 70 if action == "BUY" else 30 < rsi < 55
        votes.append(tf15_bull)

        # 1h timeframe: ADX trend strength
        tf1h_strong = adx > 22
        votes.append(tf1h_strong)

        confluence = sum(votes) / 3
        desc = f"1m={'Y' if votes[0] else 'N'} 15m={'Y' if votes[1] else 'N'} 1h={'Y' if votes[2] else 'N'}"
        return round(confluence, 2), desc

    def validate_bear_reversal(self, mkt: Dict) -> Tuple[float, int, str]:
        """
        Layer 1 — Bear Hunting: Regime-aware WEIGHTED MTF scoring.

        Weights: 1m=0.25 (noise), 15m=0.45 (trend confirm), 1h=0.30 (context)
        Gate: weighted_confluence >= 0.40 (15m oversold alone clears this).

        Returns (weighted_confluence 0-1, conditions_met 0-4, description).
        """
        rsi     = mkt.get("rsi", 50)
        macd    = mkt.get("macd", 0)
        macd_s  = mkt.get("macd_sig", 0)
        bb_pct  = mkt.get("bb_pct", 50)   # 0=lower band, 100=upper band
        change  = mkt.get("change", 0)
        vol     = mkt.get("volume", 1)
        vol_avg = mkt.get("vol_avg", 1)
        vol_ratio = vol / max(vol_avg, 1)

        W_1M, W_15M, W_1H = 0.25, 0.45, 0.30
        tf1m_ok  = macd > macd_s                   # 1m: MACD turning in our favor
        tf15m_ok = rsi < 35                          # 15m: RSI approaching oversold
        tf1h_ok  = bb_pct < 25                       # 1h: near lower Bollinger Band

        weighted = (W_1M * float(tf1m_ok)) + (W_15M * float(tf15m_ok)) + (W_1H * float(tf1h_ok))

        # Hard reversal conditions (enriched context for LLM)
        conditions_met, cond_notes = 0, []
        if rsi < 32:
            conditions_met += 1; cond_notes.append(f"RSI={rsi:.1f}<32")
        if macd > macd_s and macd < 0:             # MACD hist turning positive while still below zero
            conditions_met += 1; cond_notes.append("MACD-hist(+)")
        if bb_pct < 15:
            conditions_met += 1; cond_notes.append(f"BB%={bb_pct:.0f}<15")
        if vol_ratio < 0.7 and change < 0:         # sell volume exhaustion
            conditions_met += 1; cond_notes.append(f"VolExhaust({vol_ratio:.1f}x)")

        desc = (
            f"1m={'Y' if tf1m_ok else 'N'}(w={W_1M}) "
            f"15m={'Y' if tf15m_ok else 'N'}(w={W_15M}) "
            f"1h={'Y' if tf1h_ok else 'N'}(w={W_1H}) | "
            f"weighted={weighted:.2f} | conds={conditions_met}/4"
            + (f": {', '.join(cond_notes)}" if cond_notes else "")
        )
        return round(weighted, 2), conditions_met, desc


    def _validate_live(self, mkt: Dict, action: str) -> Tuple[float, str]:
        """Live multi-timeframe using yfinance (best-effort)."""
        try:
            import yfinance as yf
            sym = mkt["sym"]
            exchange = mkt.get("exchange", "NSE")
            ticker_sym = f"{sym}.NS" if exchange == "NSE" else sym

            ticker = yf.Ticker(ticker_sym)
            votes = []
            labels = []

            for period, interval, label in [("1d", "1m", "1m"), ("5d", "15m", "15m"), ("30d", "1h", "1h")]:
                try:
                    df = ticker.history(period=period, interval=interval)
                    if df.empty or len(df) < 14:
                        votes.append(random.random() > 0.5)
                        labels.append(f"{label}=?")
                        continue
                    close = df['Close']
                    delta = close.diff()
                    gain = delta.where(delta > 0, 0).rolling(14).mean()
                    loss = (-delta.where(delta < 0, 0)).rolling(14).mean().replace(0, 1e-5)
                    rsi = (100 - 100 / (1 + gain / loss)).iloc[-1]

                    tf_bull = rsi > 50 and close.iloc[-1] > close.iloc[-5]
                    agrees = tf_bull if action == "BUY" else not tf_bull
                    votes.append(agrees)
                    labels.append(f"{label}={'✓' if agrees else '✗'}")
                except Exception:
                    votes.append(random.random() > 0.5)
                    labels.append(f"{label}=?")

            return round(sum(votes) / 3, 2), " ".join(labels)
        except Exception as e:
            logger.debug(f"[MTF] Live validation failed: {e}")
            return self._validate_simulated(mkt, action)


# ═══════════════════════════════════════════════════════════════
# GUARDIAN AGENT — VATS Formula
# ═══════════════════════════════════════════════════════════════

class GuardianAgent:
    """
    Upgraded Guardian with Volatility-Adjusted Trailing Stop (VATS):

    VATS_t = Price_max - (ATR_n × k × ln(1 + Volatility_Ratio))

    Where:
    - Price_max: Highest price since entry (peak_price)
    - ATR_n: Current ATR
    - k: Base multiplier (Bull=2.0, Sideways=1.5, Bear=1.0)
    - Volatility_Ratio: Current ATR / ATR at entry (measures how "parabolic" the move is)
    - ln(1 + VR): Dampens the stop during explosive moves to prevent shake-outs
    """

    K_MULTIPLIERS = {
        "bull": 2.0,
        "recovery": 1.8,
        "sideways": 1.5,
        "bear": 1.0,
        "crash": 0.8
    }

    def compute_vats(self, mission: Mission, current_price: float,
                     current_atr: float, regime: str) -> float:
        """
        Compute the Volatility-Adjusted Trailing Stop.
        """
        # Update peak price
        if mission.action == "BUY":
            if current_price > mission.peak_price:
                mission.peak_price = current_price
            price_max = mission.peak_price
        else:
            if current_price < mission.peak_price or mission.peak_price == 0:
                mission.peak_price = current_price
            price_max = mission.peak_price

        atr_n = max(current_atr, 0.0001)
        k = self.K_MULTIPLIERS.get(regime, 1.5)

        # Volatility Ratio: dampens stop widening during parabolic moves
        vol_ratio = atr_n / max(mission.atr_at_entry, 0.0001)
        attenuation = math.log(1 + vol_ratio)

        vats = price_max - (atr_n * k * attenuation)

        if mission.action == "SELL":
            vats = price_max + (atr_n * k * attenuation)

        return round(vats, 4)

    def evaluate_mission(self, mission: Mission, current_data: Dict,
                         regime: str, news_threat: str = "LOW") -> Dict[str, Any]:
        mission.check_count += 1
        mission.last_check = datetime.now().strftime("%H:%M:%S")
        current_price = current_data.get("price", mission.current_price)
        mission.current_price = current_price
        current_atr = current_data.get("atr", mission.atr_at_entry)

        # Compute VATS
        new_vats = self.compute_vats(mission, current_price, current_atr, regime)

        # Update trailing stop (only move in favorable direction)
        if mission.action == "BUY":
            if new_vats > mission.trailing_stop:
                mission.trailing_stop = new_vats
        else:
            if new_vats < mission.trailing_stop or mission.trailing_stop == 0:
                mission.trailing_stop = new_vats

        # Unrealized PnL
        mult = 1 if mission.action == "BUY" else -1
        unrealized = (current_price - mission.entry_price) * mission.qty * mult
        mission.unrealized_pnl = round(unrealized, 2)
        if unrealized > mission.peak_pnl:
            mission.peak_pnl = unrealized

        # ── Decision Logic ──

        # BLACK SWAN: News threat → move to breakeven first
        if news_threat in ("HIGH", "EXTREME") and mission.status != MissionStatus.BREAKEVEN:
            verdict = "BREAKEVEN"
            reason = f"Black Swan protocol — news threat: {news_threat}"

        # EXIT: VATS stop hit
        elif mission.action == "BUY" and current_price <= mission.trailing_stop:
            verdict = "EXIT"
            reason = f"VATS stop triggered @ ₹{mission.trailing_stop:.2f}"

        elif mission.action == "SELL" and current_price >= mission.trailing_stop:
            verdict = "EXIT"
            reason = f"VATS stop triggered @ ₹{mission.trailing_stop:.2f}"

        # EXIT: Hard stop loss
        elif mission.action == "BUY" and current_price <= mission.stop_loss:
            verdict = "EXIT"
            reason = f"Hard SL hit @ ₹{mission.stop_loss:.2f}"

        elif mission.action == "SELL" and current_price >= mission.stop_loss:
            verdict = "EXIT"
            reason = f"Hard SL hit @ ₹{mission.stop_loss:.2f}"

        # EXIT: Crash regime
        elif regime == "crash":
            verdict = "EXIT"
            reason = "Emergency exit — crash regime"

        # EXIT: Gave back >60% of peak gains
        elif mission.peak_pnl > 0 and unrealized < mission.peak_pnl * 0.4:
            verdict = "EXIT"
            reason = f"Profit erosion: {int((1 - unrealized/mission.peak_pnl)*100)}% of peak gone"

        # TRIM: At take-profit, hadn't trimmed yet
        elif mission.status not in (MissionStatus.TRIMMED, MissionStatus.BREAKEVEN):
            tp_hit = (mission.action == "BUY" and current_price >= mission.take_profit) or \
                     (mission.action == "SELL" and current_price <= mission.take_profit)
            if tp_hit:
                verdict = "TRIM"
                reason = f"TP zone @ ₹{mission.take_profit:.2f}"
            else:
                verdict = "STAY"
                pnl_str = f"+₹{unrealized:.1f}" if unrealized >= 0 else f"-₹{abs(unrealized):.1f}"
                reason = f"Trend intact | PnL: {pnl_str} | VATS: ₹{mission.trailing_stop:.2f}"
        else:
            verdict = "STAY"
            pnl_str = f"+₹{unrealized:.1f}" if unrealized >= 0 else f"-₹{abs(unrealized):.1f}"
            reason = f"Riding position | PnL: {pnl_str} | VATS: ₹{mission.trailing_stop:.2f}"

        entry = f"[{mission.last_check}] {verdict}: {reason}"
        mission.guardian_verdicts.append(entry)
        if len(mission.guardian_verdicts) > 20:
            mission.guardian_verdicts.pop(0)

        return {"verdict": verdict, "reason": reason, "vats": mission.trailing_stop,
                "unrealized_pnl": mission.unrealized_pnl, "checks": mission.check_count}


# ═══════════════════════════════════════════════════════════════
# ANALYST AGENT — Learning Memory
# ═══════════════════════════════════════════════════════════════

class AnalystAgent:
    def __init__(self):
        self.memory: List[TradeMemory] = []
        self.regime_stats: Dict[str, Dict] = {}
        self.pattern_weights: Dict[str, float] = {}  # Pattern → win rate

    def analyze_trade(self, mission: Mission) -> TradeMemory:
        pnl = mission.unrealized_pnl
        win = pnl > 0

        if mission.regime_at_entry == "bull" and win and mission.action == "BUY":
            pattern = "bull_momentum_long"
        elif mission.regime_at_entry == "bear" and win and mission.action == "SELL":
            pattern = "bear_momentum_short"
        elif not win and mission.regime_at_entry == "sideways":
            pattern = "sideways_chop_loss"
        elif win and mission.check_count > 5:
            pattern = "patient_winner"
        elif not win and mission.check_count <= 2:
            pattern = "fast_stopout"
        else:
            pattern = f"{mission.regime_at_entry}_{mission.action.lower()}_{'win' if win else 'loss'}"

        if win and pnl > mission.entry_price * 0.03:
            lesson = f"Strong {mission.action} in {mission.regime_at_entry}. Confidence {mission.confidence}% was justified."
        elif win:
            lesson = f"Marginal winner. VATS held well in {mission.regime_at_entry}."
        elif not win and mission.check_count <= 2:
            lesson = f"Fast stop-out. Volatile {mission.regime_at_entry}. Consider smaller size."
        else:
            lesson = f"Loss after {mission.check_count} checks. Pattern {pattern} in {mission.exchange}."

        mem = TradeMemory(sym=mission.sym, action=mission.action, regime=mission.regime_at_entry,
                          entry_price=mission.entry_price, exit_price=mission.current_price,
                          pnl=pnl, win=win, confidence=mission.confidence, lesson=lesson,
                          pattern_tag=pattern, exchange=mission.exchange,
                          timestamp=datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        self.memory.append(mem)

        # Update regime stats
        stats = self.regime_stats.setdefault(mission.regime_at_entry, {"wins": 0, "losses": 0, "total_pnl": 0})
        if win:
            stats["wins"] += 1
        else:
            stats["losses"] += 1
        stats["total_pnl"] += pnl

        # Update pattern weights
        w = self.pattern_weights.get(pattern, 0.5)
        self.pattern_weights[pattern] = round(w * 0.8 + (1.0 if win else 0.0) * 0.2, 3)

        return mem

    def get_regime_advisory(self, regime: str) -> str:
        stats = self.regime_stats.get(regime)
        if not stats or (stats["wins"] + stats["losses"]) < 3:
            return f"Insufficient data for '{regime}' regime."
        total = stats["wins"] + stats["losses"]
        wr = (stats["wins"] / total) * 100
        avg_pnl = stats["total_pnl"] / total
        if wr >= 60:
            return f"FAVORABLE [{regime}]: {wr:.0f}% win rate, avg ₹{avg_pnl:.1f}"
        elif wr >= 45:
            return f"NEUTRAL [{regime}]: {wr:.0f}% win rate. Proceed with caution."
        else:
            return f"HOSTILE [{regime}]: {wr:.0f}% win rate. Reduce sizing."

    def get_recent_lessons(self, n: int = 5) -> List[str]:
        return [m.lesson for m in self.memory[-n:]]

    def get_memory_summary(self) -> dict:
        return {
            "total_memories": len(self.memory),
            "regime_stats": {k: {**v, "win_rate": round(v["wins"]/(v["wins"]+v["losses"])*100, 1)}
                             for k, v in self.regime_stats.items()
                             if (v["wins"] + v["losses"]) > 0},
            "recent_patterns": [m.pattern_tag for m in self.memory[-10:]],
            "pattern_weights": self.pattern_weights
        }


# ═══════════════════════════════════════════════════════════════
# CIRCUIT BREAKER 2.0
# ═══════════════════════════════════════════════════════════════

class CircuitBreaker:
    def __init__(self):
        self.is_tripped = False
        self.trip_reason = ""
        self.trip_time = ""
        self.volatility_history: List[float] = []
        self.cooldown_cycles = 0

    def check(self, crash_score: int, all_stocks: List[Dict],
              regime: str, news_threat: str = "LOW", paper_mode: bool = False) -> bool:
        avg_abs = sum(abs(s["change"]) for s in all_stocks) / len(all_stocks) if all_stocks else 0
        self.volatility_history.append(avg_abs)
        if len(self.volatility_history) > 20:
            self.volatility_history.pop(0)

        if self.cooldown_cycles > 0:
            self.cooldown_cycles -= 1
            return True

        if crash_score >= 70:
            return self._trip(f"Crash score critical: {crash_score}/100")
        if news_threat == "EXTREME":
            return self._trip("News: EXTREME geopolitical threat")
        if len(self.volatility_history) >= 5:
            recent = sum(self.volatility_history[-3:]) / 3
            baseline = sum(self.volatility_history[:-3]) / max(1, len(self.volatility_history) - 3)
            if baseline > 0 and recent / baseline > 3.0:
                return self._trip(f"Volatility spike: {recent:.2f}% vs {baseline:.2f}%")
        
        # Breadth Collapse Check
        declining = len([s for s in all_stocks if s["change"] < 0])
        if all_stocks and declining / len(all_stocks) > 0.85:
            # Task 1: Relax Safety Handbrake in Paper Mode
            if paper_mode and any(s.get("_score", 0) > 20 for s in all_stocks):
                logger.info("[CB] Paper Mode Bypass: Executing on high-conviction outlier.")
                return False
            return self._trip(f"Breadth collapse: {declining}/{len(all_stocks)} declining")

        self.is_tripped = False
        return False

    def _trip(self, reason: str) -> bool:
        self.is_tripped = True
        self.trip_reason = reason
        self.trip_time = datetime.now().strftime("%H:%M:%S")
        self.cooldown_cycles = 3
        logger.warning(f"[CB] TRIPPED: {reason}")
        return True

    def get_status(self) -> dict:
        return {"tripped": self.is_tripped, "reason": self.trip_reason,
                "trip_time": self.trip_time, "cooldown": self.cooldown_cycles,
                "avg_volatility": round(sum(self.volatility_history[-5:]) /
                                        max(1, len(self.volatility_history[-5:])), 3)}


# ═══════════════════════════════════════════════════════════════
# AGENT SWARM ORCHESTRATOR
# ═══════════════════════════════════════════════════════════════

class AgentSwarm:
    """Coordinates all agents + the StateGraph cycle."""

    def __init__(self, capital: float = 500.0):
        self.portfolio = PortfolioManager(capital=capital)
        self.scout = ScoutAgent()
        self.guardian = GuardianAgent()
        self.analyst = AnalystAgent()
        self.breaker = CircuitBreaker()
        self.mtf = MultiTimeframeValidator()
        self.cycle_count = 0
        self.news_threat = "LOW"
        self.active_exchange = "NSE"

    def get_full_state(self) -> dict:
        return {
            "portfolio": self.portfolio.get_state(),
            "circuit_breaker": self.breaker.get_status(),
            "memory": self.analyst.get_memory_summary(),
            "cycle": self.cycle_count,
            "news_threat": self.news_threat,
            "active_exchange": self.active_exchange,
            "scout_sectors": {
                k: {"momentum": v.momentum_score, "divergence": v.divergence,
                    "pick": v.top_pick, "exchange": v.exchange}
                for k, v in self.scout.last_scan.items()
            }
        }
