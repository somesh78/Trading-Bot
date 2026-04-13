'use client'

import React from 'react'
import { Telescope, TrendingUp, TrendingDown, Activity, BarChart3, Waves, Zap } from 'lucide-react'
import { useTradeStore } from '@/store/useTradeStore'
import { cn } from '@/lib/utils'

export const MarketAnalysis = () => {
  const mkt = useTradeStore(s => s.scoutTarget)

  if (!mkt) {
    return (
      <div className="glass p-5 flex flex-col items-center justify-center text-center gap-3">
        <Telescope size={32} className="text-white/10" />
        <div className="font-mono text-[10px] text-white/30 tracking-wider">
          Waiting for first Scout cycle...
        </div>
      </div>
    )
  }

  const price = Number(mkt.price || 0)
  const chg = Number(mkt.change || 0)

  return (
    <div className="glass p-5 space-y-5">
      <div className="font-display text-[10px] tracking-[0.15em] text-white/50 uppercase flex items-center gap-2">
        <Telescope size={14} /> Scout Intelligence
      </div>

      <div className="flex justify-between items-start">
        <div>
          <div className="font-display text-2xl font-black tracking-tight">{mkt.sym}</div>
          <div className="text-[11px] text-white/40 mt-1 uppercase tracking-wider">
            {mkt.sector} · {mkt.exchange} · {mkt.asset}
          </div>
        </div>
        <div className="text-right">
          <div className="font-mono text-2xl font-bold tracking-tighter">
            {price > 10 ? `₹${price.toFixed(2)}` : price.toFixed(4)}
          </div>
          <div className={cn(
            "text-xs font-mono font-bold flex items-center justify-end gap-1 mt-1",
            chg >= 0 ? "text-neon-green" : "text-crimson"
          )}>
            {chg >= 0 ? <TrendingUp size={12} /> : <TrendingDown size={12} />}
            {chg >= 0 ? '+' : ''}{chg.toFixed(2)}%
          </div>
        </div>
      </div>

      <div className="grid grid-cols-2 md:grid-cols-4 gap-2.5">
        <IndicatorCell label="RSI(14)" icon={<Waves size={10} />} value={mkt.rsi ?? '--'} />
        <IndicatorCell label="ADX" icon={<Activity size={10} />} value={mkt.adx ?? '--'} />
        <IndicatorCell label="MFI" icon={<BarChart3 size={10} />} value={mkt.mfi ?? '--'} />
        <IndicatorCell 
          label="Vol Ratio" 
          icon={<Zap size={10} />} 
          value={mkt.vol_avg > 0 ? (mkt.volume / mkt.vol_avg).toFixed(1) + '×' : '--'} 
        />
      </div>
    </div>
  )
}

const IndicatorCell = ({ label, icon, value }: { label: string, icon: React.ReactNode, value: string | number }) => (
  <div className="bg-white/[0.03] border border-white/5 rounded-xl p-3">
    <div className="text-[9px] text-white/40 flex items-center gap-1.5 uppercase font-semibold">
      {icon} {label}
    </div>
    <div className="font-mono text-sm font-bold mt-1.5 text-white/90">{value}</div>
  </div>
)
