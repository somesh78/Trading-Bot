"""
SENTINEL QUANT — main.py  (v5 — Clean Orchestration Layer)
============================================================
FastAPI entry-point. All engine logic lives in graph_engine.py.
This file is ONLY responsible for:
  1. HTTP / WebSocket I/O
  2. Lifecycle management (start / stop)
  3. Health & setup endpoints
"""

import asyncio
import json
import logging
import os
from typing import List, Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from dotenv import load_dotenv

from graph_engine import GraphEngine
from state_definition import SETUP_SQL, print_setup_sql

# More robust .env loading (check both backend/ and parent)
for env_path in [".env", "backend/.env", "../backend/.env"]:
    load_dotenv(env_path)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger("sentinel.main")

# ── FastAPI app ─────────────────────────────────────────────────
app = FastAPI(
    title="Sentinel Quant Global API v5",
    version="5.0.0",
    description="24/7 Autonomous Multi-Asset Agentic Trading Swarm",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Global state ────────────────────────────────────────────────
_engine: Optional[GraphEngine] = None
_engine_task: Optional[asyncio.Task] = None
_connections: List[WebSocket] = []


# ── Config schema (from frontend SidebarControls) ───────────────
class Config(BaseModel):
    groq_key:            str   = os.getenv("GROQ_KEY") or os.getenv("OPENROUTER_KEY") or ""
    fmp_key:             str   = ""
    news_key:            str   = ""
    finnhub_key:         str   = os.getenv("FINNHUB_KEY", "")
    alphav_key:          str   = os.getenv("ALPHAV_KEY", "")
    supabase_url:        str   = os.getenv("SUPABASE_URL", "")
    supabase_key:        str   = os.getenv("SUPABASE_KEY", "")
    capital:             float = float(os.getenv("CAPITAL", 500.0))
    risk:                float = float(os.getenv("RISK_PCT", 3.0)) / 100
    max_trades:          int   = 999999
    env:                 str   = os.getenv("ENV", "paper")
    min_conf:            int   = 55
    primary_market:      str   = "AUTO"
    BEAR_SL_PCT:         float = float(os.getenv("BEAR_SL_PCT", 2.5))
    BEAR_TP_PCT:         float = float(os.getenv("BEAR_TP_PCT", 1.5))
    min_conviction:      float = 5.0
    bear_min_conds:      int   = int(os.getenv("BEAR_MIN_CONDS", 2))
    regime_filter:       str   = "all"
    delay:               int   = 6
    target_pnl:          float = 999999.0
    max_dd:              float = 0.50
    live_data:           bool  = False
    use_crash_guard:     bool  = True
    use_multi_filter:    bool  = True
    use_reasoning:       bool  = True
    auto_execute:        bool  = True
    use_multi_timeframe: bool  = True
    mtf_min_confluence:  float = 0.50
    global_mode:         bool  = False
    vats_k:              float = 2.5


# ── Broadcast helper ─────────────────────────────────────────────
async def _broadcast(msg: dict):
    dead = []
    for ws in _connections:
        try:
            await ws.send_json(msg)
        except Exception as e:
            logger.error(f"[WS] Broadcast failed for {ws}: {e}", exc_info=True)
            dead.append(ws)
    for ws in dead:
        if ws in _connections:
            _connections.remove(ws)


# ── WebSocket endpoint ───────────────────────────────────────────
@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    global _engine, _engine_task

    await ws.accept()
    _connections.append(ws)
    logger.info(f"[WS] Client connected — total: {len(_connections)}")

    # Send current state snapshot immediately
    await ws.send_json({
        "type": "init",
        "state": _engine._state_dict() if _engine else {
            "capital": 500, "pnl": 0, "unrealized": 0, "reserved": 0,
            "wins": 0, "losses": 0, "regime": "--", "crashScore": 0,
            "maxDd": 0, "trades": 0, "activeMissions": 0,
            "newsThreat": "LOW", "available": 500,
            "market": None,
        },
        "swarm": _engine.swarm.get_full_state() if (_engine and _engine.swarm) else {},
        "is_running": bool(_engine and _engine.is_running),
        "setup_sql": SETUP_SQL,
    })

    try:
        while True:
            raw = await ws.receive_text()
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                continue

            msg_type = data.get("type", "")

            # ── start ──────────────────────────────────────────
            if msg_type == "start":
                if _engine and _engine.is_running:
                    await ws.send_json({"type": "log", "msg": f"Engine already {_engine.status}.",
                                        "level": "warn"})
                    continue

                try:
                    cfg_raw = data.get("config", {})
                    cfg = Config(**cfg_raw)
                except Exception as e:
                    await ws.send_json({"type": "error",
                                        "msg": f"Invalid config: {e}"})
                    continue

                _engine = GraphEngine()
                _engine.configure(cfg.model_dump(), broadcast_fn=_broadcast)

                # Print Supabase SQL on first run if credentials supplied
                if cfg.supabase_url and cfg.supabase_key:
                    print_setup_sql()

                _engine_task = asyncio.create_task(_engine.run())
                await _broadcast({"type": "status", "status": "running"})
                logger.info("[MAIN] Engine started.")

            # ── stop ───────────────────────────────────────────
            elif msg_type == "stop":
                if _engine:
                    _engine.stop()
                    # If missions active, it enters DRAIN mode. Notify UI.
                    status = "draining" if _engine.status == "DRAINING" else "idle"
                    await _broadcast({"type": "status", "status": status})
                    logger.info(f"[MAIN] Engine stop requested (Mode: {status}).")

            # ── ping ───────────────────────────────────────────
            elif msg_type == "ping":
                await ws.send_json({"type": "pong"})

            # ── status ─────────────────────────────────────────
            elif msg_type == "get_status":
                await ws.send_json({
                    "type": "engine_status",
                    "status": _engine.get_status() if _engine else {"is_running": False},
                })

    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.error(f"[WS] Error: {e}")
    finally:
        if ws in _connections:
            _connections.remove(ws)
        logger.info(f"[WS] Client disconnected — remaining: {len(_connections)}")


# ── REST endpoints ───────────────────────────────────────────────

@app.get("/health")
def health():
    return {
        "status": "ok",
        "engine_running": bool(_engine and _engine.is_running),
        "ws_clients": len(_connections),
    }


@app.get("/setup-sql")
def get_setup_sql():
    """Returns the Supabase SQL to run once in the SQL Editor."""
    return {"sql": SETUP_SQL}


@app.get("/engine/status")
def engine_status():
    if not _engine:
        return {"is_running": False}
    return _engine.get_status()


@app.get("/engine/state")
def engine_state():
    if not _engine:
        return {}
    return _engine._state_dict()


@app.get("/engine/missions")
def engine_missions():
    if not _engine or not _engine.swarm:
        return {"missions": []}
    return {
        "missions": [m.to_dict() for m in _engine.swarm.portfolio.missions.values()]
    }


@app.get("/engine/memory")
def engine_memory():
    if not _engine or not _engine.vec_mem:
        return {}
    return _engine.vec_mem.get_status()


@app.get("/scout/sectors")
async def scout_sectors():
    """Returns current sector performance from Alpha Vantage."""
    if not _engine or not _engine.scout:
        return {"sectors": {}}
    sectors = await _engine.scout.get_sector_leaders()
    return {"sectors": sectors}


@app.get("/scout/scan")
async def scout_scan():
    """Returns a live GlobalScout scan result (non-blocking)."""
    if not _engine or not _engine.scout:
        return {"candidates": []}
    candidates, primary = await _engine.scout.scan(n_candidates=8)
    return {"candidates": candidates, "primary_exchange": primary}


@app.on_event("startup")
async def on_startup():
    global _engine, _engine_task
    logger.info("=" * 60)
    logger.info("  SENTINEL QUANT v5 — Global Predator Swarm")
    logger.info("  Backend API: http://localhost:8000")
    logger.info("  WebSocket:   ws://localhost:8000/ws")
    logger.info("  Health:      http://localhost:8000/health")
    logger.info("=" * 60)


@app.on_event("shutdown")
async def on_shutdown():
    global _engine, _engine_task
    if _engine:
        _engine.stop()
    if _engine_task:
        _engine_task.cancel()
    logger.info("[MAIN] Shutdown complete.")
