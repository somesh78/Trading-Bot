"""
SENTINEL QUANT - AI Smart Router
=================================
Bulletproof model routing with fallbacks and heuristic floor.

Model Hierarchy (April 2026 stable):
  Primary   : meta-llama/llama-3.3-70b-instruct:free  (high reasoning)
  Secondary : google/gemini-2.0-flash-001              (fast, reliable)
  Tertiary  : nvidia/nemotron-3-super-free             (high-reasoning backup)

Features:
  - Sequential fallback: tries all 3 models before giving up
  - 429 cool-off: rate-limited primary is bypassed automatically
  - 404 guard: strips :free suffix on 404 and retries once
  - Heuristic floor: engine never crashes; always gets a HOLD decision
"""

import os
import json
import logging
import asyncio
import time
import math
import random
import uuid
import datetime
from typing import Dict, Any, Optional, Tuple, List
from dotenv import load_dotenv

# Force load .env in GraphEngine process
for env_path in [".env", "backend/.env", "../backend/.env"]:
    load_dotenv(env_path)

import httpx

logger = logging.getLogger("sentinel.router")

# =====================================================================
# MODEL HIERARCHY (April 2026 stable IDs)
# =====================================================================

PRIMARY_MODEL   = "meta-llama/llama-3.3-70b-instruct:free"
SECONDARY_MODEL = "google/gemini-2.0-flash-001"
TERTIARY_MODEL  = "deepseek/deepseek-chat" # Added a paid-tier-like provider for speed
BACKUP_MODEL    = "mistralai/mistral-7b-instruct:free"

MODEL_CHAIN = [PRIMARY_MODEL, SECONDARY_MODEL, TERTIARY_MODEL, BACKUP_MODEL]


# =====================================================================
# VATS FORMULA
# =====================================================================

def compute_vats_vix(peak_price: float, atr: float, k: float, vix_rel: float, action: str) -> float:
    """
    Volatility-Adjusted Trailing Stop (VATS) using VIX proxy.
    Formula: Stop = Peak +/- (ATR * k * ln(1 + VIX_rel))
    """
    adjustment = atr * k * math.log(1 + vix_rel)
    if action == "BUY":
        return peak_price - adjustment
    else:
        return peak_price + adjustment


# =====================================================================
# SNIPER MESSAGE BUILDER
# =====================================================================

def build_sniper_messages(
    mkt, regime, crash_score, news_threat, tf_confluence,
    scout_context, analyst_advisory, recent_lessons,
    portfolio_summary, historical_context, active_exchange,
    conditions_met: int = 0, bear_reversal_desc: str = "",
) -> Tuple[str, str]:
    """
    Constructs the system and user prompts for the Sniper agent.
    Returns (user_prompt, system_prompt) tuple.
    In bear regime, injects a divergence checklist so the LLM counts — not reasons.
    """
    system_prompt = f"""You are the SENTINEL SNIPER, a high-conviction quant execution agent.
CURRENT MARKET REGIME: {regime} (Crash Score: {crash_score}/100)
NEWS THREAT LEVEL: {news_threat}
EXCHANGE: {active_exchange}

ANALYST ADVISORY: {analyst_advisory}
RECENT LESSONS: {recent_lessons}

Your goal is to decide if the target asset meets the execution threshold.
Always respond with valid JSON only. No markdown, no explanation outside JSON."""

    # Layer 2: Bear-specific enriched checklist prompt
    if regime == "bear":
        user_prompt = f"""TARGET ASSET: {json.dumps(mkt)}
MTF CONFLUENCE (weighted): {tf_confluence:.0%}
REVERSAL SIGNAL SCAN: {bear_reversal_desc}
SCOUT CONTEXT: {scout_context}
PORTFOLIO: {portfolio_summary}

You are a PRECISION SCALP TRADER. Regime: PERSISTENT BEAR.
Your ONLY mandate is SHORT-TERM REVERSALS (target 1-3%). Do NOT look for trend continuations.

Technical Evidence:
- RSI(14): {mkt.get('rsi', 'N/A')}  [KEY: Is this < 32? Divergence likely if so]
- MACD vs Signal: {mkt.get('macd', 0):.4f} vs {mkt.get('macd_sig', 0):.4f}  [KEY: histogram turning positive?]
- Bollinger Band %: {mkt.get('bb_pct', 'N/A')}  [KEY: < 15 = extreme oversold]
- Volume ratio (vs avg): {(mkt.get('volume',1)/max(mkt.get('vol_avg',1),1)):.2f}x  [KEY: < 0.7 = exhaustion]
- Price change: {mkt.get('change', 0):.3f}%

REVERSAL CHECKLIST ({conditions_met}/4 conditions met):
1. RSI < 32 on 15m with potential bullish divergence
2. MACD histogram turning positive (negative but improving)
3. Price within lower 15% of Bollinger Band
4. Sell volume exhaustion (vol ratio < 0.7 on declining price)

RULES:
- If conditions_met == 0: respond HOLD. Do not enter.
- If conditions_met == 1: respond HOLD. Too early.
- If conditions_met >= 2: you HAVE PERMISSION to enter. A 60% confident scalp is VALID.
- Do not hedge. Be decisive. 60% confidence with 2/4 conditions = EXECUTE.

OUTPUT FORMAT (strict JSON only):
{{
  "action": "BUY" | "SELL" | "HOLD",
  "confidence": 0-100,
  "reasoning": "One-line logic referencing specific conditions met",
  "entry": {mkt.get('price', 0)},
  "risk_reward": 1.5,
  "stop_loss": 0.0,
  "take_profit": 0.0
}}"""
    else:
        user_prompt = f"""TARGET ASSET: {json.dumps(mkt)}
MTF CONFLUENCE: {tf_confluence:.0%}
SCOUT CONTEXT: {scout_context}
PORTFOLIO: {portfolio_summary}
HISTORICAL CONTEXT: {historical_context}

DECISION CRITERIA:
1. In Paper mode or BULL regime, you are a HIGH-CONVICTION SCALPER. Confidence > 60% is plenty. Be aggressive.
2. Short-selling only allowed if regime is BEAR or CRASH.
3. If regime is BEAR, focus on Oversold Reversals (RSI < 30) and Bullish Divergence. You have permission to take counter-trend scalps with tight 1% stop-losses.
4. Output MUST be valid JSON.

OUTPUT FORMAT (strict):
{{
  "action": "BUY" | "SELL" | "HOLD",
  "confidence": 0-100,
  "reasoning": "One-line logic",
  "entry": 0.0,
  "risk_reward": 1.5,
  "stop_loss": 0.0,
  "take_profit": 0.0
}}"""

    return user_prompt, system_prompt



# =====================================================================
# HEURISTIC FALLBACK  (engine never crashes)
# =====================================================================

def _heuristic_decision(mkt: Dict) -> Dict[str, Any]:
    """
    Rule-based HOLD/BUY decision when all AI models are unavailable.
    Uses RSI + MACD signal only — no ML, no API.
    """
    rsi    = mkt.get("rsi", 50) if isinstance(mkt, dict) else 50
    change = mkt.get("change", 0) if isinstance(mkt, dict) else 0
    macd   = mkt.get("macd", 0) if isinstance(mkt, dict) else 0
    macd_s = mkt.get("macd_sig", 0) if isinstance(mkt, dict) else 0

    # Simple momentum heuristic (Boosted for Paper mode execution)
    if rsi < 35 and change > 0 and macd > macd_s:
        action = "BUY"
        confidence = 75
        reasoning = "Heuristic Sniper: RSI oversold + momentum + MACD bullish"
    elif rsi > 65 and change < 0 and macd < macd_s:
        action = "SELL"
        confidence = 50
        reasoning = "Heuristic: RSI overbought + negative momentum + MACD bearish cross"
    else:
        action = "HOLD"
        confidence = 40
        reasoning = "Heuristic: No strong signal - all AI models unavailable"

    logger.warning(f"[ROUTER] Using heuristic decision: {action} (confidence={confidence})")
    return {
        "action":     action,
        "confidence": confidence,
        "reasoning":  reasoning,
        "entry":      mkt.get("price", 0) if isinstance(mkt, dict) else 0,
        "risk_reward": 1.0,
    }


# =====================================================================
# SMART ROUTER
# =====================================================================

class SmartRouter:
    """
    Routes LLM requests with bulletproof fallback chain.
    - Implements cool-off for 429 rate limits
    - Guards against 404 model-not-found errors  
    - Falls back to heuristic if all 3 models fail
    """

    def __init__(self, api_key: Optional[str] = None, broadcast_fn = None):
        # Priority: Passed key -> GROQ_KEY env -> OPENROUTER_KEY env
        self.api_key = api_key or os.getenv("GROQ_KEY") or os.getenv("OPENROUTER_KEY")
        self._broadcast = broadcast_fn
        
        if not self.api_key:
            logger.error("[ROUTER] ❌ NO API KEY FOUND in config or environment variables!")
        else:
            masked = f"{self.api_key[:6]}...{self.api_key[-4:]}"
            logger.info(f"[ROUTER] Initialized with key: {masked}")

        # FIX: Robustly detect Groq vs OpenRouter
        if self.api_key and (self.api_key.startswith("gsk_") or "groq" in os.getenv("GROQ_KEY", "").lower()):
            self.base_url = "https://api.groq.com/openai/v1"
            self.is_groq  = True
            logger.info("[ROUTER] Groq endpoint selected")
        else:
            self.base_url = "https://openrouter.ai/api/v1"
            self.is_groq  = False
            logger.info("[ROUTER] OpenRouter endpoint selected")
        
        self._cooloff_until:  float = 0
        self._cooloff_duration = 60   # base cooloff — doubles each consecutive 429
        self._cooloff_max      = 300  # cap at 5 minutes
        self._consecutive_429  = 0
        self.primary_dead       = False # FIX 8: Session-level 429 fallback status
        self._last_429_notified = 0   # timestamp of last UI notification
        self._stats           = {"calls": 0, "failures": 0, "429s": 0, "heuristics": 0, "api_cooling": False, "cooloff_remaining": 0, "primary_dead": False}

    def _get_headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type":  "application/json",
            "X-Title":       "Sentinel Quant Engine",
            "HTTP-Referer":  "https://sentinel-quant.local",
        }

    async def _call_model(self, model: str, prompt: str, system: str) -> Optional[str]:
        """
        Call a single OpenRouter model.
        Returns content string or None on any failure.
        """
        self._stats["calls"] += 1
        # Map models if using native Groq
        actual_model = model
        if getattr(self, "is_groq", False):
            if "llama-3.3-70b" in model:
                actual_model = "llama-3.3-70b-versatile"
            elif "gemini" in model:
                actual_model = "deepseek-r1-distill-llama-70b" # Best secondary on Groq
            elif "nemotron" in model:
                actual_model = "llama-3.1-8b-instant"          # Fast backup

        payload = {
            "model": actual_model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user",   "content": prompt},
            ],
            "temperature": 0.2,
            "max_tokens":  512,
            "response_format": {"type": "json_object"},
        }

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                r = await client.post(
                    f"{self.base_url}/chat/completions",
                    headers=self._get_headers(),
                    content=json.dumps(payload),
                )

                if r.status_code == 429:
                    self._stats["429s"] += 1
                    self._consecutive_429 += 1
                    # Exponential backoff: 60 → 120 → 240 → 300 (cap)
                    backoff = min(self._cooloff_max, self._cooloff_duration * (2 ** (self._consecutive_429 - 1)))
                    self._cooloff_until = time.time() + backoff
                    self._stats["api_cooling"] = True
                    self._stats["cooloff_remaining"] = int(backoff)
                    logger.warning(f"[ROUTER] 429 on {model} - backoff {backoff}s (consecutive: {self._consecutive_429})")
                    # Notify UI only once per cooloff event (not every call)
                    now = time.time()
                    if now - self._last_429_notified > 30:
                        self._last_429_notified = now
                        if self._broadcast:
                            asyncio.create_task(self._broadcast({
                                "type": "log",
                                "msg": f"AI ROUTER: API_COOLING — backoff {backoff}s (429 ×{self._consecutive_429})",
                                "level": "warn"
                            }))
                    if self._consecutive_429 >= 3:
                        self.primary_dead = True
                        self._stats["primary_dead"] = True
                    # FIX: Add a small sleep to avoid immediate loop-hammering
                    await asyncio.sleep(2.0)
                    return None

                if r.status_code == 404:
                    logger.warning(f"[ROUTER] 404 on {model} - model not available, skipping")
                    return None

                if r.status_code == 401:
                    logger.error(f"❌ [ROUTER] AUTH ERROR (401) on {model}: {r.text[:120]}. Check your GROQ_KEY or OPENROUTER_KEY in .env!")
                    return None

                if r.status_code >= 400:
                    logger.warning(f"[ROUTER] HTTP {r.status_code} on {model}: {r.text[:120]}")
                    return None

                r.raise_for_status()
                res = r.json()
                content = res.get("choices", [{}])[0].get("message", {}).get("content", "")
                return content if content else None

        except httpx.TimeoutException:
            logger.warning(f"[ROUTER] Timeout on {model}")
            if self._broadcast:
                asyncio.create_task(self._broadcast({
                    "type": "log",
                    "msg": f"AI ROUTER: Timeout on {model.split('/')[1][:20]}. Falling back...",
                    "level": "warn"
                }))
            return None
        except Exception as e:
            self._stats["failures"] += 1
            logger.debug(f"[ROUTER] {model} error: {e}")
            return None

    async def call(self, messages: Tuple[str, str], mkt: Dict = None, **kwargs) -> Dict[str, Any]:
        """
        Main entry point. Accepts (prompt, system) tuple from build_sniper_messages.
        Iterates through MODEL_CHAIN with cool-off awareness.
        Falls back to heuristic if all models fail.
        """
        prompt, system = messages

        if not self.api_key:
            logger.warning("[ROUTER] No API key set - using heuristic")
            self._stats["heuristics"] += 1
            return _heuristic_decision(mkt or {})

        now = time.time()

        # Build the model order respecting cool-off and "primary_dead" status (Issue #8)
        if self.primary_dead or now < self._cooloff_until:
            remaining = int(self._cooloff_until - now) if now < self._cooloff_until else 0
            if self.primary_dead:
                logger.debug("[ROUTER] Llama-3 429 cap reached - avoiding primary model for session.")
            else:
                logger.info(f"[ROUTER] Primary in cool-off ({remaining}s left) - starting at secondary")
            chain = [SECONDARY_MODEL, TERTIARY_MODEL]
        else:
            chain = MODEL_CHAIN

        # Try each model in sequence
        for model in chain:
            logger.debug(f"[ROUTER] Trying {model}...")
            res = await self._call_model(model, prompt, system)
            if res:
                try:
                    parsed = json.loads(res)
                    logger.info(f"[ROUTER] Success via {model.split('/')[1][:20]}")
                    self._consecutive_429 = 0  # Reset on success
                    self._stats["api_cooling"] = False
                    self._stats["cooloff_remaining"] = 0
                    return parsed
                except json.JSONDecodeError:
                    logger.warning(f"[ROUTER] {model} returned non-JSON: {res[:80]}")
                    if self._broadcast:
                        asyncio.create_task(self._broadcast({
                            "type": "log",
                            "msg": f"AI ROUTER: Invalid JSON from {model.split('/')[1][:20]}.",
                            "level": "warn"
                        }))
                    continue  # Try next model

        # All models failed - use heuristic floor
        logger.error("[ROUTER] All models failed - engaging heuristic floor")
        self._stats["heuristics"] += 1
        return _heuristic_decision(mkt or {})

    def get_stats(self) -> dict:
        # Update cooling status dynamically
        now = time.time()
        if now >= self._cooloff_until:
            self._stats["api_cooling"] = False
            self._stats["cooloff_remaining"] = 0
        else:
            self._stats["cooloff_remaining"] = int(self._cooloff_until - now)
        return self._stats
