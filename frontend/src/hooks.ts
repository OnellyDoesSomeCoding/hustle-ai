import { useEffect, useRef, useState } from "react";
import { api } from "./api";
import type { SystemInfo } from "./types";

/** Calls `fn` now and then every `ms` while `active` is true. */
export function usePolling(fn: () => void | Promise<void>, ms: number, active = true) {
  const saved = useRef(fn);
  saved.current = fn;
  useEffect(() => {
    if (!active) return;
    let stopped = false;
    let timer: number | undefined;
    const tick = async () => {
      try {
        await saved.current();
      } finally {
        if (!stopped) timer = window.setTimeout(tick, ms);
      }
    };
    tick();
    return () => {
      stopped = true;
      window.clearTimeout(timer);
    };
  }, [ms, active]);
}

export function useSystem() {
  const [system, setSystem] = useState<SystemInfo | null>(null);
  const [offline, setOffline] = useState(false);
  usePolling(async () => {
    try {
      setSystem(await api.system());
      setOffline(false);
    } catch {
      setOffline(true);
    }
  }, 15000);
  return { system, offline };
}
