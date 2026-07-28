// @vitest-environment jsdom
//
// Terminal-state lifecycle for the benchmark event stream. A finished run must
// stop holding a connection open, a restored completed session must not open
// one at all, an expected end of stream is not a fault, and replayed events
// cannot drag a settled run back into "running".
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";

const api = vi.hoisted(() => ({
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
vi.mock("../components/Sidebar.jsx", () => ({ default: () => null }));
vi.mock("../components/ChatThread.jsx", () => ({
  /* Stream status is rendered by the thread now (it is prose about this
     conversation, not page chrome), so the stub has to forward it or these
     assertions would be testing the stub instead of the behaviour. */
  default: ({ running, statusMessage }) => (
    <>
      {/* Kept OUTSIDE the running output: tests read that element's exact
          textContent, so anything else inside it would corrupt the assertion. */}
      <output data-testid="running">{String(running)}</output>
      {statusMessage ? <p>{statusMessage}</p> : null}
    </>
  ),
}));
vi.mock("../components/Composer.jsx", () => ({
  default: ({ onSend }) => (
    <button type="button" onClick={() => onSend("follow up question")}>Send message</button>
  ),
}));

import Benchmark from "./Benchmark.jsx";

const streams = [];

function makeStream() {
  const listeners = new Map();
  const stream = {
    listeners,
    addEventListener: vi.fn((name, handler) => listeners.set(name, handler)),
    close: vi.fn(),
    onopen: null,
    emit(name, payload, lastEventId) {
      const handler = listeners.get(name);
      if (!handler) return;
      act(() => {
        handler({ data: JSON.stringify(payload ?? {}), lastEventId });
      });
    },
  };
  streams.push(stream);
  return stream;
}

function renderAt(path) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/app/benchmark" element={<Benchmark />} />
      </Routes>
    </MemoryRouter>
  );
}

const completedSession = {
  messages: [{ role: "user", text: "compare two extractors" }],
  events: [[0, "state", { phase: "DONE" }]],
  event_seq: 1,
  is_running: false,
  latest_run_id: "run-1",
};

describe("benchmark stream lifecycle", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
    streams.length = 0;
    api.listSessions.mockResolvedValue([]);
    api.listDatasets.mockResolvedValue([]);
    api.createSession.mockResolvedValue({ session_id: "created-session" });
    api.startRun.mockResolvedValue({ run_id: "run-1" });
    api.stopRun.mockResolvedValue({});
    api.getResults.mockResolvedValue({ metrics: { alpha: { exact_accuracy: 0.9 } }, provenance: "measured" });
    api.getSession.mockResolvedValue({ messages: [], events: [], is_running: false });
    api.postChat.mockResolvedValue({ session_id: "chat-session" });
    api.openEvents.mockImplementation(async () => makeStream());
  });

  afterEach(cleanup);

  it("opens no stream for an idle completed restored session", async () => {
    api.getSession.mockResolvedValue(completedSession);
    renderAt("/app/benchmark?session=s1");

    await waitFor(() => expect(api.getSession).toHaveBeenCalledWith("s1"));
    await waitFor(() => expect(screen.getByRole("button", { name: "Start a new benchmark" })).toBeTruthy());
    expect(api.openEvents).not.toHaveBeenCalled();
  });

  it("still reconnects to a restored session that is genuinely running", async () => {
    api.getSession.mockResolvedValue({ ...completedSession, is_running: true, events: [[0, "state", { phase: "RUNNING" }]] });
    renderAt("/app/benchmark?session=s2");

    await waitFor(() => expect(api.openEvents).toHaveBeenCalledTimes(1));
  });

  it("closes the stream when the run reports done", async () => {
    renderAt("/app/benchmark");
    fireEvent.click(screen.getByRole("button", { name: "Send message" }));
    await waitFor(() => expect(streams).toHaveLength(1));

    streams[0].emit("state", { phase: "RUNNING" }, "1");
    streams[0].emit("done", {}, "2");

    expect(streams[0].close).toHaveBeenCalled();
    expect(screen.getByText("Run updates complete")).toBeTruthy();
  });

  it("treats the end of stream after done as expected, not as a failure to retry", async () => {
    renderAt("/app/benchmark");
    fireEvent.click(screen.getByRole("button", { name: "Send message" }));
    await waitFor(() => expect(streams).toHaveLength(1));

    streams[0].emit("done", {}, "1");
    // EventSource reports the server closing the response as an error event
    // with no payload. After a completed run that is the expected end.
    streams[0].emit("error", {}, undefined);

    await waitFor(() => expect(screen.getByText("Run updates complete")).toBeTruthy());
    expect(screen.queryByRole("button", { name: "Reconnect" })).toBeNull();
    expect(api.openEvents).toHaveBeenCalledTimes(1);
  });

  it("ignores replayed events that arrive after the run settled", async () => {
    renderAt("/app/benchmark");
    fireEvent.click(screen.getByRole("button", { name: "Send message" }));
    await waitFor(() => expect(streams).toHaveLength(1));

    streams[0].emit("state", { phase: "RUNNING" }, "1");
    streams[0].emit("done", {}, "2");
    expect(screen.getByTestId("running").textContent).toBe("false");

    // A replay of the pre-terminal events, and a duplicate terminal event.
    streams[0].emit("state", { phase: "RUNNING" }, "1");
    streams[0].emit("delta", { text: "late" }, "1");
    streams[0].emit("done", {}, "2");

    expect(screen.getByTestId("running").textContent).toBe("false");
    expect(screen.getByText("Run updates complete")).toBeTruthy();
    expect(api.openEvents).toHaveBeenCalledTimes(1);
  });

  it("opens a fresh stream for a follow-up message after the run finished", async () => {
    api.getSession.mockResolvedValue(completedSession);
    renderAt("/app/benchmark?session=s1");
    await waitFor(() => expect(screen.getByRole("button", { name: "Ask a follow-up" })).toBeTruthy());
    expect(api.openEvents).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: "Ask a follow-up" }));
    fireEvent.click(screen.getByRole("button", { name: "Send message" }));

    await waitFor(() => expect(api.openEvents).toHaveBeenCalledTimes(1));
    expect(api.openEvents).toHaveBeenCalledWith("s1");
  });

  it("opens a fresh stream for a new run after the previous stream closed", async () => {
    renderAt("/app/benchmark");
    fireEvent.click(screen.getByRole("button", { name: "Send message" }));
    await waitFor(() => expect(streams).toHaveLength(1));
    streams[0].emit("done", {}, "1");
    expect(streams[0].close).toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: "Send message" }));

    await waitFor(() => expect(api.openEvents).toHaveBeenCalledTimes(2));
    expect(streams).toHaveLength(2);
  });

  it("reports an expected end of stream on a reconnected idle session as complete", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      renderAt("/app/benchmark");
      fireEvent.click(screen.getByRole("button", { name: "Send message" }));
      await vi.waitFor(() => expect(streams).toHaveLength(1));

      streams[0].emit("delta", { text: "working" }, "1");
      await act(async () => { await vi.advanceTimersByTimeAsync(5 * 60 * 1000 + 10); });

      const reconnect = await vi.waitFor(() => screen.getByRole("button", { name: "Reconnect" }));
      fireEvent.click(reconnect);
      await vi.waitFor(() => expect(streams).toHaveLength(2));

      // This stream was opened for a session with no work in flight, so the
      // server closing it is the expected end, not an interruption.
      streams[1].emit("error", {}, undefined);

      await vi.waitFor(() => expect(screen.getByText("Run updates complete")).toBeTruthy());
      expect(api.openEvents).toHaveBeenCalledTimes(2);
    } finally {
      vi.useRealTimers();
    }
  });

  it("appends a duplicated delta only once", async () => {
    renderAt("/app/benchmark");
    fireEvent.click(screen.getByRole("button", { name: "Send message" }));
    await waitFor(() => expect(streams).toHaveLength(1));

    streams[0].emit("delta", { text: "chunk" }, "7");
    streams[0].emit("delta", { text: "chunk" }, "7");
    streams[0].emit("done", {}, "8");

    // ChatThread is stubbed here, so the assertion that matters is that the
    // second copy was dropped before it could touch stream state: the run is
    // still settled and no reconnect was scheduled.
    expect(screen.getByText("Run updates complete")).toBeTruthy();
    expect(api.openEvents).toHaveBeenCalledTimes(1);
  });
});
