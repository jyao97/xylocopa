import { useState, useEffect, useCallback } from "react";
import { useNavigate, useLocation } from "react-router-dom";
import { fetchProjectAgents, fetchTasksV2 } from "../lib/api";
import { AGENT_STATUS_COLORS, AGENT_STATUS_TEXT_COLORS } from "../lib/constants";
import { relativeTime } from "../lib/formatters";
import { forwardState } from "../lib/nav";

// Status dot colors for the tree nodes (hex values matching Tailwind classes)
const STATUS_HEX = {
  STARTING: "#6b7280",
  IDLE: "#22c55e",
  EXECUTING: "#06b6d4",
  ERROR: "#ef4444",
  STOPPED: "#4b5563",
};

const STATUS_LABEL = {
  STARTING: "Starting",
  IDLE: "Idle",
  EXECUTING: "Executing",
  ERROR: "Error",
  STOPPED: "Stopped",
};

const TASK_STATUS_HEX = {
  INBOX: "#3b82f6",
  PLANNING: "#8b5cf6",
  PENDING: "#6b7280",
  EXECUTING: "#06b6d4",
  REVIEW: "#f59e0b",
  MERGING: "#a855f7",
  CONFLICT: "#ef4444",
  COMPLETE: "#22c55e",
  REJECTED: "#f97316",
  CANCELLED: "#4b5563",
  FAILED: "#ef4444",
  TIMEOUT: "#f97316",
};

const TASK_STATUS_LABEL = {
  INBOX: "Inbox",
  PLANNING: "Planning",
  PENDING: "Pending",
  EXECUTING: "Running",
  REVIEW: "Review",
  MERGING: "Merging",
  CONFLICT: "Conflict",
  COMPLETE: "Done",
  REJECTED: "Rejected",
  CANCELLED: "Cancelled",
  FAILED: "Failed",
  TIMEOUT: "Timeout",
};

function buildTree(agents) {
  const byId = {};
  const roots = [];
  const children = {};

  for (const a of agents) {
    byId[a.id] = a;
    if (!a.is_subagent || !a.parent_id) {
      roots.push(a);
    } else {
      if (!children[a.parent_id]) children[a.parent_id] = [];
      children[a.parent_id].push(a);
    }
  }

  // Sort roots by last_message_at desc
  roots.sort((a, b) => {
    const ta = a.last_message_at ? new Date(a.last_message_at).getTime() : 0;
    const tb = b.last_message_at ? new Date(b.last_message_at).getTime() : 0;
    return tb - ta;
  });

  // Sort children by created_at
  for (const kids of Object.values(children)) {
    kids.sort((a, b) => new Date(a.created_at).getTime() - new Date(b.created_at).getTime());
  }

  return { roots, children };
}

function StatusDot({ status, pulse }) {
  const color = STATUS_HEX[status] || "#6b7280";
  return (
    <span className="relative flex-shrink-0" style={{ width: 10, height: 10 }}>
      <span
        className="absolute inset-0 rounded-full"
        style={{ backgroundColor: color, opacity: 0.25 }}
      />
      {pulse && (
        <span
          className="absolute inset-0 rounded-full animate-ping"
          style={{ backgroundColor: color, opacity: 0.5 }}
        />
      )}
      <span
        className="absolute inset-0 rounded-full"
        style={{ backgroundColor: color }}
      />
    </span>
  );
}

function TaskChip({ task, navigate, location }) {
  const color = TASK_STATUS_HEX[task.status] || "#6b7280";
  const label = TASK_STATUS_LABEL[task.status] || task.status;
  const isActive = task.status === "EXECUTING";

  return (
    <button
      type="button"
      onClick={(e) => {
        e.stopPropagation();
        navigate(`/tasks/${task.id}`, { state: forwardState(location) });
      }}
      className="group w-full text-left flex items-center gap-2 rounded-md px-2 py-1 hover:bg-hover transition-colors"
    >
      <span className="relative flex-shrink-0" style={{ width: 7, height: 7 }}>
        <span className="absolute inset-0 rounded-full" style={{ backgroundColor: color, opacity: 0.25 }} />
        {isActive && (
          <span className="absolute inset-0 rounded-full animate-ping" style={{ backgroundColor: color, opacity: 0.5 }} />
        )}
        <span className="absolute inset-0 rounded-full" style={{ backgroundColor: color }} />
      </span>
      <span className="text-[11px] text-body truncate flex-1 group-hover:text-accent transition-colors min-w-0">
        {task.title}
      </span>
      <span
        className="text-[10px] shrink-0 font-medium tabular-nums"
        style={{ color }}
      >
        {label}
      </span>
    </button>
  );
}

function AgentNode({ agent, children, agentTaskMap, depth, isLast, navigate, location }) {
  const isActive = agent.status === "EXECUTING";
  const textColor = AGENT_STATUS_TEXT_COLORS[agent.status] || "text-dim";
  const kids = children[agent.id] || [];
  const hasKids = kids.length > 0;
  const tasks = agentTaskMap[agent.id] || [];

  return (
    <div className="relative">
      <button
        type="button"
        onClick={() => navigate(`/agents/${agent.id}`, { state: forwardState(location) })}
        className="group w-full text-left flex items-start gap-2 rounded-lg px-2 py-1.5 hover:bg-hover transition-colors"
      >
        <div className="flex items-center gap-1.5 min-w-0 flex-1">
          <StatusDot status={agent.status} pulse={isActive} />
          <span className="text-xs text-heading font-medium truncate group-hover:text-accent transition-colors">
            {agent.name}
          </span>
          <span className={`text-[10px] shrink-0 ${textColor}`}>
            {STATUS_LABEL[agent.status] || agent.status}
          </span>
          {agent.last_message_at && (
            <span className="text-[10px] text-faint ml-auto shrink-0">
              {relativeTime(agent.last_message_at)}
            </span>
          )}
        </div>
      </button>

      {/* Tasks assigned to this agent */}
      {tasks.length > 0 && (
        <div className="ml-5 mb-0.5">
          {tasks.map((task) => (
            <TaskChip
              key={task.id}
              task={task}
              navigate={navigate}
              location={location}
            />
          ))}
        </div>
      )}

      {hasKids && (
        <div className="ml-5 mt-0.5 relative">
          {/* Vertical connector line */}
          <div
            className="absolute left-0 top-0 bottom-2 w-px"
            style={{ backgroundColor: "var(--color-edge)", marginLeft: -1 }}
          />
          {kids.map((child, i) => (
            <div key={child.id} className="relative pl-4">
              {/* Horizontal connector */}
              <div
                className="absolute left-0 top-3.5 h-px w-3"
                style={{ backgroundColor: "var(--color-edge)" }}
              />
              <AgentNode
                agent={child}
                children={children}
                agentTaskMap={agentTaskMap}
                depth={depth + 1}
                isLast={i === kids.length - 1}
                navigate={navigate}
                location={location}
              />
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

export default function TaskGraphSection({ projectName, visible }) {
  const navigate = useNavigate();
  const location = useLocation();

  const [agents, setAgents] = useState([]);
  const [agentTaskMap, setAgentTaskMap] = useState({});
  const [initialized, setInitialized] = useState(false);
  const [error, setError] = useState(null);

  const load = useCallback(async () => {
    try {
      const [agentData, taskData] = await Promise.all([
        fetchProjectAgents(projectName, "include_subagents=true"),
        fetchTasksV2(`project=${encodeURIComponent(projectName)}`),
      ]);
      setAgents(Array.isArray(agentData) ? agentData : []);

      // Group tasks by agent_id
      const taskMap = {};
      const tasks = Array.isArray(taskData) ? taskData : [];
      for (const task of tasks) {
        if (task.agent_id) {
          if (!taskMap[task.agent_id]) taskMap[task.agent_id] = [];
          taskMap[task.agent_id].push(task);
        }
      }
      // Sort each agent's tasks: active first, then by created_at desc
      const activeFirst = ["EXECUTING", "REVIEW", "MERGING", "CONFLICT", "PLANNING", "PENDING"];
      for (const agentId of Object.keys(taskMap)) {
        taskMap[agentId].sort((a, b) => {
          const ai = activeFirst.indexOf(a.status);
          const bi = activeFirst.indexOf(b.status);
          if (ai !== bi) return (ai === -1 ? 999 : ai) - (bi === -1 ? 999 : bi);
          return new Date(b.created_at).getTime() - new Date(a.created_at).getTime();
        });
      }
      setAgentTaskMap(taskMap);
      setError(null);
    } catch (err) {
      setError(err.message || "Failed to load task graph");
    } finally {
      setInitialized(true);
    }
  }, [projectName]);

  useEffect(() => {
    if (!visible) return;
    load();
    const interval = setInterval(load, 5000);
    return () => clearInterval(interval);
  }, [load, visible]);

  if (!visible) return null;

  if (!initialized) {
    return (
      <div className="py-8 text-center">
        <span className="text-dim text-sm animate-pulse">Loading graph...</span>
      </div>
    );
  }

  if (error) {
    return (
      <div className="rounded-xl bg-red-950/30 border border-red-800 p-3">
        <p className="text-red-400 text-sm">{error}</p>
      </div>
    );
  }

  const { roots, children } = buildTree(agents);

  // Count subagents and tasks
  const subagentCount = agents.filter((a) => a.is_subagent).length;
  const taskCount = Object.values(agentTaskMap).reduce((sum, ts) => sum + ts.length, 0);

  if (roots.length === 0) {
    return (
      <div className="py-8 text-center text-faint text-sm">
        No agents in this project
      </div>
    );
  }

  // Legend: agent statuses
  const activeStatuses = [...new Set(agents.map((a) => a.status))].filter(Boolean);

  // Active task statuses for the legend
  const allTaskStatuses = [...new Set(
    Object.values(agentTaskMap).flat().map((t) => t.status)
  )].filter(Boolean);

  return (
    <div className="space-y-3">
      {/* Summary row */}
      <div className="flex items-center gap-3 text-xs text-dim px-1">
        <span>{roots.length} parent{roots.length !== 1 ? "s" : ""}</span>
        {subagentCount > 0 && (
          <>
            <span className="text-faint">·</span>
            <span>{subagentCount} subagent{subagentCount !== 1 ? "s" : ""}</span>
          </>
        )}
        {taskCount > 0 && (
          <>
            <span className="text-faint">·</span>
            <span>{taskCount} task{taskCount !== 1 ? "s" : ""}</span>
          </>
        )}
        <div className="ml-auto flex items-center gap-2">
          {activeStatuses.map((s) => (
            <span key={s} className="flex items-center gap-1">
              <span
                className="inline-block w-2 h-2 rounded-full"
                style={{ backgroundColor: STATUS_HEX[s] || "#6b7280" }}
              />
              <span className="text-[10px]">{STATUS_LABEL[s] || s}</span>
            </span>
          ))}
        </div>
      </div>

      {/* Task status legend */}
      {allTaskStatuses.length > 0 && (
        <div className="flex items-center gap-2 px-1 flex-wrap">
          <span className="text-[10px] text-faint uppercase tracking-wider">Tasks:</span>
          {allTaskStatuses.map((s) => (
            <span key={s} className="flex items-center gap-1">
              <span
                className="inline-block w-1.5 h-1.5 rounded-full"
                style={{ backgroundColor: TASK_STATUS_HEX[s] || "#6b7280" }}
              />
              <span className="text-[10px] text-faint">{TASK_STATUS_LABEL[s] || s}</span>
            </span>
          ))}
        </div>
      )}

      {/* Tree */}
      <div className="rounded-xl bg-surface shadow-card overflow-hidden">
        <div className="p-3 space-y-0.5">
          {roots.map((agent) => (
            <AgentNode
              key={agent.id}
              agent={agent}
              children={children}
              agentTaskMap={agentTaskMap}
              depth={0}
              isLast={false}
              navigate={navigate}
              location={location}
            />
          ))}
        </div>
      </div>
    </div>
  );
}
