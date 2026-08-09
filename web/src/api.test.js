// @vitest-environment jsdom
import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  bootstrapAuthSession,
  createAuthSession,
  getAuthToken,
  isLocalMode,
  logoutAuthSession,
  deleteDataset,
  listDatasets,
  listSessions,
  openEvents,
  prepareReportPdf,
  fetchBrandLogos,
  saveProviderKey,
  startRun,
} from "./api.js";

function localSessionResponse() {
  return new Response(
    JSON.stringify({ auth_mode: "local", cookie_authenticated: true, write_authenticated: true }),
    { status: 200, headers: { "Content-Type": "application/json" } },
  );
}

describe("API authentication", () => {
  beforeEach(async () => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
    sessionStorage.clear();
    localStorage.clear();
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("{}", { status: 200 })));
    await logoutAuthSession();
    fetch.mockClear();
  });

  it("exposes an in-memory credential flow without persisting the token", async () => {
    expect(createAuthSession).toBeTypeOf("function");
    expect(logoutAuthSession).toBeTypeOf("function");
    expect(getAuthToken()).toBe("");
  });

  it("never attaches an Authorization header to an API request", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(localSessionResponse())
      .mockResolvedValueOnce(new Response("[]", {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }));
    vi.stubGlobal("fetch", fetchMock);

    await bootstrapAuthSession();
    await listSessions();

    const options = fetchMock.mock.calls[1][1];
    expect(options.credentials).toBe("include");
    expect(options.headers.has("Authorization")).toBe(false);
  });

  it("holds no credential anywhere the browser can read it", async () => {
    sessionStorage.setItem("proofbench.authToken", "stored-secret");
    localStorage.setItem("access_token", "persisted-secret");
    const fetchMock = vi.fn().mockResolvedValue(new Response("[]", { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);

    await listSessions();

    // A pre-seeded storage entry is not a credential the client will ever use.
    expect(fetchMock.mock.calls[0][1].headers.has("Authorization")).toBe(false);
  });

  it("keeps an authenticated token in memory, attaches it to writes, and clears it on logout", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify({ ok: true }), {
        status: 200, headers: { "Content-Type": "application/json" },
      }))
      .mockResolvedValueOnce(new Response("[]", {
        status: 200, headers: { "Content-Type": "application/json" },
      }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ ok: true }), {
        status: 200, headers: { "Content-Type": "application/json" },
      }));
    vi.stubGlobal("fetch", fetchMock);

    await createAuthSession("client-secret");
    expect(getAuthToken()).toBe("client-secret");
    expect(fetchMock.mock.calls[0][1].headers.Authorization).toBe("Bearer client-secret");
    expect(localStorage.length).toBe(0);
    expect(sessionStorage.length).toBe(0);

    await listSessions();
    expect(fetchMock.mock.calls[1][1].headers.get("Authorization")).toBe("Bearer client-secret");

    await logoutAuthSession();
    expect(fetchMock.mock.calls[2][1].method).toBe("DELETE");
    expect(getAuthToken()).toBe("");
  });

  it("reports an authenticated deployment and whether its read cookie survived", async () => {
    const authEvents = [];
    window.addEventListener("proofbench-auth-change", (event) => authEvents.push(event.detail), { once: true });
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(
      JSON.stringify({ auth_mode: "authenticated", cookie_authenticated: true, write_authenticated: false }),
      { status: 200, headers: { "Content-Type": "application/json" } },
    )));

    await expect(bootstrapAuthSession()).resolves.toEqual({
      authMode: "authenticated", localMode: false,
      cookieAuthenticated: true, writeAuthenticated: false,
    });
    expect(isLocalMode()).toBe(false);
    expect(authEvents).toEqual([{
      authMode: "authenticated", localMode: false,
      cookieAuthenticated: true, writeAuthenticated: false,
    }]);
  });

  it("accepts auth_mode local and holds no credential", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(localSessionResponse()));

    await expect(bootstrapAuthSession()).resolves.toMatchObject({ authMode: "local", localMode: true });
    expect(isLocalMode()).toBe(true);
    // There is no credential in the local profile, so nothing is held anywhere.
    expect(sessionStorage.length).toBe(0);
    expect(localStorage.length).toBe(0);
  });

  it("bootstraps with a credential-free read and never posts a session", async () => {
    const fetchMock = vi.fn().mockResolvedValue(localSessionResponse());
    vi.stubGlobal("fetch", fetchMock);

    await bootstrapAuthSession();

    expect(fetchMock).toHaveBeenCalledOnce();
    expect(fetchMock.mock.calls[0][0]).toContain("/api/auth/session");
    expect(fetchMock.mock.calls[0][1].method).toBeUndefined();
    expect(fetchMock.mock.calls[0][1].headers).toBeUndefined();
  });

  it("sends no Authorization placeholder on local-mode API requests", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response(
        JSON.stringify({ auth_mode: "local", cookie_authenticated: true, write_authenticated: true }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      ))
      .mockResolvedValueOnce(new Response("[]", {
        status: 200, headers: { "Content-Type": "application/json" },
      }));
    vi.stubGlobal("fetch", fetchMock);

    await bootstrapAuthSession();
    await listSessions();

    const options = fetchMock.mock.calls[1][1];
    expect(options.headers.has("Authorization")).toBe(false);
    // The cookie is still sent so native EventSource keeps working.
    expect(options.credentials).toBe("include");
  });

  it("treats an unreported auth mode as non-local so the gate fails closed", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(
      JSON.stringify({ cookie_authenticated: false, write_authenticated: false }),
      { status: 200, headers: { "Content-Type": "application/json" } },
    )));

    await expect(bootstrapAuthSession()).rejects.toThrow("invalid authentication profile");
    expect(isLocalMode()).toBe(false);
  });

  it("fails closed when the auth-mode probe is unreachable", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("{}", { status: 503 })));

    await expect(bootstrapAuthSession()).rejects.toThrow("could not verify");
    expect(isLocalMode()).toBe(false);
  });

  it("drops local mode when a request comes back unauthorised", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(localSessionResponse()));
    await bootstrapAuthSession();
    expect(isLocalMode()).toBe(true);

    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("{}", { status: 401 })));
    await expect(listSessions()).rejects.toThrow();
    expect(isLocalMode()).toBe(false);
  });

  it("does not expose credential endpoint details", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({
      detail: "API_KEY=leaked-value",
    }), { status: 500, headers: { "Content-Type": "application/json" } }));
    vi.stubGlobal("fetch", fetchMock);

    await expect(saveProviderKey("VENDOR_API_KEY", "submitted-secret"))
      .rejects.toThrow("Could not save this provider credential.");
    expect(fetchMock.mock.calls[0][1].headers.has("Authorization")).toBe(false);
  });

  it("refreshes the cookie before constructing an event stream", async () => {
    const order = [];
    vi.stubGlobal("fetch", vi.fn().mockImplementation(async () => {
      order.push("refresh");
      return localSessionResponse();
    }));
    class FakeEventSource {
      constructor(url, options) {
        order.push("event-source");
        this.url = url;
        this.options = options;
      }
    }
    vi.stubGlobal("EventSource", FakeEventSource);

    const source = await openEvents("session one");

    expect(order).toEqual(["refresh", "event-source"]);
    expect(source.url).toContain("/api/sessions/session%20one/events");
    expect(source.options).toEqual({ withCredentials: true });
  });

  it("does not construct an event stream when cookie refresh fails", async () => {
    const EventSourceMock = vi.fn();
    vi.stubGlobal("EventSource", EventSourceMock);
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("{}", { status: 401 })));

    await expect(openEvents("session-1")).rejects.toThrow("could not verify");
    expect(EventSourceMock).not.toHaveBeenCalled();
  });

  it("refreshes PDF access before returning the immutable run URL", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(localSessionResponse()));

    await expect(prepareReportPdf("run/1", true))
      .resolves.toContain("/api/runs/run%2F1/report.pdf?download=true");
    expect(fetch).toHaveBeenCalledOnce();
    expect(fetch.mock.calls[0][0]).toContain("/api/auth/session");
  });

  it("uses server dataset and immutable run contracts", async () => {
    const responses = [
      new Response(JSON.stringify({ datasets: [{ id: "dataset-1" }] }), { status: 200 }),
      new Response(JSON.stringify({ dataset_id: "dataset-1", deleted: true }), { status: 200 }),
      new Response(JSON.stringify({ session_id: "session-1", run_id: "run-9", status: "started" }), { status: 200 }),
    ];
    const fetchMock = vi.fn().mockImplementation(async () => responses.shift());
    vi.stubGlobal("fetch", fetchMock);

    await expect(listDatasets()).resolves.toEqual([{ id: "dataset-1" }]);
    await expect(deleteDataset("dataset/1")).resolves.toMatchObject({ deleted: true });
    await expect(startRun("session/1", { candidates: [] }, "real"))
      .resolves.toMatchObject({ run_id: "run-9" });

    expect(fetchMock.mock.calls[1][0]).toContain("/api/datasets/dataset%2F1");
    expect(fetchMock.mock.calls[1][1].method).toBe("DELETE");
    expect(fetchMock.mock.calls[2][0]).toContain("/api/sessions/session%2F1/run");
  });

  it("batches brand resolution so every requested name reaches the capped endpoint", async () => {
    const names = Array.from({ length: 50 }, (_, index) => `tool_${index}`);
    const fetchMock = vi.fn().mockImplementation(async (url) => {
      const query = new URL(url, "http://local.test").searchParams.get("names");
      const batch = query.split(",");
      return new Response(JSON.stringify({
        logos: Object.fromEntries(batch.map((name) => [name, `data:image/png;base64,${name}`])),
      }), { status: 200, headers: { "Content-Type": "application/json" } });
    });
    vi.stubGlobal("fetch", fetchMock);

    const logos = await fetchBrandLogos(names);

    expect(fetchMock).toHaveBeenCalledTimes(3);
    expect(fetchMock.mock.calls.map(([url]) =>
      new URL(url, "http://local.test").searchParams.get("names").split(",").length
    )).toEqual([24, 24, 2]);
    expect(Object.keys(logos)).toHaveLength(50);
    expect(logos.tool_49).toContain("tool_49");
  });
});
