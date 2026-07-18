import { useQuery } from "@tanstack/react-query"
import { useVirtualizer } from "@tanstack/react-virtual"
import {
  Activity,
  ArrowDownUp,
  ChevronDown,
  Pause,
  Play,
  Search,
  WifiOff,
} from "lucide-react"
import { useCallback, useEffect, useMemo, useRef, useState } from "react"

import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"
import { Input } from "@/components/ui/input"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { extractApiError } from "@/lib/api"
import { cn } from "@/lib/utils"
import type {
  ActivityEvent,
  ActivityEventPage,
  ActivityProvider,
} from "@/types/api"

type SortOrder = "newest" | "oldest"
type StreamStatus = "connecting" | "live" | "reconnecting" | "unavailable"
type FilterProfile = "operational" | "standard" | "all" | "failures" | "custom"

const providers: { label: string; value: ActivityProvider }[] = [
  { label: "HTTP", value: "http" },
  { label: "Chromium", value: "chromium" },
  { label: "Browserless", value: "browserless" },
  { label: "Lightpanda", value: "lightpanda" },
  { label: "Camoufox", value: "camoufox" },
]

const eventTypes = [
  "session.requested",
  "session.admitted",
  "session.open",
  "session.closing",
  "session.closed",
  "session.failed",
  "attempt.started",
  "attempt.queued",
  "attempt.acquiring",
  "attempt.connected",
  "attempt.failed",
  "attempt.closed",
  "command.received",
  "command.succeeded",
  "command.failed",
  "command.interrupted",
  "navigation.requested",
  "navigation.redirected",
  "navigation.response",
  "navigation.failed",
  "page.dom_content_loaded",
  "page.loaded",
  "page.content_observed",
  "page.crashed",
  "console.message",
  "javascript.exception",
  "provider.disconnected",
  "execution.transitioned",
] as const

type ActivityEventType = (typeof eventTypes)[number]

const eventTypeOptions = eventTypes.map((value) => ({
  value,
  label: value.replaceAll("_", " "),
}))

const failureEventTypes: ActivityEventType[] = [
  "session.failed",
  "attempt.failed",
  "command.failed",
  "navigation.failed",
  "page.crashed",
  "javascript.exception",
  "provider.disconnected",
]

const profileEventTypes: Record<
  Exclude<FilterProfile, "custom">,
  ActivityEventType[]
> = {
  operational: eventTypes.filter(
    (value) =>
      !value.startsWith("command.") &&
      !value.startsWith("console.") &&
      !value.startsWith("javascript.")
  ),
  standard: eventTypes.filter((value) => !value.startsWith("command.")),
  all: [...eventTypes],
  failures: failureEventTypes,
}

const maxVisibleEvents = 1_000

function mergeEvents(
  current: ActivityEvent[],
  incoming: ActivityEvent[]
): ActivityEvent[] {
  const events = new Map(current.map((event) => [event.event_id, event]))
  for (const event of incoming) {
    events.set(event.event_id, event)
  }
  return [...events.values()]
    .sort(
      (left, right) =>
        new Date(left.occurred_at).getTime() -
          new Date(right.occurred_at).getTime() ||
        left.event_id.localeCompare(right.event_id)
    )
    .slice(-maxVisibleEvents)
}

function buildQuery(
  selectedEventTypes: ActivityEventType[],
  selectedProviders: ActivityProvider[]
) {
  const params = new URLSearchParams()
  if (selectedEventTypes.length < eventTypes.length) {
    for (const eventType of selectedEventTypes) {
      params.append("event_type", eventType)
    }
  }
  if (selectedProviders.length < providers.length) {
    for (const provider of selectedProviders) {
      params.append("provider", provider)
    }
  }
  params.set("limit", "100")
  const value = params.toString()
  return value ? `?${value}` : ""
}

async function fetchActivityEvents(query: string): Promise<ActivityEventPage> {
  const response = await fetch(`/v1/admin/events${query}`)
  if (!response.ok) {
    const body: unknown = await response.json().catch(() => undefined)
    throw new Error(extractApiError(body))
  }
  return (await response.json()) as ActivityEventPage
}

function MultiSelect<T extends string>({
  label,
  options,
  selected,
  onToggle,
}: {
  label: string
  options: { label: string; value: T }[]
  selected: T[]
  onToggle: (value: T) => void
}) {
  const selectionLabel =
    selected.length === options.length ? "All" : `${selected.length} selected`
  return (
    <details className="group relative">
      <summary className="flex h-9 min-w-44 cursor-pointer list-none items-center justify-between gap-3 rounded-lg border border-input bg-background px-3 text-sm outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50">
        <span>
          <span className="text-muted-foreground">{label}</span>
          <span className="ml-2 font-medium">{selectionLabel}</span>
        </span>
        <ChevronDown className="size-4 text-muted-foreground transition-transform group-open:rotate-180" />
      </summary>
      <div className="absolute top-full left-0 z-30 mt-1 max-h-80 min-w-64 overflow-y-auto rounded-lg border bg-popover p-1 text-popover-foreground shadow-md">
        {options.map((option) => (
          <label
            key={option.value}
            className="flex cursor-pointer items-center gap-2 rounded-md px-2 py-1.5 text-sm hover:bg-accent"
          >
            <Checkbox
              checked={selected.includes(option.value)}
              onCheckedChange={() => onToggle(option.value)}
            />
            <span className="capitalize">{option.label}</span>
          </label>
        ))}
      </div>
    </details>
  )
}

function eventDetail(event: ActivityEvent) {
  const payload = event.payload
  if (typeof payload.reason === "string") return payload.reason
  if (typeof payload.method === "string") return payload.method
  if (typeof payload.status === "number") return `HTTP ${payload.status}`
  if (
    typeof payload.from_provider === "string" &&
    typeof payload.to_provider === "string"
  ) {
    return `${payload.from_provider} → ${payload.to_provider}`
  }
  if (typeof payload.url === "string") return payload.url
  return event.outcome ?? "observed"
}

function formatTime(value: string) {
  return new Intl.DateTimeFormat(undefined, {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    fractionalSecondDigits: 3,
    hour12: false,
  }).format(new Date(value))
}

function statusLabel(status: StreamStatus, paused: boolean) {
  if (paused) return "Paused"
  if (status === "live") return "Live activity"
  if (status === "connecting") return "Connecting"
  if (status === "reconnecting") return "Reconnecting"
  return "History only"
}

export function ActivityPage() {
  const [sortOrder, setSortOrder] = useState<SortOrder>("newest")
  const [search, setSearch] = useState("")
  const [profile, setProfile] = useState<FilterProfile>("operational")
  const [selectedEventTypes, setSelectedEventTypes] = useState<
    ActivityEventType[]
  >(profileEventTypes.operational)
  const [selectedProviders, setSelectedProviders] = useState<
    ActivityProvider[]
  >(providers.map(({ value }) => value))
  const [paused, setPaused] = useState(false)
  const [liveState, setLiveState] = useState<{
    events: ActivityEvent[]
    query: string
  }>({ events: [], query: "" })
  const [bufferedState, setBufferedState] = useState<{
    events: ActivityEvent[]
    query: string
  }>({ events: [], query: "" })
  const [statusState, setStatusState] = useState<{
    query: string
    status: StreamStatus
  }>({ query: "", status: "connecting" })
  const [streamGeneration, setStreamGeneration] = useState(0)
  const pausedRef = useRef(false)
  const scrollContainerRef = useRef<HTMLDivElement>(null)

  const query = useMemo(
    () => buildQuery(selectedEventTypes, selectedProviders),
    [selectedEventTypes, selectedProviders]
  )
  const history = useQuery({
    queryKey: ["activity-events", query],
    queryFn: () => fetchActivityEvents(query),
  })
  const refetchHistory = history.refetch

  useEffect(() => {
    const source = new EventSource(`/v1/admin/events/stream${query}`)

    source.onopen = () => setStatusState({ query, status: "live" })
    source.onerror = () => setStatusState({ query, status: "reconnecting" })
    source.addEventListener("harbor-event", (message) => {
      const event = JSON.parse(
        (message as MessageEvent<string>).data
      ) as ActivityEvent
      if (pausedRef.current) {
        setBufferedState((current) => ({
          query,
          events: mergeEvents(current.query === query ? current.events : [], [
            event,
          ]),
        }))
        return
      }
      setLiveState((current) => ({
        query,
        events: mergeEvents(current.query === query ? current.events : [], [
          event,
        ]),
      }))
    })
    source.addEventListener("stream-error", () => {
      setStatusState({ query, status: "unavailable" })
      source.close()
    })
    source.addEventListener("replay-unavailable", () => {
      source.close()
      void refetchHistory()
      setStreamGeneration((generation) => generation + 1)
    })

    return () => source.close()
  }, [query, refetchHistory, streamGeneration])

  const togglePause = useCallback(() => {
    setPaused((current) => {
      pausedRef.current = !current
      if (current) {
        setLiveState((live) => ({
          query,
          events: mergeEvents(
            live.query === query ? live.events : [],
            bufferedState.query === query ? bufferedState.events : []
          ),
        }))
        setBufferedState({ query, events: [] })
      }
      return !current
    })
  }, [bufferedState, query])

  const toggleProvider = (provider: ActivityProvider) => {
    setProfile("custom")
    setSelectedProviders((current) => {
      if (!current.includes(provider)) return [...current, provider]
      return current.length > 1
        ? current.filter((value) => value !== provider)
        : current
    })
  }

  const toggleEventType = (eventType: ActivityEventType) => {
    setProfile("custom")
    setSelectedEventTypes((current) => {
      if (!current.includes(eventType)) return [...current, eventType]
      return current.length > 1
        ? current.filter((value) => value !== eventType)
        : current
    })
  }

  const applyProfile = (value: FilterProfile) => {
    if (value === "custom") return
    setProfile(value)
    setSelectedEventTypes(profileEventTypes[value])
    setSelectedProviders(providers.map(({ value: provider }) => provider))
  }

  const events = useMemo(
    () =>
      mergeEvents(
        history.data?.events ?? [],
        liveState.query === query ? liveState.events : []
      ),
    [history.data, liveState, query]
  )

  const visibleEvents = useMemo(() => {
    const needle = search.trim().toLowerCase()
    return events.filter((event) => {
      if (!needle) return true
      return [
        event.session_id,
        event.provider,
        event.event_type,
        eventDetail(event),
      ]
        .filter(Boolean)
        .some((value) => String(value).toLowerCase().includes(needle))
    })
  }, [events, search])

  const sortedEvents = useMemo(
    () => (sortOrder === "oldest" ? visibleEvents : visibleEvents.toReversed()),
    [sortOrder, visibleEvents]
  )
  // TanStack Virtual intentionally returns an imperative, non-memoizable API.
  // eslint-disable-next-line react-hooks/incompatible-library
  const rowVirtualizer = useVirtualizer({
    count: sortedEvents.length,
    getScrollElement: () => scrollContainerRef.current,
    estimateSize: () => 45,
    overscan: 12,
  })
  const liveEdgeEventId =
    sortOrder === "newest"
      ? sortedEvents[0]?.event_id
      : sortedEvents.at(-1)?.event_id

  useEffect(() => {
    if (paused || !liveEdgeEventId) return
    const frame = window.requestAnimationFrame(() => {
      const container = scrollContainerRef.current
      if (!container) return
      container.scrollTo({
        top: sortOrder === "newest" ? 0 : container.scrollHeight,
      })
    })
    return () => window.cancelAnimationFrame(frame)
  }, [liveEdgeEventId, paused, sortOrder])

  const streamStatus =
    statusState.query === query ? statusState.status : "connecting"
  const bufferedCount =
    bufferedState.query === query ? bufferedState.events.length : 0
  const status = statusLabel(streamStatus, paused)
  return (
    <main className="mx-auto flex h-[calc(100svh-4rem)] w-full max-w-[100rem] flex-col overflow-hidden px-4 py-4 sm:px-6 md:h-svh lg:px-8 lg:py-5">
      <header className="flex flex-none flex-col gap-3 border-b pb-4 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <div className="mb-3 flex items-center gap-2 text-xs font-medium text-muted-foreground">
            <span className="relative flex size-2">
              {streamStatus === "live" && !paused && (
                <span className="absolute inline-flex size-full animate-ping rounded-full bg-emerald-500 opacity-50" />
              )}
              <span
                className={cn(
                  "relative inline-flex size-2 rounded-full",
                  streamStatus === "live" && !paused
                    ? "bg-emerald-500"
                    : streamStatus === "unavailable"
                      ? "bg-destructive"
                      : "bg-amber-500"
                )}
              />
            </span>
            {status}
          </div>
          <h1 className="text-3xl font-semibold tracking-tight sm:text-4xl">
            Activity
          </h1>
          <p className="mt-2 max-w-2xl text-sm leading-6 text-muted-foreground">
            Follow sanitized lifecycle, routing, acquisition, and browser events
            across Harbor sessions.
          </p>
        </div>
        <Button
          variant="outline"
          className="gap-2 self-start sm:self-auto"
          onClick={togglePause}
        >
          {paused ? (
            <Play className="size-4" aria-hidden />
          ) : (
            <Pause className="size-4" aria-hidden />
          )}
          {paused ? "Resume stream" : "Pause stream"}
        </Button>
      </header>

      <section
        aria-label="Activity filters"
        className="flex-none border-b py-3"
      >
        <div className="flex flex-col gap-3 xl:flex-row xl:items-center xl:justify-between">
          <div className="flex flex-wrap items-center gap-2">
            <Select
              value={profile}
              onValueChange={(value) => applyProfile(value as FilterProfile)}
            >
              <SelectTrigger className="h-9 min-w-44 bg-background">
                <span className="text-muted-foreground">Profile</span>
                <SelectValue className="ml-2 font-medium" />
              </SelectTrigger>
              <SelectContent align="start">
                <SelectItem value="operational">Operational</SelectItem>
                <SelectItem value="standard">Standard</SelectItem>
                <SelectItem value="all">All events</SelectItem>
                <SelectItem value="failures">Failures</SelectItem>
                {profile === "custom" && (
                  <SelectItem value="custom">Custom</SelectItem>
                )}
              </SelectContent>
            </Select>
            <MultiSelect
              label="Event types"
              options={eventTypeOptions}
              selected={selectedEventTypes}
              onToggle={toggleEventType}
            />
            <MultiSelect
              label="Providers"
              options={providers}
              selected={selectedProviders}
              onToggle={toggleProvider}
            />
          </div>

          <div className="flex flex-col sm:flex-row">
            <label className="relative block min-w-64">
              <Search
                className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-muted-foreground"
                aria-hidden
              />
              <span className="sr-only">Search activity</span>
              <Input
                type="search"
                value={search}
                onChange={(event) => setSearch(event.target.value)}
                placeholder="Search session, provider, or event"
                className="h-9 bg-background pr-3 pl-9"
              />
            </label>
          </div>
        </div>
      </section>

      <section className="mt-3 flex min-h-0 flex-1 flex-col overflow-hidden rounded-lg border bg-card">
        <div className="flex items-center justify-between border-b bg-muted/40 px-4 py-2.5 text-xs text-muted-foreground">
          <span>
            {visibleEvents.length} shown of {events.length} retained
          </span>
          <div className="flex items-center gap-4">
            {paused && bufferedCount > 0 && (
              <span>{bufferedCount} new while paused</span>
            )}
            <div className="flex items-center gap-2">
              <ArrowDownUp className="size-3.5" aria-hidden />
              <Select
                value={sortOrder}
                onValueChange={(value) => setSortOrder(value as SortOrder)}
              >
                <SelectTrigger
                  className="h-auto w-auto border-0 bg-transparent p-0 text-xs font-medium shadow-none"
                  aria-label="Event order"
                >
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="newest">Newest first</SelectItem>
                  <SelectItem value="oldest">Oldest first (FIFO)</SelectItem>
                </SelectContent>
              </Select>
            </div>
          </div>
        </div>

        {sortedEvents.length > 0 && (
          <div
            ref={scrollContainerRef}
            className="min-h-0 flex-1 overflow-auto"
          >
            <div className="min-w-[46rem]">
              <div className="sticky top-0 z-10 grid grid-cols-[6.5rem_6.5rem_7.5rem_minmax(11rem,2fr)_minmax(8rem,1fr)] border-b bg-card px-3 py-2 font-mono text-[0.625rem] font-medium tracking-wider text-muted-foreground uppercase shadow-[0_1px_0_var(--border)]">
                <span>Time</span>
                <span>Provider</span>
                <span>Session</span>
                <span>Event</span>
                <span>Detail</span>
              </div>

              <div
                className="relative"
                style={{ height: `${rowVirtualizer.getTotalSize()}px` }}
              >
                {rowVirtualizer.getVirtualItems().map((virtualRow) => {
                  const event = sortedEvents[virtualRow.index]
                  return (
                    <article
                      key={event.event_id}
                      className="absolute top-0 left-0 grid w-full grid-cols-[6.5rem_6.5rem_7.5rem_minmax(11rem,2fr)_minmax(8rem,1fr)] items-center border-b px-3 text-sm hover:bg-muted/25"
                      style={{
                        height: `${virtualRow.size}px`,
                        transform: `translateY(${virtualRow.start}px)`,
                      }}
                    >
                      <time
                        dateTime={event.occurred_at}
                        className="font-mono text-xs text-muted-foreground"
                      >
                        {formatTime(event.occurred_at)}
                      </time>
                      <span className="capitalize">
                        {event.provider ?? "Harbor"}
                      </span>
                      <span className="font-mono text-xs text-muted-foreground">
                        {event.session_id.slice(0, 8)}
                      </span>
                      <span className="flex items-center gap-2 font-medium">
                        <span
                          className={cn(
                            "size-1.5 rounded-full bg-muted-foreground",
                            event.outcome === "failure" && "bg-destructive",
                            event.outcome === "success" && "bg-emerald-500",
                            event.outcome === "interrupted" && "bg-amber-500"
                          )}
                        />
                        {event.event_type}
                      </span>
                      <span
                        className="truncate text-xs text-muted-foreground"
                        title={eventDetail(event)}
                      >
                        {eventDetail(event)}
                      </span>
                    </article>
                  )
                })}
              </div>
            </div>
          </div>
        )}

        {sortedEvents.length === 0 && (
          <div className="flex min-h-0 flex-1 items-center justify-center px-6 py-10 text-center">
            <div className="max-w-md">
              <span className="mx-auto flex size-12 items-center justify-center rounded-full border bg-background shadow-sm">
                {history.isError || streamStatus === "unavailable" ? (
                  <WifiOff
                    className="size-5 text-muted-foreground"
                    aria-hidden
                  />
                ) : (
                  <Activity
                    className="size-5 text-muted-foreground"
                    aria-hidden
                  />
                )}
              </span>
              <h2 className="mt-5 text-base font-semibold">
                {history.isError
                  ? "Activity history unavailable"
                  : events.length > 0
                    ? "No events match these controls"
                    : "Waiting for activity"}
              </h2>
              <p className="mt-2 text-sm leading-6 text-muted-foreground">
                {history.isError
                  ? extractApiError(history.error)
                  : "Session events appear here after Harbor receives them. Only sanitized observations are displayed."}
              </p>
            </div>
          </div>
        )}
      </section>
    </main>
  )
}
