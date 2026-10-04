import type { ActivityEvent } from "@/types/api"

export type StreamStatus =
  "connecting" | "live" | "reconnecting" | "unavailable"

export function subscribeActivityEvents(
  querySuffix: string,
  callbacks: {
    status: (status: StreamStatus) => void
    event: (event: ActivityEvent) => void
    refetchHistory: () => Promise<unknown>
    restart: () => void
  }
) {
  const source = new EventSource(`/v1/admin/events/stream${querySuffix}`)
  let disposed = false
  callbacks.status("connecting")
  source.onopen = () => {
    if (!disposed) callbacks.status("live")
  }
  source.onerror = () => {
    if (!disposed) callbacks.status("reconnecting")
  }
  source.addEventListener("stolosio-event", (message) => {
    if (disposed) return
    let event: unknown
    try {
      event = JSON.parse((message as MessageEvent<string>).data)
    } catch {
      return
    }
    if (isActivityEvent(event)) callbacks.event(event)
  })
  source.addEventListener("stream-error", () => {
    if (disposed) return
    callbacks.status("unavailable")
    source.close()
  })
  source.addEventListener("replay-unavailable", () => {
    source.close()
    void callbacks
      .refetchHistory()
      .then(() => {
        if (!disposed) callbacks.restart()
      })
      .catch(() => {
        if (!disposed) callbacks.status("unavailable")
      })
  })
  return () => {
    disposed = true
    source.close()
  }
}

export function isActivityEvent(value: unknown): value is ActivityEvent {
  return (
    typeof value === "object" &&
    value !== null &&
    typeof (value as Partial<ActivityEvent>).event_id === "string" &&
    typeof (value as Partial<ActivityEvent>).event_type === "string" &&
    typeof (value as Partial<ActivityEvent>).occurred_at === "string"
  )
}

export function mergeEvents(
  current: ActivityEvent[],
  incoming: ActivityEvent[],
  limit?: number
): ActivityEvent[] {
  const events = new Map(
    current
      .filter(isActivityEvent)
      .map((event) => [event.event_id, event] as const)
  )
  for (const event of incoming) {
    if (!isActivityEvent(event)) continue
    events.set(event.event_id, event)
  }
  const merged = [...events.values()].sort(
    (left, right) =>
      new Date(left.occurred_at).getTime() -
        new Date(right.occurred_at).getTime() ||
      left.event_id.localeCompare(right.event_id)
  )
  return limit === undefined ? merged : merged.slice(-limit)
}
