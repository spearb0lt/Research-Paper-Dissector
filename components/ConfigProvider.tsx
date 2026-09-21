"use client";

/**
 * The app's one shared context: what this deployment can actually do.
 *
 * Fetched once and read everywhere, so a control that depends on a capability
 * the server does not have renders disabled with the server's own reason
 * rather than failing when it is clicked. That is the whole point of
 * runtime.py existing on the backend, and this is the half that makes it
 * visible.
 */

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";

import { getConfig } from "@/lib/api";
import { loadPrefs, savePrefs, type Prefs } from "@/lib/keys";
import type { AppConfig, Capability } from "@/lib/types";

interface ConfigValue {
  config: AppConfig | null;
  loading: boolean;
  error: string;
  reload: () => void;
  /** Whether a runtime capability is available here. */
  can: (id: string) => boolean;
  /** Why it is not, phrased for a user. */
  why: (id: string) => string;
  prefs: Prefs;
  setPrefs: (next: Prefs) => void;
}

const ConfigContext = createContext<ConfigValue | null>(null);

export function ConfigProvider({ children }: { children: ReactNode }) {
  const [config, setConfig] = useState<AppConfig | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [prefs, setPrefsState] = useState<Prefs>({});

  const load = useCallback(() => {
    setLoading(true);
    getConfig()
      .then((next) => {
        setConfig(next);
        setError("");
      })
      .catch((exception: unknown) => {
        setError(exception instanceof Error ? exception.message : String(exception));
      })
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    load();
    // localStorage is not available during the server render, so preferences
    // are read after mount. Reading them during render would make the markup
    // differ between server and client and trip hydration.
    setPrefsState(loadPrefs());
  }, [load]);

  const setPrefs = useCallback((next: Prefs) => {
    savePrefs(next);
    setPrefsState((current) => ({ ...current, ...next }));
  }, []);

  const capabilities = useMemo(() => {
    const map = new Map<string, Capability>();
    for (const capability of config?.runtime.capabilities ?? []) {
      map.set(capability.id, capability);
    }
    return map;
  }, [config]);

  const value = useMemo<ConfigValue>(
    () => ({
      config,
      loading,
      error,
      reload: load,
      // Before the config arrives, nothing is claimed to be available. An
      // optimistic default would flash controls on and then disable them.
      can: (id: string) => capabilities.get(id)?.available ?? false,
      why: (id: string) => capabilities.get(id)?.reason ?? "",
      prefs,
      setPrefs,
    }),
    [config, loading, error, load, capabilities, prefs, setPrefs],
  );

  return <ConfigContext.Provider value={value}>{children}</ConfigContext.Provider>;
}

export function useConfig(): ConfigValue {
  const value = useContext(ConfigContext);
  if (!value) throw new Error("useConfig must be used inside ConfigProvider.");
  return value;
}

/** Providers that can actually be called right now. */
export function useAvailableProviders() {
  const { config } = useConfig();
  return useMemo(
    () => (config?.providers ?? []).filter((provider) => provider.available),
    [config],
  );
}

/** Whether any model at all can be called, which decides the default mode. */
export function useHasLlm(): boolean {
  return useAvailableProviders().length > 0;
}
