import { describe, it, expect } from "vitest";
import { pageLevel, forwardState, resolveBack } from "./nav";

describe("pageLevel", () => {
  it("assigns level 0 to main tabs", () => {
    expect(pageLevel("/projects")).toBe(0);
    expect(pageLevel("/agents")).toBe(0);
    expect(pageLevel("/tasks")).toBe(0);
    expect(pageLevel("/git")).toBe(0);
    expect(pageLevel("/")).toBe(0);
  });

  it("assigns level 1 to a specific project", () => {
    expect(pageLevel("/projects/xylocopa")).toBe(1);
    expect(pageLevel("/projects/xylocopa?tab=files")).toBe(1);
  });

  it("assigns level 2 to specific agent and task pages", () => {
    expect(pageLevel("/agents/abc-123")).toBe(2);
    expect(pageLevel("/tasks/t-42")).toBe(2);
  });

  it("assigns level 3 to /new and /new/task", () => {
    expect(pageLevel("/new")).toBe(3);
    expect(pageLevel("/new/task")).toBe(3);
  });
});

describe("forwardState", () => {
  it("captures current path + previous state", () => {
    const loc = { pathname: "/projects/p", search: "?tab=files", state: null };
    expect(forwardState(loc)).toEqual({ from: "/projects/p?tab=files", fromState: null });
  });

  it("nests prior state as fromState", () => {
    const prior = { from: "/projects", fromState: null };
    const loc = { pathname: "/projects/p", search: "", state: prior };
    expect(forwardState(loc)).toEqual({ from: "/projects/p", fromState: prior });
  });

  it("unwraps backgroundLocation for modal pages", () => {
    // /new/task opened over /projects/p — forward should treat /projects/p as the parent.
    const bg = { pathname: "/projects/p", search: "", state: { from: "/projects", fromState: null } };
    const loc = { pathname: "/new/task", search: "", state: { backgroundLocation: bg } };
    expect(forwardState(loc)).toEqual({
      from: "/projects/p",
      fromState: { from: "/projects", fromState: null },
    });
  });
});

describe("resolveBack", () => {
  it("skips same-level entries", () => {
    // /projects/p -> /agents/a -> /agents/b. Back from b should go to /projects/p.
    const state = {
      from: "/agents/a",
      fromState: { from: "/projects/p", fromState: null },
    };
    expect(resolveBack("/agents/b", state)).toEqual({ to: "/projects/p", state: null });
  });

  it("skips deeper-level entries", () => {
    // /new -> /agents/a. Back should fallback (no ancestor shallower than agent).
    const state = { from: "/new", fromState: null };
    expect(resolveBack("/agents/a", state)).toEqual({ to: "/agents", state: null });
  });

  it("uses shallower ancestor directly", () => {
    // /projects/p -> /agents/a. Back from a goes to /projects/p.
    const state = { from: "/projects/p", fromState: null };
    expect(resolveBack("/agents/a", state)).toEqual({ to: "/projects/p", state: null });
  });

  it("walks multiple levels through a chain", () => {
    // /tasks -> /projects/p -> /agents/a -> /agents/b -> /agents/c.
    const state = {
      from: "/agents/b",
      fromState: {
        from: "/agents/a",
        fromState: {
          from: "/projects/p",
          fromState: { from: "/tasks", fromState: null },
        },
      },
    };
    // Back from c skips b, a (both level 2), lands on /projects/p (level 1).
    const back = resolveBack("/agents/c", state);
    expect(back.to).toBe("/projects/p");
    expect(back.state).toEqual({ from: "/tasks", fromState: null });
  });

  it("falls back when state missing", () => {
    expect(resolveBack("/agents/a", null)).toEqual({ to: "/agents", state: null });
    expect(resolveBack("/agents/a", undefined)).toEqual({ to: "/agents", state: null });
  });

  it("respects custom fallback", () => {
    expect(resolveBack("/agents/a", null, "/projects")).toEqual({ to: "/projects", state: null });
  });
});
