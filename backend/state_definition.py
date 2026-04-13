"""
SENTINEL QUANT — Supabase Persistent State Layer
=================================================
Two responsibilities:

1. SupabaseCheckpoint
   – Saves / restores active missions so the bot survives restarts.
   – Table: sentinel_missions  (id, data jsonb, updated_at)

2. VectorMemory  (pgvector)
   – Embeds each closed trade as a 10-dim feature vector.
   – Before new entries, Sniper queries: "Is this setup similar to a recent failure?"
   – Table: sentinel_memories  (id, embedding vector(10), data jsonb, created_at)

Both classes degrade gracefully to in-memory mode when Supabase credentials
are absent — the bot always runs even without a database.

Setup (one-time in Supabase SQL Editor):
-----------------------------------------
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS sentinel_missions (
    id TEXT PRIMARY KEY,
    data JSONB NOT NULL,
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS sentinel_memories (
    id SERIAL PRIMARY KEY,
    sym TEXT,
    pattern TEXT,
    win BOOLEAN,
    pnl FLOAT,
    embedding vector(10),
    data JSONB,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX ON sentinel_memories
    USING ivfflat (embedding vector_cosine_ops) WITH (lists = 10);
-----------------------------------------
"""

import json
import logging
import math
from datetime import datetime
from typing import Dict, List, Optional, Any

import httpx

logger = logging.getLogger("sentinel.state")


# ═══════════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════════

def _trade_to_vector(trade: dict) -> List[float]:
    """
    Convert a closed trade snapshot into a 10-dim feature vector for pgvector.
    All values are normalised to roughly [0, 1] or [-1, 1].
    """
    def _safe(v, default=0.0):
        try:
            return float(v)
        except Exception:
            return default

    regime_code = {"bull": 1.0, "sideways": 0.5, "bear": -1.0,
                   "recovery": 0.3, "crash": -2.0}.get(trade.get("regime", ""), 0.0)
    action_code = 1.0 if trade.get("action") == "BUY" else -1.0

    return [
        regime_code,                                          # 0. regime
        action_code,                                          # 1. direction
        _safe(trade.get("confidence", 75)) / 100.0,          # 2. confidence
        _safe(trade.get("rsi_at_entry", 50)) / 100.0,        # 3. RSI
        _safe(trade.get("adx_at_entry", 25)) / 50.0,         # 4. ADX
        _safe(trade.get("bb_pct_at_entry", 50)) / 100.0,     # 5. BB %
        min(_safe(trade.get("vol_ratio", 1.0)) / 3.0, 1.0),  # 6. volume ratio
        min(abs(_safe(trade.get("change_at_entry", 0))) / 5.0, 1.0),   # 7. price change
        1.0 if trade.get("win") else 0.0,                    # 8. outcome
        min(_safe(trade.get("checks", 1)) / 10.0, 1.0),      # 9. number of checks
    ]


def _cosine_similarity(a: List[float], b: List[float]) -> float:
    dot  = sum(x * y for x, y in zip(a, b))
    norm = math.sqrt(sum(x**2 for x in a)) * math.sqrt(sum(x**2 for x in b))
    return dot / norm if norm > 0 else 0.0


# ═══════════════════════════════════════════════════════════════
# SUPABASE CLIENT (thin httpx wrapper — no supabase-py dep)
# ═══════════════════════════════════════════════════════════════

class _SupabaseClient:
    """Minimal Supabase REST API client using httpx."""

    def __init__(self, url: str, key: str):
        self.base = url.rstrip("/") + "/rest/v1"
        self.headers = {
            "apikey":        key,
            "Authorization": f"Bearer {key}",
            "Content-Type":  "application/json",
            "Prefer":        "return=representation",
        }

    async def validate_table(self, table: str) -> bool:
        """Check if a table exists in the database."""
        try:
            async with httpx.AsyncClient(timeout=5.0) as c:
                r = await c.get(f"{self.base}/{table}", headers=self.headers, params={"limit": "1", "select": "id"})
                if r.status_code == 404:
                    return False
                r.raise_for_status()
                return True
        except Exception as e:
            logger.debug(f"[SUPABASE] validate_table({table}) failed: {e}")
            return False

    async def validate_connection(self) -> bool:
        """Check if we can connect to Supabase and if required tables exist."""
        try:
            m_ok = await self.validate_table("sentinel_missions")
            v_ok = await self.validate_table("sentinel_memories")
            
            if not m_ok:
                logger.warning("❌ [SUPABASE] Table 'sentinel_missions' NOT FOUND in database.")
            if not v_ok:
                logger.warning("❌ [SUPABASE] Table 'sentinel_memories' NOT FOUND in database.")
            
            if m_ok and v_ok:
                logger.info("✅ [SUPABASE] Connection and tables verified.")
                return True
            return False
        except Exception as e:
            logger.warning(f"⚠️ [SUPABASE] Connection check failed: {e}")
            return False

    async def upsert(self, table: str, data: dict) -> bool:
        try:
            async with httpx.AsyncClient(timeout=10.0) as c:
                r = await c.post(
                    f"{self.base}/{table}",
                    headers={**self.headers, "Prefer": "resolution=merge-duplicates,return=minimal"},
                    content=json.dumps(data),
                )
                r.raise_for_status()
                return True
        except Exception as e:
            logger.warning(f"[SUPABASE] upsert({table}) failed: {e}")
            return False

    async def select(self, table: str, eq: Optional[Dict] = None,
                     order: Optional[str] = None, limit: int = 100) -> List[dict]:
        params: dict = {"select": "*", "limit": str(limit)}
        if eq:
            for k, v in eq.items():
                params[k] = f"eq.{v}"
        if order:
            params["order"] = order
        try:
            async with httpx.AsyncClient(timeout=10.0) as c:
                r = await c.get(f"{self.base}/{table}",
                                headers={**self.headers, "Prefer": "return=representation"}, 
                                params=params)
                r.raise_for_status()
                return r.json()
        except Exception as e:
            logger.warning(f"[SUPABASE] select({table}) failed: {e}")
            return []

    async def delete(self, table: str, eq: Dict) -> bool:
        params = {k: f"eq.{v}" for k, v in eq.items()}
        try:
            async with httpx.AsyncClient(timeout=10.0) as c:
                r = await c.delete(f"{self.base}/{table}",
                                   headers=self.headers, params=params)
                r.raise_for_status()
                return True
        except Exception as e:
            logger.warning(f"[SUPABASE] delete({table}) failed: {e}")
            return False

    async def rpc(self, fn: str, params: dict) -> Any:
        try:
            async with httpx.AsyncClient(timeout=10.0) as c:
                r = await c.post(f"{self.base}/rpc/{fn}",
                                 headers=self.headers,
                                 content=json.dumps(params))
                r.raise_for_status()
                return r.json()
        except Exception as e:
            logger.warning(f"[SUPABASE] rpc({fn}) failed: {e}")
            return None


# ═══════════════════════════════════════════════════════════════
# SUPABASE CHECKPOINT — Mission Persistence
# ═══════════════════════════════════════════════════════════════

class SupabaseCheckpoint:
    def __init__(self, supabase_url: str = "", supabase_key: str = "", is_paper: bool = True):
        self.enabled = bool(supabase_url and supabase_key)
        self.is_paper = is_paper
        self._db = _SupabaseClient(supabase_url, supabase_key) if self.enabled else None
        self._local: Dict[str, dict] = {}
        
        # Schema separation for Paper vs Live
        self.table = "sentinel_missions_paper" if is_paper else "sentinel_missions"
        
        if self.enabled:
            logger.info(f"[CHECKPOINT] Using Supabase table: {self.table}")
        else:
            logger.info("[CHECKPOINT] Running in-memory (no Supabase credentials).")

    async def save_mission(self, mission_dict: dict) -> bool:
        self._local[mission_dict["id"]] = mission_dict
        if not self.enabled:
            return True
        payload = {
            "id":         mission_dict["id"],
            "data":       json.dumps(mission_dict),
            "updated_at": datetime.utcnow().isoformat(),
        }
        return await self._db.upsert(self.table, payload)

    async def delete_mission(self, mission_id: str) -> bool:
        self._local.pop(mission_id, None)
        if not self.enabled:
            return True
        return await self._db.delete(self.table, {"id": mission_id})

    async def restore_missions(self, portfolio) -> int:
        restored = 0
        if self.enabled:
            if await self._db.validate_table(self.table):
                rows = await self._db.select(self.table, order="updated_at.desc")
            else:
                rows = []
                self.enabled = False
        else:
            rows = [{"id": k, "data": json.dumps(v)} for k, v in self._local.items()]

        from agents import Mission, MissionStatus
        for row in rows:
            try:
                data = json.loads(row["data"]) if isinstance(row["data"], str) else row["data"]
                m = Mission(
                    id=data["id"], sym=data["sym"], action=data["action"],
                    entry_price=data["entry_price"], current_price=data.get("current_price", data["entry_price"]),
                    qty=data["qty"], stop_loss=data["stop_loss"],
                    take_profit=data["take_profit"], trailing_stop=data.get("trailing_stop", 0.0),
                    status=MissionStatus(data.get("status", "ACTIVE")),
                    regime_at_entry=data.get("regime_at_entry", "unknown"),
                    exchange=data.get("exchange", "NSE"),
                    asset_type=data.get("asset_type", "equity"),
                    atr_at_entry=data.get("atr_at_entry", 1.0),
                    confidence=data.get("confidence", 75),
                    unrealized_pnl=data.get("unrealized_pnl", 0.0),
                    peak_price=data.get("peak_price", data["entry_price"]),
                    peak_pnl=data.get("peak_pnl", 0.0),
                    check_count=data.get("check_count", 0),
                    vats_multiplier_k=data.get("vats_multiplier_k", 2.5),
                    created_at=data.get("created_at", ""),
                )
                reserved = m.entry_price * m.qty
                portfolio.open_mission(m, reserved)
                restored += 1
            except Exception as e:
                logger.warning(f"[CHECKPOINT] Failed to restore row: {e}")
        return restored

    def get_status(self) -> dict:
        return {
            "enabled":          self.enabled,
            "local_count":      len(self._local),
            "backend":          "supabase" if self.enabled else "in-memory",
        }


# ═══════════════════════════════════════════════════════════════
# VECTOR MEMORY — pgvector-backed similarity search
# ═══════════════════════════════════════════════════════════════

class VectorMemory:
    FAILURE_SIMILARITY_THRESHOLD = 0.88

    def __init__(self, supabase_url: str = "", supabase_key: str = "", is_paper: bool = True):
        self.enabled  = bool(supabase_url and supabase_key)
        self.is_paper = is_paper
        self._db      = _SupabaseClient(supabase_url, supabase_key) if self.enabled else None
        self._local: List[dict] = []
        self.table    = "sentinel_memories_paper" if is_paper else "sentinel_memories"
        self._total_count = 0 # Cached DB count

    async def restore_memories(self) -> int:
        """Load recent memories from Supabase into local cache."""
        if not self.enabled:
            return 0
        try:
            rows = await self._db.select(self.table, order="created_at.desc", limit=100)
            self._local = []
            for r in rows:
                try:
                    data = json.loads(r["data"]) if isinstance(r["data"], str) else r["data"]
                    # Embedding string '[0.1, ...]' to list
                    emb_str = r.get("embedding", "[]")
                    emb = json.loads(emb_str.replace("'", '"')) if isinstance(emb_str, str) else emb_str
                    self._local.append({
                        "vector": emb, 
                        "data": data, 
                        "win": r.get("win", False), 
                        "sym": r.get("sym", "?")
                    })
                except Exception:
                    continue
            self._total_count = len(self._local)
            logger.info(f"[MEMORY] Restored {self._total_count} memories from {self.table}")
            return self._total_count
        except Exception as e:
            logger.warning(f"[MEMORY] Restore failed: {e}")
            return 0

    async def store(self, mission_dict: dict, trade_memory_dict: dict) -> bool:
        feature = {
            "regime":              mission_dict.get("regime_at_entry", "unknown"),
            "action":              mission_dict.get("action", "BUY"),
            "confidence":          mission_dict.get("confidence", 75),
            "rsi_at_entry":        trade_memory_dict.get("rsi_at_entry", 50),
            "adx_at_entry":        trade_memory_dict.get("adx_at_entry", 25),
            "bb_pct_at_entry":     trade_memory_dict.get("bb_pct_at_entry", 50),
            "vol_ratio":           trade_memory_dict.get("vol_ratio", 1.0),
            "change_at_entry":     trade_memory_dict.get("change_at_entry", 0),
            "win":                 mission_dict.get("unrealized_pnl", 0) > 0,
            "checks":              mission_dict.get("check_count", 1),
        }
        vec  = _trade_to_vector(feature)
        data = {**mission_dict, **trade_memory_dict, "feature": feature}
        self._local.append({"vector": vec, "data": data, "win": feature["win"], "sym": mission_dict.get("sym", "?")})

        if self.enabled and self._db:
            if await self._db.validate_table(self.table):
                vec_str = "[" + ",".join(f"{v:.6f}" for v in vec) + "]"
                payload = {
                    "sym":       mission_dict.get("sym", "?"),
                    "pattern":   trade_memory_dict.get("pattern_tag", "unknown"),
                    "win":       feature["win"],
                    "pnl":       mission_dict.get("unrealized_pnl", 0),
                    "embedding": vec_str,
                    "data":      json.dumps(data),
                }
                return await self._db.upsert(self.table, payload)
            else:
                self.enabled = False
        return True

    async def find_similar_failures(self, current_mkt: dict, action: str, confidence: int, regime: str, n: int = 3) -> List[dict]:
        query_feature = {
            "regime":          regime, "action": action, "confidence": confidence,
            "rsi_at_entry":    current_mkt.get("rsi", 50),
            "adx_at_entry":    current_mkt.get("adx", 25),
            "bb_pct_at_entry": current_mkt.get("bb_pct", 50),
            "vol_ratio":       current_mkt.get("volume", 1) / max(current_mkt.get("vol_avg", 1), 1),
            "change_at_entry": current_mkt.get("change", 0),
            "win": False, "checks": 1,
        }
        query_vec = _trade_to_vector(query_feature)

        if self.enabled and self._db:
            if await self._db.validate_table(self.table):
                # We always query from the SAME mode table (paper vs live)
                results = await self._db.rpc("find_similar_failures", {
                    "query_vec": "[" + ",".join(f"{v:.6f}" for v in query_vec) + "]",
                    "table_name": self.table,
                    "top_n":     n,
                })
                if results: return results[:n]
            else:
                self.enabled = False

        failures = [r for r in self._local if not r["win"]]
        if not failures: return []
        scored = sorted([(r, _cosine_similarity(query_vec, r["vector"])) for r in failures], key=lambda x: x[1], reverse=True)
        return [{**r["data"], "_similarity": round(sim, 3)} for r, sim in scored[:n] if sim > 0.5]

    def is_similar_to_failure(self, similar_failures: List[dict]) -> bool:
        return any(f.get("_similarity", 0) >= self.FAILURE_SIMILARITY_THRESHOLD for f in similar_failures)

    def get_failure_warning(self, similar_failures: List[dict]) -> str:
        if not similar_failures: return ""
        top = similar_failures[0]
        return f"⚠️ DÉJÀ VU: {top.get('_similarity', 0):.0%} similar failure (PnL: ₹{top.get('pnl', 0):.2f})"

    def get_status(self) -> dict:
        return {
            "backend": "supabase" if self.enabled else "in-memory",
            "vectors": len(self._local),
            "threshold": self.FAILURE_SIMILARITY_THRESHOLD,
        }


# ═══════════════════════════════════════════════════════════════
# SQL SETUP (Run in Supabase SQL Editor)
# ═══════════════════════════════════════════════════════════════

SETUP_SQL = """
CREATE EXTENSION IF NOT EXISTS vector;

-- Live Tables
CREATE TABLE IF NOT EXISTS sentinel_missions (
    id TEXT PRIMARY KEY,
    data JSONB NOT NULL,
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS sentinel_memories (
    id SERIAL PRIMARY KEY,
    sym TEXT,
    pattern TEXT,
    win BOOLEAN,
    pnl FLOAT,
    embedding vector(10),
    data JSONB,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Paper Tables
CREATE TABLE IF NOT EXISTS sentinel_missions_paper (
    id TEXT PRIMARY KEY,
    data JSONB NOT NULL,
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS sentinel_memories_paper (
    id SERIAL PRIMARY KEY,
    sym TEXT,
    pattern TEXT,
    win BOOLEAN,
    pnl FLOAT,
    embedding vector(10),
    data JSONB,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS mem_embedding_idx ON sentinel_memories USING ivfflat (embedding vector_cosine_ops) WITH (lists = 10);
CREATE INDEX IF NOT EXISTS mem_paper_embedding_idx ON sentinel_memories_paper USING ivfflat (embedding vector_cosine_ops) WITH (lists = 10);

-- RPC Function for Similarity Search
CREATE OR REPLACE FUNCTION find_similar_failures(query_vec vector(10), table_name text DEFAULT 'sentinel_memories', top_n int DEFAULT 3)
RETURNS TABLE (sym text, pattern text, pnl float, win boolean, data jsonb, similarity float)
LANGUAGE plpgsql AS $$
BEGIN
    RETURN QUERY EXECUTE format('
        SELECT sym, pattern, pnl, win, data, 1 - (embedding <=> $1) AS similarity
        FROM %I WHERE win = false
        ORDER BY embedding <=> $1 LIMIT $2', table_name)
    USING query_vec, top_n;
END;
$$;
"""

def print_setup_sql():
    print("\n" + "="*50 + "\nSUPABASE SETUP SQL:\n" + "="*50 + SETUP_SQL + "\n" + "="*50)
