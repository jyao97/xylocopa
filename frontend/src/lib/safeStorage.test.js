import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { safeSetItem } from "./safeStorage";

const quotaErr = () => {
  const e = new Error("The quota has been exceeded.");
  e.name = "QuotaExceededError";
  return e;
};

describe("safeSetItem", () => {
  beforeEach(() => localStorage.clear());
  afterEach(() => vi.restoreAllMocks());

  it("writes normally when quota is fine", () => {
    expect(safeSetItem("draft:chat:x", "hi")).toBe(true);
    expect(localStorage.getItem("draft:chat:x")).toBe("hi");
  });

  it("evicts advisory caches and retries on quota error", () => {
    localStorage.setItem("xy:agent-brief-cache:v1", "B".repeat(100));
    localStorage.setItem("draft:chat:other", "keep me");
    const orig = Storage.prototype.setItem;
    // Simulate a full store: the target write throws until the big
    // advisory cache has been evicted.
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(function (k, v) {
      if (k === "draft:chat:x" && localStorage.getItem("xy:agent-brief-cache:v1") !== null) {
        throw quotaErr();
      }
      return orig.call(this, k, v);
    });
    expect(safeSetItem("draft:chat:x", "hi")).toBe(true);
    expect(localStorage.getItem("xy:agent-brief-cache:v1")).toBe(null); // evicted
    expect(localStorage.getItem("draft:chat:other")).toBe("keep me"); // untouched
    expect(localStorage.getItem("draft:chat:x")).toBe("hi");
  });

  it("evicts attachment chip caches but never real drafts", () => {
    localStorage.setItem("draft:chat:a:attachments", "[]");
    localStorage.setItem("draft:chat:a", "real draft");
    const orig = Storage.prototype.setItem;
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(function (k, v) {
      if (k === "draft:chat:x" && localStorage.getItem("draft:chat:a:attachments") !== null) {
        throw quotaErr();
      }
      return orig.call(this, k, v);
    });
    expect(safeSetItem("draft:chat:x", "hi")).toBe(true);
    expect(localStorage.getItem("draft:chat:a:attachments")).toBe(null);
    expect(localStorage.getItem("draft:chat:a")).toBe("real draft");
  });

  it("returns false when nothing evictable frees enough space", () => {
    localStorage.setItem("draft:chat:other", "keep");
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw quotaErr();
    });
    expect(safeSetItem("draft:chat:x", "hi")).toBe(false);
    expect(localStorage.getItem("draft:chat:other")).toBe("keep");
  });

  it("does not evict on non-quota errors", () => {
    localStorage.setItem("xy:agent-brief-cache:v1", "cache");
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      const e = new Error("SecurityError");
      e.name = "SecurityError";
      throw e;
    });
    expect(safeSetItem("draft:chat:x", "hi")).toBe(false);
    expect(localStorage.getItem("xy:agent-brief-cache:v1")).toBe("cache");
  });
});
