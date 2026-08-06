// @vitest-environment jsdom
//
// Reordering providers is a preference, so it saves on click and redraws
// immediately. Demoting one must never read as disabling it: every provider
// holding credentials stays in the chain, because a search that returns nothing
// ends an intake turn with no candidates at all.
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

const api = vi.hoisted(() => ({
  getScraperOrder: vi.fn(),
  saveScraperOrder: vi.fn(),
  getSettingsDefaults: vi.fn(),
  saveSettingsDefaults: vi.fn(),
  listProviderKeys: vi.fn(),
  saveProviderKey: vi.fn(),
  revealProviderKey: vi.fn(),
  deleteProviderKey: vi.fn(),
  fetchBrandLogos: vi.fn(),
  getProviderReadiness: vi.fn(),
  getIntegrationAgentStatus: vi.fn(),
  sendIntegrationAgentMessage: vi.fn(),
  streamIntegrationAgentMessage: vi.fn(),
}));
vi.mock("../api.js", () => api);

import Settings from "./Settings.jsx";

const ORDER = {
  order: ["scrapedo", "oxylabs", "brightdata"],
  default: ["scrapedo", "oxylabs", "brightdata"],
  providers: [
    { name: "scrapedo", label: "Scrape.do", configured: true },
    { name: "oxylabs", label: "Oxylabs", configured: true },
    { name: "brightdata", label: "Bright Data", configured: false },
  ],
};

beforeEach(() => {
  api.getScraperOrder.mockResolvedValue(ORDER);
  api.saveScraperOrder.mockImplementation(async (order) => ({ order }));
  api.fetchBrandLogos.mockResolvedValue({});
  api.getSettingsDefaults.mockResolvedValue({
    llm: [], scrapers: { order: [], default: [], providers: [] },
  });
  api.listProviderKeys.mockResolvedValue({ keys: [], runtime_writes_enabled: false });
  api.getProviderReadiness.mockResolvedValue({ services: [] });
  api.getIntegrationAgentStatus.mockResolvedValue({
    ready: false,
    llm: { configured: false, provider: null },
    scraper: { configured: true, provider: "oxylabs" },
    missing: ["llm"],
  });
});
afterEach(cleanup);

const rows = () => screen.getAllByRole("listitem")
  .filter((li) => /Scrape\.do|Oxylabs|Bright Data/.test(li.textContent))
  .map((li) => li.textContent);

describe("documentation source order", () => {
  it("lists the providers in the order they will be tried", async () => {
    render(<Settings />);
    await waitFor(() => expect(rows().length).toBe(3));

    expect(rows()[0]).toContain("Scrape.do");
    expect(rows()[2]).toContain("Bright Data");
  });

  it("says when a provider in the chain cannot actually answer", async () => {
    render(<Settings />);
    // Otherwise a provider that is first in line but has no credentials is
    // invisible until a benchmark runs slowly.
    expect(await screen.findByText(/No credentials configured/)).toBeTruthy();
  });

  it("saves the new order when a provider is promoted", async () => {
    render(<Settings />);
    await waitFor(() => expect(rows().length).toBe(3));

    await act(async () => { fireEvent.click(screen.getByLabelText("Move Oxylabs earlier")); });

    await waitFor(() =>
      expect(api.saveScraperOrder).toHaveBeenCalledWith(["oxylabs", "scrapedo", "brightdata"]));
    expect(rows()[0]).toContain("Oxylabs");
  });

  it("cannot promote the first provider or demote the last", async () => {
    render(<Settings />);
    await waitFor(() => expect(rows().length).toBe(3));

    expect(screen.getByLabelText("Move Scrape.do earlier").disabled).toBe(true);
    expect(screen.getByLabelText("Move Bright Data later").disabled).toBe(true);
  });

  it("restores the previous order when saving fails", async () => {
    api.saveScraperOrder.mockRejectedValue(new Error("offline"));
    render(<Settings />);
    await waitFor(() => expect(rows().length).toBe(3));

    await act(async () => { fireEvent.click(screen.getByLabelText("Move Oxylabs earlier")); });

    // The list must not keep showing an order the server never accepted.
    await waitFor(() => expect(rows()[0]).toContain("Scrape.do"));
    expect(screen.getByText(/Could not save the order/)).toBeTruthy();
  });
});
