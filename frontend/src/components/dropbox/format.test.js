import { describe, it, expect, vi, afterEach } from "vitest";
import { formatBytes, formatRelative } from "./format";

describe("formatBytes", () => {
  it("returns '0 B' for zero", () => {
    expect(formatBytes(0)).toBe("0 B");
  });

  it("formats KB", () => {
    expect(formatBytes(1024)).toBe("1.0 KB");
  });

  it("formats MB with one decimal when < 10", () => {
    expect(formatBytes(5 * 1024 * 1024)).toBe("5.0 MB");
  });

  it("rounds MB when >= 10", () => {
    expect(formatBytes(15 * 1024 * 1024)).toBe("15 MB");
  });

  it("formats GB", () => {
    expect(formatBytes(2 * 1024 * 1024 * 1024)).toBe("2.0 GB");
  });
});

describe("formatRelative", () => {
  afterEach(() => {
    vi.useRealTimers();
  });

  it("returns 'never' for null/undefined/empty", () => {
    expect(formatRelative(null)).toBe("never");
    expect(formatRelative(undefined)).toBe("never");
    expect(formatRelative("")).toBe("never");
  });

  it("returns 'never' for invalid ISO string", () => {
    expect(formatRelative("not-a-date")).toBe("never");
  });

  it("returns 'just now' for timestamps within 30 seconds (past)", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2025-06-01T12:00:30Z"));
    expect(formatRelative("2025-06-01T12:00:05Z")).toBe("just now");
    vi.useRealTimers();
  });

  it("returns 'just now' for timestamps within 30 seconds (future)", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2025-06-01T12:00:00Z"));
    expect(formatRelative("2025-06-01T12:00:20Z")).toBe("just now");
    vi.useRealTimers();
  });

  it("returns 'N min ago' for past timestamps under 1 hour", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2025-06-01T12:05:00Z"));
    expect(formatRelative("2025-06-01T12:00:00Z")).toBe("5 min ago");
    vi.useRealTimers();
  });

  it("returns 'in N min' for future timestamps under 1 hour", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2025-06-01T12:00:00Z"));
    expect(formatRelative("2025-06-01T12:05:00Z")).toBe("in 5 min");
    vi.useRealTimers();
  });

  it("returns 'N h ago' for past timestamps under 24 hours", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2025-06-01T14:00:00Z"));
    expect(formatRelative("2025-06-01T12:00:00Z")).toBe("2 h ago");
    vi.useRealTimers();
  });

  it("returns 'in N h' for future timestamps under 24 hours", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2025-06-01T12:00:00Z"));
    expect(formatRelative("2025-06-01T14:00:00Z")).toBe("in 2 h");
    vi.useRealTimers();
  });

  it("returns 'N d ago' for past timestamps over 24 hours", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2025-06-04T12:00:00Z"));
    expect(formatRelative("2025-06-01T12:00:00Z")).toBe("3 d ago");
    vi.useRealTimers();
  });

  it("returns 'in N d' for future timestamps over 24 hours", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2025-06-01T12:00:00Z"));
    expect(formatRelative("2025-06-04T12:00:00Z")).toBe("in 3 d");
    vi.useRealTimers();
  });
});
