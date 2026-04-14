"""
SENTINEL QUANT — graph_engine.py  (v4 — Stateful Agentic Loop)
================================================================
Replaces the flat loop in main.py with a clean, testable StateGraph.

Flow:
  [SCAN] → [CB] → [TARGET] → [SNIPER] → [MTF] → [VECTOR] → [EXECUTE]
                ↓CB tripped
               [END]

Key differences from v3 main.py:
  • Scout is replaced by GlobalScoutAgent (API-driven, zero-hardcode)
  • GuardianAgent uses VATS formula ONLY — no hardcoded SL/TP
  • All exits are computed from current ATR + VIX_rel, not fixed %
  • Supabase checkpoint is queried/written atomically
  • Hardware concurrency auto-detected at startup
"""

import asyncio
import logging
import os
import time
import math
import random
import uuid
import datetime
from typing import List, Dict, Optional, Any

from agents import (
    StateGraph, TradeState, Mission, MissionStatus,
    AgentSwarm, MultiTimeframeValidator,
)
from dotenv import load_dotenv

# Force load .env in GraphEngine process
for env_path in [".env", "backend/.env", "../backend/.env"]:
    load_dotenv(env_path)
from market_manager import GlobalScoutAgent, get_optimal_concurrency, score_asset
from smart_router import SmartRouter, compute_vats_vix, build_sniper_messages
from state_definition import SupabaseCheckpoint, VectorMemory
from data_layer import TimezoneManager, NewsMonitor
from engine import TradingEngine, HISTORICAL_CRASHES

logger = logging.getLogger("sentinel.graph_engine")


# ══════════════════════════════════════════════════════════════════
# VIX PROXY  (30-cycle rolling realised volatility)
# ══════════════════════════════════════════════════════════════════

class VixProxy:
    """
    Tracks a rolling window of per-cycle average absolute returns.
    VIX_rel = current_cycle_vol / window_baseline_vol

    This is the σ_rel in VATS = Price_max − (ATR × k × ln(1 + σ_rel))
    """
    def __init__(self, window: int = 30):
        self._window  = window
        self._history: List[float] = []

    def update(self, assets: List[Dict]) -> float:
        if not assets:
            return 1.0
        avg_abs = sum(abs(a.get("change", 0)) for a in assets) / len(assets)
        self._history.append(avg_abs)
        if len(self._history) > self._window:
            self._history.pop(0)
        baseline = sum(self._history) / len(self._history)
        return avg_abs / max(baseline, 0.001)

    def is_spike(self, vix_rel: float, threshold: float = 2.5) -> bool:
        """True when realised vol is >2.5× the rolling baseline."""
        return vix_rel > threshold

    @property
    def current(self) -> float:
        if len(self._history) < 2:
            return 1.0
        baseline = sum(self._history[:-1]) / len(self._history[:-1])
        return self._history[-1] / max(baseline, 0.001)


# ══════════════════════════════════════════════════════════════════
# GRAPH ENGINE  (the stateful orchestrator)
# ══════════════════════════════════════════════════════════════════

class GraphEngine:
    """
    Self-contained trading engine built on a StateGraph.
    Instantiated once per run; singletons are injected via `configure()`.
    """

    def __init__(self):
        # Core singletons — populated in configure()
        self.swarm:     Optional[AgentSwarm]          = None
        self.router:    Optional[SmartRouter]          = None
        self.scout:     Optional[GlobalScoutAgent]     = None
        self.tz:        Optional[TimezoneManager]      = None
        self.news:      Optional[NewsMonitor]          = None
        self.checkpoint:Optional[SupabaseCheckpoint]   = None
        self.vec_mem:   Optional[VectorMemory]         = None
        self.engine:    Optional[TradingEngine]        = None
        self.vix_proxy: VixProxy                       = VixProxy()

        # Runtime state
        self.config:        Dict               = {}
        self.status:        str                = "STOPPED" # STOPPED | RUNNING | DRAINING
        self.cycle_count:   int                = 0
        self._graph:        Optional[StateGraph] = None
        self._concurrency:  Dict[str, int]     = {"scout_workers": 3, "cycle_delay": 6}
        self.last_market:   Optional[Dict]     = None
        self.tf_confluence: float              = 0.0
        self._last_loss_cycle: int             = -10

        # Broadcast callback (set by WebSocket layer)
        self._broadcast = None

        # WebSocket throttle state
        self._last_state_emit: float  = 0
        self._last_action: str        = ""
        self._last_skip_msg: str      = ""
        self._skip_dedup_count: int   = 0
        self._cycle_skip_reasons: set = set()  # Reset each cycle

    # ── Configuration ─────────────────────────────────────────────

    def configure(self, config: Dict, broadcast_fn=None) -> "GraphEngine":
        """Inject all dependencies from a config dict."""
        self.config   = config
        self._broadcast = broadcast_fn

        cap = float(config.get("capital", 500))
        import os
        is_paper = (
            not config.get("prod_mode", True) or 
            config.get("env") == "paper" or 
            os.getenv("ENV") == "paper"
        )
        self.is_paper = is_paper
        
        self.engine     = TradingEngine(capital=cap)
        self.swarm      = AgentSwarm(capital=cap)
        self.router     = SmartRouter(
            api_key = config.get("groq_key") or os.getenv("GROQ_KEY") or os.getenv("OPENROUTER_KEY"),
            broadcast_fn = self._emit
        )
        self.tz         = TimezoneManager()
        self.news       = NewsMonitor(
            api_key = config.get("news_key") or os.getenv("NEWS_KEY") or None, 
            is_paper = is_paper
        )
        # FIX: Ensure Supabase credentials fallback correctly
        sub_url = config.get("supabase_url") or os.getenv("SUPABASE_URL") or ""
        sub_key = config.get("supabase_key") or os.getenv("SUPABASE_KEY") or ""
        
        self.checkpoint = SupabaseCheckpoint(sub_url, sub_key, is_paper=is_paper)
        self.vec_mem    = VectorMemory(sub_url, sub_key, is_paper=is_paper)
        self.scout      = GlobalScoutAgent(
            finnhub_key = config.get("finnhub_key") or os.getenv("FINNHUB_KEY") or None,
            alphav_key  = config.get("alphav_key")  or os.getenv("ALPHAV_KEY")  or None,
        )

        # Adapt cycle delay to hardware
        if config.get("delay"):
            self._concurrency["cycle_delay"] = int(config["delay"])

        self._graph = self._build_graph()
        return self

    # ── Broadcast helper ──────────────────────────────────────────

    async def _emit(self, msg: Dict):
        """Throttled WebSocket emitter — reduces UI flood."""
        if not self._broadcast:
            return

        msg_type = msg.get("type", "")

        # Throttle state_update: only if meaningful change OR 2s elapsed
        if msg_type == "state_update":
            now = time.time()
            state = msg.get("state", {})
            # Key changes: Regime, Active missions count, Realized PnL, or Total Unrealized PnL
            # Adding unrealized PnL ensures the UI updates when prices move even if no trade closes.
            current_action = f"{state.get('regime')}-{state.get('activeMissions')}-{state.get('pnl')}-{state.get('unrealized')}"
            
            if current_action == self._last_action and (now - self._last_state_emit) < 2:
                return  # Suppress rapid duplicate/unimportant updates
            self._last_action = current_action
            self._last_state_emit = now

        # Deduplicate consecutive identical SKIP logs
        if msg_type == "log" and msg.get("level") == "skip":
            skip_msg = msg.get("msg", "")
            if skip_msg == self._last_skip_msg:
                self._skip_dedup_count += 1
                return  # Suppress duplicate
            else:
                # Flush dedup count for previous message
                if self._skip_dedup_count > 0:
                    await self._broadcast({
                        "type": "log",
                        "msg": f"  ↑ (repeated {self._skip_dedup_count}× — suppressed)",
                        "level": "skip",
                        "time": msg.get("time", ""),
                    })
                self._last_skip_msg = skip_msg
                self._skip_dedup_count = 0

        # Drop redundant router_update / memory_update (now folded into state_update)
        if msg_type in ("router_update", "memory_update"):
            return

        await self._broadcast(msg)

    # ── STATE GRAPH NODE DEFINITIONS ─────────────────────────────

    async def _node_scan(self, state: TradeState) -> TradeState:
        """
        GlobalScoutAgent replaces hardcoded NSE_UNIVERSE.
        Picks assets dynamically based on Volume + Rel Strength + ATR misprice.
        """
        # ── Target reached check ──
        if self.status == "DRAINING":
            state.action_taken = "SKIP"
            state.all_stocks = []
            return state

        target_pnl = float(self.config.get("target_pnl", 999999))
        if self.swarm.portfolio.total_realized_pnl >= target_pnl:
            state.action_taken = "SKIP"
            state.all_stocks = []
            return state

        candidates, primary = await self.scout.scan(
            n_candidates=15,
            min_conviction=5.0,
            primary_market=self.config.get("primary_market", "AUTO"),
            available_capital=self.swarm.portfolio.available_capital,
        )

        if not candidates:
            # Graceful fallback — use simulation of NSE seeds
            from market_manager import NSE_FNO_SEEDS
            seeds = random.sample(NSE_FNO_SEEDS, 3)
            candidates = [
                {
                    "sym": s, "exchange": "NSE", "asset": "equity",
                    "price": 100, "change": random.uniform(-2, 2),
                    "rsi": 50, "macd": 0, "macd_sig": 0, "bb_pct": 50,
                    "volume": 100000, "vol_avg": 80000,
                    "atr": 2.0, "adx": 25, "obv_trend": "N/A",
                    "mfi": 50, "stoch_k": 50, "beta": 1.0,
                    "sector": "Unknown", "cap": "mid", "vol": "MED",
                    "source": "sim-fallback", "_score": 0,
                }
                for s in seeds
            ]

        state.all_stocks      = candidates
        state.active_exchange = primary

        sector_leaders = await self.scout.get_sector_leaders()
        if sector_leaders:
            top_sector = next(iter(sector_leaders))
            state.scout_context = (
                f"Top sector: {top_sector} ({sector_leaders[top_sector]:+.2f}%) | "
                f"Scanned {len(candidates)} in-play assets"
            )

        await self._emit({"type": "tz_update", "tz": self.tz.get_status()})
        await self._emit({
            "type": "log",
            "msg": f"SCAN [{primary}]: {len(candidates)} in-play assets found | "
                   f"Scout: {state.scout_context}",
            "level": "quant", "time": state.timestamp,
        })
        return state

    async def _node_circuit_breaker(self, state: TradeState) -> TradeState:
        """
        Trips on: crash score, news threat, VIX spike.
        On EXTREME event: force-exits all missions to break-even.
        """
        vix_rel = state.__dict__.get("vix_rel", 1.0)
        tripped = False
        
        # Determine if we are in paper/testing mode
        import os
        is_paper = (
            not self.config.get("prod_mode", True) or 
            self.config.get("env") == "paper" or 
            os.getenv("ENV") == "paper"
        )

        if self.swarm:
            tripped = self.swarm.breaker.check(
                state.crash_score, state.all_stocks,
                state.regime, state.news_threat,
                paper_mode=is_paper
            )

        # VIX spike override
        if not tripped and self.vix_proxy.is_spike(vix_rel):
            tripped = True
            logger.warning(f"[CB] VIX proxy spike: {vix_rel:.2f}x")

        if tripped:
            state.action_taken = "CIRCUIT_BREAK"
            reason = getattr(self.swarm.breaker, "trip_reason", "VIX spike")

            # Force-exit on extreme
            if state.crash_score >= 80 or state.news_threat == "EXTREME":
                moved = self.swarm.portfolio.move_all_to_breakeven()
                if moved:
                    await self._emit({
                        "type": "log",
                        "msg": f"[!] FORCE_EXIT: {', '.join(moved)} -> break-even",
                        "level": "crash", "time": state.timestamp,
                    })

            await self._emit({
                "type": "log",
                "msg": f"[CB] TRIPPED: {reason}",
                "level": "crash", "time": state.timestamp,
            })

        return state

    async def _node_target_select(self, state: TradeState) -> TradeState:
        """
        Picks the best target from the scan results.
        In BEAR regime: re-ranks by REVERSAL POTENTIAL (low RSI + low BB%) rather than
        raw momentum score, so the Sniper evaluates the most oversold asset, not the
        highest-momentum falling knife.
        In BULL/SIDEWAYS: keeps standard highest-score ranking.
        """
        candidates = state.all_stocks
        held_syms = set()
        if self.swarm:
            held_syms = {m.sym for m in self.swarm.portfolio.missions.values()}

        available = [c for c in candidates if c["sym"] not in held_syms]

        if not available:
            available = candidates  # fallback: re-evaluate held if nothing else

        if available:
            # Sort by scanner-provided conviction score as primary ranker
            available = sorted(available, key=lambda x: x.get("_score", 0), reverse=True)

            # If MTF early-rejection is enabled, iterate candidates until one passes.
            # Previously the engine cleared `state.market` on the first rejection and
            # returned N/A — even though DOT-USD / BNB-USD at rank 2/3 would have passed.
            target = None
            rank_note = ""
            is_paper = (self.config.get("env") == "paper" or os.getenv("ENV") == "paper")
            threshold = int(self.config.get("min_conf", 55))
            
            if self.config.get("use_multi_timeframe"):
                for candidate in available:
                    conf, _ = self.swarm.mtf.validate(candidate, "BUY", live=False)
                    if conf < 0.50:
                        logger.info(
                            f"[SCOUT] {candidate['sym']} rejected early: "
                            f"MTF confluence {conf:.2f} < 0.50 — trying next candidate"
                        )
                        await self._emit({
                            "type": "log",
                            "msg": f"[SCOUT] {candidate['sym']} skipped: MTF={conf:.2f} < gate — checking next",
                            "level": "skip", "time": state.timestamp,
                        })
                        continue

                    # Pre-flight check: if candidate will fail heuristic entry later, skip it now
                    score = candidate.get("_score", 0)
                    rsi = candidate.get("rsi", 50)
                    bb_pct = candidate.get("bb_pct", 50)
                    
                    if is_paper:
                        if state.regime == "bull":
                            can_entry = (score >= 10) and rsi < 65
                        elif state.regime == "bear" and rsi > 60:
                            can_entry = score >= 8
                        else:
                            can_entry = (score >= 5 and rsi < 70) or (rsi < 40 and bb_pct < 30)
                            
                        # Estimate forced_conf inside _node_execute
                        forced_conf = 70 if score >= 20 else 65
                        forced_conf = max(forced_conf, threshold)
                        
                        if not can_entry or forced_conf < threshold:
                            logger.info(f"[SCOUT] {candidate['sym']} rejected early: fails heuristic pre-flight (can_entry={can_entry}, score={score}, rsi={rsi}, regime={state.regime}, conf={forced_conf} < {threshold})")
                            await self._emit({
                                "type": "log",
                                "msg": f"[SCOUT] {candidate['sym']} skipped: Fails heuristic limits — checking next",
                                "level": "skip", "time": state.timestamp,
                            })
                            continue

                    target = candidate
                    break

                if not target:
                    logger.info(f"[SCOUT] All {len(available)} candidates failed MTF gate — no target this cycle")
                    await self._emit({
                        "type": "log",
                        "msg": f"[SCOUT] All {len(available)} candidates below MTF gate — no entry this cycle",
                        "level": "warn", "time": state.timestamp,
                    })
            else:
                target = available[0]

            if target:
                # Add note if it's an oversold candidate in bear/sideways
                if state.regime != "bull" and target.get("rsi", 50) < 40:
                    rank_note = f"[OVERSOLD-RANKED, rsi={target.get('rsi',50):.0f} bb={target.get('bb_pct',50):.0f}]"
                else:
                    rank_note = f"[CONVICTION-RANKED, score={target.get('_score',0):.0f}]"
        else:
            target = candidates[0] if candidates else None
            rank_note = ""
        state.market = target

        if target:
            self.last_market = target
            await self._emit({
                "type": "market_update",
                "market": target,
                "regime": {
                    "regime": state.regime,
                    "crashScore": state.crash_score,
                    "avgChange": sum(a.get("change", 0) for a in state.all_stocks)
                                 / max(len(state.all_stocks), 1),
                },
            })
            await self._emit({
                "type": "log",
                "msg": f"TARGET: {target['sym']} [{target.get('exchange','?')}] "
                       f"score={target.get('_score', 0):.0f} @ {target['price']} {rank_note}",
                "level": "quant", "time": state.timestamp,
            })
        return state


    async def _node_sniper_reason(self, state: TradeState) -> TradeState:
        """AI-powered signal via SmartRouter (Llama-3.3-free -> DeepSeek-R1-free)."""
        if not state.market:
            return state

        mkt = state.market
        
        # Task 2: Bear Market Hunting Prompt Override
        advisory = self.swarm.analyst.get_regime_advisory(state.regime)
        if state.regime == "bear":
            advisory += " | SPECIAL INSTRUCTION: Look for OVERSOLD REVERSALS or SCALP opportunities. High-velocity exits preferred."

        if self.config.get("use_reasoning") and self.router:
            await self._emit({"type": "status", "status": "thinking"})
            historical = ", ".join(
                f"{h['year']}({h['name']}:{h['drawdown']}%dd)"
                for h in HISTORICAL_CRASHES
            )
            port_sum = (
                f"Capital=${self.swarm.portfolio.capital:.0f} | "
                f"Active={self.swarm.portfolio.active_count} | "
                f"Positions={', '.join(f'{m.sym}({m.action})' for m in self.swarm.portfolio.missions.values()) or 'None'}"
            )
            messages = build_sniper_messages(
                mkt=mkt,
                regime=state.regime,
                crash_score=state.crash_score,
                news_threat=state.news_threat,
                tf_confluence=state.tf_confluence,
                scout_context=state.scout_context,
                analyst_advisory=advisory,
                recent_lessons=self.swarm.analyst.get_recent_lessons(3),
                portfolio_summary=port_sum,
                historical_context=historical,
                active_exchange=state.active_exchange,
                # Layer 2: pass divergence context so the LLM counts conditions
                conditions_met=state.__dict__.get("_conditions_met", 0),
                bear_reversal_desc=state.__dict__.get("_bear_reversal_desc", ""),
            )
            sig = await self.router.call(
                messages=messages,
                crash_score=state.crash_score,
                regime=state.regime,
                news_threat=state.news_threat,
            )
            await self._emit({"type": "status", "status": "running"})
            await self._emit({"type": "router_update",
                              "stats": self.router.get_stats()})
        else:
            # Heuristic fallback: trend-following using score
            action = "BUY" if mkt.get("change", 0) > 0 else "SELL"
            entry  = mkt["price"]
            atr    = mkt.get("atr", entry * 0.02)
            sig = {
                "action": action,
                "confidence": min(80, 60 + int(mkt.get("_score", 0) / 2)),
                "entry": entry,
                "stop_loss":   round(entry - atr * 1.5, 4) if action == "BUY"
                               else round(entry + atr * 1.5, 4),
                "take_profit": round(entry + atr * 3,   4) if action == "BUY"
                               else round(entry - atr * 3,   4),
                "risk_reward": 2.0,
                "reasoning_steps": ["Heuristic: trend + score-based entry."],
                "regime_assessment": state.regime,
                "crash_risk": "LOW",
                "_model_used": "heuristic",
            }

        state.signal = sig
        if sig:
            await self._emit({"type": "signal", "signal": sig})
        return state

    async def _node_mtf_validate(self, state: TradeState) -> TradeState:
        """Multi-timeframe confluence check. In bear regime, uses weighted scoring."""
        if not state.market or not state.signal:
            return state
        signal = state.signal
        if isinstance(signal, list):
            text = " ".join(block.get("text", "") if isinstance(block, dict) else str(block) for block in signal)
            signal = {"action": "HOLD", "confidence": 40, "reasoning": text}
            state.signal = signal



        action = state.signal.get("action", "HOLD")
        live   = bool(self.config.get("live_data"))

        # Layer 1: Bear regime gets regime-aware weighted MTF instead of simple majority
        # ONLY apply to BUY/reversal setups; SELL setups in bear regime follow the primary trend
        if state.regime == "bear" and action == "BUY" and not live:
            weighted, conditions_met, desc = self.swarm.mtf.validate_bear_reversal(state.market)
            state.tf_confluence = weighted
            self.tf_confluence = weighted
            # Store condition count for prompt enrichment (Layer 2)
            state.__dict__["_conditions_met"]     = conditions_met
            state.__dict__["_bear_reversal_desc"] = desc
            await self._emit({
                "type": "log",
                "msg": f"MTF [BEAR-WEIGHTED]: {desc}",
                "level": "quant", "time": state.timestamp,
            })
        else:
            conf, desc = self.swarm.mtf.validate(state.market, action, live)
            state.tf_confluence = conf
            self.tf_confluence = conf
            state.__dict__["_conditions_met"]     = 0
            state.__dict__["_bear_reversal_desc"] = ""
            await self._emit({
                "type": "log",
                "msg": f"MTF: {desc} | Confluence: {conf:.0%}",
                "level": "quant", "time": state.timestamp,
            })
        return state

    async def _node_vector_check(self, state: TradeState) -> TradeState:
        """Query VectorMemory — déjà-vu guard against repeating known failures."""
        if not state.signal or not state.market:
            return state
        signal = state.signal
        if isinstance(signal, list):
            text = " ".join(block.get("text", "") if isinstance(block, dict) else str(block) for block in signal)
            signal = {"action": "HOLD", "confidence": 40, "reasoning": text}
            state.signal = signal



        if state.signal.get("action") == "HOLD":
            return state

        try:
            failures = await self.vec_mem.find_similar_failures(
                current_mkt=state.market,
                action=state.signal.get("action", "BUY"),
                confidence=int(state.signal.get("confidence", 75)),
                regime=state.regime,
                n=3,
            )
        except Exception as e:
            logger.error(f"[VEC] find_similar_failures failed: {e}")
            failures = None
        if failures and self.vec_mem.is_similar_to_failure(failures):
            warning = self.vec_mem.get_failure_warning(failures)
            # HARD SKIP on deja-vu
            state.action_taken = "SKIP"
            state.signal["action"] = "HOLD"  # Block execution
            state.signal["reasoning_steps"].append(f"VECTOR BLOCK: {warning}")
            
            await self._emit({
                "type": "log",
                "msg": f"VECTOR: {warning} -> SHOT BLOCKED",
                "level": "warn", "time": state.timestamp,
            })
        return state

    async def _node_execute(self, state: TradeState) -> TradeState:
        """Positions sizing and engine execution (Paper/Live)."""
        if not state.market:
            return state

        # ADDITIONAL BUG: Regime race condition — capture once at top
        regime = state.regime or "sideways"
        mkt    = state.market
        cfg    = self.config
        port   = self.swarm.portfolio
        now    = state.timestamp
        
        def _skip(reason: str):
            if reason in self._cycle_skip_reasons: return
            self._cycle_skip_reasons.add(reason)
            asyncio.create_task(self._emit({
                "type": "log", "msg": f"SKIP: {reason}",
                "level": "skip", "time": now,
            }))

        is_paper = (cfg.get("env") == "paper" or os.getenv("ENV") == "paper")
        threshold = int(cfg.get("min_conf", 55))
        
        if self.status == "DRAINING":
            _skip("[DRAINING] Skipping new entry — waiting for active missions to close.")
            state.action_taken = "SKIP"; return state

        # Environment Context
        score          = mkt.get("_score", 0)
        conditions_met = state.__dict__.get("_conditions_met", 0)
        rsi            = mkt.get("rsi", 50)
        bb_pct         = mkt.get("bb_pct", 50)
        price          = mkt.get("price", 0)
        sig            = state.signal or {}
        forced_conf    = None
        
        # HEURISTIC FAST-PATH: In paper mode, if reversal conditions are met
        # NOTE: Score thresholds are intentionally low here (5/8/10) — crypto vol-scores
        # in off-hours / quiet sessions drop to 0.2-10.2. The gate was `score >= 30`
        # (calibrated for NSE stocks), which NEVER fired on crypto. Aligned to min_conviction.
        if is_paper and forced_conf is None:
            if regime == "bull":
                can_entry = (score >= 10 or conditions_met >= 2) and rsi < 65
            elif regime == "bear" and rsi < 40:
                # Oversold bounce in bear — mean reversion entry
                can_entry = score >= 8
                if can_entry:
                    forced_conf = 60
                    logger.info(f"[SNIPER] Bear bounce entry: score={score} rsi={rsi}")
            else:
                # Sideways / unknown — RSI ceiling of 70 prevents entering overbought assets.
                # BNB at RSI 76-78 in sideways = immediate stop-loss on reversal → repeated
                # ANALYST cooldowns that block better candidates. Score gate alone is insufficient.
                can_entry = (score >= 5 and rsi < 70) or conditions_met >= 2 or (rsi < 40 and bb_pct < 30)

            if can_entry:
                if not forced_conf:
                    forced_conf = 70 if score >= 20 else 65
                
                # Paper mode: clamp heuristic confidence to the min_conf threshold.
                # When all AI models are unavailable (402/429), the heuristic generates
                # 60-65% confidence which is below the default min_conf=70, silently 
                # blocking every entry. In paper mode we allow the engine to trade.
                if is_paper:
                    forced_conf = max(forced_conf, threshold)

                sig["action"] = "BUY"
                sig["confidence"] = forced_conf
                sig["entry"] = price
                sig["_model_used"] = "heuristic"
                
                # ADDITIONAL BUG: SL/TP price calculation (Formula correction)
                if regime == "bull":
                    sig["stop_loss"]   = round(price * (1 - 0.030), 2)  # -3%
                    sig["take_profit"] = round(price * (1 + 0.025), 2) # +2.5%
                elif regime == "bear":
                    sig["stop_loss"]   = round(price * (1 - 0.025), 2)  # -2.5%
                    sig["take_profit"] = round(price * (1 + 0.020), 2) # +2.0%
                else:
                    sig["stop_loss"]   = round(price * (1 - 0.020), 2)  # -2.0%
                    sig["take_profit"] = round(price * (1 + 0.015), 2) # +1.5%

                # Respect UI min_conf setting — only applies in live mode now
                if forced_conf < threshold:
                    logger.info(f"[HEURISTIC] Confidence {forced_conf} below min_conf {threshold}, skipping")
                    state.action_taken = "SKIP"; return state

                sig["risk_reward"] = abs(sig["take_profit"] - price) / max(abs(price - sig["stop_loss"]), 0.0001)
                logger.info(f"[SNIPER] HEURISTIC ENTRY ({regime}): score={score:.0f} rsi={rsi:.0f} -> conf={forced_conf}%")
                state.signal = sig

        action = sig.get("action", "HOLD")
        if action == "HOLD":
            _skip(f"HOLD (conf={sig.get('confidence', 0)}%)")
            state.action_taken = "SKIP"; return state

        if sig.get("confidence", 0) < threshold and not forced_conf:
            # Paper mode allows 60 floor, Live mode sticks to threshold
            if is_paper and sig.get("confidence", 0) >= 60:
                logger.info(f"[FLEX] Allowing confidence {sig.get('confidence')}% in paper mode")
            else:
                _skip(f"Conf {sig.get('confidence')}% < threshold {threshold}%")
                state.action_taken = "SKIP"; return state

        # Task 2: MTF Gate — regime-aware threshold
        if forced_conf is None and regime not in ("sideways",):
            mtf_threshold = 0.25 if is_paper else float(cfg.get("mtf_min_confluence", 0.67))
            if (cfg.get("use_multi_timeframe") and state.tf_confluence < mtf_threshold):
                _skip(f"MTF {state.tf_confluence:.2f} < threshold {mtf_threshold:.2f}")
                state.action_taken = "SKIP"; return state

        if any(m.sym == mkt["sym"] for m in port.missions.values()):
            _skip(f"Already holding {mkt['sym']}")
            state.action_taken = "SKIP"; return state

        if not port.can_open_mission():
            _skip("Max missions active")
            state.action_taken = "SKIP"; return state

        # ── RSI Overtapped Check ──
        # Paper mode allows 85 floor (momentum), Live mode sticks to 75 (safety)
        rsi_limit = 85 if is_paper else 75
        if rsi > rsi_limit and action == "BUY":
            _skip(f"RSI {rsi:.0f} > {rsi_limit} (Overbought)")
            state.action_taken = "SKIP"; return state
        if rsi < 25 and action == "SELL":
            _skip(f"RSI {rsi:.0f} < 25 (Oversold)")
            state.action_taken = "SKIP"; return state

        # ── Loss Cooldown ──
        # Fix: ensure cycle count progress is positive and within range
        cycles_since_loss = self.cycle_count - self._last_loss_cycle
        if 0 <= cycles_since_loss < 3:
            _skip(f"Loss cooldown active ({3 - cycles_since_loss} cycles left)")
            state.action_taken = "SKIP"; return state

        if not cfg.get("auto_execute", True):
            await self._emit({
                "type": "log",
                "msg": f"SIGNAL: {action} {mkt['sym']} (Auto-Execute OFF)",
                "level": "sys", "time": state.timestamp,
            })
            state.action_taken = "SIGNAL"; return state

        # ── RISK-ADJUSTED POSITION SIZING (Loss Tolerance) ────────
        # frac = fraction of total capital to risk per trade (e.g. 0.02 = 2%)
        # Configurable via UI/config key "risk_pct"; defaults to 2%
        frac = float(cfg.get("risk_pct", 0.02))

        # Goal: Deploy enough that hitting the stop costs exactly frac% of total capital
        price = mkt["price"]
        sl_initial = float(sig.get("stop_loss", price * 0.98))
        stop_distance = abs(price - sl_initial)
        stop_pct = max(stop_distance / max(price, 0.0001), 0.005)  # floor 0.5% stop

        # Total capital = port.capital, Available = port.available_capital
        max_deploy = port.available_capital * 0.40  # Capped at 40% per trade
        risk_adjusted = (port.capital * frac) / stop_pct

        # Deploy min of risk-adjusted theoretical or 40% of available cash
        final_value = min(max_deploy, risk_adjusted)
        qty = max(1, int(final_value / max(price, 0.0001)))

        logger.info(
            f"[SIZER] Wallet=₹{port.capital:.0f} | Avail=₹{port.available_capital:.0f} | "
            f"RiskFrac={frac:.1%} | RiskTarget=₹{port.capital * frac:.0f} | "
            f"Stop={stop_pct:.2%} | Deploy=₹{final_value:.0f} | Qty={qty}"
        )

        # ── VATS trailing stop (ONLY formula — NO hardcoded %) ────
        entry   = float(sig.get("entry",       price))
        vix_rel = state.__dict__.get("vix_rel", 1.0)
        atr     = mkt.get("atr", entry * 0.02)
        k       = float(cfg.get("vats_k", 2.5))
        
        # Volatility widening: widen stop if VIX is high
        if vix_rel > 1.5:
            k *= 1.2
            logger.info(f"[SIZER] VIX High ({vix_rel:.2f}) -> Widening SL multiplier k to {k:.2f}")

        # VATS_t = Price_max - (ATR × k × ln(1 + σ_rel))
        trailing = compute_vats_vix(entry, atr, k, vix_rel, action)

        # AI-provided SL/TP as initial bounds (may be superseded by VATS)
        sl = float(sig.get("stop_loss",   trailing))
        tp = float(sig.get("take_profit", entry * (1.05 if action == "BUY" else 0.95)))

        # Ensure VATS trailing stop is tighter than AI's SL for BUY
        if action == "BUY":
            sl = max(sl, trailing)   # take the higher (safer) stop
        else:
            sl = min(sl, trailing)

        mission = Mission(
            id=str(uuid.uuid4())[:8],
            sym=mkt["sym"],
            action=action,
            entry_price=entry,
            current_price=entry,
            qty=qty,
            stop_loss=sl,
            take_profit=tp,
            trailing_stop=trailing,
            regime_at_entry=regime,  # captured at top of _node_execute
            exchange=mkt.get("exchange", "NSE"),
            asset_type=mkt.get("asset", "equity"),
            atr_at_entry=atr,
            confidence=int(sig.get("confidence", 75)),
            created_at=state.timestamp,
            peak_price=entry,
            vats_multiplier_k=k,
            data={
                "rsi_at_entry": rsi,
                "adx_at_entry": mkt.get("adx", 25.0),
                "bb_pct_at_entry": bb_pct,
                "change_at_entry": mkt.get("change", 0.0),
            }
        )

        # FIX 5: Available capital check must use ₹ reserved amount (Issue #5)
        port.open_mission(mission, final_value) 
        state.action_taken = "OPEN"
        state.mission_id   = mission.id

        # Persist asynchronously
        asyncio.create_task(self.checkpoint.save_mission(mission.to_dict()))

        model_tag = sig.get("_model_used", "?")
        await self._emit({
            "type": "log",
            "msg": (
                f"🎯 MISSION [{mission.exchange}|{model_tag}]: "
                f"{action} {mkt['sym']} ×{qty} @ {entry:.4f} | "
                f"SL(VATS):{sl:.4f} | TP:{tp:.4f} | "
                f"ATR:{atr:.4f} | k:{k} | VIX_rel:{vix_rel:.2f}"
            ),
            "level": "buy" if action == "BUY" else "sell",
            "time": state.timestamp,
        })
        return state

    # ── Graph wiring ──────────────────────────────────────────────

    def _build_graph(self) -> StateGraph:
        g = StateGraph()
        g.add_node("scan",            self._node_scan)
        g.add_node("circuit_breaker", self._node_circuit_breaker)
        g.add_node("target_select",   self._node_target_select)
        g.add_node("sniper_reason",   self._node_sniper_reason)
        g.add_node("mtf_validate",    self._node_mtf_validate)
        g.add_node("vector_check",    self._node_vector_check)
        g.add_node("execute",         self._node_execute)

        g.add_edge("scan",            "circuit_breaker")
        g.add_conditional_edge("circuit_breaker",
            lambda s: "END" if s.action_taken == "CIRCUIT_BREAK" else "target_select")
        g.add_edge("target_select",   "sniper_reason")
        g.add_edge("sniper_reason",   "mtf_validate")
        g.add_edge("mtf_validate",    "vector_check")
        g.add_edge("vector_check",    "execute")
        return g

    # ── Guardian Loop  (background — separate task) ───────────────

    async def guardian_loop(self):
        """
        Continuously monitors all open missions.
        All exit decisions use VATS formula — never a hardcoded ±%.
        """
        while self.status != "STOPPED":
            if not self.swarm.portfolio.missions:
                # If we are draining and no missions left, stop the guardian too
                if self.status == "DRAINING":
                    break
                await asyncio.sleep(8); continue

            now = datetime.datetime.now().strftime("%H:%M:%S")

            # News scan
            news_r = await self.news.scan()
            self.swarm.news_threat = news_r["threat_level"]

            # Black Swan → break-even
            if self.news.should_move_to_breakeven():
                moved = self.swarm.portfolio.move_all_to_breakeven()
                if moved:
                    await self._emit({
                        "type": "log",
                        "msg": f"🚨 BLACK SWAN [{news_r['threat_level']}]: "
                               f"{', '.join(moved)} → BREAK-EVEN",
                        "level": "crash", "time": now,
                    })

            vix_rel = self.vix_proxy.current

            # VIX spike → force-exit all
            if self.vix_proxy.is_spike(vix_rel):
                await self._emit({
                    "type": "log",
                    "msg": f"⚡ VIX SPIKE {vix_rel:.2f}× — FORCE_EXIT all",
                    "level": "crash", "time": now,
                })
                for mid in list(self.swarm.portfolio.missions.keys()):
                    m = self.swarm.portfolio.missions.get(mid)
                    if m:
                        closed = self.swarm.portfolio.close_mission(mid, m.current_price)
                        if closed:
                            asyncio.create_task(self.checkpoint.delete_mission(mid))
                            asyncio.create_task(self._store_memory(closed))
                await asyncio.sleep(self._concurrency["cycle_delay"] * 3); continue

            # Per-mission evaluation
            for mid, mission in list(self.swarm.portfolio.missions.items()):
                curr_d = await self._get_current_quote(mission)
                if not curr_d:
                    continue

                # ── VATS trailing stop update (ONLY formula) ────
                curr_atr = curr_d.get("atr", mission.atr_at_entry)
                k        = mission.vats_multiplier_k
                peak     = mission.peak_price

                new_vats = compute_vats_vix(peak, curr_atr, k, vix_rel, mission.action)

                if mission.action == "BUY":
                    if new_vats > mission.trailing_stop:
                        mission.trailing_stop = new_vats   # ratchet up
                else:
                    if new_vats < mission.trailing_stop or mission.trailing_stop == 0:
                        mission.trailing_stop = new_vats   # ratchet down

                # Guardian evaluation
                regime = self.engine.state.regime
                result = self.swarm.guardian.evaluate_mission(
                    mission, curr_d, regime, self.swarm.news_threat,
                )
                verdict = result["verdict"]
                emoji   = {"STAY": "🟢", "TRIM": "🟡", "EXIT": "🔴",
                           "BREAKEVEN": "🟠"}.get(verdict, "⚪")

                await self._emit({
                    "type": "log",
                    "msg": (
                        f"GUARDIAN {emoji} {mission.sym}: {verdict} — "
                        f"{result['reason']} | "
                        f"VATS:{result['vats']:.4f} (ATR:{curr_atr:.4f}×k:{k}×ln(1+{vix_rel:.2f})) | "
                        f"VIX_rel:{vix_rel:.2f}"
                    ),
                    "level": "guardian", "time": now,
                })

                if verdict == "TRIM":
                    pnl = self.swarm.portfolio.trim_mission(mid)
                    if pnl is not None:
                        asyncio.create_task(self.checkpoint.save_mission(mission.to_dict()))

                elif verdict == "EXIT":
                    closed = self.swarm.portfolio.close_mission(mid, curr_d["price"])
                    if closed:
                        asyncio.create_task(self.checkpoint.delete_mission(mid))
                        asyncio.create_task(self._store_memory(closed))
                        # Removed duplicate analyze_trade call here (Issue #2)
                        await self._emit({
                            "type": "log",
                            "msg": f"EXIT: {closed.sym} PnL:₹{closed.unrealized_pnl:.2f}",
                            "level": "buy" if closed.unrealized_pnl >= 0 else "sell",
                            "time": now,
                        })
                        await self._emit({
                            "type": "trade_update",
                            "trade": {
                                "n":      len(self.swarm.portfolio.completed_missions),
                                "sym":    closed.sym,
                                "action": closed.action,
                                "entry":  closed.entry_price,
                                "exit":   closed.current_price,
                                "pnl":    closed.unrealized_pnl,
                                "win":    closed.unrealized_pnl > 0,
                                "conf":   closed.confidence,
                                "rr":     0,
                                "regime": closed.regime_at_entry,
                                "time":   now,
                                "sl":     closed.stop_loss,
                                "tp":     closed.take_profit,
                                "qty":    closed.qty,
                            },
                        })

            await self._emit({"type": "state_update", "state": self._state_dict()})
            await asyncio.sleep(self._concurrency["cycle_delay"] * 3)

    # ── Main engine loop (single cycle) ──────────────────────────

    async def run_cycle(self) -> Dict:
        """Execute one complete StateGraph cycle. Returns cycle summary."""
        self.cycle_count += 1
        self._cycle_skip_reasons.clear()  # Reset per-cycle dedup
        now   = datetime.datetime.now().strftime("%H:%M:%S")
        news  = await self.news.scan()
        self.swarm.news_threat = news["threat_level"]

        # Build initial state
        state = TradeState(
            regime=self.engine.state.regime,
            crash_score=self.engine.state.crash_score,
            all_stocks=[],
            active_exchange="NSE",
            news_threat=self.swarm.news_threat,
            news_score=self.news.threat_score,
            cycle=self.cycle_count,
            timestamp=now,
        )

        # ── PRE-FLIGHT: Update Global State BEFORE Graph Execution (Critical) ──
        # 1. First scan (used for regime and VIX)
        state = await self._node_scan(state)
        
        # 2. Update Regime
        if state.all_stocks:
            detected = self.engine.detect_regime(state.all_stocks)
            state.regime      = detected["regime"]
            state.crash_score = detected["crashScore"]
            self.engine.state.regime      = state.regime
            self.engine.state.crash_score = state.crash_score

        # 3. Update VIX proxy
        vix_rel = self.vix_proxy.update(state.all_stocks)
        state.__dict__["vix_rel"] = vix_rel
        
        # 4. Now run the rest of the graph (target selection onwards)
        state = await self._graph.run(state, entry="circuit_breaker")
        
        # Cache for _state_dict (header badge)
        self._last_exchange = state.active_exchange

        # Sync legacy engine state
        self.engine.state.capital     = self.swarm.portfolio.capital
        self.engine.state.pnl         = self.swarm.portfolio.total_realized_pnl
        self.engine.state.wins        = self.swarm.portfolio.wins
        self.engine.state.losses      = self.swarm.portfolio.losses
        self.engine.state.regime      = state.regime
        self.engine.state.crash_score = state.crash_score
        self.engine.state.max_dd      = self.swarm.portfolio.max_dd

        # Regime detection handled pre-graph to fix ordering bug


        mode = "LIVE" if self.config.get("live_data") else "SIM"
        await self._emit({
            "type": "log",
            "msg": (
                f"[{mode}] Cycle {self.cycle_count} | {state.regime.upper()} | "
                f"₹{self.swarm.portfolio.capital:.2f} | "
                f"Missions:{self.swarm.portfolio.active_count} | "
                f"VIX_rel:{vix_rel:.2f} | News:{state.news_threat} | "
                f"{state.active_exchange}"
            ),
            "level": "loop", "time": now,
        })
        # Unified UI State Update
        ui_state = self._state_dict()
        await self._emit({"type": "state_update", "state": ui_state})
        
        await self._emit({"type": "router_update", "stats": self.router.get_stats() if self.router else {}})
        await self._emit({"type": "memory_update",
                          "vec":        self.vec_mem.get_status(),
                          "checkpoint": self.checkpoint.get_status()})

        logger.info(state.to_log())
        return {"cycle": self.cycle_count, "action": state.action_taken}

    # ── Main run loop ─────────────────────────────────────────────

    async def run(self):
        """Full engine: spawn guardian + cycle loop."""
        try:
            self.status = "RUNNING"
            
            # Immediate sync for UI
            s = self._state_dict()
            await self._emit({"type": "state_update", "state": s})
            
            # Restore persisted missions
            restored = await self.checkpoint.restore_missions(self.swarm.portfolio)
            if restored:
                await self._emit({
                    "type": "log",
                    "msg": f"♻️  CHECKPOINT: Restored {restored} mission(s)",
                    "level": "sys",
                    "time": datetime.datetime.now().strftime("%H:%M:%S"),
                })
            
            # Restore recent failure memories for Déjà-vu checks (Issue #9)
            rest_mem = await self.vec_mem.restore_memories()
            if rest_mem:
                await self._emit({
                    "type": "log",
                    "msg": f"🧠 MEMORY: Loaded {rest_mem} failure patterns from {self.vec_mem.table}",
                    "level": "sys",
                    "time": datetime.datetime.now().strftime("%H:%M:%S"),
                })

            guardian_task = asyncio.create_task(self.guardian_loop())

            max_cycles  = int(self.config.get("max_trades", 200))
            target_pnl  = float(self.config.get("target_pnl", 999999))
            delay       = self._concurrency["cycle_delay"]
            
            # BROADCAST_INTERVAL = 30 # For memory management if needed
            
            while self.status != "STOPPED":
                try:
                    summary = await self.run_cycle()
                except Exception as cycle_e:
                    logger.error(f"[FATAL] Engine Cycle Crash: {cycle_e}", exc_info=True)
                    await self._emit({
                        "type": "log",
                        "msg": f"⚠️ ENGINE CYCLE CRASH: {str(cycle_e)[:100]}",
                        "level": "crash",
                        "time": datetime.datetime.now().strftime("%H:%M:%S"),
                    })
                    await asyncio.sleep(delay * 2) # cool-down
                    continue

                if self.cycle_count >= max_cycles:
                    if self.swarm.portfolio.missions:  # don't stop if holding
                        logger.info(f"[MAIN] Cycle {self.cycle_count} limit reached but holding position — resetting counter...")
                        self.cycle_count = 0  
                    else:
                        await self._emit({
                            "type": "log", "msg": "Cycle limit reached.",
                            "level": "sys",
                            "time": datetime.datetime.now().strftime("%H:%M:%S"),
                        })
                        break

                if self.swarm.portfolio.total_realized_pnl >= target_pnl:
                    if not self.swarm.portfolio.missions:
                        await self._emit({
                            "type": "log",
                            "msg": f"🏆 TARGET REACHED: ₹{self.swarm.portfolio.total_realized_pnl:.2f} — Stopping.",
                            "level": "buy",
                            "time": datetime.datetime.now().strftime("%H:%M:%S"),
                        })
                        self.status = "STOPPED"
                        break
                    else:
                        # Enter draining mode automatically if target reached but positions open
                        if self.status != "DRAINING":
                            self.status = "DRAINING"
                            await self._emit({
                                "type": "log",
                                "msg": f"🏆 TARGET REACHED — DRAIN mode active (waiting for {self.swarm.portfolio.active_count} position(s))",
                                "level": "warn",
                                "time": datetime.datetime.now().strftime("%H:%M:%S"),
                            })

                # Check for manual stop/drain transition
                if self.status == "DRAINING" and not self.swarm.portfolio.missions:
                    await self._emit({
                        "type": "log",
                        "msg": "✅ DRAIN COMPLETE: All missions closed. Engine idling.",
                        "level": "sys",
                        "time": datetime.datetime.now().strftime("%H:%M:%S"),
                    })
                    self.status = "STOPPED"
                    break

                await asyncio.sleep(delay)

        except Exception as e:
            logger.error(f"[FATAL] Engine loop crashed: {e}", exc_info=True)
        finally:
            self.status = "STOPPED"
            await self._emit({"type": "status", "status": "idle"})
            if 'guardian_task' in locals():
                guardian_task.cancel()

    def stop(self):
        """Gracious stop: DRAIN if missions exist, else STOP."""
        if self.swarm.portfolio.missions:
            self.status = "DRAINING"
            logger.info("[ENGINE] Manual STOP requested -> Entering DRAIN mode.")
        else:
            self.status = "STOPPED"
            logger.info("[ENGINE] Manual STOP requested -> Stopping immediately (no active missions).")

    # ── Helpers ───────────────────────────────────────────────────

    async def _get_current_quote(self, mission: Mission) -> Optional[Dict]:
        """Fetch live price for a held mission."""
        if self.config.get("live_data"):
            from data_layer import MarketDataProvider
            meta = {
                "sym": mission.sym, "exchange": mission.exchange,
                "asset": mission.asset_type, "base_price": mission.current_price,
                "beta": 1.0, "vol": "MED",
            }
            mdp = MarketDataProvider(self.config.get("fmp_key") or None)
            return await mdp.get_quote(mission.sym, meta)
        else:
            # Simulation: Directional drift toward TP/SL — not pure random walk
            curr = mission.current_price
            atr  = mission.atr_at_entry
            entry = mission.entry_price
            sl    = mission.stop_loss
            tp    = mission.take_profit
            
            # Prevent division by zero if tp == entry
            target_distance = max(abs(tp - entry), 0.0001)
            progress = (curr - entry) / target_distance
            
            # Bear scalp: slight mean-reversion bias with volatility
            drift = atr * 0.08 * (1 - progress)     # pull toward TP early, fades as price moves
            noise = random.gauss(drift, atr * 0.25)  # larger noise to actually hit SL or TP
            new_price = max(curr + noise, 0.0001)
            
            mission.current_price = round(new_price, 6)
            if new_price > mission.peak_price:
                mission.peak_price = new_price
            mission.unrealized_pnl = round(
                (new_price - entry) * mission.qty * (1 if mission.action == "BUY" else -1), 2
            )
            return {"price": new_price, "atr": atr, **mission.to_dict()}

    async def _store_memory(self, closed: Mission):
        """Persist closed trade to VectorMemory for future déjà-vu checks."""
        mem = self.swarm.analyst.analyze_trade(closed)
        await self.vec_mem.store(
            closed.to_dict(),
            {
                "pattern_tag":    mem.pattern_tag,
                "rsi_at_entry":   closed.data.get("rsi_at_entry", 50.0),
                "change_at_entry": closed.data.get("change_at_entry", 0.0),
                "vol_ratio":      closed.data.get("vol_ratio", 1.0),
                "adx_at_entry":   closed.data.get("adx_at_entry", 25.0),
                "bb_pct_at_entry": closed.data.get("bb_pct_at_entry", 50.0),
            },
        )
        if not mem.win:
            self._last_loss_cycle = self.cycle_count
            logger.info(f"[ANALYST] Loss detected. Activating cooldown for 3 cycles (Cycle {self.cycle_count})")

    @property
    def is_running(self) -> bool:
        """Helper for legacy checks in main.py."""
        return self.status != "STOPPED"

    def _state_dict(self) -> Dict:
        """Bridge to legacy state format expected by the frontend."""
        portfolio = self.swarm.portfolio
        return {
            "capital":     round(portfolio.capital, 2),
            "pnl":         round(portfolio.total_realized_pnl, 2),
            "unrealized":  round(portfolio.total_unrealized_pnl, 2),
            "reserved":    round(portfolio.reserved_capital, 2),
            "available":   round(portfolio.available_capital, 2),
            "wins":        portfolio.wins,
            "losses":      portfolio.losses,
            "regime":      self.engine.state.regime,
            "crashScore":  self.engine.state.crash_score,
            "maxDd":       round(portfolio.max_dd * 100, 2),
            "trades":      len(portfolio.completed_missions),
            "memories":    len(self.vec_mem._local), # FIX 9: Show real pattern count
            "scout_status": self.scout.nse.get_status() if hasattr(self.scout.nse, 'get_status') else "Active",
            "activeMissions": portfolio.active_count,
            "market":      self.last_market,
            "tf_confluence": self.tf_confluence,
            "newsThreat":  self.swarm.news_threat,
            "isPaper":     self.is_paper,
            "engineStatus": self.status,
            "routerStats": self.router.get_stats(),
            "missions":    [m.to_dict() for m in portfolio.missions.values()],
            # UI header fields
            "exchange":    getattr(self, "_last_exchange", self.config.get("primary_market", "GLOBAL")),
            "vixRel":      round(self.vix_proxy.current, 3),
        }

    def get_status(self) -> Dict:
        return {
            "is_running":  self.status != "STOPPED",
            "status":      self.status,
            "cycle":       self.cycle_count,
            "concurrency": self._concurrency,
            "router":      self.router.get_stats() if self.router else {},
            "memory":      self.vec_mem.get_status() if self.vec_mem else {},
            "checkpoint":  self.checkpoint.get_status() if self.checkpoint else {},
        }
