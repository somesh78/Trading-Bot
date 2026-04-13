'use client'

import React from 'react'
import { StatsBar } from '@/components/dashboard/StatsBar'
import { SidebarControls } from '@/components/dashboard/SidebarControls'
import { MarketAnalysis } from '@/components/dashboard/MarketAnalysis'
import { Terminal } from '@/components/dashboard/Terminal'
import { MissionCard } from '@/components/dashboard/MissionCard'
import { useTradeStore } from '@/store/useTradeStore'
import { Shield, Database, Route, Radar, Zap, BrainCircuit } from 'lucide-react'
import { cn } from '@/lib/utils'
import { useTradeSocket } from '@/hooks/useTradeSocket'

export default function WarRoom() {
  const store = useTradeStore()
  const { send } = useTradeSocket()
  
  const handleStart = (config: any) => {
    // Inject hardware profile based on environment
    const profile = process.env.NODE_ENV === 'production' ? 'SERVER' : 'LAPTOP';
    const enrichedConfig = { 
      ...config, 
      profile,
      delay: profile === 'SERVER' ? Math.max(1, config.delay / 2) : config.delay 
    };
    
    send('start', { config: enrichedConfig })
  }

  const handleStop = () => {
    send('stop', {})
    store.setEngineStatus('IDLE')
  }

  return (
    <main className="min-h-screen p-4 xl:p-6 flex flex-col gap-4 bg-[#0a0a0c]">
      {/* ── HEADER ── */}
      <header className="glass px-6 py-4 flex items-center justify-between relative overflow-hidden">
        {store.isPaper && (
          <div className="absolute top-0 left-0 right-0 h-0.5 bg-gradient-to-r from-transparent via-amber/50 to-transparent" />
        )}
        
        <div className="flex items-center gap-4">
          <div className="w-10 h-10 bg-gradient-to-br from-electric to-neon-green rounded-xl grid place-items-center font-display font-black text-xs text-background shadow-[0_0_20px_rgba(0,255,135,0.2)]">
            SQ
          </div>
          <div>
            <div className="flex items-center gap-2">
              <h1 className="font-display font-black text-lg tracking-wider text-white">SENTINEL QUANT</h1>
              {store.isPaper && (
                <span className="px-2 py-0.5 rounded bg-amber/10 border border-amber/30 text-amber text-[8px] font-bold tracking-widest uppercase">
                  Paper Mode
                </span>
              )}
            </div>
            <p className="text-[9px] text-white/40 uppercase tracking-[0.2em] font-medium">Global Predator Swarm · 2026</p>
          </div>
        </div>

        <div className="hidden lg:flex gap-3">
          <Badge 
            active={store.engineStatus === 'RUNNING' || store.engineStatus === 'DRAINING'} 
            type={
              store.connectionStatus === 'OFFLINE' ? 'danger' : 
              store.engineStatus === 'DRAINING' ? 'warning' : 'default'
            }
            label={`ENGINE: ${store.engineStatus}`} 
          />
          <Badge active label={`REGIME: ${store.regime}`} pulse={store.regime === 'BULL'} />
          <Badge active type="warning" label={`EXCHANGE: ${store.exchange}`} />
          <Badge active type={store.newsThreat === 'LOW' ? 'default' : 'danger'} label={`NEWS: ${store.newsThreat}`} />
          <Badge active type="purple" label={`VIX: ${store.vixRel.toFixed(2)}×`} />
        </div>
      </header>

      {/* ── STATS ── */}
      <StatsBar />

      <div className="grid grid-cols-1 lg:grid-cols-[320px_1fr_340px] gap-4 flex-1">
        {/* ── LEFT: CONTROLS ── */}
        <SidebarControls onStart={handleStart} onStop={handleStop} />

        {/* ── CENTER: FEED ── */}
        <div className="flex flex-col gap-4 min-h-0">
          <MarketAnalysis />
          <Terminal />
        </div>

        {/* ── RIGHT: SNIPER & MEMORY ── */}
        <div className="flex flex-col gap-4">
          <div className="glass p-5 space-y-4">
             <div className="font-display text-[10px] tracking-[0.15em] text-white/50 uppercase flex items-center justify-between">
              <div className="flex items-center gap-2">
                <Shield size={14} className="text-neon-green" /> Sniper Signal
              </div>
              <span className="text-[8px] text-white/20 font-mono">
                {store.signal?._model_used || 'IDLE'}
              </span>
            </div>
            
            <div className={cn(
               "font-display text-4xl font-black text-center py-4 tracking-widest transition-all duration-500",
               store.signal?.action === 'BUY' ? "text-neon-green drop-shadow-[0_0_20px_rgba(0,255,135,0.4)]" :
               store.signal?.action === 'SELL' ? "text-crimson drop-shadow-[0_0_20px_rgba(255,45,85,0.4)]" : "text-white/10"
            )}>
              {store.signal?.action || 'STANDBY'}
            </div>

            <div className="grid grid-cols-2 gap-2">
               <div className="bg-white/[0.03] p-2 rounded-lg border border-white/5">
                  <div className="text-[8px] text-white/40 mb-1 leading-none">CONFIDENCE</div>
                  <div className="font-mono text-sm">{store.signal?.confidence || '--'}%</div>
               </div>
               <div className="bg-white/[0.03] p-2 rounded-lg border border-white/5">
                  <div className="text-[8px] text-white/40 mb-1 leading-none">RISK/REWARD</div>
                  <div className="font-mono text-sm">{store.signal?.risk_reward || '--'}:1</div>
               </div>
            </div>

            {/* AI REASONING LOG */}
            <div className="pt-2 border-t border-white/5">
              <div className="text-[8px] text-white/30 uppercase tracking-widest mb-2 flex items-center gap-1.5">
                <BrainCircuit size={10} className="text-electric" /> AI Reasoning
              </div>
              <div className="bg-white/[0.02] p-2.5 rounded border border-white/5 min-h-[60px] max-h-[100px] overflow-y-auto custom-scrollbar">
                {store.signal?.reasoning_steps?.length > 0 ? (
                  <ul className="space-y-1.5">
                    {store.signal.reasoning_steps.map((step: string, i: number) => (
                      <li key={i} className="text-[10px] text-white/60 leading-relaxed list-none pl-3 relative">
                        <span className="absolute left-0 top-1.5 w-1 h-1 rounded-full bg-electric/40" />
                        {step}
                      </li>
                    ))}
                  </ul>
                ) : (
                  <p className="text-[10px] text-white/20 italic">Awaiting AI signal context...</p>
                )}
              </div>
            </div>
          </div>

          <div className="glass p-5 flex-1">
             <div className="font-display text-[10px] tracking-[0.15em] text-white/50 uppercase flex items-center gap-2 mb-4">
              <Database size={14} className="text-purple" /> Analyst Memory
            </div>
            <div className="space-y-4">
               <div className="grid grid-cols-2 gap-2">
                  <div className="bg-white/[0.03] p-2 rounded-lg border border-white/5">
                     <div className="text-[8px] text-white/40 mb-1">MEMORIES</div>
                     <div className="font-mono text-sm">{store.memory?.total_memories || 0}</div>
                  </div>
                  <div className="bg-white/[0.03] p-2 rounded-lg border border-white/5">
                     <div className="text-[8px] text-white/40 mb-1">CIRCUIT BREAKER</div>
                     <div className="font-mono text-sm text-neon-green">NORMAL</div>
                  </div>
               </div>
            </div>
          </div>
        </div>
      </div>

      {/* ── MISSIONS BAR ── */}
      <div className="glass p-5 space-y-4">
        <div className="font-display text-[10px] tracking-[0.15em] text-white/50 uppercase flex items-center gap-2">
          <Shield size={14} /> Active Missions <span className="text-white/20">({store.missions.length})</span>
        </div>
        <div className="flex gap-4 overflow-x-auto pb-2 custom-scrollbar">
          {store.missions.length === 0 ? (
            <div className="w-full text-center py-6 text-white/10 font-mono text-xs tracking-widest">
              Guardian is on standby. No active missions.
            </div>
          ) : (
            store.missions.map(m => <MissionCard key={m.id} mission={m} />)
          )}
        </div>
      </div>

      {/* ── FOOTER STATS ── */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        <div className="glass p-4">
          <div className="font-display text-[9px] tracking-[0.1em] text-white/40 uppercase flex items-center gap-2 mb-3">
            <Route size={12} /> AI Router Stats
          </div>
          <div className="grid grid-cols-2 gap-4">
             <div className="bg-white/[0.02] p-2 rounded-lg border border-white/5">
                <div className="text-[8px] text-white/40 mb-1">CALLS</div>
                <div className="font-mono text-sm leading-none">{store.routerStats?.calls || 0}</div>
             </div>
             <div className="bg-white/[0.02] p-2 rounded-lg border border-white/5">
                <div className="text-[8px] text-white/40 mb-1">429 ERRORS</div>
                <div className={cn(
                  "font-mono text-sm leading-none",
                  (store.routerStats?.["429s"] || 0) > 0 ? "text-crimson" : "text-neon-green"
                )}>
                  {store.routerStats?.["429s"] || 0}
                </div>
             </div>
          </div>
        </div>

        <div className="glass p-4">
          <div className="font-display text-[9px] tracking-[0.1em] text-white/40 uppercase flex items-center gap-2 mb-3">
             <Radar size={12} /> Recent Patterns
          </div>
          <div className="flex flex-wrap gap-1.5">
             <span className="px-2 py-0.5 rounded text-[8px] font-bold bg-neon-green/10 text-neon-green border border-neon-green/20">BULL_MOMENTUM</span>
             <span className="px-2 py-0.5 rounded text-[8px] font-bold bg-purple/10 text-purple border border-purple/20">MEAN_REVERSION</span>
          </div>
        </div>

        <div className="glass p-4">
          <div className="font-display text-[9px] tracking-[0.1em] text-white/40 uppercase flex items-center gap-2 mb-3">
             <Zap size={12} /> System Health
          </div>
          <div className="flex items-center gap-4">
             <HealthIndicator label="DB" active={store.connectionStatus !== 'OFFLINE'} />
             <HealthIndicator label="AI" active={store.connectionStatus !== 'OFFLINE' && (store.routerStats?.calls > 0)} />
             <HealthIndicator label="WS" active={store.connectionStatus !== 'OFFLINE' && store.engineStatus !== 'IDLE'} />
          </div>
        </div>
      </div>

    </main>
  )
}

const Badge = ({ label, active, pulse, type = 'default' }: any) => (
  <div className={cn(
    "px-3 py-1 rounded-full border text-[9px] font-bold tracking-widest flex items-center gap-2 uppercase transition-all",
    !active && type !== 'danger' ? "border-white/5 text-white/20 bg-white/[0.02]" :
    type === 'danger' ? "border-crimson/50 text-crimson bg-crimson/10" :
    type === 'warning' ? "border-amber/50 text-amber bg-amber/10" :
    type === 'purple' ? "border-purple/50 text-purple bg-purple/10" :
    "border-neon-green/50 text-neon-green bg-neon-green/10"
  )}>
    <div className={cn(
      "w-1.5 h-1.5 rounded-full shrink-0",
      !active && type !== 'danger' ? "bg-white/10" : 
      type === 'danger' ? "bg-crimson shadow-[0_0_8px_var(--crimson)]" :
      type === 'warning' ? "bg-amber" :
      type === 'purple' ? "bg-purple" : "bg-neon-green shadow-[0_0_8px_var(--neon-green)]",
      pulse && "animate-pulse"
    )} />
    {label}
  </div>
)

const HealthIndicator = ({ label, active }: any) => (
  <div className="flex items-center gap-1.5 grayscale-[0.5]">
    <div className={cn("w-1.5 h-1.5 rounded-full", active ? "bg-neon-green" : "bg-white/10")} />
    <span className="text-[10px] font-display font-bold text-white/30">{label}</span>
  </div>
)
