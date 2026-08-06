// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

const api = vi.hoisted(() => ({
  deleteProviderKey: vi.fn(),
  getHealth: vi.fn(),
  getProviderReadiness: vi.fn(),
  getScraperOrder: vi.fn(),
  saveScraperOrder: vi.fn(),
  getSettingsDefaults: vi.fn(),
  saveSettingsDefaults: vi.fn(),
  listProviderKeys: vi.fn(),
  saveProviderKey: vi.fn(),
  revealProviderKey: vi.fn(),
  fetchBrandLogos: vi.fn(),
  getIntegrationAgentStatus: vi.fn(),
  sendIntegrationAgentMessage: vi.fn(),
  streamIntegrationAgentMessage: vi.fn(),
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
      provider: "openrouter",
      label: "OpenRouter",
      capability: "Optional hosted model routing for candidate tools.",
      essential: false,
      status: "partial",
      required: ["OPENROUTER_BASE_URL", "OPENROUTER_API_KEY", "OPENROUTER_MODEL"],
      missing: ["OPENROUTER_API_KEY"],
      optional_configured: [],
    },
  ],
};

describe("Settings about disclosure", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    api.getHealth.mockResolvedValue({});
    api.getProviderReadiness.mockResolvedValue(READINESS);
    api.getScraperOrder.mockResolvedValue({ order: [], default: [], providers: [] });
    // No runtime brand lookup resolves by default, so a provider without a
    // bundled mark keeps its monogram instead of waiting on the network.
    api.fetchBrandLogos.mockResolvedValue({});
    api.getSettingsDefaults.mockResolvedValue({
      llm: [], scrapers: { order: [], default: [], providers: [] },
    });
    api.listProviderKeys.mockResolvedValue({
      keys: [],
      runtime_writes_enabled: false,
      managed_by: "deployment",
    });
    api.getIntegrationAgentStatus.mockResolvedValue({
      ready: false,
      llm: { configured: false, provider: null },
      scraper: { configured: false, provider: null },
      missing: ["llm", "scraper"],
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

  it("reports readiness as a configuration check that calls no provider", async () => {
    render(<Settings />);

    // The readiness list and the credential list were merged into one "Services"
    // card; its reassurance copy moved with it. The claim is scoped to
    // readiness: resolving an unbundled provider's logo does reach the server,
    // so the copy no longer promises the page contacts nothing at all.
    const heading = await screen.findByText("Services");
    const section = heading.closest("section");
    expect(section.textContent).toMatch(/configuration check/i);
    expect(section.textContent).toMatch(/never calls a provider/i);
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
    expect(within(missing).queryByLabelText("Value for OPENAI_API_KEY")).toBeNull();

    const partial = screen.getByText("OpenRouter").closest("li");
    expect(within(partial).getByText("partly configured")).toBeTruthy();
    fireEvent.click(within(partial).getByRole("button"));
    const partialKey = within(partial).getByText("OPENROUTER_API_KEY").closest("li");
    expect(within(partialKey).getByText("missing")).toBeTruthy();
    // Not essential, so it must not be marked required nor block the run.
    expect(partial.textContent).not.toMatch(/required/);
  });

  it("adds a credential for a provider the built-in list does not cover", async () => {
    api.listProviderKeys.mockResolvedValue({
      keys: [],
      runtime_writes_enabled: true,
      managed_by: "runtime",
    });
    api.saveProviderKey.mockResolvedValue({ env: "MISTRAL_API_KEY", source: "settings" });

    render(<Settings />);
    const services = (await screen.findByText("Services")).closest("section");

    // Collapsed by default so the card stays scannable; the form opens on ask.
    fireEvent.click(within(services).getByRole("button", { name: "Add a service" }));
    fireEvent.change(within(services).getByLabelText("Environment variable name"), {
      target: { value: "mistral_api_key" },
    });
    fireEvent.change(within(services).getByLabelText("Credential value"), {
      target: { value: "mistral-secret-value" },
    });
    fireEvent.click(within(services).getByRole("button", { name: "Add" }));

    // The name is uppercased for the backend; the value is posted, not echoed.
    await waitFor(() =>
      expect(api.saveProviderKey).toHaveBeenCalledWith("MISTRAL_API_KEY", "mistral-secret-value"),
    );
    expect(services.textContent).not.toContain("mistral-secret-value");
  });

  it("hides the add-a-service control when runtime writes are disabled", async () => {
    api.listProviderKeys.mockResolvedValue({
      keys: [],
      runtime_writes_enabled: false,
      managed_by: "deployment",
    });
    render(<Settings />);
    await screen.findByText("Services");
    expect(screen.queryByRole("button", { name: "Add a service" })).toBeNull();
  });

  it("saves a missing value inline and refreshes readiness without echoing it", async () => {
    const configured = {
      ...READINESS,
      run_ready: true,
      blocked_by: [],
      providers: READINESS.providers.map((provider) =>
        provider.provider === "openai"
          ? { ...provider, status: "ready", missing: [] }
          : provider
      ),
    };
    api.listProviderKeys
      .mockResolvedValueOnce({
        keys: [],
        runtime_writes_enabled: true,
        managed_by: "runtime",
      })
      .mockResolvedValueOnce({
        keys: [{
          env: "OPENAI_API_KEY", source: "settings", secret: true,
          masked: "••••alue", revealable: true,
        }],
        runtime_writes_enabled: true,
        managed_by: "runtime",
      });
    api.getProviderReadiness
      .mockResolvedValueOnce(READINESS)
      .mockResolvedValueOnce(configured);
    api.saveProviderKey.mockResolvedValue({ env: "OPENAI_API_KEY", source: "settings" });

    const { container } = render(<Settings />);
    const row = (await screen.findByText("OpenAI")).closest("li");
    fireEvent.click(within(row).getByRole("button", { name: /OpenAI/ }));
    // Editing is now a deliberate act: the field opens from the row's own
    // Add control rather than being present the moment the row is expanded.
    fireEvent.click(within(row).getByRole("button", { name: "Add" }));
    const input = within(row).getByLabelText("Value for OPENAI_API_KEY");
    fireEvent.change(input, { target: { value: "local-provider-value" } });
    fireEvent.click(within(row).getByRole("button", { name: "Save" }));

    await waitFor(() => {
      expect(api.saveProviderKey).toHaveBeenCalledWith(
        "OPENAI_API_KEY",
        "local-provider-value"
      );
      expect(api.getProviderReadiness).toHaveBeenCalledTimes(2);
      expect(api.listProviderKeys).toHaveBeenCalledTimes(2);
    });
    // The row now reports the server's mask rather than the word "set", and the
    // editor closes. Either way the typed value is never rendered back.
    expect(within(row).getByText("••••alue")).toBeTruthy();
    expect(within(row).queryByLabelText("Value for OPENAI_API_KEY")).toBeNull();
    expect(container.textContent).not.toContain("local-provider-value");
  });

  it("reveals a stored key only when asked, and hides it again", async () => {
    api.listProviderKeys.mockResolvedValue({
      keys: [{
        env: "DAYTONA_API_KEY", source: "system", secret: true,
        masked: "••••9f21", revealable: true,
      }],
      runtime_writes_enabled: true,
      managed_by: "runtime",
    });
    api.revealProviderKey.mockResolvedValue({
      env: "DAYTONA_API_KEY", source: "system", value: "daytona-real-secret-9f21",
    });

    render(<Settings />);
    const row = (await screen.findByText("Daytona sandboxes")).closest("li");
    fireEvent.click(within(row).getByRole("button", { name: /Daytona sandboxes/ }));

    // Masked until asked: nothing is fetched just by opening the row.
    expect(within(row).getByText("••••9f21")).toBeTruthy();
    expect(api.revealProviderKey).not.toHaveBeenCalled();

    fireEvent.click(within(row).getByRole("button", { name: "Reveal DAYTONA_API_KEY" }));
    expect(await within(row).findByText("daytona-real-secret-9f21")).toBeTruthy();
    expect(api.revealProviderKey).toHaveBeenCalledWith("DAYTONA_API_KEY");

    // Hiding drops the value rather than leaving it parked in the DOM.
    fireEvent.click(within(row).getByRole("button", { name: "Hide DAYTONA_API_KEY" }));
    expect(within(row).queryByText("daytona-real-secret-9f21")).toBeNull();
    expect(within(row).getByText("••••9f21")).toBeTruthy();
  });

  it("keeps an inline value available for retry when saving fails", async () => {
    api.listProviderKeys.mockResolvedValue({
      keys: [],
      runtime_writes_enabled: true,
      managed_by: "runtime",
    });
    api.saveProviderKey.mockRejectedValue(new Error("Could not save this provider credential."));

    render(<Settings />);
    const row = (await screen.findByText("OpenAI")).closest("li");
    fireEvent.click(within(row).getByRole("button", { name: /OpenAI/ }));
    fireEvent.click(within(row).getByRole("button", { name: "Add" }));
    const input = within(row).getByLabelText("Value for OPENAI_API_KEY");
    fireEvent.change(input, { target: { value: "retry-provider-value" } });
    fireEvent.click(within(row).getByRole("button", { name: "Save" }));

    expect((await within(row).findByRole("alert")).textContent).toContain(
      "Could not save this provider credential."
    );
    expect(input.value).toBe("retry-provider-value");
    expect(api.getProviderReadiness).toHaveBeenCalledTimes(1);
  });

  it("renders locally bundled brand marks for known providers", async () => {
    const { container } = render(<Settings />);
    await screen.findByText("Daytona sandboxes");

    for (const provider of ["daytona", "openai", "openrouter"]) {
      const logo = container.querySelector(`[data-provider-logo="${provider}"]`);
      expect(logo).toBeTruthy();
      expect(logo.getAttribute("src")).toMatch(/^(?:data:image\/svg\+xml|.*\.svg$)/);
      expect(logo.getAttribute("alt")).toBe("");
    }
  });

  it("falls back to a monogram for an unknown future provider", async () => {
    api.getProviderReadiness.mockResolvedValue({
      mode: "real",
      run_ready: true,
      blocked_by: [],
      providers: [
        {
          provider: "future_service",
          label: "Future Service",
          capability: "A future provider capability.",
          essential: false,
          status: "ready",
          required: [],
          missing: [],
          optional_configured: [],
        },
      ],
    });

    const { container } = render(<Settings />);
    const row = (await screen.findByText("Future Service")).closest("li");
    expect(row.textContent).toContain("FS");
    expect(container.querySelector('[data-provider-logo="future_service"]')).toBeNull();
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

  it("places the integration agent beside the settings without dropping any of them", async () => {
    const { container } = render(<Settings />);

    expect(await screen.findByText("Integration agent")).toBeTruthy();
    for (const heading of [
      "Services",
      "Documentation sources",
      "Appearance",
      "About this deployment",
    ]) {
      expect(screen.getByText(heading)).toBeTruthy();
    }
    // Credentials are edited on the service they belong to now, so the separate
    // "Runtime credentials" card is gone rather than duplicating that list.
    expect(screen.queryByText("Runtime credentials")).toBeNull();
    const settingsScroll = container.querySelector("[data-settings-scroll-region]");
    expect(settingsScroll).toBeTruthy();
    expect(settingsScroll.className).toContain("xl:overflow-y-auto");

    const agent = screen.getByText("Integration agent").closest("section");
    expect(agent.parentElement.className).toContain("xl:h-full");
    expect(agent.className).not.toContain("xl:sticky");
    expect(agent.parentElement.className).not.toContain("xl:sticky");
    expect(api.getIntegrationAgentStatus).toHaveBeenCalledTimes(1);
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
