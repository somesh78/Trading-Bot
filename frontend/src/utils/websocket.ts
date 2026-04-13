/**
 * SENTINEL QUANT — WebSocket Utilities
 * ====================================
 * Environment-agnostic URL discovery logic for 2026 Global Predator Swarm.
 */

export const getDynamicWsUrl = (): string => {
  // 1. Priority: Explicit Environment Variable
  const envUrl = process.env.NEXT_PUBLIC_WS_URL;
  if (envUrl && envUrl.trim() !== '') {
    return envUrl;
  }

  // 2. Fallback: Browser Location Sensing (Client-only)
  if (typeof window !== 'undefined') {
    const { hostname, protocol, port } = window.location;
    
    // Detect protocol: https -> wss, http -> ws
    const wsProtocol = protocol === 'https:' ? 'wss:' : 'ws:';
    
    // Detect host: if localhost, use backend port 8000. 
    // If production domain, assume proxy/load-balancer on 80/443.
    const isLocal = hostname === 'localhost' || hostname === '127.0.0.1';
    const wsPort = isLocal ? ':8000' : (port ? `:${port}` : '');
    
    return `${wsProtocol}//${hostname}${wsPort}/ws`;
  }

  // 3. SSR Fallback (Should not happen if hook is client-side)
  return 'ws://localhost:8000/ws';
};

/**
 * Returns the hardware profile based on NEXT environment.
 * Used to set default concurrency and scan rates.
 */
export const getSystemProfile = () => {
  const isProd = process.env.NODE_ENV === 'production';
  return isProd ? 'SERVER' : 'LAPTOP';
};
