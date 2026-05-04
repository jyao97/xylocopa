import { createContext, useContext, useState, useEffect, useCallback, useRef, useMemo } from "react";
import { fetchAllFolders, fetchTrashFolders, clog } from "../lib/api";
import { cacheProjectBriefs } from "../lib/detailCache";
import usePageVisible from "../hooks/usePageVisible";
import { useWsEvent } from "../hooks/useWebSocket";

// Single source of truth for the project folder list. Replaces the
// per-ProjectsPage useState + setInterval(load, 10000) so the keep-mounted
// main ProjectsPage and any split-screen pane that mounts ProjectsPage or
// ProjectDetailPage all share one fetch and one stable array reference —
// downstream useMemo (sortedAll, filtered) stops invalidating across pane
// mounts because `folders` keeps the same identity unless the data changed.

const POLL_MS = 10000;

const StateContext = createContext(null);
const ActionsContext = createContext(null);

const _fallbackState = { folders: [], trashFolders: [], loading: true, error: null, version: 0, seeded: false };
const _fallbackActions = { refetch: () => {} };

export function FoldersProvider({ children }) {
  const [folders, setFolders] = useState([]);
  const [trashFolders, setTrashFolders] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [version, setVersion] = useState(0);
  const [seeded, setSeeded] = useState(false);
  const visible = usePageVisible();

  const prevHashRef = useRef("");
  const prevTrashHashRef = useRef("");

  const refetch = useCallback(async () => {
    const t0 = performance.now();
    try {
      const [data, trash] = await Promise.all([
        fetchAllFolders(),
        fetchTrashFolders(),
      ]);
      const t1 = performance.now();
      const arr = Array.isArray(data) ? data : [];
      const trashArr = Array.isArray(trash) ? trash : [];
      cacheProjectBriefs(arr);
      const hash = arr.map((f) => `${f.name}|${f.last_activity || ""}|${f.active ? 1 : 0}`).join(",");
      const trashHash = trashArr.map((f) => f.name).join(",");
      const changed = hash !== prevHashRef.current;
      const trashChanged = trashHash !== prevTrashHashRef.current;
      prevHashRef.current = hash;
      prevTrashHashRef.current = trashHash;
      clog(`[projects] fetch ${(t1 - t0).toFixed(0)}ms n=${arr.length} dataChanged=${changed}`);
      if (changed) {
        setFolders(arr);
        setVersion((v) => v + 1);
      }
      if (trashChanged) setTrashFolders(trashArr);
      setError(null);
      setSeeded(true);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }, []);

  // Poll while document is visible. One shared poll for everyone.
  useEffect(() => {
    if (!visible) return;
    refetch();
    const t = setInterval(refetch, POLL_MS);
    return () => clearInterval(t);
  }, [visible, refetch]);

  // Cross-pane invalidation event (existing convention used by writers).
  useEffect(() => {
    const onChanged = () => refetch();
    window.addEventListener("projects-data-changed", onChanged);
    return () => window.removeEventListener("projects-data-changed", onChanged);
  }, [refetch]);

  // Cross-device WS invalidation: another device changed project settings
  // (emoji/toggles) or starred a session.
  useWsEvent(useCallback((event) => {
    if (event.type === "project_update") refetch();
  }, [refetch]));

  const state = useMemo(() => ({ folders, trashFolders, loading, error, version, seeded }), [folders, trashFolders, loading, error, version, seeded]);
  const actions = useMemo(() => ({ refetch }), [refetch]);

  return (
    <StateContext.Provider value={state}>
      <ActionsContext.Provider value={actions}>
        {children}
      </ActionsContext.Provider>
    </StateContext.Provider>
  );
}

export function useFolders() {
  return useContext(StateContext) || _fallbackState;
}

export function useFoldersActions() {
  return useContext(ActionsContext) || _fallbackActions;
}
