// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";

const api = vi.hoisted(() => ({
  AUTH_CHANGE_EVENT: "proofbench-auth-change",
  bootstrapAuthSession: vi.fn(),
  listSessions: vi.fn(),
}));

vi.mock("../api.js", () => api);

import Shell from "./Shell.jsx";

function renderShell() {
  return render(
    <MemoryRouter initialEntries={["/app"]}>
      <Routes>
        <Route path="/" element={<p>Landing destination</p>} />
        <Route path="/app" element={<Shell />}>
          <Route index element={<button type="button">Write action</button>} />
        </Route>
      </Routes>
    </MemoryRouter>
  );
}

// Regression: no browser path may render an API-token field or a sign-in
// heading. The console is local-profile only; a token would not help.
function expectNoSignInAffordance(container) {
  expect(screen.queryByLabelText(/API token/i)).toBeNull();
  expect(container.querySelector("#proofbench-token")).toBeNull();
  expect(container.querySelector('input[type="password"]')).toBeNull();
  expect(container.querySelector("form")).toBeNull();
  expect(screen.queryByRole("heading", { name: /Sign in/i })).toBeNull();
  expect(screen.queryByRole("heading", { name: /API token/i })).toBeNull();
  expect(screen.queryByRole("button", { name: /^Sign (in|out)$/i })).toBeNull();
  expect(screen.queryByRole("button", { name: /Restore write access/i })).toBeNull();
}

describe("Shell without a local profile", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    api.listSessions.mockResolvedValue([]);
  });

  afterEach(cleanup);

  it("shows a static notice, never a token field, when bootstrap fails", async () => {
    api.bootstrapAuthSession.mockRejectedValue(new Error("unreachable"));
    const { container } = renderShell();

    expect(await screen.findByRole("heading", { name: "Console unavailable" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Write action" })).toBeNull();
    expectNoSignInAffordance(container);
  });

  it("shows the same notice when the deployment reports authenticated mode", async () => {
    api.bootstrapAuthSession.mockResolvedValue({ localMode: false });
    const { container } = renderShell();

    expect(await screen.findByRole("heading", { name: "Console unavailable" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Write action" })).toBeNull();
    expectNoSignInAffordance(container);
  });

  it("retries without a credential and enters the console once local mode reports", async () => {
    api.bootstrapAuthSession
      .mockRejectedValueOnce(new Error("unreachable"))
      .mockResolvedValueOnce({ localMode: true });
    renderShell();

    fireEvent.click(await screen.findByRole("button", { name: "Retry" }));

    await waitFor(() => expect(api.bootstrapAuthSession).toHaveBeenCalledTimes(2));
    // The retry carries no argument: there is nothing for the browser to send.
    expect(api.bootstrapAuthSession.mock.calls[1]).toEqual([]);
    expect(await screen.findByRole("button", { name: "Write action" })).toBeTruthy();
  });

  it("leaves the console when an auth change event reports a non-local profile", async () => {
    api.bootstrapAuthSession.mockResolvedValue({ localMode: true });
    const { container } = renderShell();
    await screen.findByRole("button", { name: "Write action" });

    fireEvent(
      window,
      new CustomEvent(api.AUTH_CHANGE_EVENT, { detail: { localMode: false } }),
    );

    expect(await screen.findByRole("heading", { name: "Console unavailable" })).toBeTruthy();
    expectNoSignInAffordance(container);
  });
});

describe("Shell local tokenless mode", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    api.listSessions.mockResolvedValue([]);
    api.bootstrapAuthSession.mockResolvedValue({ localMode: true });
  });

  afterEach(cleanup);

  it("enters the console with no token gate and no sign-out control", async () => {
    const { container } = renderShell();

    expect(await screen.findByRole("button", { name: "Write action" })).toBeTruthy();
    expect(screen.getByText("Local operator")).toBeTruthy();
    expectNoSignInAffordance(container);
  });

  it("stays in the console when an auth change event reports local mode", async () => {
    renderShell();
    await screen.findByRole("button", { name: "Write action" });

    fireEvent(
      window,
      new CustomEvent(api.AUTH_CHANGE_EVENT, { detail: { localMode: true } }),
    );

    expect(await screen.findByRole("button", { name: "Write action" })).toBeTruthy();
    expect(screen.queryByLabelText("API token")).toBeNull();
  });

  it("links the shell wordmark back to the landing page", async () => {
    renderShell();
    await screen.findByRole("button", { name: "Write action" });

    const homeLinks = screen.getAllByRole("link", { name: "ProofBench home" });
    expect(homeLinks.length).toBeGreaterThanOrEqual(1);
    for (const link of homeLinks) expect(link.getAttribute("href")).toBe("/");

    fireEvent.click(homeLinks[0]);
    expect(await screen.findByText("Landing destination")).toBeTruthy();
  });
});

describe("Shell main scroll region keyboard access", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    api.listSessions.mockResolvedValue([]);
    api.bootstrapAuthSession.mockResolvedValue({ localMode: true });
  });

  afterEach(cleanup);

  // Regression: axe scrollable-region-focusable failed on Settings, where every
  // control is disabled, leaving the scrollable <main> with no focusable content.
  it("keeps the scrollable main region reachable by keyboard", async () => {
    const { container } = render(
      <MemoryRouter initialEntries={["/app"]}>
        <Routes>
          <Route path="/app" element={<Shell />}>
            <Route index element={<p>Only static, non-focusable content</p>} />
          </Route>
        </Routes>
      </MemoryRouter>
    );
    await screen.findByText("Only static, non-focusable content");

    const main = container.querySelector("#proofbench-main");
    expect(main).toBeTruthy();
    expect(main.tabIndex).toBe(0);

    // The skip link must still land on it.
    const skipLink = screen.getByRole("link", { name: "Skip to main content" });
    expect(skipLink.getAttribute("href")).toBe("#proofbench-main");
    main.focus();
    expect(document.activeElement).toBe(main);
  });
});

describe("Shell third-party attribution", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    api.listSessions.mockResolvedValue([]);
    api.bootstrapAuthSession.mockResolvedValue({ localMode: true });
  });

  afterEach(cleanup);

  // The console renders no third-party integration names at all: no implied
  // endorsement, and no chrome spent on attribution inside the product.
  it("renders no integration attribution in the console shell", async () => {
    const { container } = renderShell();
    await screen.findByRole("button", { name: "Write action" });

    const text = container.textContent;
    expect(text).not.toMatch(/Built with/i);
    for (const name of ["Daytona", "Kimi", "Nosana", "Doubleword", "Oxylabs"]) {
      expect(text).not.toMatch(new RegExp(`\\b${name}\\b`, "i"));
    }
  });

  it("never renders a sponsorship claim", async () => {
    const { container } = renderShell();
    await screen.findByRole("button", { name: "Write action" });

    const text = container.textContent;
    expect(text).not.toMatch(/\bSponsors?\b/i);
    expect(text).not.toMatch(/\bsponsored\b/i);
    expect(text).not.toMatch(/\bbuilt on\b/i);
  });
});
