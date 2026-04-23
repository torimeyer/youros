import { describe, it, expect, beforeEach, vi } from "vitest";
import { clientTrace, setTraceId, getTraceId } from "./trace";

describe("clientTrace", () => {
  beforeEach(() => {
    setTraceId("");
    vi.restoreAllMocks();
    // Force the fetch path by removing sendBeacon.
    // @ts-expect-error navigator in jsdom
    globalThis.navigator = { sendBeacon: undefined };
  });

  it("POSTs to /api/trace/client with event and fields", () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(null, { status: 204 }));
    // @ts-expect-error override global fetch
    globalThis.fetch = fetchMock;

    setTraceId("abc-123");
    clientTrace("ui.clicked", { button: "save" });

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/trace/client");
    expect(init.method).toBe("POST");
    const body = JSON.parse(init.body);
    expect(body.event).toBe("ui.clicked");
    expect(body.trace_id).toBe("abc-123");
    expect(body.button).toBe("save");
  });

  it("swallows fetch errors", () => {
    const fetchMock = vi.fn().mockRejectedValue(new Error("network"));
    // @ts-expect-error override
    globalThis.fetch = fetchMock;
    expect(() => clientTrace("x", {})).not.toThrow();
  });

  it("setTraceId + getTraceId roundtrip", () => {
    setTraceId("trace-xyz");
    expect(getTraceId()).toBe("trace-xyz");
    setTraceId("");
    expect(getTraceId()).toBe("");
  });
});
