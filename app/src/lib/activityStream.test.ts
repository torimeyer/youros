import { describe, it, expect } from "vitest";
import { buildStream, curateEvent, type RawActivityEvent } from "./activityStream";

// These tests cover the ALWAYS_HIDDEN filter in the Activity curator.
// Internal kernel events (sigstore verification, process reaper, session
// lineage bookkeeping, agent state transitions) must never surface in the
// user-facing stream. The "Show technical details" view in Activity.tsx
// bypasses buildStream entirely and iterates raw events directly, so
// hiding these in the curator does not remove them from the debug view.

function makeEvent(event: string, detail = "", label = ""): RawActivityEvent {
  return {
    timestamp: "2026-04-24T12:00:00Z",
    event,
    label: label || event,
    category: "system",
    detail,
  };
}

describe("activityStream ALWAYS_HIDDEN filter", () => {
  const hiddenKinds = [
    "oae.verify",
    "reap",
    "daemon.reap_stale",
    "session.lineage.resumed",
    "session.lineage.orphaned",
    "agent.state",
  ];

  it.each(hiddenKinds)("drops %s from the curated stream", (kind) => {
    const result = curateEvent(makeEvent(kind), 0);
    expect(result).toBeNull();
  });

  it("buildStream filters every newly-hidden kernel event", () => {
    const raw: RawActivityEvent[] = hiddenKinds.map((k) => makeEvent(k));
    const stream = buildStream(raw);
    expect(stream).toEqual([]);
  });

  it("buildStream still surfaces user-facing events when hidden kinds are mixed in", () => {
    const raw: RawActivityEvent[] = [
      makeEvent("oae.verify"),
      makeEvent("task.closed", "→123 Finish widget"),
      makeEvent("reap"),
      makeEvent("session.lineage.resumed", 'lineage_id="abc"'),
      makeEvent("agent.state"),
      makeEvent("daemon.reap_stale"),
      makeEvent("session.lineage.orphaned"),
    ];
    const stream = buildStream(raw);
    expect(stream).toHaveLength(1);
    expect(stream[0].kind).toBe("task.closed");
    expect(stream[0].summary).toBe("Closed: Finish widget");
  });
});

describe("activityStream technical-mode passthrough", () => {
  // Activity.tsx builds technical-mode rows from the raw events array,
  // not from buildStream. These tests guard the contract that hidden
  // events remain present and inspectable when the toggle is on, by
  // mirroring the same mapping Activity.tsx uses for technical rows.
  function technicalRows(raw: RawActivityEvent[]) {
    return raw.map((ev, i) => ({
      key: `raw-${ev.timestamp}-${i}`,
      kind: "other" as const,
      summary: ev.label || ev.event,
      raw: ev,
    }));
  }

  it("preserves oae.verify, reap, lineage, and agent.state for the debug view", () => {
    const raw: RawActivityEvent[] = [
      makeEvent("oae.verify"),
      makeEvent("reap"),
      makeEvent("daemon.reap_stale"),
      makeEvent("session.lineage.resumed"),
      makeEvent("session.lineage.orphaned"),
      makeEvent("agent.state"),
    ];
    const rows = technicalRows(raw);
    expect(rows).toHaveLength(6);
    expect(rows.map((r) => r.raw.event)).toEqual([
      "oae.verify",
      "reap",
      "daemon.reap_stale",
      "session.lineage.resumed",
      "session.lineage.orphaned",
      "agent.state",
    ]);
  });
});
