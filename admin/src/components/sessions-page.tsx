import { providerLabels } from "@/lib/session-format"
import { SessionDetailView } from "@/components/session-detail-view"
import { StateBadge, ProviderPath } from "@/components/session-presentation"
import { formatDate, formatDuration, humanize } from "@/lib/session-format"
import { useInfiniteQuery } from "@tanstack/react-query"
import { CircleDot, LoaderCircle, Search } from "lucide-react"
import { useEffect, useMemo, useState } from "react"

import { Metric, WindowPicker } from "@/components/observability"
import {
  duration,
  number,
  percent,
  plural,
  useOverview,
} from "@/lib/observability"
import { Button } from "@/components/ui/button"
import { Card, CardContent } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { apiRequest, extractApiError } from "@/lib/api"
import type { OverviewWindow, SessionPage } from "@/types/api"

function SessionList({ navigate }: { navigate: (href: string) => void }) {
  const [searchInput, setSearchInput] = useState(
    new URLSearchParams(window.location.search).get("search") ?? ""
  )
  const [search, setSearch] = useState(
    new URLSearchParams(window.location.search).get("search") ?? ""
  )
  const [state, setState] = useState(
    new URLSearchParams(window.location.search).get("state") ?? "all"
  )
  const [period, setPeriod] = useState<OverviewWindow>(() => {
    const value = new URLSearchParams(window.location.search).get("window")
    return value === "7d" || value === "30d" ? value : "24h"
  })
  const overview = useOverview(period)
  const [reason, setReason] = useState(
    new URLSearchParams(window.location.search).get("reason") ?? ""
  )
  const [provider, setProvider] = useState(
    new URLSearchParams(window.location.search).get("provider") ?? "all"
  )

  useEffect(() => {
    const timeout = window.setTimeout(() => setSearch(searchInput.trim()), 250)
    return () => window.clearTimeout(timeout)
  }, [searchInput])

  const queryString = useMemo(() => {
    const params = new URLSearchParams({
      limit: "50",
      workload: "automation",
      window: period,
    })
    if (search) params.set("search", search)
    if (reason) params.set("reason", reason)
    if (state !== "all") params.set("state", state)
    if (provider !== "all") params.set("provider", provider)
    return params.toString()
  }, [provider, search, state, period, reason])

  const sessions = useInfiniteQuery({
    queryKey: ["sessions", queryString],
    queryFn: ({ pageParam }) =>
      apiRequest<SessionPage>(
        `/v1/admin/sessions?${queryString}${pageParam ? `&before=${encodeURIComponent(pageParam)}` : ""}`
      ),
    initialPageParam: "",
    getNextPageParam: (page) => page.next_cursor ?? undefined,
    refetchInterval: 10_000,
  })

  const listParams = new URLSearchParams({ window: period })
  if (state !== "all") listParams.set("state", state)
  if (provider !== "all") listParams.set("provider", provider)
  if (search) listParams.set("search", search)
  if (reason) listParams.set("reason", reason)
  const listUrl = listParams.toString()
  useEffect(() => {
    window.history.replaceState({}, "", `/sessions?${listUrl}`)
  }, [listUrl])
  const rows = sessions.data?.pages.flatMap((page) => page.sessions) ?? []
  const stats = overview.data?.automation

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
            Inspect browser connections, command failures, and resource usage.
          </p>
        </div>
        <WindowPicker
          value={period}
          onChange={(value) => {
            setPeriod(value)
          }}
        />
      </div>

      {stats && (
        <div className="mt-6 grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
          <Metric
            label="In-progress sessions"
            value={number(stats.active)}
            detail="Sessions holding global gateway capacity"
          />
          <Metric
            label="Closed normally"
            value={number(stats.counts.closed ?? 0)}
            detail={`${percent(stats.counts.closed ?? 0, (stats.counts.closed ?? 0) + (stats.counts.failed ?? 0))} of terminal sessions`}
          />
          <Metric
            label="Commands failed / interrupted"
            value={`${number(stats.failed_commands)} / ${number(stats.interrupted_commands)}`}
            detail={`${number(stats.command_count)} commands in retained summaries`}
          />
          <Metric
            label="Median session length"
            value={duration(stats.median_duration_ms)}
            detail={`p95 ${duration(stats.p95_duration_ms)}`}
          />
        </div>
      )}
      {overview.isError && (
        <p className="mt-4 text-sm text-destructive">
          Session totals unavailable: {extractApiError(overview.error)}
        </p>
      )}
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

      {reason && (
        <div className="mt-3 flex items-center gap-2 text-xs text-muted-foreground">
          <span>Reason: {humanize(reason)}</span>
          <Button variant="ghost" size="sm" onClick={() => setReason("")}>
            Clear reason filter
          </Button>
        </div>
      )}
      <Card className="mt-4 overflow-hidden py-0">
        <div className="hidden grid-cols-[1.45fr_1.15fr_1fr_.65fr_.55fr] gap-3 border-b bg-muted/35 px-4 py-2 text-xs font-medium text-muted-foreground md:grid">
          <span>Session</span>
          <span>Browser provider</span>
          <span>Domains</span>
          <span>State</span>
          <span className="min-w-0 text-right break-all">Cost</span>
        </div>
        {sessions.isLoading ? (
          <div className="flex h-52 items-center justify-center">
            <LoaderCircle className="size-5 animate-spin text-muted-foreground" />
          </div>
        ) : sessions.isError ? (
          <div className="p-8 text-sm text-destructive">
            {extractApiError(sessions.error)}
          </div>
        ) : rows.length ? (
          <div className="divide-y">
            {rows.map((session) => (
              <button
                type="button"
                key={session.id}
                onClick={() => navigate(`/sessions/${session.id}?${listUrl}`)}
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
                  {session.modeled_cost_units} units
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
      {sessions.hasNextPage && (
        <div className="mt-4 text-center">
          <Button
            variant="outline"
            disabled={sessions.isFetchingNextPage}
            onClick={() => void sessions.fetchNextPage()}
          >
            {sessions.isFetchingNextPage ? "Loading…" : "Load more sessions"}
          </Button>
        </div>
      )}
      <p className="mt-4 text-xs leading-5 text-muted-foreground">
        {plural(rows.length, "session")} shown. Normal closure describes the
        connection, not the caller’s task outcome. Totals cover the period,
        independent of list filters.
      </p>
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
