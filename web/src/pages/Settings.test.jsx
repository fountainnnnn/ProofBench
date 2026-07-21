// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";

const api = vi.hoisted(() => ({
  deleteProviderKey: vi.fn(),
  getHealth: vi.fn(),
  getProviderReadiness: vi.fn(),
  listProviderKeys: vi.fn(),
  saveProviderKey: vi.fn(),
}));

vi.mock("../api.js", () => api);

import Settings from "./Settings.jsx";

// Mirrors the GET /api/providers contract in server/main.py: a pure
// configuration check that reports environment variable NAMES and a status,
// and never any credential value.
const READINESS = {
  mode: "real",
  run_ready: false,
  blocked_by: ["openai"],
  providers: [
    {
      provider: "daytona",
      label: "Daytona sandboxes",
      capability: "Runs candidate tools in an isolated sandbox.",
      essential: true,
      status: "ready",
      required: ["DAYTONA_API_KEY"],
      missing: [],
      optional_configured: [],
    },
    {
      provider: "openai",
      label: "OpenAI",
      capability: "Plans benchmark specifications and grades outputs.",
      essential: true,
      status: "missing",
      required: ["OPENAI_API_KEY"],
      missing: ["OPENAI_API_KEY"],
      optional_configured: [],
    },
    {
      provider: "nosana_vlm",
      label: "Nosana VLM",
      capability: "Optional built-in nosana_vlm candidate.",
      essential: false,
      status: "partial",
      required: ["NOSANA_BASE_URL", "NOSANA_API_KEY", "NOSANA_MODEL"],
      missing: ["NOSANA_API_KEY"],
      optional_configured: [],
    },
  ],
};

describe("Settings about disclosure", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    api.getHealth.mockResolvedValue({});
    api.getProviderReadiness.mockResolvedValue(READINESS);
    api.listProviderKeys.mockResolvedValue({
      keys: [],
      runtime_writes_enabled: false,
      managed_by: "deployment",
    });
  });

  afterEach(cleanup);

  it("states the proprietary license and that dependencies keep their own terms", async () => {
    render(<Settings />);

    const licensing = (await screen.findByText("Licensing")).closest("div");
    expect(licensing.textContent).toMatch(/proprietary software/i);
    expect(licensing.textContent).toMatch(/all rights are reserved/i);
    expect(licensing.textContent).toMatch(/prior written permission/i);
    expect(licensing.textContent).toMatch(
      /third-party dependencies are not covered/i
    );
  });

  it("describes the local deployment without implying a public service", async () => {
    render(<Settings />);

    const deployment = (await screen.findByText("Deployment")).closest("div");
    expect(deployment.textContent).toMatch(/local instance/i);
    expect(deployment.textContent).toMatch(
      /no public or hosted ProofBench service/i
    );
  });

  it("discloses proprietary pre-release status and that visibility is not permission", async () => {
    render(<Settings />);

    const status = (await screen.findByText("Status")).closest("div");
    expect(status.textContent).toMatch(/proprietary/i);
    expect(status.textContent).toMatch(/pre-release/i);
    expect(status.textContent).toMatch(/not a hosted or supported product/i);
    expect(status.textContent).toMatch(/not permission to use it/i);
  });

  it("does not link to repository documents the app cannot serve", async () => {
    const { container } = render(<Settings />);
    await screen.findByText("Licensing");

    expect(container.querySelector('a[href$="CONTRACTS.md"]')).toBeNull();
    expect(container.querySelector('a[href$=".md"]')).toBeNull();
  });

  it("makes no availability, support, privacy, or terms commitment", async () => {
    render(<Settings />);

    const commitment = (await screen.findByText("No service commitment")).closest("div");
    expect(commitment.textContent).toMatch(
      /no availability, support, or response commitment/i
    );
    expect(commitment.textContent).toMatch(
      /no privacy notice or terms of service have\s+been published/i
    );
  });

  it("reports readiness as a configuration check that contacts no provider", async () => {
    render(<Settings />);

    // The readiness list and the credential list were merged into one "Services"
    // card; its reassurance copy moved with it.
    const heading = await screen.findByText("Services");
    const section = heading.closest("section");
    expect(section.textContent).toMatch(/Configuration check only/i);
    expect(section.textContent).toMatch(/does not contact any provider/i);
    expect(api.getProviderReadiness).toHaveBeenCalledTimes(1);
  });

  it("names the blocking provider when real benchmarks are not runnable", async () => {
    render(<Settings />);

    // The run-ready banner is now a full-width element above the Services card,
    // so it is found by its copy rather than scoped to the readiness section.
    const banner = await screen.findByText(
      /Real benchmarks are blocked until these are configured: openai\./
    );
    expect(banner.textContent).not.toMatch(/Ready to run real benchmarks/);
  });

  it("shows ready, partial, and missing provider states with their required env names", async () => {
    render(<Settings />);

    const ready = (await screen.findByText("Daytona sandboxes")).closest("li");
    expect(within(ready).getByText("ready")).toBeTruthy();
    expect(within(ready).getByText("required")).toBeTruthy();

    // The header line stays visible; the per-service env-var breakdown now lives
    // behind each row's disclosure, so expand it before asserting the env names.
    const missing = screen.getByText("OpenAI").closest("li");
    expect(within(missing).getByText("not configured")).toBeTruthy();
    fireEvent.click(within(missing).getByRole("button"));
    expect(within(missing).getByText("OPENAI_API_KEY")).toBeTruthy();
    expect(within(missing).getByText("missing")).toBeTruthy();

    const partial = screen.getByText("Nosana VLM").closest("li");
    expect(within(partial).getByText("partly configured")).toBeTruthy();
    fireEvent.click(within(partial).getByRole("button"));
    const nosanaKey = within(partial).getByText("NOSANA_API_KEY").closest("li");
    expect(within(nosanaKey).getByText("missing")).toBeTruthy();
    // Not essential, so it must not be marked required nor block the run.
    expect(partial.textContent).not.toMatch(/required/);
  });

  it("renders readiness without exposing any credential value", async () => {
    const { container } = render(<Settings />);
    await screen.findByText("Daytona sandboxes");

    const text = container.textContent;
    // Only environment variable names may appear, never a value assigned to one.
    expect(text).not.toMatch(/(API_KEY|BASE_URL|MODEL)\s*[:=]\s*\S/);
    expect(text).not.toMatch(/\b(sk|pb|dtn)-[A-Za-z0-9_-]{8,}/);
    expect(text).not.toMatch(/Bearer\s+\S/);
  });

  it("degrades to an unavailable notice when readiness cannot be loaded", async () => {
    api.getProviderReadiness.mockRejectedValue(new Error("Could not load provider readiness."));
    render(<Settings />);

    expect(await screen.findByText(/Provider readiness is unavailable right now/)).toBeTruthy();
    expect(screen.queryByText("OpenAI")).toBeNull();
  });

  it("does not fabricate a company, domain, or support contact", async () => {
    const { container } = render(<Settings />);
    await screen.findByText("Licensing");

    const text = container.textContent;
    expect(text).not.toMatch(/\b(Inc|LLC|Ltd|GmbH|Pty|Corp)\b/);
    expect(text).not.toMatch(/[\w.+-]+@[\w-]+\.[a-z]{2,}/i);
    expect(text).not.toMatch(/https?:\/\/(?!localhost)/i);
  });
});
