import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import DropboxLinkModal from "./DropboxLinkModal";

// Mock the API module
vi.mock("../../lib/api", () => ({
  startDropboxLink: vi.fn(),
  completeDropboxLink: vi.fn(),
}));

import { startDropboxLink, completeDropboxLink } from "../../lib/api";

// Stub window.location.assign — jsdom does not implement navigation
const originalLocation = window.location;
let assignMock;

beforeEach(() => {
  vi.clearAllMocks();
  assignMock = vi.fn();
  Object.defineProperty(window, "location", {
    configurable: true,
    value: {
      ...originalLocation,
      origin: "https://localhost:3000",
      pathname: "/projects/test",
      search: "",
      href: "https://localhost:3000/projects/test",
      assign: assignMock,
    },
  });
});

afterEach(() => {
  Object.defineProperty(window, "location", {
    configurable: true,
    value: originalLocation,
  });
});

function setup(props = {}) {
  const defaultProps = {
    open: true,
    appKey: "testkey1234",
    returnTo: "/projects/test",
    onClose: vi.fn(),
    onLinked: vi.fn(),
    ...props,
  };
  return render(<DropboxLinkModal {...defaultProps} />);
}

describe("DropboxLinkModal", () => {
  it("shows not-configured view with redirect URI when appKey is falsy", () => {
    setup({ appKey: "" });

    expect(screen.getByText("Dropbox app key not configured")).toBeInTheDocument();

    // Shows the computed redirect URI
    const uriInput = screen.getByTestId("redirect-uri");
    expect(uriInput.value).toBe("https://localhost:3000/api/dropbox/callback");

    // Has a Close button, no Continue
    expect(screen.getByText("Close")).toBeInTheDocument();
    expect(screen.queryByText("Continue to Dropbox")).not.toBeInTheDocument();
  });

  it("Continue to Dropbox calls startDropboxLink with redirect mode and assigns URL", async () => {
    startDropboxLink.mockResolvedValue({
      authorize_url: "https://www.dropbox.com/oauth2/authorize?client_id=testkey1234&response_type=code",
      state: "abc123",
      mode: "redirect",
      redirect_uri: "https://localhost:3000/api/dropbox/callback",
    });

    setup({ returnTo: "/projects/test" });

    expect(screen.getByText("Connect Dropbox")).toBeInTheDocument();

    const continueBtn = screen.getByText("Continue to Dropbox");
    fireEvent.click(continueBtn);

    await waitFor(() => {
      expect(startDropboxLink).toHaveBeenCalledWith({
        mode: "redirect",
        returnTo: "/projects/test",
      });
    });

    await waitFor(() => {
      expect(assignMock).toHaveBeenCalledWith(
        "https://www.dropbox.com/oauth2/authorize?client_id=testkey1234&response_type=code",
      );
    });
  });

  it("shows redirect URI line in the default view", () => {
    setup();

    expect(screen.getByText(/Redirect URI:/)).toBeInTheDocument();
    expect(screen.getByText(/\/api\/dropbox\/callback/)).toBeInTheDocument();
  });

  it("'Use a code instead' reveals code input and Submit calls completeDropboxLink", async () => {
    startDropboxLink.mockResolvedValue({
      authorize_url: "https://www.dropbox.com/oauth2/authorize?code_flow=1",
      state: "xyz",
      mode: "code",
    });

    completeDropboxLink.mockResolvedValue({
      detail: "ok",
      account: { account_id: "dbid:AAA", name: "Jane", email: "jane@example.com" },
    });

    setup();

    // Click "Use a code instead"
    const codeLink = screen.getByText("Use a code instead");
    fireEvent.click(codeLink);

    // Wait for the code step to appear
    await waitFor(() => {
      expect(screen.getByText("Authorize")).toBeInTheDocument();
    });

    // Should show code input
    const codeInput = screen.getByPlaceholderText("Paste the access code");
    expect(codeInput).toBeInTheDocument();

    // Type a code and submit
    fireEvent.change(codeInput, { target: { value: "auth-code-123" } });
    const submitBtn = screen.getByText("Submit");
    fireEvent.click(submitBtn);

    await waitFor(() => {
      expect(completeDropboxLink).toHaveBeenCalledWith("auth-code-123");
    });

    // After success, shows Connected step
    await waitFor(() => {
      expect(screen.getByText("Connected")).toBeInTheDocument();
      expect(screen.getByText("Jane")).toBeInTheDocument();
    });
  });

  it("renders nothing when open is false", () => {
    const { container } = setup({ open: false });
    expect(container.innerHTML).toBe("");
  });

  it("shows inline error when startDropboxLink fails", async () => {
    startDropboxLink.mockRejectedValue(new Error("No Dropbox app key configured"));

    setup();

    fireEvent.click(screen.getByText("Continue to Dropbox"));

    await waitFor(() => {
      expect(screen.getByText("No Dropbox app key configured")).toBeInTheDocument();
    });
  });
});
