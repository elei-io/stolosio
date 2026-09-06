import { useInfiniteQuery } from "@tanstack/react-query"
import { useVirtualizer } from "@tanstack/react-virtual"
import {
  Activity,
  ArrowDown,
  ChevronDown,
  Clock3,
  Copy,
  ExternalLink,
  Pause,
  Play,
  Search,
  WifiOff,
  X,
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
} from "@/components/ui/select"
import { extractApiError } from "@/lib/api"
import { summarizeCommandMethods } from "@/lib/events"
import { cn } from "@/lib/utils"
import type {
  ActivityEvent,
  ActivityEventPage,
  ActivityProvider,
} from "@/types/api"

type SortOrder = "oldest" | "newest"
type StreamStatus = "connecting" | "live" | "reconnecting" | "unavailable"
type FilterProfile = "operational" | "standard" | "all" | "failures" | "custom"
type OutcomeFilter = "all" | "success" | "failure" | "interrupted"
type HistoryWindow = "all" | "15m" | "1h" | "24h" | "7d" | "30d"

type ActivityPageProps = {
  navigate: (href: string) => void
}

const providers: { label: string; value: ActivityProvider }[] = [
  { label: "HTTP", value: "http" },
  { label: "Browserbase", value: "browserbase" },
  { label: "Browserless", value: "browserless" },
]

const eventTypes = [
  "session.open",
  "session.closed",
  "session.failed",
  "attempt.connected",
  "attempt.failed",
  "attempt.closed",
  "command.summary",
  "command.failed",
  "command.interrupted",
  "navigation.requested",
  "navigation.redirected",
  "navigation.response",
  "navigation.failed",
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

const operationalEventTypes: ActivityEventType[] = [
  "session.open",
  "session.closed",
  "session.failed",
  "attempt.connected",
  "attempt.failed",
  "attempt.closed",
  "navigation.failed",
  "page.crashed",
  "provider.disconnected",
  "execution.transitioned",
]

const standardEventTypes = eventTypes.filter(
  (value) => !value.startsWith("command.") && !value.startsWith("console.")
)

const profileEventTypes: Record<
  Exclude<FilterProfile, "custom">,
  ActivityEventType[]
> = {
  operational: operationalEventTypes,
  standard: standardEventTypes,
  all: [...eventTypes],
  failures: failureEventTypes,
}

const maxLiveEvents = 1_000
const uuidPattern =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i

function isActivityEvent(value: unknown): value is ActivityEvent {
  return (
    typeof value === "object" &&
    value !== null &&
    typeof (value as Partial<ActivityEvent>).event_id === "string" &&
    typeof (value as Partial<ActivityEvent>).event_type === "string" &&
    typeof (value as Partial<ActivityEvent>).occurred_at === "string"
  )
}

function mergeEvents(
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

function historyCutoff(window: HistoryWindow): string | null {
  const durations: Partial<Record<HistoryWindow, number>> = {
    "15m": 15 * 60_000,
    "1h": 60 * 60_000,
    "24h": 24 * 60 * 60_000,
    "7d": 7 * 24 * 60 * 60_000,
    "30d": 30 * 24 * 60 * 60_000,
  }
  const duration = durations[window]
  return duration === undefined
    ? null
    : new Date(Date.now() - duration).toISOString()
}

function buildFilterQuery(
  selectedEventTypes: ActivityEventType[],
  selectedProviders: ActivityProvider[],
  outcome: OutcomeFilter,
  sessionId: string,
  attemptId: string
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
  if (outcome !== "all") params.set("outcome", outcome)
  if (uuidPattern.test(sessionId.trim())) {
    params.set("session_id", sessionId.trim())
  }
  if (uuidPattern.test(attemptId.trim())) {
    params.set("attempt_id", attemptId.trim())
  }
  return params
}

async function fetchActivityEvents(
  filterQuery: string,
  cutoff: string | null,
  before: string | null
): Promise<ActivityEventPage> {
  const params = new URLSearchParams(filterQuery)
  params.set("limit", "100")
  if (cutoff) params.set("occurred_after", cutoff)
  if (before) params.set("before", before)
  const response = await fetch(`/v1/admin/events?${params.toString()}`)
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
      <summary className="flex h-8 min-w-40 cursor-pointer list-none items-center justify-between gap-3 rounded-lg border border-input bg-background px-2.5 text-sm outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50">
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
  const commandSummary = summarizeCommandMethods(payload)
  if (commandSummary) return commandSummary
  if (
    typeof payload.from_provider === "string" &&
    typeof payload.to_provider === "string"
  ) {
    return `${payload.from_provider} → ${payload.to_provider}`
  }
  if (typeof payload.reason === "string") {
    return payload.reason.replaceAll("_", " ")
  }
  if (typeof payload.method === "string") return payload.method
  if (typeof payload.status === "number") {
    const url = typeof payload.url === "string" ? ` · ${payload.url}` : ""
    return `HTTP ${payload.status}${url}`
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

function formatDateTime(value: string) {
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
    timeStyle: "long",
  }).format(new Date(value))
}

function statusLabel(status: StreamStatus, paused: boolean) {
  if (paused) return "Paused"
  if (status === "live") return "Live"
  if (status === "connecting") return "Connecting"
  if (status === "reconnecting") return "Reconnecting"
  return "Live unavailable — showing retained history"
}

function eventTone(event: ActivityEvent) {
  if (event.outcome === "failure") return "text-rose-300"
  if (event.outcome === "interrupted") return "text-amber-300"
  if (event.event_type === "execution.transitioned") return "text-violet-300"
  if (event.event_family === "navigation") return "text-sky-300"
  if (event.event_family === "session") return "text-emerald-300"
  return "text-slate-300"
}

function providerTone(provider: ActivityProvider | null) {
  if (provider === "browserbase") return "text-violet-300"
  if (provider === "browserless") return "text-cyan-300"
  if (provider === "http") return "text-amber-200"
  return "text-slate-500"
}

function outcomeGlyph(event: ActivityEvent) {
  if (event.outcome === "failure") return "×"
  if (event.outcome === "interrupted") return "!"
  if (event.event_type === "execution.transitioned") return "↳"
  if (event.outcome === "success") return "✓"
  return "·"
}

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

function EventDetails({
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
                onClick={() => navigate(`/sessions/${event.session_id}`)}
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

export function ActivityPage({ navigate }: ActivityPageProps) {
  const [sortOrder, setSortOrder] = useState<SortOrder>("oldest")
  const [search, setSearch] = useState("")
  const [profile, setProfile] = useState<FilterProfile>("operational")
  const [selectedEventTypes, setSelectedEventTypes] = useState<
    ActivityEventType[]
  >(profileEventTypes.operational)
  const [selectedProviders, setSelectedProviders] = useState<
    ActivityProvider[]
  >(providers.map(({ value }) => value))
  const [outcome, setOutcome] = useState<OutcomeFilter>("all")
  const [historyWindow, setHistoryWindow] = useState<HistoryWindow>("24h")
  const [sessionId, setSessionId] = useState("")
  const [attemptId, setAttemptId] = useState("")
  const [paused, setPaused] = useState(false)
  const [following, setFollowing] = useState(true)
  const [unseenCount, setUnseenCount] = useState(0)
  const [selectedEvent, setSelectedEvent] = useState<ActivityEvent | null>(null)
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
  const followingRef = useRef(true)
  const scrollContainerRef = useRef<HTMLDivElement>(null)

  const filterQuery = useMemo(
    () =>
      buildFilterQuery(
        selectedEventTypes,
        selectedProviders,
        outcome,
        sessionId,
        attemptId
      ).toString(),
    [attemptId, outcome, selectedEventTypes, selectedProviders, sessionId]
  )
  const cutoff = useMemo(() => historyCutoff(historyWindow), [historyWindow])
  const history = useInfiniteQuery({
    queryKey: ["activity-events", filterQuery, cutoff],
    queryFn: ({ pageParam }) =>
      fetchActivityEvents(filterQuery, cutoff, pageParam),
    initialPageParam: null as string | null,
    getNextPageParam: (lastPage) => lastPage.next_cursor ?? undefined,
  })
  const refetchHistory = history.refetch

  useEffect(() => {
    const querySuffix = filterQuery ? `?${filterQuery}` : ""
    const source = new EventSource(`/v1/admin/events/stream${querySuffix}`)
    setStatusState({ query: filterQuery, status: "connecting" })

    source.onopen = () => setStatusState({ query: filterQuery, status: "live" })
    source.onerror = () =>
      setStatusState({ query: filterQuery, status: "reconnecting" })
    source.addEventListener("stolosio-event", (message) => {
      const event: unknown = JSON.parse((message as MessageEvent<string>).data)
      if (!isActivityEvent(event)) return
      if (pausedRef.current) {
        setBufferedState((current) => ({
          query: filterQuery,
          events: mergeEvents(
            current.query === filterQuery ? current.events : [],
            [event],
            maxLiveEvents
          ),
        }))
        return
      }
      setLiveState((current) => ({
        query: filterQuery,
        events: mergeEvents(
          current.query === filterQuery ? current.events : [],
          [event],
          maxLiveEvents
        ),
      }))
      if (!followingRef.current) {
        setUnseenCount((current) => current + 1)
      }
    })
    source.addEventListener("stream-error", () => {
      setStatusState({ query: filterQuery, status: "unavailable" })
      source.close()
    })
    source.addEventListener("replay-unavailable", () => {
      source.close()
      void refetchHistory()
      setStreamGeneration((generation) => generation + 1)
    })

    return () => source.close()
  }, [filterQuery, refetchHistory, streamGeneration])

  const togglePause = useCallback(() => {
    setPaused((current) => {
      pausedRef.current = !current
      if (current) {
        setLiveState((live) => ({
          query: filterQuery,
          events: mergeEvents(
            live.query === filterQuery ? live.events : [],
            bufferedState.query === filterQuery ? bufferedState.events : [],
            maxLiveEvents
          ),
        }))
        setBufferedState({ query: filterQuery, events: [] })
      }
      return !current
    })
  }, [bufferedState, filterQuery])

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
    setOutcome("all")
    setSelectedEventTypes(profileEventTypes[value])
    setSelectedProviders(providers.map(({ value: provider }) => provider))
  }

  const historyEvents = useMemo(
    () => history.data?.pages.flatMap((page) => page.events) ?? [],
    [history.data]
  )
  const events = useMemo(
    () =>
      mergeEvents(
        historyEvents,
        liveState.query === filterQuery ? liveState.events : []
      ),
    [filterQuery, historyEvents, liveState]
  )

  const visibleEvents = useMemo(() => {
    const needle = search.trim().toLowerCase()
    return events.filter((event) => {
      if (!needle) return true
      return [
        event.session_id,
        event.attempt_id,
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
    estimateSize: () => 34,
    overscan: 16,
  })

  const liveEdgeEventId =
    sortOrder === "oldest"
      ? sortedEvents.at(-1)?.event_id
      : sortedEvents[0]?.event_id

  const scrollToLive = useCallback(() => {
    if (!sortedEvents.length) return
    const index = sortOrder === "oldest" ? sortedEvents.length - 1 : 0
    rowVirtualizer.scrollToIndex(index, {
      align: sortOrder === "oldest" ? "end" : "start",
    })
    followingRef.current = true
    setFollowing(true)
    setUnseenCount(0)
  }, [rowVirtualizer, sortOrder, sortedEvents.length])

  useEffect(() => {
    if (paused || !following || !liveEdgeEventId) return
    const frame = window.requestAnimationFrame(scrollToLive)
    return () => window.cancelAnimationFrame(frame)
  }, [following, liveEdgeEventId, paused, scrollToLive])

  const detectFollowState = () => {
    const container = scrollContainerRef.current
    if (!container) return
    const distance =
      sortOrder === "oldest"
        ? container.scrollHeight - container.scrollTop - container.clientHeight
        : container.scrollTop
    const isFollowing = distance < 48
    followingRef.current = isFollowing
    setFollowing(isFollowing)
    if (isFollowing) setUnseenCount(0)
  }

  const changeSortOrder = (value: SortOrder) => {
    setSortOrder(value)
    followingRef.current = true
    setFollowing(true)
    setUnseenCount(0)
  }

  const streamStatus =
    statusState.query === filterQuery ? statusState.status : "connecting"
  const bufferedCount =
    bufferedState.query === filterQuery ? bufferedState.events.length : 0
  const invalidSession = sessionId.length > 0 && !uuidPattern.test(sessionId)
  const invalidAttempt = attemptId.length > 0 && !uuidPattern.test(attemptId)

  return (
    <main className="mx-auto flex h-[calc(100svh-4rem)] w-full max-w-[112rem] flex-col overflow-hidden px-4 py-4 sm:px-6 md:h-svh lg:px-8 lg:py-5">
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
            {statusLabel(streamStatus, paused)}
          </div>
          <h1 className="text-3xl font-semibold tracking-tight sm:text-4xl">
            Activity
          </h1>
          <p className="mt-2 max-w-2xl text-sm leading-6 text-muted-foreground">
            A live, sanitized operational log across Stolosio sessions.
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
          {paused ? "Resume" : "Pause"}
          {paused && bufferedCount > 0 && (
            <span className="rounded-full bg-amber-500/15 px-1.5 text-xs text-amber-700 dark:text-amber-300">
              {bufferedCount}
            </span>
          )}
        </Button>
      </header>

      <section
        aria-label="Activity filters"
        className="flex-none border-b py-3"
      >
        <div className="flex flex-wrap items-center gap-2">
          <Select
            value={profile}
            onValueChange={(value) => applyProfile(value as FilterProfile)}
          >
            <SelectTrigger className="min-w-40 bg-background">
              <span className="text-muted-foreground">Profile</span>
              <span className="ml-2 font-medium capitalize">{profile}</span>
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
            label="Events"
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
          <Select
            value={outcome}
            onValueChange={(value) => setOutcome(value as OutcomeFilter)}
          >
            <SelectTrigger className="min-w-32 bg-background">
              <span>
                {outcome === "all"
                  ? "Any outcome"
                  : outcome[0].toUpperCase() + outcome.slice(1)}
              </span>
            </SelectTrigger>
            <SelectContent align="start">
              <SelectItem value="all">Any outcome</SelectItem>
              <SelectItem value="success">Success</SelectItem>
              <SelectItem value="failure">Failure</SelectItem>
              <SelectItem value="interrupted">Interrupted</SelectItem>
            </SelectContent>
          </Select>
          <Select
            value={historyWindow}
            onValueChange={(value) => setHistoryWindow(value as HistoryWindow)}
          >
            <SelectTrigger className="min-w-32 bg-background">
              <Clock3 className="size-3.5 text-muted-foreground" />
              <span>
                {
                  {
                    "15m": "Last 15 min",
                    "1h": "Last hour",
                    "24h": "Last 24 hours",
                    "7d": "Last 7 days",
                    "30d": "Last 30 days",
                    all: "All retained",
                  }[historyWindow]
                }
              </span>
            </SelectTrigger>
            <SelectContent align="start">
              <SelectItem value="15m">Last 15 min</SelectItem>
              <SelectItem value="1h">Last hour</SelectItem>
              <SelectItem value="24h">Last 24 hours</SelectItem>
              <SelectItem value="7d">Last 7 days</SelectItem>
              <SelectItem value="30d">Last 30 days</SelectItem>
              <SelectItem value="all">All retained</SelectItem>
            </SelectContent>
          </Select>
        </div>

        <div className="mt-2 grid gap-2 md:grid-cols-[minmax(12rem,1fr)_minmax(12rem,1fr)_minmax(12rem,1fr)]">
          <label className="relative block">
            <Search
              className="pointer-events-none absolute top-1/2 left-2.5 size-4 -translate-y-1/2 text-muted-foreground"
              aria-hidden
            />
            <span className="sr-only">Filter loaded events</span>
            <Input
              type="search"
              value={search}
              onChange={(event) => setSearch(event.target.value)}
              placeholder="Filter loaded events"
              className="pl-8"
            />
          </label>
          <Input
            value={sessionId}
            onChange={(event) => setSessionId(event.target.value)}
            placeholder="Exact session UUID"
            aria-invalid={invalidSession}
            title={invalidSession ? "Enter a complete session UUID" : undefined}
            className="font-mono text-xs"
          />
          <Input
            value={attemptId}
            onChange={(event) => setAttemptId(event.target.value)}
            placeholder="Exact attempt UUID"
            aria-invalid={invalidAttempt}
            title={invalidAttempt ? "Enter a complete attempt UUID" : undefined}
            className="font-mono text-xs"
          />
        </div>
      </section>

      <section className="mt-3 flex min-h-0 flex-1 flex-col overflow-hidden rounded-lg border border-slate-800 bg-[#0b1015] shadow-sm">
        <div className="flex flex-none flex-wrap items-center justify-between gap-2 border-b border-slate-800 bg-[#10171e] px-3 py-2 text-xs text-slate-400">
          <div className="flex items-center gap-3">
            <span>
              {visibleEvents.length === events.length
                ? `${events.length} loaded`
                : `${visibleEvents.length} shown · ${events.length} loaded`}
            </span>
            {history.hasNextPage && (
              <Button
                variant="ghost"
                size="xs"
                className="text-slate-300 hover:bg-slate-800 hover:text-white"
                disabled={history.isFetchingNextPage}
                onClick={() => void history.fetchNextPage()}
              >
                {history.isFetchingNextPage ? "Loading…" : "Load older"}
              </Button>
            )}
          </div>
          <Select
            value={sortOrder}
            onValueChange={(value) => changeSortOrder(value as SortOrder)}
          >
            <SelectTrigger className="h-6 border-slate-700 bg-slate-900 px-2 text-xs text-slate-300 dark:bg-slate-900">
              <span>
                {sortOrder === "oldest" ? "Live tail" : "Newest first"}
              </span>
            </SelectTrigger>
            <SelectContent align="end">
              <SelectItem value="oldest">Live tail</SelectItem>
              <SelectItem value="newest">Newest first</SelectItem>
            </SelectContent>
          </Select>
        </div>

        {sortedEvents.length > 0 && (
          <div className="relative min-h-0 flex-1">
            <div
              ref={scrollContainerRef}
              className="h-full overflow-auto"
              onScroll={detectFollowState}
            >
              <div className="min-w-[62rem]">
                <div
                  className="relative"
                  style={{ height: `${rowVirtualizer.getTotalSize()}px` }}
                >
                  {rowVirtualizer.getVirtualItems().map((virtualRow) => {
                    const event = sortedEvents[virtualRow.index]
                    return (
                      <button
                        type="button"
                        key={event.event_id}
                        className="absolute top-0 left-0 grid w-full grid-cols-[6.25rem_5.25rem_7.25rem_12rem_minmax(18rem,1fr)] items-center gap-2 border-b border-slate-800/60 px-3 text-left font-mono text-xs transition-colors hover:bg-slate-800/55 focus-visible:z-10 focus-visible:bg-slate-800 focus-visible:outline-none"
                        style={{
                          height: `${virtualRow.size}px`,
                          transform: `translateY(${virtualRow.start}px)`,
                        }}
                        onClick={() => setSelectedEvent(event)}
                      >
                        <time
                          dateTime={event.occurred_at}
                          className="text-slate-500 tabular-nums"
                          title={formatDateTime(event.occurred_at)}
                        >
                          {formatTime(event.occurred_at)}
                        </time>
                        <span
                          className="truncate text-slate-500"
                          title={event.session_id}
                        >
                          {event.session_id.slice(0, 8)}
                        </span>
                        <span
                          className={cn(
                            "truncate uppercase",
                            providerTone(event.provider)
                          )}
                        >
                          {event.provider ?? "stolosio"}
                        </span>
                        <span
                          className={cn(
                            "flex min-w-0 items-center gap-2",
                            eventTone(event)
                          )}
                        >
                          <span className="w-2 text-center font-semibold">
                            {outcomeGlyph(event)}
                          </span>
                          <span className="truncate">{event.event_type}</span>
                        </span>
                        <span
                          className="truncate text-slate-400"
                          title={eventDetail(event)}
                        >
                          {eventDetail(event)}
                        </span>
                      </button>
                    )
                  })}
                </div>
              </div>
            </div>

            {!following && (
              <Button
                size="sm"
                className="absolute right-4 bottom-4 gap-2 rounded-full bg-slate-100 text-slate-950 shadow-lg hover:bg-white"
                onClick={scrollToLive}
              >
                <ArrowDown aria-hidden />
                Jump to live
                {unseenCount > 0 && (
                  <span className="rounded-full bg-slate-900 px-1.5 text-[0.65rem] text-white">
                    {unseenCount}
                  </span>
                )}
              </Button>
            )}
          </div>
        )}

        {sortedEvents.length === 0 && (
          <div className="flex min-h-0 flex-1 items-center justify-center px-6 py-10 text-center">
            <div className="max-w-md">
              <span className="mx-auto flex size-12 items-center justify-center rounded-full border border-slate-700 bg-slate-900">
                {history.isError || streamStatus === "unavailable" ? (
                  <WifiOff className="size-5 text-slate-400" aria-hidden />
                ) : (
                  <Activity className="size-5 text-slate-400" aria-hidden />
                )}
              </span>
              <h2 className="mt-5 text-sm font-semibold text-slate-200">
                {history.isError
                  ? "Activity history unavailable"
                  : events.length > 0
                    ? "No loaded events match the text filter"
                    : "Waiting for activity"}
              </h2>
              <p className="mt-2 text-xs leading-5 text-slate-400">
                {history.isError
                  ? extractApiError(history.error)
                  : "Sanitized operational events appear here as Stolosio observes them."}
              </p>
            </div>
          </div>
        )}
      </section>

      <p className="mt-2 flex-none text-xs text-muted-foreground">
        Event history is retained for 30 days. Text search filters only the
        events currently loaded in this view.
      </p>

      {selectedEvent && (
        <EventDetails
          event={selectedEvent}
          close={() => setSelectedEvent(null)}
          navigate={navigate}
        />
      )}
    </main>
  )
}
