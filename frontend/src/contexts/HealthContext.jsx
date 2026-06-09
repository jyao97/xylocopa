import { createContext, useContext, useEffect, useRef, useState } from "react";
import { fetchHealth } from "../lib/api";

const HealthContext = createContext(null);

/**
 * Single source of truth for system health status across the app.
 *
 * Previously each consumer (PageHeader, AgentChatPage) called the
 * useHealthStatus hook locally — every component mount re-initialized
 * health to null and re-fetched /api/health, causing the OK chip to
 * visibly flicker gray → green ~150-250ms after each chat-page open.
 *
 * Lifted to a top-level provider here so the hook returns the already-
 * resolved value from app boot. Polling cadence preserved: 3s when
 * unhealthy, 15s when healthy.
 */
export function HealthProvider({ children }) {
  const [health, setHealth] = useState(null);
  const intervalRef = useRef(null);
  const healthyRef = useRef(false);

  useEffect(() => {
    let cancelled = false;

    const schedule = (ms) => {
      if (intervalRef.current) clearInterval(intervalRef.current);
      intervalRef.current = setInterval(check, ms);
    };

    const check = async () => {
      try {
        const data = await fetchHealth();
        if (cancelled) return;
        setHealth(data);
        const ok = data?.status === "ok";
        if (ok !== healthyRef.current) {
          healthyRef.current = ok;
          schedule(ok ? 15000 : 3000);
        }
      } catch (err) {
        if (cancelled) return;
        console.warn("HealthProvider: health check failed:", err);
        setHealth({ status: "error", db: "unknown", claude_cli: "unknown" });
        if (healthyRef.current) {
          healthyRef.current = false;
          schedule(3000);
        }
      }
    };

    check();
    schedule(15000);
    return () => { cancelled = true; clearInterval(intervalRef.current); };
  }, []);

  return <HealthContext.Provider value={health}>{children}</HealthContext.Provider>;
}

export function useHealthContext() {
  return useContext(HealthContext);
}
