'use client'

import React, { useEffect, useRef } from 'react'
import { createChart, ISeriesApi } from 'lightweight-charts'
import { motion } from 'framer-motion'
import { Shield, Zap } from 'lucide-react'
import { cn } from '@/lib/utils'

interface MissionCardProps {
  mission: any
}

export const MissionCard: React.FC<MissionCardProps> = ({ mission }) => {
  const chartContainerRef = useRef<HTMLDivElement>(null)
  const seriesRef = useRef<ISeriesApi<'Line'>>(null)
  const priceHistoryRef = useRef<any[]>([])

  useEffect(() => {
    if (!chartContainerRef.current) return

    const chart = createChart(chartContainerRef.current, {
      width: chartContainerRef.current.clientWidth,
      height: 40,
      layout: {
        background: { color: 'transparent' },
        textColor: 'transparent',
      },
      grid: {
        vertLines: { visible: false },
        horzLines: { visible: false },
      },
      crosshair: { mode: 0 },
      rightPriceScale: { visible: false },
      timeScale: { visible: false },
      handleScroll: false,
      handleScale: false,
    })

    const series = chart.addLineSeries({
      color: mission.action === 'BUY' ? '#00ff87' : '#ff2d55',
      lineWidth: 2,
      crosshairMarkerVisible: false,
      lastValueVisible: false,
      priceLineVisible: false,
    })

    seriesRef.current = series

    const handleResize = () => {
      chart.applyOptions({ width: chartContainerRef.current?.clientWidth })
    }

    window.addEventListener('resize', handleResize)

    return () => {
      window.removeEventListener('resize', handleResize)
      chart.remove()
    }
  }, [mission.action])

  useEffect(() => {
    if (seriesRef.current && mission.current_price) {
      const now = Math.floor(Date.now() / 1000)
      const lastPoint = priceHistoryRef.current[priceHistoryRef.current.length - 1]
      const point = { time: now, value: mission.current_price }
      
      if (lastPoint && lastPoint.time >= now) {
        lastPoint.value = mission.current_price
      } else {
        priceHistoryRef.current.push(point)
        if (priceHistoryRef.current.length > 60) priceHistoryRef.current.shift()
      }
      
      seriesRef.current.update(point as any)
    }
  }, [mission.current_price])

  const pnl = mission.unrealized_pnl ?? 0
  const entry = mission.entry_price || 1
  const tp = mission.take_profit || entry
  const curr = mission.current_price || entry
  const progressRaw = tp !== entry ? (curr - entry) / (tp - entry) : 0
  const progress = Math.max(0, Math.min(1, progressRaw))
  const lastVerdict = mission.guardian_verdicts?.length 
    ? mission.guardian_verdicts[mission.guardian_verdicts.length - 1] 
    : '🛡️ Monitoring...'

  return (
    <motion.div 
      initial={{ opacity: 0, x: 20 }}
      animate={{ opacity: 1, x: 0 }}
      className={cn(
        "min-w-[290px] max-w-[290px] glass-hi p-4 flex-shrink-0 relative overflow-hidden border-t-2",
        mission.action === 'BUY' ? "border-t-neon-green" : "border-t-crimson",
        mission.status === 'TRIMMED' && "border-t-amber",
        mission.status === 'BREAKEVEN' && "border-t-orange animate-pulse"
      )}
    >
      <div className="flex items-center justify-between mb-2">
        <div>
          <div className="font-display font-bold text-sm text-foreground">{mission.sym}</div>
          <div className="font-mono text-[10px] text-text-3 opacity-60">
            {mission.exchange || 'NSE'} · {mission.action || 'BUY'} · Q{mission.qty}
          </div>
        </div>
        <div className={cn(
          "w-2.5 h-2.5 rounded-full",
          mission.status === 'ACTIVE' ? "bg-neon-green animate-pulse-dot" : 
          mission.status === 'TRIMMED' ? "bg-amber" : "bg-orange"
        )} />
      </div>

      <div ref={chartContainerRef} className="h-10 mb-2 rounded overflow-hidden" />

      <div className="grid grid-cols-3 gap-1.5 mb-2.5">
        <div className="text-center">
          <div className="text-[9px] uppercase text-text-3 opacity-60">Entry</div>
          <div className="font-mono text-xs font-semibold">₹{entry.toFixed(2)}</div>
        </div>
        <div className="text-center">
          <div className="text-[9px] uppercase text-text-3 opacity-60">Current</div>
          <div className="font-mono text-xs font-semibold">₹{curr.toFixed(2)}</div>
        </div>
        <div className="text-center">
          <div className="text-[9px] uppercase text-text-3 opacity-60">P&L</div>
          <div className={cn(
            "font-mono text-xs font-bold",
            pnl >= 0 ? "text-neon-green" : "text-crimson"
          )}>
            {pnl >= 0 ? '+' : ''}₹{pnl.toFixed(2)}
          </div>
        </div>
      </div>

      <div className="mb-2.5">
        <div className="flex justify-between text-[9px] text-text-3 opacity-60 mb-1">
          <span>Entry</span>
          <span>TP ₹{tp.toFixed(2)}</span>
        </div>
        <div className="h-1 bg-white/5 rounded-full overflow-hidden">
          <motion.div 
            initial={{ width: 0 }}
            animate={{ width: `${progress * 100}%` }}
            className={cn(
              "h-full rounded-full",
              pnl >= 0 ? "bg-gradient-to-r from-neon-green/20 to-neon-green" : "bg-gradient-to-r from-crimson/20 to-crimson"
            )}
          />
        </div>
      </div>

      <div className="bg-orange/5 border border-orange/20 rounded-md py-1.5 px-2 flex justify-between items-center mb-2">
        <span className="text-[10px] font-semibold text-orange flex items-center gap-1">
          <Zap size={10} /> VATS Stop
        </span>
        <span className="font-mono text-[10px]">₹{(mission.trailing_stop || 0).toFixed(2)}</span>
      </div>

      <div className="text-[10px] text-text-3 opacity-60 border-t border-border pt-2 font-mono truncate">
        {lastVerdict}
      </div>
    </motion.div>
  )
}
