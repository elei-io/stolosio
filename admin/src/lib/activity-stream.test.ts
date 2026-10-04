import { describe, expect, it, vi } from "vitest"
import { mergeEvents, subscribeActivityEvents } from "@/lib/activity-stream"
import type { ActivityEvent } from "@/types/api"

class FakeEventSource extends EventTarget {
  static latest: FakeEventSource
  onopen: (() => void) | null = null
  onerror: (() => void) | null = null
  close = vi.fn()
  readonly url: string
  constructor(url: string) {
    super()
    this.url = url
    FakeEventSource.latest = this
  }
}

function callbacks() {
  return {
    status: vi.fn(),
    event: vi.fn(),
    refetchHistory: vi.fn().mockResolvedValue(undefined),
    restart: vi.fn(),
  }
}

function event(id: string, time: string): ActivityEvent {
  return {
    event_id: id,
    occurred_at: time,
    event_type: "capture.completed",
    event_family: "capture",
    schema_version: 1,
    session_id: "session",
    attempt_id: null,
    provider: null,
    outcome: "success",
    payload: {},
  }
}

describe("activity streaming", () => {
  it("refetches history before restarting after replay becomes unavailable", async () => {
    vi.stubGlobal("EventSource", FakeEventSource)
    let resolve!: () => void
    const handlers = callbacks()
    handlers.refetchHistory.mockImplementation(
      () =>
        new Promise<void>((done) => {
          resolve = done
        })
    )
    const dispose = subscribeActivityEvents("?outcome=failure", handlers)
    const source = FakeEventSource.latest
    expect(source.url).toBe("/v1/admin/events/stream?outcome=failure")
    source.onopen?.()
    source.onerror?.()
    expect(handlers.status.mock.calls.map(([status]) => status)).toEqual([
      "connecting",
      "live",
      "reconnecting",
    ])
    source.dispatchEvent(new Event("replay-unavailable"))
    expect(source.close).toHaveBeenCalled()
    expect(handlers.restart).not.toHaveBeenCalled()
    resolve()
    await vi.waitFor(() => expect(handlers.restart).toHaveBeenCalledOnce())
    dispose()
  })

  it("does not restart a disposed filter subscription", async () => {
    vi.stubGlobal("EventSource", FakeEventSource)
    const handlers = callbacks()
    const dispose = subscribeActivityEvents("", handlers)
    FakeEventSource.latest.dispatchEvent(new Event("replay-unavailable"))
    dispose()
    await Promise.resolve()
    expect(handlers.restart).not.toHaveBeenCalled()
  })

  it("ignores malformed messages and bounds/deduplicates ordered events", () => {
    vi.stubGlobal("EventSource", FakeEventSource)
    const handlers = callbacks()
    const dispose = subscribeActivityEvents("", handlers)
    const first = event("a", "2026-10-04T00:00:00Z")
    const last = event("b", "2026-10-04T00:00:01Z")
    FakeEventSource.latest.dispatchEvent(
      new MessageEvent("stolosio-event", { data: "invalid json" })
    )
    FakeEventSource.latest.dispatchEvent(
      new MessageEvent("stolosio-event", { data: JSON.stringify(first) })
    )
    expect(handlers.event).toHaveBeenCalledExactlyOnceWith(first)
    expect(mergeEvents([last], [first, last], 1)).toEqual([last])
    dispose()
  })
})
