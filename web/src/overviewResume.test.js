import { describe, expect, it } from "vitest";
import { selectResumeSession } from "./overviewResume.js";

describe("overview resume session", () => {
  it("selects the session used most recently, not the last row returned", () => {
    const latest = {
      id: "latest",
      title: "Latest project",
      created_at: "2026-07-28T05:00:00Z",
      updated_at: "2026-07-28T06:00:00Z",
    };
    const stale = {
      id: "stale",
      title: "Older project",
      created_at: "2026-07-28T05:30:00Z",
      updated_at: "2026-07-28T05:31:00Z",
    };

    expect(selectResumeSession([latest, stale])).toBe(latest);
  });

  it("prioritizes a running session and resolves multiple live rows by activity", () => {
    const idle = { id: "idle", updated_at: "2026-07-28T07:00:00Z" };
    const olderLive = { id: "older-live", is_running: true, updated_at: "2026-07-28T05:00:00Z" };
    const latestLive = { id: "latest-live", is_running: true, updated_at: "2026-07-28T06:00:00Z" };

    expect(selectResumeSession([idle, olderLive, latestLive])).toBe(latestLive);
  });

  it("falls back to creation time for legacy summaries", () => {
    const old = { id: "old", created_at: "2026-07-27T06:00:00Z" };
    const recent = { id: "recent", created_at: "2026-07-28T06:00:00Z" };

    expect(selectResumeSession([recent, old])).toBe(recent);
  });
});
