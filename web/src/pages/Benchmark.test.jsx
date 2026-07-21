// @vitest-environment jsdom
import React, { StrictMode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";

const api = vi.hoisted(() => ({
  // Benchmark.jsx imports this module constant, so the mock must export it too.
  // It is the only mode the client can request.
  RUN_MODE: "real",
  postChat: vi.fn(),
  uploadDataset: vi.fn(),
  startRun: vi.fn(),
  stopRun: vi.fn(),
  openEvents: vi.fn(),
  listSessions: vi.fn(),
  createSession: vi.fn(),
  deleteSession: vi.fn(),
  getSession: vi.fn(),
  getResults: vi.fn(),
  listDatasets: vi.fn(),
}));

vi.mock("../api.js", () => api);
vi.mock("../components/Sidebar.jsx", () => ({
  default: ({ onClose }) => (
    <div>
      <button type="button">First session action</button>
      <button type="button" onClick={onClose}>Last session action</button>
    </div>
  ),
}));
vi.mock("../components/ChatThread.jsx", () => ({ default: () => null }));
vi.mock("../components/Composer.jsx", () => ({
  default: ({ onSend, onUpload, dataset }) => (
    <div>
      <button type="button" onClick={() => { onSend("hello"); onSend("hello"); }}>Rapid double send</button>
      <button type="button" onClick={() => onUpload({ useSynthetic: true })}>Upload once</button>
      <output>{dataset?.id || "no-dataset"}</output>
    </div>
  ),
}));

import Benchmark, { buildRunSpec } from "./Benchmark.jsx";

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((res, rej) => { resolve = res; reject = rej; });
  return { promise, resolve, reject };
}

function renderBenchmark(strict = false) {
  const view = (
    <MemoryRouter initialEntries={["/app/benchmark"]}>
      <Routes>
        <Route path="/app/benchmark" element={<Benchmark />} />
      </Routes>
    </MemoryRouter>
  );
  return render(strict ? <StrictMode>{view}</StrictMode> : view);
}

describe("Benchmark async reentrancy", () => {
  const streams = [];

  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
    streams.length = 0;
    api.listSessions.mockResolvedValue([]);
    api.listDatasets.mockResolvedValue([]);
    api.createSession.mockResolvedValue({ session_id: "created-session" });
    api.stopRun.mockResolvedValue({});
    api.startRun.mockResolvedValue({ run_id: "run-1" });
    api.getResults.mockResolvedValue(null);
    api.getSession.mockResolvedValue({ messages: [], events: [], mode: "demo", is_running: false });
    api.openEvents.mockImplementation(async () => {
      const stream = { addEventListener: vi.fn(), close: vi.fn(), onopen: null };
      streams.push(stream);
      return stream;
    });
  });

  afterEach(cleanup);

  it("accepts only one rapid chat submit and does not close its live stream", async () => {
    api.postChat
      .mockResolvedValueOnce({ session_id: "chat-session" })
      .mockImplementationOnce(() => new Promise(() => {}));
    renderBenchmark();

    fireEvent.click(screen.getByRole("button", { name: "Rapid double send" }));
    await waitFor(() => expect(api.postChat).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(streams).toHaveLength(1));

    fireEvent.click(screen.getByRole("button", { name: "Rapid double send" }));
    expect(api.postChat).toHaveBeenCalledTimes(2);
    expect(streams[0].close).not.toHaveBeenCalled();
  });

  it("commits an upload result after the StrictMode setup-cleanup-setup cycle", async () => {
    const upload = deferred();
    api.uploadDataset.mockReturnValue(upload.promise);
    renderBenchmark(true);

    fireEvent.click(screen.getByRole("button", { name: "Upload once" }));
    expect(api.uploadDataset).toHaveBeenCalledTimes(1);
    upload.resolve({ dataset_id: "strict-upload" });

    await waitFor(() => expect(screen.getByText("strict-upload")).toBeTruthy());
  });

  it("contains focus in the modal sessions drawer and restores it on Escape", async () => {
    renderBenchmark();
    const trigger = screen.getByRole("button", { name: /Sessions/ });
    fireEvent.click(trigger);

    const dialog = screen.getByRole("dialog", { name: "Benchmark sessions" });
    expect(dialog.getAttribute("aria-modal")).toBe("true");
    const first = screen.getByRole("button", { name: "First session action" });
    const last = screen.getByRole("button", { name: "Last session action" });
    await waitFor(() => expect(document.activeElement).toBe(first));

    last.focus();
    fireEvent.keyDown(document, { key: "Tab" });
    expect(document.activeElement).toBe(first);
    fireEvent.keyDown(document, { key: "Escape" });
    await waitFor(() => expect(screen.queryByRole("dialog", { name: "Benchmark sessions" })).toBeNull());
    expect(document.activeElement).toBe(trigger);
  });
});

describe("benchmark request construction", () => {
  it("does not attach the selected extraction dataset to a tool assessment", () => {
    const spec = {
      benchmark_type: "tool_assessment",
      category: "cloud_platforms",
      objective: "Compare hosting platforms",
      candidates: [],
    };

    expect(buildRunSpec(spec, { id: "sample-dataset" })).toBe(spec);
  });

  it("attaches the selected server dataset to extraction runs", () => {
    const spec = {
      benchmark_type: "extraction",
      category: "invoice_ocr",
      fields: ["invoice_number", "date", "vendor", "total"],
      candidates: [],
    };

    expect(buildRunSpec(spec, { id: "sample-dataset" })).toEqual({
      ...spec,
      dataset: { dataset_id: "sample-dataset" },
    });
  });
});
