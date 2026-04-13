'use client'

import React, { useState } from 'react'
import { Settings, Zap, Shield, Brain, MousePointer2, Layers } from 'lucide-react'
import { useTradeStore } from '@/store/useTradeStore'
import { cn } from '@/lib/utils'
import { motion } from 'framer-motion'

interface SidebarControlsProps {
  onStart: (config: any) => void
  onStop: () => void
}

export const SidebarControls: React.FC<SidebarControlsProps> = ({ onStart, onStop }) => {
  const [config, setConfig] = useState({
    groq_key: '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',
    supabase_url: 'https://uejsdhpfrshphmnildme.supabase.co',
    supabase_key: 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InVlanNkaHBmcnNocGhtbmlsZG1lIiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc3NTkxNjEwNiwiZXhwIjoyMDkxNDkyMTA2fQ.KJ7n0q7MMu547LRBJNdWxLWjJdq5Muf3jNlgXZemftw',
    capital: 100000,
    risk: 0.03,
    min_conf: 70,
    regime_filter:  'all',
    primary_market: 'AUTO',
    live_data:      false,
    global_mode: false,
    max_trades: 200,
    delay: 6,
    target_pnl: 1000000,
    vats_k: 2.5,
    mtf_min_confluence: 0.67
  })

  const [toggles, setToggles] = useState({
    crash: true,
    filter: true,
    reasoning: true,
    auto: true,
    mtf: true
  })

  const engineStatus = useTradeStore(s => s.engineStatus)
  const isRunning = engineStatus === 'RUNNING'
  const isDraining = engineStatus === 'DRAINING'
  const connectionStatus = useTradeStore(s => s.connectionStatus)

  const handleStart = () => {
    // If keys are empty, the backend will attempt to load them from .env
    onStart({
      ...config,
      use_crash_guard: toggles.crash,
      use_multi_filter: toggles.filter,
      use_reasoning: toggles.reasoning,
      auto_execute: toggles.auto,
      use_multi_timeframe: toggles.mtf,
      primary_market: config.primary_market
    })
  }

  return (
    <div className="glass p-5 h-full flex flex-col gap-5 overflow-y-auto custom-scrollbar">
      <div className="font-display text-[10px] tracking-[0.15em] text-white/50 uppercase flex items-center justify-between gap-1">
        <div className="flex items-center gap-2">
          <Settings size={14} /> Strategy Config
        </div>
        <div className="flex items-center gap-1.5 px-2 py-1 rounded bg-white/5 border border-white/5">
          <div className={cn(
            "w-1.5 h-1.5 rounded-full animate-pulse",
            connectionStatus === 'ONLINE' ? "bg-neon-green shadow-[0_0_8px_rgba(0,255,135,0.4)]" : "bg-crimson shadow-[0_0_8px_rgba(255,45,85,0.4)]"
          )} />
          <span className={cn(
            "text-[9px] font-bold tracking-tighter",
            connectionStatus === 'ONLINE' ? "text-neon-green/90" : "text-crimson/90"
          )}>
            {connectionStatus}
          </span>
        </div>
      </div>

      <div className="space-y-4">
        <InputGroup label="OpenRouter API Key">
          <input 
            type="password" 
            placeholder="sk-or-v1-..." 
            className="w-full bg-white/5 border border-white/10 rounded-lg px-3 py-2 text-xs font-mono focus:border-electric outline-none transition-colors"
            value={config.groq_key}
            onChange={e => setConfig({...config, groq_key: e.target.value})}
          />
        </InputGroup>

        <div className="grid grid-cols-2 gap-2.5">
          <InputGroup label="Supabase URL">
            <input 
              type="text" 
              placeholder="https://..." 
              className="w-full bg-white/5 border border-white/10 rounded-lg px-3 py-2 text-xs font-mono focus:border-electric outline-none transition-colors"
              value={config.supabase_url}
              onChange={e => setConfig({...config, supabase_url: e.target.value})}
            />
          </InputGroup>
          <InputGroup label="Supabase Key">
            <input 
              type="password" 
              placeholder="eyJ..." 
              className="w-full bg-white/5 border border-white/10 rounded-lg px-3 py-2 text-xs font-mono focus:border-electric outline-none transition-colors"
              value={config.supabase_key}
              onChange={e => setConfig({...config, supabase_key: e.target.value})}
            />
          </InputGroup>
        </div>

        <div className="grid grid-cols-2 gap-2.5">
          <InputGroup label="Capital (₹)">
            <input 
              type="number" 
              value={config.capital === 0 ? '' : config.capital}
              onChange={e => {
                const val = e.target.value
                setConfig({...config, capital: val === '' ? 0 : parseFloat(val)})
              }}
              className="w-full bg-white/5 border border-white/10 rounded-lg px-3 py-2 text-xs font-mono focus:border-electric outline-none transition-colors"
            />
          </InputGroup>
          <InputGroup label="Risk / Trade">
            <select 
              value={config.risk}
              onChange={e => setConfig({...config, risk: parseFloat(e.target.value)})}
              className="w-full bg-white/5 border border-white/10 rounded-lg px-3 py-2 text-xs focus:border-electric outline-none transition-colors appearance-none"
            >
              <option value={0.02}>2% Safe</option>
              <option value={0.03}>3% Standard</option>
              <option value={0.05}>5% Aggressive</option>
            </select>
          </InputGroup>
        </div>

        <div className="grid grid-cols-2 gap-2.5">
          <InputGroup label="Min Confidence">
            <select 
              value={config.min_conf}
              onChange={e => setConfig({...config, min_conf: parseInt(e.target.value)})}
              className="w-full bg-white/5 border border-white/10 rounded-lg px-3 py-2 text-xs focus:border-electric outline-none transition-colors appearance-none"
            >
              <option value={70}>70%</option>
              <option value={75}>75%</option>
              <option value={80}>80%</option>
            </select>
          </InputGroup>
          <InputGroup label="Primary Market">
            <select 
              value={config.primary_market}
              onChange={e => setConfig({...config, primary_market: e.target.value})}
              className="w-full bg-white/5 border border-white/10 rounded-lg px-3 py-2 text-xs focus:border-electric outline-none transition-colors appearance-none text-neon-green"
            >
              <option value="AUTO">AUTO (Clock)</option>
              <option value="NSE">NSE (Indian)</option>
              <option value="NYSE">NYSE (US)</option>
              <option value="COMMODITY">COMMODITIES</option>
              <option value="CRYPTO">CRYPTO 24/7</option>
            </select>
          </InputGroup>
        </div>

        <InputGroup label="Regime Filter">
          <select 
            value={config.regime_filter}
            onChange={e => setConfig({...config, regime_filter: e.target.value})}
            className="w-full bg-white/5 border border-white/10 rounded-lg px-3 py-2 text-xs focus:border-electric outline-none transition-colors appearance-none"
          >
            <option value="bull_only">Bull Only</option>
            <option value="bull_sideways">Bull + Sideways</option>
            <option value="all">Unfiltered</option>
          </select>
        </InputGroup>

        <div className="pt-2 space-y-3 border-t border-white/10">
          <Toggle 
            label="Crash Guard" 
            icon={<Shield size={12} />} 
            checked={toggles.crash}
            onChange={() => setToggles({...toggles, crash: !toggles.crash})}
          />
          <Toggle 
            label="Multi-Filter" 
            icon={<Zap size={12} />} 
            checked={toggles.filter}
            onChange={() => setToggles({...toggles, filter: !toggles.filter})}
          />
          <Toggle 
            label="AI Reasoning" 
            icon={<Brain size={12} />} 
            checked={toggles.reasoning}
            onChange={() => setToggles({...toggles, reasoning: !toggles.reasoning})}
          />
          <Toggle 
            label="Auto-Execute" 
            icon={<MousePointer2 size={12} />} 
            checked={toggles.auto}
            onChange={() => setToggles({...toggles, auto: !toggles.auto})}
          />
          <Toggle 
            label="Multi-TF" 
            icon={<Layers size={12} />} 
            checked={toggles.mtf}
            onChange={() => setToggles({...toggles, mtf: !toggles.mtf})}
          />
        </div>

        <div className="flex gap-2.5 pt-4 mt-auto">
          <button 
            onClick={handleStart}
            disabled={isRunning || isDraining}
            className={cn(
              "flex-1 py-3 rounded-lg font-display text-[10px] font-bold tracking-widest transition-all",
              (isRunning || isDraining)
                ? "bg-white/5 text-white/30 cursor-not-allowed" 
                : "bg-gradient-to-br from-electric/20 to-neon-green/10 border border-neon-green/30 text-neon-green hover:bg-neon-green/20 hover:shadow-[0_0_20px_rgba(0,255,135,0.2)]"
            )}
          >
            {isDraining ? 'DRAINING...' : '▶ START ENGINE'}
          </button>
          <button 
            onClick={onStop}
            disabled={!isRunning}
            className={cn(
              "flex-1 py-3 rounded-lg font-display text-[10px] font-bold tracking-widest transition-all border",
              (!isRunning)
                ? "bg-white/5 text-white/30 cursor-not-allowed border-white/10"
                : "bg-crimson/10 border-crimson/30 text-crimson hover:bg-crimson/20"
            )}
          >
            {isRunning ? '■ STOP' : '■ HALT'}
          </button>
        </div>
      </div>
    </div>
  )
}

const InputGroup = ({ label, children }: { label: string, children: React.ReactNode }) => (
  <div className="flex flex-col gap-1.5">
    <label className="text-[10px] uppercase tracking-wider text-white/40">{label}</label>
    {children}
  </div>
)

const Toggle = ({ label, icon, checked, onChange }: { label: string, icon: React.ReactNode, checked: boolean, onChange: () => void }) => (
  <div className="flex items-center justify-between group cursor-pointer" onClick={onChange}>
    <div className="flex items-center gap-2 text-[11px] text-white/50 group-hover:text-white/80 transition-colors">
      <span className="text-electric">{icon}</span>
      {label}
    </div>
    <div className={cn(
      "w-8 h-4.5 rounded-full relative transition-colors duration-300",
      checked ? "bg-neon-green/20" : "bg-white/10"
    )}>
      <motion.div 
        animate={{ x: checked ? 14 : 2 }}
        initial={false}
        className={cn(
          "w-3.5 h-3.5 rounded-full absolute top-0.5",
          checked ? "bg-neon-green" : "bg-white/30"
        )}
      />
    </div>
  </div>
)
