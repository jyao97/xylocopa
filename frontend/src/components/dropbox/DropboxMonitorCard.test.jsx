import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import DropboxMonitorCard from "./DropboxMonitorCard";

// Mock api module
vi.mock("../../lib/api", () => ({
  fetchDropboxStatus: vi.fn(),
  updateDropboxConfig: vi.fn(),
  triggerDropboxSync: vi.fn(),
  pauseDropboxSync: vi.fn(),
  resumeDropboxSync: vi.fn(),
  unlinkDropbox: vi.fn(),
}));

// Mock hooks
vi.mock("../../hooks/useWebSocket", () => ({
  useWsEvent: vi.fn(),
}));

vi.mock("../../hooks/usePageVisible", () => ({
  default: () => true,
}));

import { fetchDropboxStatus } from "../../lib/api";

const MOCK_STATUS = {
  linked: true,
  account: { name: "Jane Doe", email: "jane@example.com" },
  config: { interval_minutes: 360, paused: false, concurrency: 4, prune: false, allowlist_mode: false },
  space: { used: 5 * 1024 * 1024 * 1024, allocated: 10 * 1024 * 1024 * 1024 },
  next_run_at: new Date(Date.now() + 3600000).toISOString(),
  current_run: null,
  last_run: {
    status: "ok",
    finished_at: new Date(Date.now() - 600000).toISOString(),
    files_uploaded: 42,
    bytes_uploaded: 1024 * 1024 * 100,
    errors: 0,
  },
  projects: [
    { name: "alpha", display_name: "Alpha", enabled: true, files_synced: 10, bytes_synced: 5000, last_synced_at: new Date(Date.now() - 600000).toISOString(), last_error: null },
    { name: "beta", display_name: "Beta", enabled: true, files_synced: 5, bytes_synced: 2000, last_synced_at: new Date(Date.now() - 3600000).toISOString(), last_error: "upload timeout" },
    { name: "gamma", display_name: "Gamma", enabled: false, files_synced: null, bytes_synced: 0, last_synced_at: null, last_error: null },
    { name: "delta", display_name: "Delta", enabled: false, files_synced: null, bytes_synced: 0, last_synced_at: null, last_error: null },
    { name: "epsilon", display_name: "Epsilon", enabled: false, files_synced: null, bytes_synced: 0, last_synced_at: null, last_error: null },
  ],
  recent_errors: [
    { at: new Date(Date.now() - 300000).toISOString(), project: "beta", message: "upload timeout", path: "/data/file.bin" },
  ],
  app_key: "testkey",
  link_mode: "relay",
};

beforeEach(() => {
  vi.clearAllMocks();
});

describe("DropboxMonitorCard", () => {
  it("lists only enabled projects", async () => {
    fetchDropboxStatus.mockResolvedValue(MOCK_STATUS);
    render(<DropboxMonitorCard />);

    // Wait for async load
    expect(await screen.findByText("Alpha")).toBeInTheDocument();
    expect(screen.getByText("Beta")).toBeInTheDocument();

    // Disabled projects should NOT be in the list
    expect(screen.queryByText("Gamma")).not.toBeInTheDocument();
    expect(screen.queryByText("Delta")).not.toBeInTheDocument();
    expect(screen.queryByText("Epsilon")).not.toBeInTheDocument();
  });

  it("shows the disabled-projects hint with correct count", async () => {
    fetchDropboxStatus.mockResolvedValue(MOCK_STATUS);
    render(<DropboxMonitorCard />);

    const hint = await screen.findByText(/3 projects not syncing/);
    expect(hint).toBeInTheDocument();
  });

  it("shows error message for projects with last_error", async () => {
    fetchDropboxStatus.mockResolvedValue(MOCK_STATUS);
    render(<DropboxMonitorCard />);

    // Beta's error should be visible in the project row
    const errorEl = await screen.findByText("upload timeout");
    expect(errorEl).toBeInTheDocument();
  });

  it("shows space bar with allocated size", async () => {
    fetchDropboxStatus.mockResolvedValue(MOCK_STATUS);
    render(<DropboxMonitorCard />);

    // The UsageBar-style detail should contain the allocated size
    const detail = await screen.findByText(/10 GB/);
    expect(detail).toBeInTheDocument();
  });

  it("shows fallback interval in subtitle when interval > 0", async () => {
    fetchDropboxStatus.mockResolvedValue(MOCK_STATUS);
    render(<DropboxMonitorCard />);

    // 360 minutes = 6 h
    const subtitle = await screen.findByText(/Auto: on change · fallback every 6 h/);
    expect(subtitle).toBeInTheDocument();
  });

  it("shows fallback interval in minutes when < 60", async () => {
    fetchDropboxStatus.mockResolvedValue({
      ...MOCK_STATUS,
      config: { ...MOCK_STATUS.config, interval_minutes: 15 },
    });
    render(<DropboxMonitorCard />);

    const subtitle = await screen.findByText(/Auto: on change · fallback every 15 min/);
    expect(subtitle).toBeInTheDocument();
  });

  it("shows 'on change' subtitle when interval_minutes is 0", async () => {
    fetchDropboxStatus.mockResolvedValue({
      ...MOCK_STATUS,
      config: { ...MOCK_STATUS.config, interval_minutes: 0 },
      next_run_at: null,
    });
    render(<DropboxMonitorCard />);

    const subtitle = await screen.findByText(/Auto: on change · 2 projects/);
    expect(subtitle).toBeInTheDocument();
    // Should NOT contain "fallback"
    expect(subtitle.textContent).not.toMatch(/fallback/);
  });

  it("config select defaults to Off when interval_minutes is 0", async () => {
    fetchDropboxStatus.mockResolvedValue({
      ...MOCK_STATUS,
      config: { ...MOCK_STATUS.config, interval_minutes: 0 },
    });
    render(<DropboxMonitorCard />);

    // Open config panel
    const settingsBtn = await screen.findByTitle("Settings");
    settingsBtn.click();

    // Find the Fallback check label
    const label = await screen.findByText("Fallback check");
    expect(label).toBeInTheDocument();

    // The select sibling should have value "0" (Off)
    const select = label.closest("div").querySelector("select");
    expect(select.value).toBe("0");
  });

  it("shows 'Up to date' when last_check found no changes", async () => {
    const checkedAt = new Date(Date.now() - 60000).toISOString();
    const finishedAt = new Date(Date.now() - 600000).toISOString();
    fetchDropboxStatus.mockResolvedValue({
      ...MOCK_STATUS,
      last_check: { at: checkedAt, changed: [] },
      last_run: {
        status: "ok",
        finished_at: finishedAt,
        files_uploaded: 42,
        bytes_uploaded: 1024 * 1024 * 100,
        errors: 0,
      },
    });
    render(<DropboxMonitorCard />);

    const statusLine = await screen.findByText(/Up to date/);
    expect(statusLine).toBeInTheDocument();
    expect(statusLine.textContent).toMatch(/checked/);
  });

  it("falls through to last_run when last_check has changes", async () => {
    fetchDropboxStatus.mockResolvedValue({
      ...MOCK_STATUS,
      last_check: {
        at: new Date(Date.now() - 30000).toISOString(),
        changed: ["alpha"],
      },
    });
    render(<DropboxMonitorCard />);

    // Should show the lastRun "Synced" line, not "Up to date"
    const synced = await screen.findByText(/Synced/);
    expect(synced).toBeInTheDocument();
  });

  it("shows Connect button in not-linked state", async () => {
    fetchDropboxStatus.mockResolvedValue({
      ...MOCK_STATUS,
      linked: false,
      account: null,
      config: null,
      projects: [],
      space: null,
      last_run: null,
      current_run: null,
      recent_errors: [],
    });
    render(<DropboxMonitorCard />);

    const btn = await screen.findByText("Connect");
    expect(btn).toBeInTheDocument();
    // "Not linked" heading
    expect(screen.getByText("Not linked")).toBeInTheDocument();
  });
});
