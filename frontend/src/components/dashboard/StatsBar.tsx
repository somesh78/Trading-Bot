'use client'

import React from 'react'
import { useTradeStore } from '@/store/useTradeStore'
import { cn } from '@/lib/utils'

export const StatsBar = () => {
  const stats = useTradeStore((state) => state.stats)
  
  const equity = stats.capital + stats.unrealized
  const roiNum = (stats.pnl / Math.max(stats.capital - stats.pnl, 1)) * 100
  const roi = roiNum.toFixed(2)
  const total = stats.wins + stats.losses
  const wr = total > 0 ? Math.round((stats.wins / total) * 100) : null

  return (
    <div className="grid grid-cols-2 lg:grid-cols-6 gap-2.5">
      <StatCell 
        label="Account Equity" 
        value={`₹${equity.toFixed(2)}`} 
        sub={`Wallet: ₹${stats.capital.toFixed(2)}`}
      />
      <StatCell 
        label="Floating P&L" 
        value={(stats.unrealized >= 0 ? '+' : '') + `₹${stats.unrealized.toFixed(2)}`} 
        sub={`${stats.activeMissions} missions`}
        variant={stats.unrealized >= 0 ? 'success' : 'danger'}
      />
      <StatCell 
        label="Realized Profit" 
        value={(stats.pnl >= 0 ? '+' : '') + `₹${stats.pnl.toFixed(2)}`} 
        sub={`${roi}% Overall ROI`}
        variant={stats.pnl >= 0 ? 'success' : 'danger'}
      />
      <StatCell 
        label="Deployed" 
        value={`₹${stats.reserved.toFixed(0)}`} 
        sub={`Idle: ₹${stats.available.toFixed(0)}`}
        variant="warning"
      />
      <StatCell 
        label="Performance" 
        value={wr !== null ? `${wr}% WR` : '—'} 
        sub={`${stats.wins}W / ${stats.losses}L`}
      />
      <StatCell 
        label="Risk Pulse" 
        value={stats.crashScore.toString()} 
        sub={stats.crashScore >= 70 ? 'CRITICAL' : stats.crashScore >= 40 ? 'ELEVATED' : 'LOW RISK'}
        variant={stats.crashScore >= 70 ? 'danger' : stats.crashScore >= 40 ? 'warning' : 'success'}
      />
    </div>
  )
}

interface StatCellProps {
  label: string
  value: string
  sub: string
  variant?: 'default' | 'success' | 'danger' | 'warning'
}

const StatCell: React.FC<StatCellProps> = ({ label, value, sub, variant = 'default' }) => {
  return (
    <div className={cn(
      "glass p-4 relative overflow-hidden transition-all duration-300",
      variant === 'success' && "after:absolute after:inset-0 after:bg-neon-green/5",
      variant === 'danger' && "after:absolute after:inset-0 after:bg-crimson/5",
      variant === 'warning' && "after:absolute after:inset-0 after:bg-amber/5"
    )}>
      <div className="text-[10px] tracking-wider text-white/50 uppercase">{label}</div>
      <div className={cn(
        "font-mono text-xl font-bold mt-1 tracking-tight",
        variant === 'success' && "text-neon-green",
        variant === 'danger' && "text-crimson",
        variant === 'warning' && "text-amber"
      )}>
        {value}
      </div>
      <div className="text-[11px] text-white/40 mt-1">{sub}</div>
    </div>
  )
}
