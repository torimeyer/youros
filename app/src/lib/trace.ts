/**
 * Frontend trace helper.
 *
 * Emits fire-and-forget POSTs to /api/trace/client so browser-side
 * events can join the same audit stream as backend events. Failures
 * are swallowed. Never throws.
 */

let currentTraceId = "";

/** Set the trace ID to attach to subsequent clientTrace() calls. */
export function setTraceId(id: string): void {
  currentTraceId = id || "";
}

/** Get the current trace ID. */
export function getTraceId(): string {
  return currentTraceId;
}

/**
 * Emit a client-side trace event. Fire-and-forget: the returned
 * promise is not awaited, and network/JSON errors are swallowed.
 */
export function clientTrace(
  eventName: string,
  fields: Record<string, unknown> = {}
): void {
  try {
    const body = JSON.stringify({
      event: eventName,
      trace_id: currentTraceId,
      ...fields,
    });
    // Prefer sendBeacon when available for reliability on unload.
    if (typeof navigator !== "undefined" && navigator.sendBeacon) {
      try {
        const blob = new Blob([body], { type: "application/json" });
        if (navigator.sendBeacon("/api/trace/client", blob)) return;
      } catch {
        // fall through to fetch
      }
    }
    void fetch("/api/trace/client", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body,
      keepalive: true,
    }).catch(() => {
      /* swallow */
    });
  } catch {
    /* swallow */
  }
}
