import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import DropboxFolderPicker from "./DropboxFolderPicker";

// Mock the API module
vi.mock("../../lib/api", () => ({
  fetchDropboxFolders: vi.fn(),
  startDropboxDryRun: vi.fn(),
  fetchDropboxDryRun: vi.fn(),
  stopDropboxDryRun: vi.fn(),
  updateProjectSettings: vi.fn(),
  triggerDropboxSync: vi.fn(),
}));

import {
  fetchDropboxFolders,
  startDropboxDryRun,
  fetchDropboxDryRun,
  stopDropboxDryRun,
  updateProjectSettings,
  triggerDropboxSync,
} from "../../lib/api";

const MOCK_ENTRIES = [
  { name: ".", type: "root", default_ignored: false },
  { name: "src", type: "dir", default_ignored: false },
  { name: "data", type: "dir", default_ignored: false },
  { name: "node_modules", type: "dir", default_ignored: true },
];

const MOCK_DRY_RUN_COMPLETE = {
  job_id: "job-1",
  status: "complete",
  entries: {
    ".": { files: 5, bytes: 1024 },
    src: { files: 100, bytes: 500000 },
    data: { files: 50, bytes: 2000000 },
    node_modules: { files: 10000, bytes: 90000000 },
  },
  total: { files: 10155, bytes: 92501024 },
};

function setup(props = {}) {
  const defaultProps = {
    open: true,
    project: "test-project",
    remoteRoot: "/test-project",
    initialFolders: null,
    initialIgnore: "",
    onClose: vi.fn(),
    onSaved: vi.fn(),
    syncAfterSave: true,
    ...props,
  };
  return render(<DropboxFolderPicker {...defaultProps} />);
}

describe("DropboxFolderPicker", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.useFakeTimers({ shouldAdvanceTime: true });

    fetchDropboxFolders.mockResolvedValue({ entries: MOCK_ENTRIES });
    startDropboxDryRun.mockResolvedValue({ job_id: "job-1" });
    fetchDropboxDryRun.mockResolvedValue(MOCK_DRY_RUN_COMPLETE);
    updateProjectSettings.mockResolvedValue({
      name: "test-project",
      dropbox_sync: true,
      dropbox_folders: '[".", "data", "src"]',
    });
    triggerDropboxSync.mockResolvedValue({ detail: "queued" });
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("renders entries and excludes default-ignored from initial selection", async () => {
    setup();

    await waitFor(() => {
      expect(screen.getByText("Files in project root")).toBeInTheDocument();
    });

    expect(screen.getByText("src")).toBeInTheDocument();
    expect(screen.getByText("data")).toBeInTheDocument();
    expect(screen.getByText("node_modules")).toBeInTheDocument();

    // Check that selectable entries are checked
    const checkboxes = screen.getAllByRole("checkbox");
    // "." (root), "src", "data" should be checked; "node_modules" should not
    const rootCb = checkboxes[0]; // "."
    const srcCb = checkboxes[1]; // "src"
    const dataCb = checkboxes[2]; // "data"
    const nmCb = checkboxes[3]; // "node_modules"

    expect(rootCb.checked).toBe(true);
    expect(srcCb.checked).toBe(true);
    expect(dataCb.checked).toBe(true);
    expect(nmCb.checked).toBe(false);
    expect(nmCb.disabled).toBe(true);
  });

  it("shows ignored badge on default_ignored rows", async () => {
    setup();

    await waitFor(() => {
      expect(screen.getByText("node_modules")).toBeInTheDocument();
    });

    expect(screen.getByText("ignored")).toBeInTheDocument();
  });

  it("Select all selects all selectable rows and flips to Deselect all", async () => {
    setup({ initialFolders: ["src"] });

    await waitFor(() => {
      expect(screen.getByText("src")).toBeInTheDocument();
    });

    // Initially only "src" selected, so button says "Select all"
    const selectAllBtn = screen.getByText("Select all");
    expect(selectAllBtn).toBeInTheDocument();

    fireEvent.click(selectAllBtn);

    // Now all selectable should be checked
    const checkboxes = screen.getAllByRole("checkbox");
    expect(checkboxes[0].checked).toBe(true); // "."
    expect(checkboxes[1].checked).toBe(true); // "src"
    expect(checkboxes[2].checked).toBe(true); // "data"
    expect(checkboxes[3].checked).toBe(false); // "node_modules" (disabled)

    // Button should now say "Deselect all"
    expect(screen.getByText("Deselect all")).toBeInTheDocument();
  });

  it("Deselect all clears all selections", async () => {
    setup(); // initialFolders=null => all selectable checked

    await waitFor(() => {
      // All selectable checked => "Deselect all" should appear
      expect(screen.getByText("Deselect all")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByText("Deselect all"));

    const checkboxes = screen.getAllByRole("checkbox");
    expect(checkboxes[0].checked).toBe(false);
    expect(checkboxes[1].checked).toBe(false);
    expect(checkboxes[2].checked).toBe(false);

    expect(screen.getByText("Select all")).toBeInTheDocument();
  });

  it("toggling a row works", async () => {
    setup();

    await waitFor(() => {
      expect(screen.getByText("src")).toBeInTheDocument();
    });

    const checkboxes = screen.getAllByRole("checkbox");
    const srcCb = checkboxes[1]; // "src"
    expect(srcCb.checked).toBe(true);

    // Uncheck src
    fireEvent.click(srcCb);
    expect(srcCb.checked).toBe(false);

    // Re-check src
    fireEvent.click(srcCb);
    expect(srcCb.checked).toBe(true);
  });

  it("Save calls updateProjectSettings with dropbox_sync true and sorted selection", async () => {
    setup({ initialFolders: ["data", "src"] });

    await waitFor(() => {
      expect(screen.getByText("src")).toBeInTheDocument();
    });

    const saveBtn = screen.getByText("Save");
    fireEvent.click(saveBtn);

    await waitFor(() => {
      expect(updateProjectSettings).toHaveBeenCalledWith("test-project", {
        dropbox_sync: true,
        dropbox_folders: ["data", "src"],
        dropbox_ignore: null,
      });
    });
  });

  it("Save is disabled when nothing is selected", async () => {
    setup({ initialFolders: [] });

    await waitFor(() => {
      expect(screen.getByText("src")).toBeInTheDocument();
    });

    const saveBtn = screen.getByText("Save");
    expect(saveBtn.disabled).toBe(true);
  });

  it("ignored row checkbox is disabled", async () => {
    setup();

    await waitFor(() => {
      expect(screen.getByText("node_modules")).toBeInTheDocument();
    });

    const checkboxes = screen.getAllByRole("checkbox");
    const nmCb = checkboxes[3];
    expect(nmCb.disabled).toBe(true);
  });
});
