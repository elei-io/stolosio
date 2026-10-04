import { Copy, ExternalLink, X } from "lucide-react"
import { useEffect } from "react"

import { Button } from "@/components/ui/button"
import { cn } from "@/lib/utils"
import type { ActivityEvent } from "@/types/api"

import {
  eventDetail,
  eventTone,
  formatDateTime,
} from "@/lib/activity-presentation"

function CommandSummaryDetails({ event }: { event: ActivityEvent }) {
  const methods = event.payload.methods
  if (!methods || typeof methods !== "object" || Array.isArray(methods)) {
    return null
  }
  const rows = Object.entries(methods)
    .flatMap(([method, value]) => {
      if (!value || typeof value !== "object" || Array.isArray(value)) return []
      const usage = value as Record<string, unknown>
      return [
        {
          method,
          count: typeof usage.count === "number" ? usage.count : 0,
          failures:
            typeof usage.failed_count === "number" ? usage.failed_count : 0,
          duration:
            typeof usage.duration_ms === "number" ? usage.duration_ms : 0,
        },
      ]
    })
    .sort((left, right) => right.count - left.count)

  return (
    <section className="mt-6">
      <h3 className="text-xs font-semibold tracking-wider text-muted-foreground uppercase">
        Command methods
      </h3>
      <div className="mt-2 max-h-72 overflow-auto rounded-lg border">
        {rows.map((row) => (
          <div
            key={row.method}
            className="grid grid-cols-[minmax(0,1fr)_3.5rem_3.5rem_4.5rem] gap-2 border-b px-3 py-2 font-mono text-xs last:border-b-0"
          >
            <span className="truncate" title={row.method}>
              {row.method}
            </span>
            <span className="text-right text-muted-foreground">
              {row.count}×
            </span>
            <span
              className={cn(
                "text-right text-muted-foreground",
                row.failures > 0 && "text-destructive"
              )}
            >
              {row.failures} err
            </span>
            <span className="text-right text-muted-foreground">
              {row.duration} ms
            </span>
          </div>
        ))}
      </div>
    </section>
  )
}

export function EventDetails({
  event,
  close,
  navigate,
}: {
  event: ActivityEvent
  close: () => void
  navigate: (href: string) => void
}) {
  useEffect(() => {
    const onKeyDown = (keyboardEvent: KeyboardEvent) => {
      if (keyboardEvent.key === "Escape") close()
    }
    window.addEventListener("keydown", onKeyDown)
    return () => window.removeEventListener("keydown", onKeyDown)
  }, [close])

  return (
    <div className="fixed inset-0 z-50">
      <button
        type="button"
        className="absolute inset-0 h-full w-full bg-black/45"
        aria-label="Close event details"
        onClick={close}
      />
      <aside
        role="dialog"
        aria-modal="true"
        aria-labelledby="activity-event-title"
        className="absolute inset-y-0 right-0 flex w-full max-w-xl flex-col border-l bg-background shadow-2xl"
      >
        <header className="flex items-start justify-between gap-4 border-b px-5 py-4">
          <div className="min-w-0">
            <p className="font-mono text-xs text-muted-foreground">
              {formatDateTime(event.occurred_at)}
            </p>
            <h2
              id="activity-event-title"
              className="mt-1 truncate font-mono text-lg font-semibold"
            >
              {event.event_type}
            </h2>
          </div>
          <Button
            variant="ghost"
            size="icon"
            aria-label="Close event details"
            onClick={close}
          >
            <X aria-hidden />
          </Button>
        </header>

        <div className="min-h-0 flex-1 overflow-y-auto px-5 py-5">
          <p className={cn("font-mono text-sm", eventTone(event))}>
            {eventDetail(event)}
          </p>

          <dl className="mt-6 grid grid-cols-[7rem_minmax(0,1fr)] gap-x-3 gap-y-3 text-sm">
            <dt className="text-muted-foreground">Provider</dt>
            <dd className="font-mono">{event.provider ?? "Stolosio"}</dd>
            <dt className="text-muted-foreground">Outcome</dt>
            <dd className="font-mono">{event.outcome ?? "observation"}</dd>
            <dt className="text-muted-foreground">Session</dt>
            <dd className="flex min-w-0 items-center gap-2">
              <button
                type="button"
                className="truncate font-mono text-primary hover:underline"
                onClick={() =>
                  navigate(
                    `/${event.event_family === "capture" ? "captures" : "sessions"}/${event.session_id}`
                  )
                }
              >
                {event.session_id}
              </button>
              <ExternalLink className="size-3 text-muted-foreground" />
            </dd>
            <dt className="text-muted-foreground">Attempt</dt>
            <dd className="truncate font-mono">
              {event.attempt_id ?? "Not applicable"}
            </dd>
            <dt className="text-muted-foreground">Event ID</dt>
            <dd className="truncate font-mono">{event.event_id}</dd>
          </dl>

          <CommandSummaryDetails event={event} />

          <section className="mt-6">
            <div className="flex items-center justify-between gap-3">
              <h3 className="text-xs font-semibold tracking-wider text-muted-foreground uppercase">
                Sanitized payload
              </h3>
              <Button
                variant="ghost"
                size="xs"
                onClick={() =>
                  void navigator.clipboard.writeText(
                    JSON.stringify(event.payload, null, 2)
                  )
                }
              >
                <Copy aria-hidden />
                Copy
              </Button>
            </div>
            <pre className="mt-2 overflow-x-auto rounded-lg border bg-muted/40 p-3 font-mono text-xs leading-5 whitespace-pre-wrap">
              {JSON.stringify(event.payload, null, 2)}
            </pre>
          </section>
        </div>
      </aside>
    </div>
  )
}
