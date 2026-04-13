'use client'

import React, { useEffect, useRef } from 'react'
import { Terminal as TerminalIcon, Shield, Target, Radar, Database, Zap, Cpu } from 'lucide-react'
import { useTradeStore } from '@/store/useTradeStore'
import { cn } from '@/lib/utils'

export const Terminal = () => {
  const terminalRef = useRef<HTMLDivElement>(null)
  const logs = useTradeStore(s => s.logs)

  useEffect(() => {
    if (terminalRef.current) {
      terminalRef.current.scrollTop = terminalRef.current.scrollHeight
    }
  }, [logs])

  return (
    <div className="glass p-4 flex flex-col h-[400px] min-h-0">
      <div className="font-display text-[10px] tracking-[0.15em] text-white/50 uppercase flex items-center gap-2 mb-4">
        <TerminalIcon size={14} /> Engine Terminal
      </div>

      <div 
        ref={terminalRef}
        className="flex-1 overflow-y-auto font-mono text-[11px] space-y-2 custom-scrollbar pr-2"
      >
        {logs.length === 0 ? (
          <LogEntry 
            time="00:00:00" 
            tag="SYS" 
            tagType="sys" 
            msg="SENTINEL QUANT Swarm Initialized. Waiting for engine start..." 
          />
        ) : (
          logs.map((log: any) => (
            <LogEntry 
              key={log.id}
              time={log.time} 
              tag={log.tag} 
              tagType={log.tagType} 
              msg={log.msg} 
            />
          ))
        )}
      </div>
    </div>
  )
}

const LogEntry = ({ time, tag, tagType, msg }: { time: string, tag: string, tagType: string, msg: string }) => {
  const tagColors = {
    sys: "bg-electric/10 text-electric border-electric/20",
    scout: "bg-electric/10 text-electric border-electric/20",
    sniper: "bg-neon-green/10 text-neon-green border-neon-green/20",
    guardian: "bg-amber/10 text-amber border-amber/20",
    analyst: "bg-purple/10 text-purple border-purple/20",
    circuit: "bg-crimson/10 text-crimson border-crimson/20",
  }

  const icons = {
    sys: <Cpu size={10} />,
    scout: <Radar size={10} />,
    sniper: <Target size={10} />,
    guardian: <Shield size={10} />,
    analyst: <Database size={10} />,
    circuit: <Zap size={10} />,
  }

  return (
    <div className="flex gap-3 leading-relaxed animate-in fade-in slide-in-from-left-2 duration-300">
      <span className="text-white/20 shrink-0">{time}</span>
      <div className="shrink-0">
        <span className={cn(
          "px-1.5 py-0.5 rounded border text-[9px] font-bold tracking-wider flex items-center gap-1 uppercase",
          tagColors[tagType as keyof typeof tagColors] || tagColors.sys
        )}>
          {icons[tagType as keyof typeof icons] || icons.sys}
          {tag}
        </span>
      </div>
      <span className="text-white/70 break-all">{msg}</span>
    </div>
  )
}
