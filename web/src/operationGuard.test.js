import { describe, expect, it, vi } from "vitest";
import { acquireOperation, releaseOperation } from "./operationGuard.js";

describe("operation guards", () => {
  it("rejects a rapid duplicate without closing the legitimate stream", () => {
    const busyRef = { current: false };
    const liveStream = { close: vi.fn() };

    expect(acquireOperation(busyRef)).toBe(true);
    expect(acquireOperation(busyRef)).toBe(false);
    expect(liveStream.close).not.toHaveBeenCalled();

    releaseOperation(busyRef);
    expect(acquireOperation(busyRef)).toBe(true);
  });
});
