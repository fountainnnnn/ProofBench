// @vitest-environment jsdom
//
// Routing and lifecycle for the direction confirmation card.
//
// The card is a question put to the user, not a record of work done, so it must
// reach the chat surface and never the trace log. And it must not outlive its
// answer: a card re-offered after the user already settled the direction asks
// them to decide something they have decided.
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
// The thread is stubbed down to the one thing these tests need to see: how many
// trace rows it was handed. A direction artifact reaching that list is the
// routing bug this file exists to catch.
vi.mock("../components/ChatThread.jsx", () => ({
  default: ({ trace }) => (
    <output data-testid="trace">{JSON.stringify((trace || []).map((t) => t.kind))}</output>
  ),
}));
vi.mock("../components/Composer.jsx", () => ({
  default: ({ onSend }) => (
    <button type="button" onClick={() => onSend("typed by hand")}>Send message</button>
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

const DIRECTION = {
  kind: "direction",
  improved_prompt: "Find a self-hosted retrieval platform for internal documents.",
};

function renderAt(path) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/app/benchmark" element={<Benchmark />} />
      </Routes>
    </MemoryRouter>
  );
}

describe("direction confirmation", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
    streams.length = 0;
    api.listSessions.mockResolvedValue([]);
    api.listDatasets.mockResolvedValue([]);
    api.createSession.mockResolvedValue({ session_id: "created-session" });
    api.getSession.mockResolvedValue({ messages: [], events: [], is_running: false });
    api.postChat.mockResolvedValue({ session_id: "chat-session" });
    api.openEvents.mockImplementation(async () => makeStream());
  });

  afterEach(cleanup);

  async function openGatedSession() {
    renderAt("/app/benchmark");
    fireEvent.click(screen.getByRole("button", { name: "Send message" }));
    await waitFor(() => expect(streams).toHaveLength(1));
    streams[0].emit("artifact", DIRECTION, "1");
    await waitFor(() => expect(screen.getByText(DIRECTION.improved_prompt)).toBeTruthy());
  }

  it("renders the card on the chat surface, never as a trace row", async () => {
    await openGatedSession();

    expect(screen.getByText("Is this what you mean?")).toBeTruthy();
    expect(screen.getByTestId("trace").textContent).toBe("[]");
  });

  it("still records ordinary trace artifacts alongside it", async () => {
    await openGatedSession();
    streams[0].emit("artifact", { kind: "trace", tool: "prompt_brief" }, "2");

    await waitFor(() =>
      expect(screen.getByTestId("trace").textContent).toBe(JSON.stringify(["trace"])));
    // And the card is untouched by a trace arriving after it.
    expect(screen.getByText("Is this what you mean?")).toBeTruthy();
  });

  it("sends the confirmed direction as an ordinary message", async () => {
    await openGatedSession();

    fireEvent.click(screen.getByRole("button", { name: "Yes" }));

    // Call 0 is the opening message that triggered the gate; the confirmation
    // is the one after it.
    await waitFor(() => expect(api.postChat).toHaveBeenCalledTimes(2));
    const [text] = api.postChat.mock.calls[1];
    expect(text).toContain(DIRECTION.improved_prompt);
    // Answered: the card comes down rather than waiting to be asked again.
    await waitFor(() => expect(screen.queryByText("Is this what you mean?")).toBeNull());
  });

  it("comes down when the user types their own message instead", async () => {
    await openGatedSession();

    fireEvent.click(screen.getByRole("button", { name: "Send message" }));

    await waitFor(() => expect(screen.queryByText("Is this what you mean?")).toBeNull());
  });

  it("sends a correction as an ordinary message when the user answers no", async () => {
    await openGatedSession();

    fireEvent.click(screen.getByRole("button", { name: "No" }));
    fireEvent.change(screen.getByRole("textbox"), {
      target: { value: "Compare hosted OCR APIs instead." },
    });
    fireEvent.click(screen.getByRole("button", { name: "Send correction" }));

    await waitFor(() => expect(api.postChat).toHaveBeenCalledTimes(2));
    const [text] = api.postChat.mock.calls[1];
    expect(text).toContain("Compare hosted OCR APIs instead.");
    expect(text).toContain(DIRECTION.improved_prompt);
    // Answered: the card comes down rather than waiting to be asked again.
    await waitFor(() => expect(screen.queryByText("Is this what you mean?")).toBeNull());
  });

  it("is not re-offered on a restored session that already answered it", async () => {
    api.getSession.mockResolvedValue({
      messages: [
        { role: "user", text: "something vague" },
        { role: "assistant", text: "Confirm or correct the direction above." },
        { role: "user", text: "Proceed with this direction: ..." },
        { role: "assistant", text: "Here are four candidates." },
      ],
      events: [[0, "artifact", DIRECTION]],
      event_seq: 1,
      is_running: false,
    });

    renderAt("/app/benchmark?session=s9");

    await waitFor(() => expect(api.getSession).toHaveBeenCalledWith("s9"));
    expect(screen.queryByText("Is this what you mean?")).toBeNull();
  });

  it("is restored for a session that was left waiting on it", async () => {
    api.getSession.mockResolvedValue({
      messages: [
        { role: "user", text: "something vague" },
        { role: "assistant", text: "Confirm or correct the direction above." },
      ],
      events: [[0, "artifact", DIRECTION]],
      event_seq: 1,
      is_running: false,
    });

    renderAt("/app/benchmark?session=s10");

    await waitFor(() => expect(screen.getByText(DIRECTION.improved_prompt)).toBeTruthy());
  });
});
