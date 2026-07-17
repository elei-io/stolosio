import { useQuery } from "@tanstack/react-query"
import { useVirtualizer } from "@tanstack/react-virtual"
import {
  Activity,
  ArrowDownUp,
  Check,
  ChevronDown,
  Filter,
  Pause,
  Play,
  Radio,
  Search,
  Settings2,
  WifiOff,
} from "lucide-react"
import { useCallback, useEffect, useMemo, useRef, useState } from "react"

import { Button } from "@/components/ui/button"
import { extractApiError } from "@/lib/api"
import { cn } from "@/lib/utils"
import type {
  ActivityEvent,
  ActivityEventPage,
  ActivityProvider,
} from "@/types/api"

type Density = "summary" | "standard" | "verbose"
type SortOrder = "newest" | "oldest"
type StreamStatus = "connecting" | "live" | "reconnecting" | "unavailable"

const providers: { label: string; value: ActivityProvider }[] = [
  { label: "HTTP", value: "http" },
  { label: "Chromium", value: "chromium" },
  { label: "Browserless", value: "browserless" },
  { label: "Lightpanda", value: "lightpanda" },
  { label: "Camoufox", value: "camoufox" },
]

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
  selectedProviders: ActivityProvider[],
  failuresOnly: boolean
) {
  const params = new URLSearchParams()
  for (const provider of selectedProviders) {
    params.append("provider", provider)
  }
  if (failuresOnly) {
    params.set("outcome", "failure")
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

function DensityButton({
  active,
  children,
  onClick,
}: {
  active: boolean
  children: string
  onClick: () => void
}) {
  return (
    <button
      type="button"
      aria-pressed={active}
      onClick={onClick}
      className={cn(
        "rounded-md border px-3 py-1.5 text-xs font-medium transition-colors",
        active
          ? "border-foreground/15 bg-foreground text-background"
          : "bg-background text-muted-foreground hover:bg-muted hover:text-foreground"
      )}
    >
      {children}
    </button>
  )
}

function isVisibleAtDensity(event: ActivityEvent, density: Density) {
  if (density === "verbose") return true
  if (density === "standard") return event.event_family !== "command"
  return !["command", "console", "javascript"].includes(event.event_family)
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
  const [density, setDensity] = useState<Density>("summary")
  const [sortOrder, setSortOrder] = useState<SortOrder>("newest")
  const [followLive, setFollowLive] = useState(true)
  const [search, setSearch] = useState("")
  const [filtersOpen, setFiltersOpen] = useState(false)
  const [selectedProviders, setSelectedProviders] = useState<
    ActivityProvider[]
  >([])
  const [failuresOnly, setFailuresOnly] = useState(false)
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
    () => buildQuery(selectedProviders, failuresOnly),
    [selectedProviders, failuresOnly]
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
    setSelectedProviders((current) =>
      current.includes(provider)
        ? current.filter((value) => value !== provider)
        : [...current, provider]
    )
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
      if (!isVisibleAtDensity(event, density)) return false
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
  }, [density, events, search])

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
    if (!followLive || paused || !liveEdgeEventId) return
    const frame = window.requestAnimationFrame(() => {
      const container = scrollContainerRef.current
      if (!container) return
      container.scrollTo({
        top: sortOrder === "newest" ? 0 : container.scrollHeight,
      })
    })
    return () => window.cancelAnimationFrame(frame)
  }, [followLive, liveEdgeEventId, paused, sortOrder])

  const streamStatus =
    statusState.query === query ? statusState.status : "connecting"
  const bufferedCount =
    bufferedState.query === query ? bufferedState.events.length : 0
  const status = statusLabel(streamStatus, paused)
  const activeFilterCount = selectedProviders.length + (failuresOnly ? 1 : 0)

  return (
    <main className="mx-auto flex h-[calc(100svh-4rem)] w-full max-w-[100rem] flex-col overflow-hidden px-4 py-5 sm:px-6 md:h-svh lg:px-10 lg:py-6">
      <header className="flex flex-none flex-col gap-4 border-b pb-5 sm:flex-row sm:items-end sm:justify-between">
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
            <span className="mr-1 flex items-center gap-2 text-xs font-medium text-muted-foreground">
              <Filter className="size-3.5" aria-hidden />
              Density
            </span>
            {(["summary", "standard", "verbose"] as const).map((value) => (
              <DensityButton
                key={value}
                active={density === value}
                onClick={() => setDensity(value)}
              >
                {value[0].toUpperCase() + value.slice(1)}
              </DensityButton>
            ))}
          </div>

          <div className="flex flex-col gap-2 sm:flex-row">
            <label className="relative block min-w-64">
              <Search
                className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-muted-foreground"
                aria-hidden
              />
              <span className="sr-only">Search activity</span>
              <input
                type="search"
                value={search}
                onChange={(event) => setSearch(event.target.value)}
                placeholder="Search session, provider, or event"
                className="h-9 w-full rounded-md border bg-background pr-3 pl-9 text-sm outline-none placeholder:text-muted-foreground focus-visible:ring-2 focus-visible:ring-ring"
              />
            </label>
            <Button
              variant="outline"
              className="gap-2"
              aria-expanded={filtersOpen}
              onClick={() => setFiltersOpen((open) => !open)}
            >
              <Settings2 className="size-4" aria-hidden />
              Filters
              {activeFilterCount > 0 && (
                <span className="rounded-full bg-foreground px-1.5 text-[0.625rem] leading-5 text-background">
                  {activeFilterCount}
                </span>
              )}
              <ChevronDown
                className={cn(
                  "size-3.5 transition-transform",
                  filtersOpen && "rotate-180"
                )}
                aria-hidden
              />
            </Button>
          </div>
        </div>

        {filtersOpen && (
          <div className="mt-4 grid gap-5 rounded-lg border bg-muted/25 p-4 lg:grid-cols-[1fr_auto] lg:items-end">
            <fieldset>
              <legend className="mb-2 text-xs font-medium text-muted-foreground">
                Providers
              </legend>
              <div className="flex flex-wrap gap-2">
                {providers.map((provider) => {
                  const selected = selectedProviders.includes(provider.value)
                  return (
                    <button
                      key={provider.value}
                      type="button"
                      aria-pressed={selected}
                      onClick={() => toggleProvider(provider.value)}
                      className={cn(
                        "flex items-center gap-2 rounded-md border bg-background px-3 py-2 text-xs font-medium transition-colors",
                        selected
                          ? "border-foreground/30 text-foreground"
                          : "text-muted-foreground hover:text-foreground"
                      )}
                    >
                      <span
                        className={cn(
                          "flex size-3.5 items-center justify-center rounded-sm border",
                          selected &&
                            "border-foreground bg-foreground text-background"
                        )}
                      >
                        {selected && <Check className="size-2.5" aria-hidden />}
                      </span>
                      {provider.label}
                    </button>
                  )
                })}
              </div>
            </fieldset>

            <label className="flex items-center gap-3 rounded-md border bg-background px-3 py-2 text-xs font-medium">
              <input
                type="checkbox"
                checked={failuresOnly}
                onChange={(event) => setFailuresOnly(event.target.checked)}
                className="size-4 accent-foreground"
              />
              Failures only
            </label>
          </div>
        )}
      </section>

      <section className="mt-4 flex min-h-0 flex-1 flex-col overflow-hidden rounded-lg border bg-card">
        <div className="flex items-center justify-between border-b bg-muted/40 px-4 py-2.5 text-xs text-muted-foreground">
          <span>
            {visibleEvents.length} shown of {events.length} retained
          </span>
          <div className="flex items-center gap-4">
            {paused && bufferedCount > 0 && (
              <span>{bufferedCount} new while paused</span>
            )}
            <button
              type="button"
              aria-pressed={followLive}
              onClick={() => setFollowLive((current) => !current)}
              className={cn(
                "flex items-center gap-1.5 font-medium transition-colors",
                followLive
                  ? "text-foreground"
                  : "text-muted-foreground hover:text-foreground"
              )}
            >
              <Radio
                className={cn("size-3.5", followLive && "text-emerald-500")}
                aria-hidden
              />
              Follow live
            </button>
            <label className="flex items-center gap-2">
              <ArrowDownUp className="size-3.5" aria-hidden />
              <span className="sr-only">Event order</span>
              <select
                value={sortOrder}
                onChange={(event) =>
                  setSortOrder(event.target.value as SortOrder)
                }
                className="bg-transparent font-medium text-foreground outline-none"
              >
                <option value="newest">Newest first</option>
                <option value="oldest">Oldest first (FIFO)</option>
              </select>
            </label>
          </div>
        </div>

        {sortedEvents.length > 0 && (
          <div
            ref={scrollContainerRef}
            className="min-h-0 flex-1 overflow-auto"
          >
            <div className="min-w-[52rem]">
              <div className="sticky top-0 z-10 grid grid-cols-[7.5rem_7.5rem_8.5rem_minmax(13rem,2fr)_minmax(10rem,1fr)] border-b bg-card px-4 py-2.5 font-mono text-[0.6875rem] font-medium tracking-wider text-muted-foreground uppercase shadow-[0_1px_0_var(--border)]">
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
                      className="absolute top-0 left-0 grid w-full grid-cols-[7.5rem_7.5rem_8.5rem_minmax(13rem,2fr)_minmax(10rem,1fr)] items-center border-b px-4 text-sm hover:bg-muted/25"
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
