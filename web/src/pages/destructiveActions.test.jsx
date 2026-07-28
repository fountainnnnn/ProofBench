// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import Sidebar from "../components/Sidebar.jsx";

const api = vi.hoisted(() => ({
  deleteProviderKey: vi.fn(),
  getHealth: vi.fn(),
  getProviderReadiness: vi.fn(),
  getScraperOrder: vi.fn(),
  saveScraperOrder: vi.fn(),
  listProviderKeys: vi.fn(),
  listSessions: vi.fn(),
  saveProviderKey: vi.fn(),
}));

vi.mock("../api.js", () => api);

import Runs from "./Runs.jsx";
import Settings from "./Settings.jsx";

afterEach(cleanup);

beforeEach(() => {
  vi.clearAllMocks();
  api.getHealth.mockResolvedValue({});
  api.listSessions.mockResolvedValue([]);
  api.getScraperOrder.mockResolvedValue({ order: [], default: [], providers: [] });
  // Readiness is a configuration check only; a ready deployment keeps these
  // tests focused on the destructive-action confirmations.
  api.getProviderReadiness.mockResolvedValue({
    mode: "real",
    run_ready: true,
    blocked_by: [],
    providers: [{
      provider: "daytona",
      label: "Daytona sandboxes",
      capability: "Runs candidate tools in an isolated sandbox.",
      essential: true,
      status: "ready",
      required: ["DAYTONA_API_KEY"],
      missing: [],
      optional_configured: [],
    }],
  });
  api.listProviderKeys.mockResolvedValue({
    keys: [{ env: "VENDOR_API_KEY", source: "settings" }],
    runtime_writes_enabled: true,
    managed_by: "runtime",
  });
  api.deleteProviderKey.mockResolvedValue({ deleted: true });
});

describe("destructive action confirmation and session presentation", () => {
  it("requires inline confirmation before deleting a session and sanitizes its title", () => {
    const onDelete = vi.fn();
    render(
      <Sidebar
        sessions={[{ id: "session-1", title: "Authorization: Bearer sidebar-secret", phase: "DONE" }]}
        onSelect={vi.fn()}
        onNew={vi.fn()}
        onDelete={onDelete}
        onClose={vi.fn()}
      />
    );

    expect(document.body.textContent).not.toContain("sidebar-secret");
    fireEvent.click(screen.getByRole("button", { name: /delete authorization/i }));
    expect(onDelete).not.toHaveBeenCalled();
    // The confirm control is an icon button, so its accessible name carries the
    // session it would delete rather than a bare "Confirm".
    fireEvent.click(screen.getByRole("button", { name: /^confirm deleting/i }));
    expect(onDelete).toHaveBeenCalledTimes(1);
  });

  it("requires confirmation before removing a runtime provider credential", async () => {
    render(<Settings />);
    const remove = await screen.findByRole("button", { name: "Remove" });

    fireEvent.click(remove);
    expect(api.deleteProviderKey).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Confirm remove" }));
    await waitFor(() => expect(api.deleteProviderKey).toHaveBeenCalledWith("VENDOR_API_KEY"));
  });

  it("disables credential edits when settings are managed by deployment", async () => {
    api.listProviderKeys.mockResolvedValue({
      keys: [{ env: "VENDOR_API_KEY", source: "settings" }],
      runtime_writes_enabled: false,
      managed_by: "deployment",
    });
    render(<Settings />);

    expect(await screen.findByText(/Managed by deployment/)).toBeTruthy();
    expect(screen.getByRole("button", { name: "Add key" }).disabled).toBe(true);
    expect(screen.getByRole("button", { name: "Remove" }).disabled).toBe(true);
  });

  it("sanitizes API session titles in the runs table", async () => {
    api.listSessions.mockResolvedValue([{
      id: "run-session",
      title: "access_token=run-secret",
      phase: "DONE",
      mode: "real",
      created_at: "2026-07-20T00:00:00Z",
    }]);
    render(<MemoryRouter><Runs /></MemoryRouter>);

    await screen.findByRole("link", { name: /access_token/i });
    expect(document.body.textContent).not.toContain("run-secret");
  });
});
