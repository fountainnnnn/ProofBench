// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

const api = vi.hoisted(() => ({
  getIntegrationAgentStatus: vi.fn(),
  sendIntegrationAgentMessage: vi.fn(),
  streamIntegrationAgentMessage: vi.fn(),
  saveProviderKey: vi.fn(),
}));

vi.mock("../api.js", () => api);

import IntegrationAgentPanel from "./IntegrationAgentPanel.jsx";

const BLOCKED = {
  ready: false,
  llm: { configured: false, provider: null },
  scraper: { configured: true, provider: "oxylabs" },
  missing: ["llm"],
};

const READY = {
  ready: true,
  llm: { configured: true, provider: "deepseek" },
  scraper: { configured: true, provider: "oxylabs" },
  missing: [],
};

describe("Integration agent panel", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  afterEach(cleanup);

  it("blocks the composer and names the prerequisites until the server reports ready", async () => {
    api.getIntegrationAgentStatus.mockResolvedValue(BLOCKED);

    render(<IntegrationAgentPanel />);

    expect(
      await screen.findByText(
        /One default LLM and one web scraping API must be configured first/i,
      ),
    ).toBeTruthy();

    const composer = screen.getByLabelText("Message the integration agent");
    expect(composer.disabled).toBe(true);
    expect(screen.getByRole("button", { name: "Send" }).disabled).toBe(true);

    // The unmet requirement is named; the met one is not dressed up as an alert.
    expect(screen.getByText("not configured")).toBeTruthy();
    expect(screen.getByText("oxylabs")).toBeTruthy();

    // Nothing is sent while blocked, even if a key press gets through.
    fireEvent.change(composer, { target: { value: "Add a provider" } });
    fireEvent.keyDown(composer, { key: "Enter" });
    expect(api.streamIntegrationAgentMessage).not.toHaveBeenCalled();
  });

  it("explains what the agent does before any turn exists", async () => {
    api.getIntegrationAgentStatus.mockResolvedValue(READY);

    render(<IntegrationAgentPanel />);

    // The panel says what the agent will do on its own and what it will not,
    // because "set it for me" is now a real offer and the limit has to be as
    // legible as the capability.
    expect(
      await screen.findByText(/can set a key, a model, or the scraper order for you/i),
    ).toBeTruthy();
    expect(screen.getByText(/activating it stays your call/i)).toBeTruthy();
    expect(screen.getByText(/Ask about a provider, or set one up/i)).toBeTruthy();
    // Prompt starters give the empty state something to act on immediately:
    // one question, and one instruction that the agent carries out itself.
    expect(screen.getByRole("button", { name: "What are the model options for Doubleword?" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Try Oxylabs before Scrape.do" })).toBeTruthy();
    // Credentials are the Services card's job, not this panel's.
    expect(screen.queryByLabelText(/API key/i)).toBeNull();
  });

  it("sends on Enter and renders the reply with its sources and validation result", async () => {
    api.getIntegrationAgentStatus.mockResolvedValue(READY);
    api.streamIntegrationAgentMessage.mockResolvedValue({
      message: "Read the public API reference and drafted a connector.",
      sources: [{ title: "Extraction API reference", url: "https://docs.example.com/api" }],
      implementation: { status: "validated", summary: "Connector passed a dry run." },
    });

    render(<IntegrationAgentPanel />);
    const composer = await screen.findByLabelText("Message the integration agent");
    await waitFor(() => expect(composer.disabled).toBe(false));

    fireEvent.change(composer, { target: { value: "Add a hosted extraction API" } });
    fireEvent.keyDown(composer, { key: "Enter" });

    await waitFor(() => {
      expect(api.streamIntegrationAgentMessage).toHaveBeenCalledWith(
        "Add a hosted extraction API",
        [],
        expect.any(Function),
      );
    });

    // The question stays in the log; the composer clears for the next one.
    expect(await screen.findByText("Add a hosted extraction API")).toBeTruthy();
    expect(composer.value).toBe("");

    expect(
      await screen.findByText("Read the public API reference and drafted a connector."),
    ).toBeTruthy();
    // The machine word is shown to a person, so it is capitalized for reading.
    expect(screen.getByText("Validated")).toBeTruthy();
    expect(screen.getByText("Connector passed a dry run.")).toBeTruthy();

    const source = screen.getByRole("link", { name: "Extraction API reference" });
    expect(source.getAttribute("href")).toBe("https://docs.example.com/api");
    expect(source.getAttribute("rel")).toContain("noopener");

    // The reply arrives asynchronously, so it must be announced, not just drawn.
    expect(screen.getByRole("log").getAttribute("aria-live")).toBe("polite");
  });

  it("narrates the research while it runs and retires it once the turn lands", async () => {
    api.getIntegrationAgentStatus.mockResolvedValue(READY);
    let emit;
    api.streamIntegrationAgentMessage.mockImplementation(
      (_message, _history, onProgress) =>
        new Promise((resolve) => {
          emit = (event) => onProgress(event);
          resolve.done = resolve;
          setTimeout(() => {
            onProgress({ phase: "search", query: "Groq official API documentation" });
            onProgress({ phase: "read", title: "Groq docs", url: "https://console.groq.com/docs" });
            resolve({
              message: "Groq is OpenAI compatible.",
              sources: [],
              implementation: { status: "proposal", summary: "Configure a base URL." },
            });
          }, 0);
        }),
    );

    render(<IntegrationAgentPanel />);
    const composer = await screen.findByLabelText("Message the integration agent");
    await waitFor(() => expect(composer.disabled).toBe(false));

    fireEvent.change(composer, { target: { value: "Check if Groq is supported" } });
    fireEvent.keyDown(composer, { key: "Enter" });

    // The answer replaces the narration rather than sitting beneath it.
    expect(await screen.findByText("Groq is OpenAI compatible.")).toBeTruthy();
    expect(screen.queryByText(/Reading Groq docs/i)).toBeNull();
    expect(screen.queryByText(/Searching for/i)).toBeNull();
    expect(emit).toBeTruthy();
  });

  it("renders a fenced code block as code rather than literal backticks", async () => {
    api.getIntegrationAgentStatus.mockResolvedValue(READY);
    api.streamIntegrationAgentMessage.mockResolvedValue({
      message: 'Use this config:\n\n```json\n{\n  "provider": "groq"\n}\n```\n\nNo custom code needed.',
      sources: [],
      implementation: { status: "proposal", summary: "Configuration only." },
    });

    render(<IntegrationAgentPanel />);
    const composer = await screen.findByLabelText("Message the integration agent");
    await waitFor(() => expect(composer.disabled).toBe(false));
    fireEvent.change(composer, { target: { value: "Add Groq" } });
    fireEvent.keyDown(composer, { key: "Enter" });

    // The prose either side of the fence survives, and the fence itself does
    // not leak its backticks or its language tag into the readable text.
    expect(await screen.findByText("Use this config:")).toBeTruthy();
    expect(screen.getByText("No custom code needed.")).toBeTruthy();
    const code = document.querySelector("pre");
    expect(code).toBeTruthy();
    expect(code.textContent).toContain('"provider": "groq"');
    expect(code.textContent).not.toContain("```");
    expect(document.body.textContent).not.toContain("```json");
  });

  it("keeps a newline on Shift plus Enter instead of sending", async () => {
    api.getIntegrationAgentStatus.mockResolvedValue(READY);

    render(<IntegrationAgentPanel />);
    const composer = await screen.findByLabelText("Message the integration agent");
    await waitFor(() => expect(composer.disabled).toBe(false));

    fireEvent.change(composer, { target: { value: "First line" } });
    fireEvent.keyDown(composer, { key: "Enter", shiftKey: true });

    expect(api.streamIntegrationAgentMessage).not.toHaveBeenCalled();
    expect(composer.value).toBe("First line");
  });

  it("does not send an empty or whitespace-only message", async () => {
    api.getIntegrationAgentStatus.mockResolvedValue(READY);

    render(<IntegrationAgentPanel />);
    const composer = await screen.findByLabelText("Message the integration agent");
    await waitFor(() => expect(composer.disabled).toBe(false));

    fireEvent.change(composer, { target: { value: "   " } });
    expect(screen.getByRole("button", { name: "Send" }).disabled).toBe(true);
    fireEvent.keyDown(composer, { key: "Enter" });
    expect(api.streamIntegrationAgentMessage).not.toHaveBeenCalled();
  });

  it("reports a failed turn as an alert and keeps the panel usable", async () => {
    api.getIntegrationAgentStatus.mockResolvedValue(READY);
    api.streamIntegrationAgentMessage.mockRejectedValue(
      new Error("The integration agent could not answer that."),
    );

    render(<IntegrationAgentPanel />);
    const composer = await screen.findByLabelText("Message the integration agent");
    await waitFor(() => expect(composer.disabled).toBe(false));

    fireEvent.change(composer, { target: { value: "Add a provider" } });
    fireEvent.keyDown(composer, { key: "Enter" });

    expect((await screen.findByRole("alert")).textContent).toContain(
      "The integration agent could not answer that.",
    );
    await waitFor(() => expect(composer.disabled).toBe(false));
  });

  it("degrades to a quiet notice when the status call fails", async () => {
    api.getIntegrationAgentStatus.mockRejectedValue(new Error("nope"));

    render(<IntegrationAgentPanel />);

    expect(
      await screen.findByText(/The integration agent is unavailable right now/i),
    ).toBeTruthy();
    expect(screen.getByLabelText("Message the integration agent").disabled).toBe(true);
  });

  it("saves a pasted key under the variable name the agent resolved", async () => {
    api.getIntegrationAgentStatus.mockResolvedValue(READY);
    api.streamIntegrationAgentMessage.mockResolvedValue({
      message: "Mistral is OpenAI-compatible.",
      sources: [],
      implementation: { status: "proposal", summary: "Config-only connector." },
      credential: { env: "MISTRAL_API_KEY", label: "Mistral" },
    });
    api.saveProviderKey.mockResolvedValue({ env: "MISTRAL_API_KEY" });
    const onCredentialSaved = vi.fn();

    render(<IntegrationAgentPanel onCredentialSaved={onCredentialSaved} />);
    const composer = await screen.findByLabelText("Message the integration agent");
    fireEvent.change(composer, { target: { value: "Add support for Mistral" } });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));

    // The operator never types the variable name; the agent supplies it.
    const field = await screen.findByLabelText("Mistral API key");
    fireEvent.change(field, { target: { value: "mistral-secret-value" } });
    fireEvent.click(screen.getByRole("button", { name: "Save key" }));

    await waitFor(() =>
      expect(api.saveProviderKey).toHaveBeenCalledWith("MISTRAL_API_KEY", "mistral-secret-value"),
    );
    await waitFor(() => expect(onCredentialSaved).toHaveBeenCalled());
    // The key itself must not survive anywhere in the rendered transcript.
    expect(document.body.textContent).not.toContain("mistral-secret-value");
    // And it was never handed to the agent.
    const [, history] = api.streamIntegrationAgentMessage.mock.calls[0];
    expect(JSON.stringify(history)).not.toContain("mistral-secret-value");
  });

  it("offers no key field when the agent named no credential", async () => {
    api.getIntegrationAgentStatus.mockResolvedValue(READY);
    api.streamIntegrationAgentMessage.mockResolvedValue({
      message: "Groq is already supported.",
      sources: [],
      implementation: { status: "proposal", summary: "No change needed." },
    });

    render(<IntegrationAgentPanel />);
    const composer = await screen.findByLabelText("Message the integration agent");
    fireEvent.change(composer, { target: { value: "Check if Groq is supported" } });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));

    await screen.findByText("Groq is already supported.");
    expect(screen.queryByRole("button", { name: "Save key" })).toBeNull();
  });

  it("logs each step the agent took, naming any setting it changed", async () => {
    api.getIntegrationAgentStatus.mockResolvedValue(READY);
    let emit;
    api.streamIntegrationAgentMessage.mockImplementation((message, history, onEvent) => {
      emit = onEvent;
      return new Promise(() => {});
    });

    render(<IntegrationAgentPanel />);
    const composer = await screen.findByLabelText("Message the integration agent");
    fireEvent.change(composer, { target: { value: "add this key to scrape.do" } });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));

    await waitFor(() => expect(emit).toBeTruthy());

    emit({ phase: "thinking" });
    expect(await screen.findByText("Working out what to do\u2026")).toBeTruthy();

    // Reading its own configuration is a step worth showing: it is why the
    // agent can answer without searching at all.
    emit({ phase: "state" });
    expect(await screen.findByText("Checking how this deployment is configured")).toBeTruthy();

    // A search names what it is actually looking for.
    emit({ phase: "search", query: "Mistral API documentation" });
    expect(await screen.findByText("Searching for Mistral API documentation")).toBeTruthy();

    // A write is the step an operator must not miss, so it names the setting.
    emit({ phase: "wrote", env: "SCRAPEDO_API_TOKEN" });
    expect(await screen.findByText("Updated SCRAPEDO_API_TOKEN")).toBeTruthy();
  });

  it("refreshes the rest of settings once the agent has changed something", async () => {
    api.getIntegrationAgentStatus.mockResolvedValue(READY);
    api.streamIntegrationAgentMessage.mockResolvedValue({
      message: "Saved your Scrape.do key.",
      sources: [],
      implementation: { status: "applied", summary: "Saved SCRAPEDO_API_TOKEN" },
    });
    const onCredentialSaved = vi.fn();

    render(<IntegrationAgentPanel onCredentialSaved={onCredentialSaved} />);
    const composer = await screen.findByLabelText("Message the integration agent");
    fireEvent.change(composer, { target: { value: "add this key to scrape.do" } });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));

    await screen.findByText("Saved your Scrape.do key.");
    // Services and readiness go stale the moment the agent writes, so the panel
    // triggers the same refresh the paste-a-key field does.
    await waitFor(() => expect(onCredentialSaved).toHaveBeenCalled());
    expect(screen.getByText("Applied")).toBeTruthy();
  });

  it("renders a markdown answer as markup rather than literal syntax", async () => {
    api.getIntegrationAgentStatus.mockResolvedValue(READY);
    api.streamIntegrationAgentMessage.mockResolvedValue({
      message: "### LLM providers\n- **openai**: credential `OPENAI_API_KEY` (configured)",
      sources: [],
      implementation: { status: "answer", summary: "Listed what ships." },
    });

    const { container } = render(<IntegrationAgentPanel />);
    const composer = await screen.findByLabelText("Message the integration agent");
    fireEvent.change(composer, { target: { value: "what is implemented" } });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));

    // The heading, the emphasis, and the code span become elements. Before
    // this the reply printed its own ###, ** and backticks at the reader.
    expect(await screen.findByText("LLM providers")).toBeTruthy();
    expect(container.querySelector("li strong")).toBeTruthy();
    expect(container.querySelector("li code").textContent).toBe("OPENAI_API_KEY");
    expect(container.textContent).not.toContain("###");
    expect(container.textContent).not.toContain("**openai**");

    // A direct answer carries no proposal verdict block.
    expect(screen.queryByText("Answer")).toBeNull();
  });
});
