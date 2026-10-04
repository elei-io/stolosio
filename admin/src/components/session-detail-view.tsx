import { providerLabels } from "@/lib/session-format"
import { useInfiniteQuery, useQuery } from "@tanstack/react-query"
import {
  ArrowLeft,
  Clock3,
  Coins,
  LoaderCircle,
  Settings2,
  XCircle,
} from "lucide-react"

import { CaptureDetailView } from "@/components/captures-page"
import { number } from "@/lib/observability"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { apiRequest, extractApiError } from "@/lib/api"
import { eventTitle, summarizeCommandMethods } from "@/lib/events"
import type {
  ActivityEvent,
  ActivityEventPage,
  SessionDetail,
} from "@/types/api"

import { StateBadge, ProviderPath } from "@/components/session-presentation"
import {
  formatDate,
  formatDuration,
  formatMilliseconds,
  humanize,
} from "@/lib/session-format"
function MetricCard({
  icon: Icon,
  label,
  value,
}: {
  icon: typeof Clock3
  label: string
  value: string
}) {
  return (
    <Card>
      <CardContent className="flex items-center gap-3 p-4">
        <span className="flex size-9 items-center justify-center rounded-md bg-muted">
          <Icon className="size-4 text-muted-foreground" />
        </span>
        <div>
          <p className="text-xs text-muted-foreground">{label}</p>
          <p className="mt-0.5 text-sm font-medium">{value}</p>
        </div>
      </CardContent>
    </Card>
  )
}

function EventTimeline({ events }: { events: ActivityEvent[] }) {
  return (
    <div className="divide-y">
      {events.length ? (
        events.map((event) => (
          <div
            key={event.event_id}
            className="grid gap-2 px-4 py-3 sm:grid-cols-[120px_1fr_auto] sm:items-start"
          >
            <p className="font-mono text-xs text-muted-foreground">
              {new Intl.DateTimeFormat(undefined, {
                hour: "2-digit",
                minute: "2-digit",
                second: "2-digit",
              }).format(new Date(event.occurred_at))}
            </p>
            <div>
              <p className="text-sm font-medium">{eventTitle(event)}</p>
              <p className="mt-1 text-xs break-all text-muted-foreground">
                {summarizeCommandMethods(event.payload) ??
                  (typeof event.payload.method === "string"
                    ? event.payload.method
                    : typeof event.payload.reason === "string"
                      ? humanize(event.payload.reason)
                      : typeof event.payload.url === "string"
                        ? event.payload.url
                        : typeof event.payload.status === "number"
                          ? `HTTP ${event.payload.status}`
                          : typeof event.payload.error_type === "string"
                            ? humanize(event.payload.error_type)
                            : "Lifecycle observation")}
              </p>
              {typeof event.payload.duration_ms === "number" && (
                <p className="mt-1 text-xs text-muted-foreground">
                  {formatMilliseconds(event.payload.duration_ms)} total
                  {typeof event.payload.provider_latency_ms === "number" &&
                    ` · ${formatMilliseconds(event.payload.provider_latency_ms)} provider`}
                  {typeof event.payload.stolosio_queue_ms === "number" &&
                    ` · ${formatMilliseconds(event.payload.stolosio_queue_ms)} Stolosio`}
                </p>
              )}
            </div>
            {event.provider && (
              <Badge variant="secondary">
                {providerLabels[event.provider]}
              </Badge>
            )}
          </div>
        ))
      ) : (
        <p className="p-8 text-center text-sm text-muted-foreground">
          No retained events for this session.
        </p>
      )}
    </div>
  )
}

export function SessionDetailView({
  sessionId,
  navigate,
}: {
  sessionId: string
  navigate: (href: string) => void
}) {
  const detail = useQuery({
    queryKey: ["session", sessionId],
    queryFn: () => apiRequest<SessionDetail>(`/v1/admin/sessions/${sessionId}`),
    refetchInterval: 5_000,
  })
  const events = useInfiniteQuery({
    queryKey: ["session-events", sessionId],
    queryFn: ({ pageParam }) =>
      apiRequest<ActivityEventPage>(
        `/v1/admin/events?session_id=${encodeURIComponent(sessionId)}&limit=500${pageParam ? `&before=${encodeURIComponent(pageParam)}` : ""}`
      ),
    initialPageParam: "",
    getNextPageParam: (page) => page.next_cursor ?? undefined,
    refetchInterval: 5_000,
  })

  if (detail.isLoading) {
    return (
      <main className="flex min-h-svh items-center justify-center">
        <LoaderCircle className="size-5 animate-spin text-muted-foreground" />
      </main>
    )
  }
  if (detail.isError || !detail.data) {
    return (
      <main className="mx-auto max-w-[100rem] px-4 py-6 sm:px-6 lg:px-8 lg:py-8">
        <Button
          variant="ghost"
          onClick={() => navigate(`/sessions${window.location.search}`)}
        >
          <ArrowLeft />
          Sessions
        </Button>
        <Card className="mt-6">
          <CardContent className="p-8 text-sm text-destructive">
            {extractApiError(detail.error)}
          </CardContent>
        </Card>
      </main>
    )
  }
  const session = detail.data
  if (session.workload === "capture")
    return <CaptureDetailView sessionId={sessionId} navigate={navigate} />
  const retainedEvents = [...(events.data?.pages ?? [])]
    .reverse()
    .flatMap((page) => page.events)
  const commandStats = retainedEvents.reduce(
    (totals, event) => {
      if (
        event.event_type !== "command.summary" ||
        !event.payload.methods ||
        typeof event.payload.methods !== "object"
      )
        return totals
      for (const usage of Object.values(
        event.payload.methods as Record<string, Record<string, unknown>>
      )) {
        totals.count += typeof usage.count === "number" ? usage.count : 0
        totals.failed +=
          typeof usage.failed_count === "number" ? usage.failed_count : 0
        totals.interrupted +=
          typeof usage.interrupted_count === "number"
            ? usage.interrupted_count
            : 0
      }
      return totals
    },
    { count: 0, failed: 0, interrupted: 0 }
  )

  return (
    <main className="mx-auto min-h-svh w-full max-w-[100rem] px-4 py-6 sm:px-6 lg:px-8 lg:py-8">
      <Button
        variant="ghost"
        size="sm"
        className="-ml-2"
        onClick={() => navigate(`/sessions${window.location.search}`)}
      >
        <ArrowLeft />
        All sessions
      </Button>
      <div className="mt-5 flex flex-col justify-between gap-5 lg:flex-row lg:items-start">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-3">
            <h1 className="truncate font-mono text-xl font-semibold">
              Session {session.id.slice(0, 8)}
            </h1>
            <StateBadge state={session.state} />
          </div>
          <p className="mt-2 text-sm text-muted-foreground">
            Client reference:{" "}
            <span className="font-mono">
              {session.client_reference ?? "not supplied"}
            </span>
          </p>
        </div>
        <ProviderPath providers={session.providers} />
      </div>

      <div className="mt-5 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <MetricCard
          icon={Clock3}
          label="Duration"
          value={formatDuration(session.duration_seconds)}
        />
        <MetricCard
          icon={Coins}
          label="Modeled cost"
          value={`${session.modeled_cost_units} units`}
        />
        <MetricCard
          icon={Settings2}
          label="Providers"
          value={
            [...new Set(session.providers)]
              .map((provider) => providerLabels[provider])
              .join(", ") || "—"
          }
        />
        <MetricCard
          icon={Clock3}
          label="Browser time"
          value={formatMilliseconds(session.total_browser_time_ms)}
        />
        <MetricCard
          icon={Clock3}
          label="Capacity occupied"
          value={formatMilliseconds(session.total_capacity_occupied_ms)}
        />
      </div>

      <div className="mt-4 rounded-lg border bg-card p-4">
        <p className="text-sm font-medium">Command outcomes</p>
        <p className="mt-2 text-sm text-muted-foreground">
          {number(commandStats.count)} commands · {number(commandStats.failed)}{" "}
          failed · {number(commandStats.interrupted)} interrupted
        </p>
        <p className="mt-1 text-xs leading-5 text-muted-foreground">
          From loaded, retained attempt summaries. Active attempts publish their
          summary when they finish.
        </p>
      </div>
      {session.state === "failed" && session.terminal_reason && (
        <Card className="mt-4 border-destructive/30 bg-destructive/5">
          <CardContent className="flex items-center gap-3 p-4 text-sm text-destructive">
            <XCircle className="size-4" />
            {humanize(session.terminal_reason)}
          </CardContent>
        </Card>
      )}

      <div className="mt-5 grid gap-4 xl:grid-cols-[1.35fr_.85fr]">
        <div className="space-y-6">
          <Card className="overflow-hidden py-0">
            <CardHeader className="border-b py-4">
              <CardTitle className="text-base">Timeline</CardTitle>
            </CardHeader>
            {events.isError ? (
              <CardContent className="p-5 text-sm text-destructive">
                {extractApiError(events.error)}
              </CardContent>
            ) : events.isLoading ? (
              <CardContent className="flex h-32 items-center justify-center">
                <LoaderCircle className="size-5 animate-spin text-muted-foreground" />
              </CardContent>
            ) : (
              <>
                {events.hasNextPage && (
                  <div className="border-b p-3">
                    <Button
                      variant="outline"
                      size="sm"
                      disabled={events.isFetchingNextPage}
                      onClick={() => void events.fetchNextPage()}
                    >
                      {events.isFetchingNextPage
                        ? "Loading…"
                        : "Load earlier events"}
                    </Button>
                  </div>
                )}
                <EventTimeline events={retainedEvents} />
              </>
            )}
          </Card>

          <Card className="overflow-hidden py-0">
            <CardHeader className="border-b py-4">
              <CardTitle className="text-base">Acquisition attempts</CardTitle>
            </CardHeader>
            <div className="divide-y">
              {session.attempts.length ? (
                session.attempts.map((attempt) => (
                  <div className="p-4" key={attempt.id}>
                    <div className="flex flex-wrap items-center justify-between gap-3">
                      <div className="flex items-center gap-2">
                        <Badge variant="secondary">
                          {providerLabels[attempt.provider]}
                        </Badge>
                        <span className="text-sm font-medium">
                          Attempt {attempt.ordinal}
                        </span>
                      </div>
                      <Badge variant="outline" className="capitalize">
                        {attempt.state}
                      </Badge>
                    </div>
                    <div className="mt-3 grid gap-3 text-sm sm:grid-cols-2 xl:grid-cols-4">
                      <div>
                        <p className="text-xs text-muted-foreground">
                          Modeled cost
                        </p>
                        <p className="mt-1">
                          {attempt.modeled_cost_units ?? 0} units
                        </p>
                      </div>
                      <div>
                        <p className="text-xs text-muted-foreground">
                          Browser time
                        </p>
                        <p className="mt-1">
                          {formatMilliseconds(
                            attempt.provider_reported_ms ??
                              attempt.browser_connected_ms
                          )}
                        </p>
                      </div>
                      <div>
                        <p className="text-xs text-muted-foreground">
                          Capacity occupied
                        </p>
                        <p className="mt-1">
                          {formatMilliseconds(attempt.capacity_occupied_ms)}
                        </p>
                      </div>
                      <div>
                        <p className="text-xs text-muted-foreground">
                          Chargeable time
                        </p>
                        <p className="mt-1">
                          {formatMilliseconds(attempt.chargeable_time_ms)}
                        </p>
                      </div>
                      <div>
                        <p className="text-xs text-muted-foreground">
                          Cost basis
                        </p>
                        <p className="mt-1 capitalize">
                          {humanize(attempt.cost_basis)}
                        </p>
                      </div>
                      <div>
                        <p className="text-xs text-muted-foreground">
                          Cost rate
                        </p>
                        <p className="mt-1">
                          {attempt.cost_rate_units_per_second === null
                            ? "—"
                            : `${attempt.cost_rate_units_per_second} units/s`}
                        </p>
                      </div>
                    </div>
                    {attempt.resolved_setting_keys.length > 0 && (
                      <div className="mt-4 flex flex-wrap gap-1.5">
                        {attempt.resolved_setting_keys.map((key) => (
                          <Badge
                            variant="outline"
                            key={key}
                            className="font-mono font-normal"
                          >
                            {key}
                          </Badge>
                        ))}
                      </div>
                    )}
                    {attempt.phase_summary && (
                      <div className="mt-4 rounded-md border bg-muted/20 p-3">
                        <p className="text-xs font-medium text-muted-foreground">
                          How browser time was spent
                        </p>
                        <div className="mt-3 grid gap-3 text-sm sm:grid-cols-2 xl:grid-cols-4">
                          {[
                            {
                              label: "Observed",
                              value: attempt.phase_summary.observed_session_ms,
                            },
                            {
                              label: "Commands in progress",
                              value: attempt.phase_summary.command_active_ms,
                            },
                            {
                              label: "Between commands",
                              value:
                                attempt.phase_summary.no_command_in_flight_ms,
                            },
                            {
                              label: "Before first command",
                              value: attempt.phase_summary.pre_first_command_ms,
                            },
                            {
                              label: "After last command",
                              value: attempt.phase_summary.post_last_command_ms,
                            },
                            {
                              label: "Provider bootstrap",
                              value:
                                attempt.phase_summary.provider_bootstrap_ms,
                            },
                            {
                              label: "Provider close",
                              value: attempt.phase_summary.provider_close_ms,
                            },
                          ].map(({ label, value }) => (
                            <div key={label}>
                              <p className="text-xs text-muted-foreground">
                                {label}
                              </p>
                              <p className="mt-1">
                                {formatMilliseconds(value)}
                              </p>
                            </div>
                          ))}
                        </div>
                      </div>
                    )}
                  </div>
                ))
              ) : (
                <p className="p-8 text-center text-sm text-muted-foreground">
                  No acquisition attempt was created.
                </p>
              )}
            </div>
          </Card>
        </div>

        <div className="space-y-6">
          <Card>
            <CardHeader>
              <CardTitle className="text-base">Session facts</CardTitle>
            </CardHeader>
            <CardContent className="space-y-4 text-sm">
              {[
                ["Session ID", session.id],
                ["Created", formatDate(session.created_at)],
                ["Admitted", formatDate(session.admitted_at)],
                ["Opened", formatDate(session.opened_at)],
                ["Closed", formatDate(session.closed_at)],
                ["Outcome reason", humanize(session.terminal_reason)],
              ].map(([label, value]) => (
                <div className="flex justify-between gap-4" key={label}>
                  <span className="text-muted-foreground">{label}</span>
                  <span className="min-w-0 text-right break-all">{value}</span>
                </div>
              ))}
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle className="text-base">Observed domains</CardTitle>
            </CardHeader>
            <CardContent className="space-y-2">
              {session.domains.length ? (
                session.domains.map((domain) => (
                  <p
                    key={domain.id}
                    className="truncate rounded-md border px-3 py-2 text-sm"
                  >
                    {domain.hostname}
                  </p>
                ))
              ) : (
                <p className="text-sm text-muted-foreground">
                  No domain observed.
                </p>
              )}
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle className="text-base">Requested settings</CardTitle>
            </CardHeader>
            <CardContent>
              {session.requested_setting_keys.length ? (
                <div className="flex flex-wrap gap-1.5">
                  {session.requested_setting_keys.map((key) => (
                    <Badge
                      variant="outline"
                      className="font-mono font-normal"
                      key={key}
                    >
                      {key}
                    </Badge>
                  ))}
                </div>
              ) : (
                <p className="text-sm text-muted-foreground">Defaults only.</p>
              )}
              <p className="mt-3 text-xs leading-5 text-muted-foreground">
                Values are withheld from the operator read model.
              </p>
            </CardContent>
          </Card>
        </div>
      </div>
    </main>
  )
}
