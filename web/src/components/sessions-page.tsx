import { useQuery } from "@tanstack/react-query"
import {
  ArrowLeft,
  ArrowRight,
  CheckCircle2,
  CircleDot,
  Clock3,
  Coins,
  ExternalLink,
  LoaderCircle,
  Search,
  Settings2,
  Terminal,
  XCircle,
} from "lucide-react"
import { useEffect, useMemo, useState } from "react"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
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
  HarborSessionState,
  SessionDetail,
  SessionPage,
} from "@/types/api"

const providerLabels: Record<ActivityProvider, string> = {
  http: "HTTP",
  chromium: "Chromium",
  browserless: "Browserless",
  lightpanda: "Lightpanda",
  camoufox: "Camoufox",
}

async function fetchJson<T>(path: string): Promise<T> {
  const response = await fetch(path)
  if (!response.ok) {
    const body: unknown = await response.json().catch(() => undefined)
    throw new Error(extractApiError(body))
  }
  return (await response.json()) as T
}

function formatDate(value: string | null) {
  if (!value) return "—"
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
    timeStyle: "medium",
  }).format(new Date(value))
}

function formatDuration(seconds: number | null) {
  if (seconds === null) return "In progress"
  if (seconds < 1) return "<1s"
  if (seconds < 60) return `${Math.round(seconds)}s`
  const minutes = Math.floor(seconds / 60)
  const remainder = Math.round(seconds % 60)
  return `${minutes}m ${remainder}s`
}

function humanize(value: string | null) {
  return value?.replaceAll("_", " ") ?? "—"
}

function StateBadge({ state }: { state: HarborSessionState }) {
  const terminal = state === "closed"
  const failed = state === "failed"
  const Icon = terminal ? CheckCircle2 : failed ? XCircle : CircleDot
  return (
    <Badge
      variant="outline"
      className={cn(
        "gap-1.5 capitalize",
        terminal &&
          "border-emerald-500/25 text-emerald-700 dark:text-emerald-400",
        failed && "border-destructive/25 text-destructive",
        !terminal &&
          !failed &&
          "border-amber-500/25 text-amber-700 dark:text-amber-400"
      )}
    >
      <Icon className="size-3" />
      {state}
    </Badge>
  )
}

function ProviderPath({ providers }: { providers: ActivityProvider[] }) {
  if (!providers.length) {
    return <span className="text-muted-foreground">No attempt</span>
  }
  return (
    <span className="flex flex-wrap items-center gap-1.5">
      {providers.map((provider, index) => (
        <span className="contents" key={`${provider}-${index}`}>
          {index > 0 && <ArrowRight className="size-3 text-muted-foreground" />}
          <Badge variant="secondary">{providerLabels[provider]}</Badge>
        </span>
      ))}
    </span>
  )
}

function SessionList({ navigate }: { navigate: (href: string) => void }) {
  const [searchInput, setSearchInput] = useState("")
  const [search, setSearch] = useState("")
  const [state, setState] = useState("all")
  const [provider, setProvider] = useState("all")

  useEffect(() => {
    const timeout = window.setTimeout(() => setSearch(searchInput.trim()), 250)
    return () => window.clearTimeout(timeout)
  }, [searchInput])

  const queryString = useMemo(() => {
    const params = new URLSearchParams({ limit: "100" })
    if (search) params.set("search", search)
    if (state !== "all") params.set("state", state)
    if (provider !== "all") params.set("provider", provider)
    return params.toString()
  }, [provider, search, state])

  const sessions = useQuery({
    queryKey: ["sessions", queryString],
    queryFn: () => fetchJson<SessionPage>(`/v1/admin/sessions?${queryString}`),
    refetchInterval: 10_000,
  })

  return (
    <main className="mx-auto min-h-svh w-full max-w-[100rem] px-4 py-6 sm:px-6 lg:px-8 lg:py-8">
      <div className="flex flex-col justify-between gap-4 sm:flex-row sm:items-end">
        <div>
          <p className="font-mono text-xs font-medium tracking-[0.18em] text-muted-foreground uppercase">
            Operate
          </p>
          <h1 className="mt-2 text-3xl font-semibold tracking-tight">
            Sessions
          </h1>
          <p className="mt-2 text-sm text-muted-foreground">
            Search logical session history and inspect provider journeys.
          </p>
        </div>
        <Badge variant="outline" className="w-fit font-mono">
          {sessions.data?.sessions.length ?? 0} shown
        </Badge>
      </div>

      <Card className="mt-6">
        <CardContent className="grid gap-3 p-4 md:grid-cols-[1fr_180px_180px]">
          <div className="relative">
            <Search className="absolute top-1/2 left-3 size-4 -translate-y-1/2 text-muted-foreground" />
            <Input
              value={searchInput}
              onChange={(event) => setSearchInput(event.target.value)}
              placeholder="Search session or client reference"
              className="pl-9"
            />
          </div>
          <Select
            value={state}
            onValueChange={(value) => value && setState(value)}
          >
            <SelectTrigger className="w-full">
              <SelectValue placeholder="State" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">All states</SelectItem>
              {[
                "requested",
                "admitted",
                "open",
                "closing",
                "closed",
                "failed",
              ].map((value) => (
                <SelectItem value={value} key={value} className="capitalize">
                  {value}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Select
            value={provider}
            onValueChange={(value) => value && setProvider(value)}
          >
            <SelectTrigger className="w-full">
              <SelectValue placeholder="Provider" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">All providers</SelectItem>
              {Object.entries(providerLabels).map(([value, label]) => (
                <SelectItem value={value} key={value}>
                  {label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </CardContent>
      </Card>

      <Card className="mt-4 overflow-hidden py-0">
        <div className="hidden grid-cols-[1.45fr_1.15fr_1fr_.65fr_.55fr] gap-3 border-b bg-muted/35 px-4 py-2 text-xs font-medium text-muted-foreground md:grid">
          <span>Session</span>
          <span>Provider path</span>
          <span>Domains</span>
          <span>State</span>
          <span className="text-right">Cost</span>
        </div>
        {sessions.isLoading ? (
          <div className="flex h-52 items-center justify-center">
            <LoaderCircle className="size-5 animate-spin text-muted-foreground" />
          </div>
        ) : sessions.isError ? (
          <div className="p-8 text-sm text-destructive">
            {extractApiError(sessions.error)}
          </div>
        ) : sessions.data?.sessions.length ? (
          <div className="divide-y">
            {sessions.data.sessions.map((session) => (
              <button
                type="button"
                key={session.id}
                onClick={() => navigate(`/sessions/${session.id}`)}
                className="grid w-full gap-3 px-4 py-3 text-left transition-colors hover:bg-muted/35 md:grid-cols-[1.45fr_1.15fr_1fr_.65fr_.55fr] md:items-center"
              >
                <div className="min-w-0">
                  <p className="truncate font-mono text-xs">{session.id}</p>
                  <p className="mt-1 text-xs text-muted-foreground">
                    {formatDate(session.created_at)} ·{" "}
                    {formatDuration(session.duration_seconds)}
                  </p>
                </div>
                <ProviderPath providers={session.providers} />
                <p className="truncate text-sm text-muted-foreground">
                  {session.domains
                    .map((domain) => domain.hostname)
                    .join(", ") || "No domain observed"}
                </p>
                <StateBadge state={session.state} />
                <p className="text-sm font-medium md:text-right">
                  {session.actual_cost_units} units
                </p>
              </button>
            ))}
          </div>
        ) : (
          <div className="p-12 text-center">
            <CircleDot className="mx-auto size-8 text-muted-foreground/50" />
            <p className="mt-3 text-sm font-medium">No sessions found</p>
            <p className="mt-1 text-sm text-muted-foreground">
              Try changing the search or filters.
            </p>
          </div>
        )}
      </Card>
    </main>
  )
}

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
              <p className="text-sm font-medium">
                {humanize(event.event_type)}
              </p>
              <p className="mt-1 font-mono text-xs break-all text-muted-foreground">
                {typeof event.payload.method === "string"
                  ? event.payload.method
                  : typeof event.payload.reason === "string"
                    ? humanize(event.payload.reason)
                    : (event.attempt_id ?? "Session event")}
              </p>
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

function SessionDetailView({
  sessionId,
  navigate,
}: {
  sessionId: string
  navigate: (href: string) => void
}) {
  const detail = useQuery({
    queryKey: ["session", sessionId],
    queryFn: () => fetchJson<SessionDetail>(`/v1/admin/sessions/${sessionId}`),
    refetchInterval: 5_000,
  })
  const events = useQuery({
    queryKey: ["session-events", sessionId],
    queryFn: () =>
      fetchJson<ActivityEventPage>(
        `/v1/admin/events?session_id=${encodeURIComponent(sessionId)}&limit=500`
      ),
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
        <Button variant="ghost" onClick={() => navigate("/sessions")}>
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

  return (
    <main className="mx-auto min-h-svh w-full max-w-[100rem] px-4 py-6 sm:px-6 lg:px-8 lg:py-8">
      <Button
        variant="ghost"
        size="sm"
        className="-ml-2"
        onClick={() => navigate("/sessions")}
      >
        <ArrowLeft />
        All sessions
      </Button>
      <div className="mt-5 flex flex-col justify-between gap-5 lg:flex-row lg:items-start">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-3">
            <h1 className="truncate font-mono text-xl font-semibold">
              {session.id}
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
          label="Actual cost"
          value={`${session.actual_cost_units} units`}
        />
        <MetricCard
          icon={Terminal}
          label="Observed methods"
          value={String(session.command_count)}
        />
        <MetricCard
          icon={Settings2}
          label="Selection"
          value={`${session.selection_mode} · ${humanize(session.selection_reason)}`}
        />
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
              <EventTimeline
                events={[...(events.data?.events ?? [])].reverse()}
              />
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
                    <div className="mt-3 grid gap-3 text-sm sm:grid-cols-3">
                      <div>
                        <p className="text-xs text-muted-foreground">
                          Selection
                        </p>
                        <p className="mt-1 capitalize">
                          {humanize(attempt.selection_reason)}
                        </p>
                      </div>
                      <div>
                        <p className="text-xs text-muted-foreground">
                          Transition trigger
                        </p>
                        <p className="mt-1 capitalize">
                          {humanize(attempt.transition_trigger)}
                        </p>
                      </div>
                      <div>
                        <p className="text-xs text-muted-foreground">Cost</p>
                        <p className="mt-1">
                          {attempt.actual_cost_units ?? 0} units
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
                ["Created", formatDate(session.created_at)],
                ["Admitted", formatDate(session.admitted_at)],
                ["Opened", formatDate(session.opened_at)],
                ["Closed", formatDate(session.closed_at)],
                ["Outcome reason", humanize(session.terminal_reason)],
              ].map(([label, value]) => (
                <div className="flex justify-between gap-4" key={label}>
                  <span className="text-muted-foreground">{label}</span>
                  <span className="text-right">{value}</span>
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
                  <Button
                    key={domain.id}
                    variant="outline"
                    className="w-full justify-between"
                    onClick={() => navigate(`/domains/${domain.id}`)}
                  >
                    <span className="truncate">{domain.hostname}</span>
                    <ExternalLink className="size-3.5" />
                  </Button>
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

export function SessionsPage({
  sessionId,
  navigate,
}: {
  sessionId?: string
  navigate: (href: string) => void
}) {
  return sessionId ? (
    <SessionDetailView sessionId={sessionId} navigate={navigate} />
  ) : (
    <SessionList navigate={navigate} />
  )
}
