// Module-level caches for dynamic-route detail pages.
//
// These pages (`/projects/:name`, `/tasks/:id`) mount fresh on every
// navigation. Without a cache, re-entering a page shows a loading
// state until 4 parallel fetches resolve — visible as a flash even
// when the data hasn't changed. The keep-mounted main tabs (Projects/
// Agents/Tasks/Git) avoid this because they survive navigation; the
// detail pages don't, so we stash their data here.
//
// Pattern (cache-first + background refetch):
//   const cached = projectDetailCache.get(name);
//   if (cached) {
//     // Render from cache immediately
//     setProject(cached.project); setAgents(cached.agents); ...
//     setLoading(false);
//   }
//   // Always fetch fresh in the background
//   const fresh = await fetchEverything();
//   projectDetailCache.set(name, fresh);
//   setProject(fresh.project); ...
//
// Lifetime: in-memory only, lost on full page reload. That's fine —
// the SW precache + browser http cache handle cold-start latency.
//
// Invalidation: callers should invalidate on user-driven mutations
// (rename, archive, delete) so stale data doesn't paint after the
// action completes. Background polls are not invalidations — they
// just overwrite the entry.

function makeCache({ persistKey } = {}) {
  const store = new Map();
  let saveTimer = null;

  // Hydrate from localStorage on init. Tolerate any error (quota,
  // disabled storage, malformed payload) — caches are advisory.
  if (persistKey && typeof localStorage !== "undefined") {
    try {
      const raw = localStorage.getItem(persistKey);
      if (raw) {
        const parsed = JSON.parse(raw);
        if (parsed && typeof parsed === "object") {
          for (const [k, v] of Object.entries(parsed)) {
            if (v && typeof v === "object") store.set(k, v);
          }
        }
      }
    } catch {
      // ignore — fall back to empty cache
    }
  }

  function scheduleSave() {
    if (!persistKey || typeof localStorage === "undefined") return;
    if (saveTimer) return;
    saveTimer = setTimeout(() => {
      saveTimer = null;
      try {
        localStorage.setItem(persistKey, JSON.stringify(Object.fromEntries(store)));
      } catch {
        // quota exceeded etc — drop silently, in-memory cache still works
      }
    }, 300);
  }

  return {
    get(key) { return store.get(key); },
    set(key, value) {
      store.set(key, { ...value, ts: Date.now() });
      scheduleSave();
    },
    invalidate(key) {
      store.delete(key);
      scheduleSave();
    },
    clear() {
      store.clear();
      scheduleSave();
    },
    size() { return store.size; },
  };
}

// Keys: project name. Value shape: { project, agents, stats, bookmarks, ts }
export const projectDetailCache = makeCache();

// Keys: task id. Value shape: { task, related, ts }
export const taskDetailCache = makeCache();

// Keys: agent id. Value shape: { ...agentBrief, ts }
// Populated by AgentsPage / ProjectDetailPage when their lists load AND
// patched in real-time by the WS agent_update handler (AgentsPage), so
// AgentChatPage can paint chat header + insights cards immediately on
// entry without waiting for fetchAgent.
//
// Persisted to localStorage so deep links / page reloads / new tabs hit
// a warm cache too. Backend fetchAgent in background corrects any drift
// within ~500ms; persisted snapshot is advisory, never authoritative.
export const agentBriefCache = makeCache({ persistKey: "xy:agent-brief-cache:v1" });
export function cacheAgentBriefs(list) {
  if (!Array.isArray(list)) return;
  for (const a of list) {
    if (a?.id) agentBriefCache.set(a.id, a);
  }
}

// Keys: project name. Value shape: { ...folderBrief, ts }
// Populated by ProjectsPage when its folder list loads, consumed by
// ProjectDetailPage to render the project header (emoji, name, stats)
// before fetchAllFolders + fetchProjectAgents finish.
export const projectBriefCache = makeCache();
export function cacheProjectBriefs(list) {
  if (!Array.isArray(list)) return;
  for (const f of list) {
    if (f?.name) projectBriefCache.set(f.name, f);
  }
}

// Keys: task id. Value shape: { ...taskBrief, ts }
// Populated by TasksPage / InboxView when the task list loads, consumed
// by TaskDetailPage to paint the title / status / project chip before
// fetchTaskV2 returns.
export const taskBriefCache = makeCache();
export function cacheTaskBriefs(list) {
  if (!Array.isArray(list)) return;
  for (const t of list) {
    if (t?.id) taskBriefCache.set(t.id, t);
  }
}

// Keys: agent id. Value shape: { items: AgentInsightSuggestion[], ts }
// Populated by InsightsHistoryCard's fetch on mount; consumed on
// re-mount (chat-page re-entry / refresh) to render the collapsed
// "X applied" chrome immediately instead of the "Loading insights…"
// shimmer for ~100-300ms.
//
// Invalidate from ProgressSuggestionsCard's apply / discard handlers
// so the next mount of InsightsHistoryCard sees fresh data.
//
// Persisted so deep links / reloads also skip the loading state.
export const processedSuggestionsCache = makeCache({
  persistKey: "xy:processed-suggestions-cache:v1",
});
