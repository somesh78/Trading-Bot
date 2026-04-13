import { create } from 'zustand'

export interface Trade {
  id: string
  sym: string
  action: 'BUY' | 'SELL'
  entry_price: number
  current_price: number
  qty: number
  pnl: number
  tp?: number
  sl?: number
}

interface TradeStats {
  capital: number
  pnl: number
  unrealized: number
  available: number
  reserved: number
  wins: number
  losses: number
  maxDd: number
  crashScore: number
  trades: number
  activeMissions: number
}

interface TradeState {
  engineStatus: 'IDLE' | 'RUNNING' | 'DRAINING' | 'HALTING' | 'ERROR'
  connectionStatus: 'ONLINE' | 'OFFLINE'
  stats: TradeStats
  missions: any[]
  logs: any[]
  signal: any
  regime: string
  vixRel: number
  newsThreat: string
  scoutTarget: any
  memory: any
  isPaper: boolean
  routerStats: any
  exchange: string
  
  setEngineStatus: (status: any) => void
  setConnectionStatus: (status: 'ONLINE' | 'OFFLINE') => void
  addLog: (log: any) => void
  updateFromSocket: (data: any) => void
  reset: () => void
}

const initialStats: TradeStats = {
  capital: 500,
  pnl: 0,
  unrealized: 0,
  available: 500,
  reserved: 0,
  wins: 0,
  losses: 0,
  maxDd: 0,
  crashScore: 0,
  trades: 0,
  activeMissions: 0,
}

export const useTradeStore = create<TradeState>((set) => ({
  engineStatus: 'IDLE',
  connectionStatus: 'OFFLINE',
  stats: initialStats,
  missions: [],
  logs: [],
  signal: null,
  regime: '--',
  vixRel: 1.0,
  newsThreat: 'LOW',
  scoutTarget: null,
  memory: {},
  isPaper: true,
  routerStats: { calls: 0, successes: 0, failures: 0, heuristics: 0, "429s": 0 },
  exchange: 'NSE',

  setEngineStatus: (status) => set({ engineStatus: status }),
  setConnectionStatus: (status) => set({ connectionStatus: status }),
  
  addLog: (log) => set((state) => ({ 
    logs: [...state.logs, log].slice(-100)
  })),

  reset: () => set({
     stats: initialStats,
     missions: [],
     logs: [],
     signal: null,
     regime: '--',
     scoutTarget: null
  }),

  updateFromSocket: (data: any) => {
    switch (data.type) {
        case 'init':
          if (data.state) {
            const s = data.state;
            set((state) => ({ 
              engineStatus: data.is_running ? 'RUNNING' : 'IDLE',
              stats: { 
                ...state.stats, 
                capital: Number(s.capital ?? state.stats.capital),
                pnl: Number(s.pnl ?? state.stats.pnl),
                available: Number(s.available ?? state.stats.available),
                unrealized: Number(s.unrealized ?? state.stats.unrealized),
                reserved: Number(s.reserved ?? state.stats.reserved),
                wins: Number(s.wins ?? state.stats.wins),
                losses: Number(s.losses ?? state.stats.losses),
                crashScore: Number(s.crashScore ?? state.stats.crashScore),
                maxDd: Number(s.maxDd ?? state.stats.maxDd),
                trades: Number(s.trades ?? state.stats.trades),
                activeMissions: Number(s.activeMissions ?? s.missions?.length ?? state.stats.activeMissions)
              },
              regime: s.regime ?? state.regime,
              scoutTarget: s.market ?? state.scoutTarget,
              newsThreat: s.newsThreat ?? state.newsThreat,
              missions: s.missions ?? state.missions,
              isPaper: s.isPaper ?? state.isPaper,
              routerStats: s.routerStats || state.routerStats,
              exchange: s.exchange ?? state.exchange,
              vixRel: s.vixRel != null ? Number(s.vixRel) : state.vixRel
            }))
          }
          break;
        
        case 'state_update':
          const su = data.state;
          if (!su) return;
          console.log("[WS] State Update:", su);
          set((state) => ({ 
            missions: su.missions || state.missions,
            memory: su.memory || state.memory,
            newsThreat: su.newsThreat || state.newsThreat,
            scoutTarget: su.market || state.scoutTarget,
            regime: su.regime || state.regime,
            isPaper: su.isPaper ?? state.isPaper,
            routerStats: su.routerStats || state.routerStats,
            exchange: su.exchange ?? state.exchange,
            vixRel: su.vixRel != null ? Number(su.vixRel) : state.vixRel,
            stats: { 
              ...state.stats, 
              capital: Number(su.capital ?? state.stats.capital),
              pnl: Number(su.pnl ?? state.stats.pnl),
              available: Number(su.available ?? state.stats.available),
              unrealized: Number(su.unrealized ?? state.stats.unrealized),
              reserved: Number(su.reserved ?? state.stats.reserved),
              wins: Number(su.wins ?? state.stats.wins),
              losses: Number(su.losses ?? state.stats.losses),
              crashScore: Number(su.crashScore ?? state.stats.crashScore),
              maxDd: Number(su.maxDd ?? state.stats.maxDd),
              trades: Number(su.trades ?? state.stats.trades),
              activeMissions: Number(su.activeMissions ?? su.missions?.length ?? state.stats.activeMissions)
            } 
          }))
          break;

        case 'market_update':
          set({ scoutTarget: data.market })
          if (data.regime) {
            set({ regime: data.regime.regime })
          }
          break;

        case 'signal':
          console.log("[WS] Signal Update:", data.signal);
          set({ signal: data.signal })
          break;

        case 'log':
          set((state) => ({ 
            logs: [...state.logs, {
              id: Math.random().toString(36).substr(2, 9),
              tagType: data.level || 'sys',
              tag: (data.level || 'sys').toUpperCase(),
              msg: data.msg,
              time: data.time || new Date().toLocaleTimeString()
            }].slice(-100)  // keep last 100 entries (newest at bottom)
          }))
          break;

        case 'status':
          set({ engineStatus: data.status.toUpperCase() as any })
          break;
          
        case 'router_update':
          set({ routerStats: data.stats })
          break;

        case 'error':
          console.error("[WS] Backend Error:", data.msg);
          set((state) => ({ 
            engineStatus: 'IDLE',
            logs: [...state.logs, {
              id: Math.random().toString(36).substr(2, 9),
              tagType: 'err',
              tag: 'ERROR',
              msg: data.msg,
              time: new Date().toLocaleTimeString()
            }].slice(-100)  // keep last 100 entries (newest at bottom)
          }));
          break;
    }
  }
}))

if (typeof window !== 'undefined') {
  (window as any).useTradeStore = useTradeStore
}
