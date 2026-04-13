'use client'

import { useEffect, useRef, useState, useCallback } from 'react'
import { useTradeStore } from '@/store/useTradeStore'

// Resolve WS URL: env var > dynamic detection > localhost fallback
function resolveWsUrl(): string {
  if (typeof process !== 'undefined' && process.env.NEXT_PUBLIC_WS_URL) {
    return process.env.NEXT_PUBLIC_WS_URL
  }
  if (typeof window !== 'undefined') {
    const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
    const host  = window.location.hostname
    return `${proto}//${host}:8000/ws`
  }
  return 'ws://localhost:8000/ws'
}

export const useTradeSocket = () => {
  const socketRef = useRef<WebSocket | null>(null)
  const [retryCount, setRetryCount] = useState(0)
  const reconnectTimeoutRef = useRef<NodeJS.Timeout | null>(null)
  const heartbeatIntervalRef = useRef<NodeJS.Timeout | null>(null)
  
  const updateFromSocket = useTradeStore(s => s.updateFromSocket)
  const setConnectionStatus = useTradeStore(s => s.setConnectionStatus)

  const connect = () => {
    // Clear existing timeouts
    if (reconnectTimeoutRef.current) clearTimeout(reconnectTimeoutRef.current)
    if (heartbeatIntervalRef.current) clearInterval(heartbeatIntervalRef.current)

    const url = resolveWsUrl()
    console.log(`[WS] Connecting to ${url} (attempt ${retryCount + 1})`)

    try {
      const socket = new WebSocket(url)
      socketRef.current = socket

      socket.onopen = () => {
        console.log('[WS] Connected successfully')
        setConnectionStatus('ONLINE')
        setRetryCount(0)

        // 30s heartbeat ping
        heartbeatIntervalRef.current = setInterval(() => {
          if (socket.readyState === WebSocket.OPEN) {
            socket.send(JSON.stringify({ type: 'ping' }))
          }
        }, 30000)
      }

      socket.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data)
          if (data.type === 'pong') return
          updateFromSocket(data)
        } catch {
          // Silently ignore malformed frames
        }
      }

      // Log only event.type — avoids "[object Event]" console spam
      socket.onerror = (event) => {
        console.warn(`[WS] Socket error: type=${event.type}`)
        // onclose will fire next and handle reconnection
      }

      socket.onclose = (event) => {
        setConnectionStatus('OFFLINE')
        if (heartbeatIntervalRef.current) clearInterval(heartbeatIntervalRef.current)

        if (event.wasClean) {
          console.log(`[WS] Closed cleanly (code=${event.code})`)
          return
        }

        // Exponential backoff: 3s base, 30s max
        const delay = Math.min(3000 * Math.pow(1.5, retryCount), 30000)
        console.warn(`[WS] Disconnected. Retrying in ${Math.round(delay / 1000)}s...`)

        reconnectTimeoutRef.current = setTimeout(() => {
          setRetryCount(prev => prev + 1)
          connect()
        }, delay)
      }

    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err)
      console.error('[WS] Init error:', msg)
      setConnectionStatus('OFFLINE')
      reconnectTimeoutRef.current = setTimeout(() => {
        setRetryCount(prev => prev + 1)
        connect()
      }, 5000)
    }
  }


  useEffect(() => {
    connect()
    return () => {
      console.log('[WS] Cleaning up socket resources...')
      if (socketRef.current) {
        socketRef.current.close()
      }
      if (reconnectTimeoutRef.current) clearTimeout(reconnectTimeoutRef.current)
      if (heartbeatIntervalRef.current) clearInterval(heartbeatIntervalRef.current)
    }
  }, []) // Empty dependency array as we use getDynamicWsUrl internally

  const send = (type: string, payload: any = {}) => {
    if (socketRef.current?.readyState === WebSocket.OPEN) {
      socketRef.current.send(JSON.stringify({ type, ...payload }))
    } else {
      console.error('[WS] Cannot send message: Socket is not OPEN')
    }
  }

  return { send }
}
