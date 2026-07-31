import { useQuery } from "@tanstack/react-query"
import {
  ArrowRight,
  CircleAlert,
  Clock3,
  Coins,
  Gauge,
  RefreshCw,
} from "lucide-react"
import { useState } from "react"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { extractApiError } from "@/lib/api"
import type {
  ActivityProvider,
  CommandCostStat,
  CostOverview,
  CostWindow,
} from "@/types/api"

type CostPageProps = {
  navigate: (href: string) => void
}

const providerLabels: Record<ActivityProvider, string> = {
  http: "HTTP",
  browserless: "Browserless",
  browserbase: "Browserbase",
}

const windowLabels: Record<CostWindow, string> = {
  "24h": "Last 24 hours",
  "7d": "Last 7 days",
  "30d": "Last 30 days",
  "90d": "Last 90 days",
}

const numberFormatter = new Intl.NumberFormat()

async function apiRequest<T>(url: string): Promise<T> {
  const response = await fetch(url)
  if (!response.ok) {
    const body: unknown = await response.json().catch(() => undefined)
    throw new Error(extractApiError(body))
  }
  return (await response.json()) as T
}

function formatMilliseconds(value: number) {
  if (value < 1_000) return `${numberFormatter.format(value)}ms`
  const seconds = value / 1_000
  if (seconds < 60) return `${seconds.toFixed(seconds < 10 ? 1 : 0)}s`
  const minutes = seconds / 60
  if (minutes < 60) return `${minutes.toFixed(minutes < 10 ? 1 : 0)}m`
  const hours = minutes / 60
  return `${hours.toFixed(hours < 10 ? 1 : 0)}h`
}

function formatDate(value: string | null) {
  if (!value) return "Not finalized"
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(new Date(value))
}

function Metric({
  detail,
  icon: Icon,
  label,
  value,
}: {
  detail: string
  icon: typeof Coins
  label: string
  value: string
}) {
  return (
    <Card className="gap-0 p-5">
      <div className="flex items-start justify-between gap-4">
        <div>
          <p className="text-xs font-medium text-muted-foreground">{label}</p>
          <p className="mt-2 text-2xl font-semibold tracking-tight">{value}</p>
        </div>
        <span className="flex size-9 items-center justify-center rounded-md border bg-muted/30">
          <Icon className="size-4 text-muted-foreground" aria-hidden />
        </span>
      </div>
      <p className="mt-3 text-xs text-muted-foreground">{detail}</p>
    </Card>
  )
}

function CostHistory({ data }: { data: CostOverview }) {
  const grouped = new Map<
    string,
    Partial<Record<ActivityProvider, number>>
  >()
  for (const row of data.buckets) {
    const providers = grouped.get(row.started_at) ?? {}
    providers[row.provider] = row.modeled_cost_units
    grouped.set(row.started_at, providers)
  }
  const buckets = [...grouped.entries()].sort(([left], [right]) =>
    left.localeCompare(right)
  )
  const maximum = Math.max(
    1,
    ...buckets.map(([, providers]) =>
      Object.values(providers).reduce((total, value) => total + value, 0)
    )
  )
  const colors: Record<ActivityProvider, string> = {
    http: "bg-sky-500",
    browserless: "bg-emerald-500",
    browserbase: "bg-violet-500",
  }

  return (
    <div>
      <div className="flex h-40 items-end gap-1 border-b">
        {buckets.length ? (
          buckets.map(([startedAt, providers]) => {
            const total = Object.values(providers).reduce(
              (sum, value) => sum + value,
              0
            )
            return (
              <div
                className="flex h-full min-w-2 flex-1 flex-col justify-end"
                key={startedAt}
                title={`${formatDate(startedAt)} · ${numberFormatter.format(total)} units`}
              >
                <div
                  className="flex min-h-0 flex-col-reverse overflow-hidden rounded-t-sm"
                  style={{
                    height: `${Math.max(2, (total / maximum) * 100)}%`,
                  }}
                >
                  {(
                    [
                      "http",
                      "browserless",
                      "browserbase",
                    ] as ActivityProvider[]
                  ).map((provider) => {
                    const value = providers[provider] ?? 0
                    return value ? (
                      <span
                        className={colors[provider]}
                        key={provider}
                        style={{ flexGrow: value }}
                      />
                    ) : null
                  })}
                </div>
              </div>
            )
          })
        ) : (
          <p className="m-auto text-sm text-muted-foreground">
            No finalized usage in this window.
          </p>
        )}
      </div>
      <div className="mt-3 flex flex-wrap justify-end gap-3 text-xs text-muted-foreground">
        {(Object.keys(providerLabels) as ActivityProvider[]).map(
          (provider) => (
            <span className="flex items-center gap-1.5" key={provider}>
              <span className={`size-2 rounded-sm ${colors[provider]}`} />
              {providerLabels[provider]}
            </span>
          )
        )}
      </div>
    </div>
  )
}

function QueryError({
  error,
  retry,
}: {
  error: unknown
  retry: () => void
}) {
  return (
    <div className="flex min-h-48 items-center justify-center p-8 text-center">
      <div>
        <CircleAlert className="mx-auto size-5 text-destructive" aria-hidden />
        <p className="mt-3 text-sm font-medium">Cost data unavailable</p>
        <p className="mt-1 text-xs text-muted-foreground">
          {extractApiError(error)}
        </p>
        <Button
          variant="outline"
          size="sm"
          className="mt-4 gap-2"
          onClick={retry}
        >
          <RefreshCw className="size-3.5" aria-hidden />
          Try again
        </Button>
      </div>
    </div>
  )
}

export function CostPage({ navigate }: CostPageProps) {
  const [window, setWindow] = useState<CostWindow>("7d")
  const overview = useQuery({
    queryKey: ["cost-overview", window],
    queryFn: () =>
      apiRequest<CostOverview>(`/v1/admin/costs/overview?window=${window}`),
    refetchInterval: 30_000,
  })
  const commandCosts = useQuery({
    queryKey: ["command-costs", "attributed-cost"],
    queryFn: () =>
      apiRequest<CommandCostStat[]>(
        "/v1/admin/command-costs?include_overhead=true&sort_by=cost&limit=100"
      ),
    refetchInterval: 30_000,
  })

  const data = overview.data
  const attributedCommands = (commandCosts.data ?? []).filter(
    (row) =>
      row.attributed_cost_units > 0 || row.attributed_browser_time_ms > 0
  )
  const maxCommandCost = Math.max(
    1,
    ...attributedCommands.map((row) => row.attributed_cost_units)
  )

  return (
    <main className="mx-auto w-full max-w-7xl px-4 py-8 sm:px-6 lg:px-10">
      <div className="flex flex-col justify-between gap-4 sm:flex-row sm:items-end">
        <div>
          <p className="font-mono text-xs font-medium tracking-[0.18em] text-muted-foreground uppercase">
            Usage accounting
          </p>
          <h1 className="mt-2 text-3xl font-semibold tracking-tight">Cost</h1>
          <p className="mt-2 max-w-2xl text-sm text-muted-foreground">
            Modeled provider spend and bounded CDP method attribution. These
            values use the rate captured when each attempt finished.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Select
            value={window}
            onValueChange={(value) => setWindow(value as CostWindow)}
          >
            <SelectTrigger className="h-9 w-44 bg-background">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {Object.entries(windowLabels).map(([value, label]) => (
                <SelectItem key={value} value={value}>
                  {label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Button
            variant="outline"
            size="icon"
            aria-label="Refresh cost data"
            disabled={overview.isFetching || commandCosts.isFetching}
            onClick={() => {
              void overview.refetch()
              void commandCosts.refetch()
            }}
          >
            <RefreshCw
              className={
                overview.isFetching || commandCosts.isFetching
                  ? "animate-spin"
                  : undefined
              }
              aria-hidden
            />
          </Button>
        </div>
      </div>

      {overview.isLoading ? (
        <div className="mt-8 h-40 animate-pulse rounded-xl bg-muted/50" />
      ) : overview.error || !data ? (
        <Card className="mt-8">
          <QueryError
            error={overview.error}
            retry={() => void overview.refetch()}
          />
        </Card>
      ) : (
        <>
          <div className="mt-8 grid gap-3 sm:grid-cols-2 xl:grid-cols-5">
            <Metric
              icon={Coins}
              label="Modeled cost"
              value={`${numberFormatter.format(data.totals.modeled_cost_units)} units`}
              detail={`${numberFormatter.format(data.totals.attempt_count)} finalized attempts`}
            />
            <Metric
              icon={Gauge}
              label="Browserless slot time"
              value={formatMilliseconds(
                data.totals.browserless_slot_time_ms
              )}
              detail="Time occupying managed Browserless capacity"
            />
            <Metric
              icon={Clock3}
              label="Browserbase billable"
              value={formatMilliseconds(
                data.totals.browserbase_billable_time_ms
              )}
              detail="Estimated billable time, including minimums"
            />
            <Metric
              icon={Clock3}
              label="Browser-connected"
              value={formatMilliseconds(
                data.totals.browser_connected_time_ms
              )}
              detail="Measured connected time, excluding the HTTP path"
            />
            <Metric
              icon={CircleAlert}
              label="Sessions with usage"
              value={numberFormatter.format(data.totals.session_count)}
              detail={`${numberFormatter.format(data.totals.attempt_count)} finalized attempts · ${numberFormatter.format(data.totals.failed_attempt_count)} failed`}
            />
          </div>

          <Card className="mt-6">
            <CardHeader className="pt-5">
              <CardTitle>Modeled cost over time</CardTitle>
              <CardDescription>
                Finalized attempts grouped by provider.
              </CardDescription>
            </CardHeader>
            <CardContent className="mt-3 pb-5">
              <CostHistory data={data} />
            </CardContent>
          </Card>

          <div className="mt-6 grid gap-6 xl:grid-cols-[1.25fr_0.75fr]">
            <Card>
              <CardHeader className="pt-5">
                <CardTitle>Provider usage</CardTitle>
                <CardDescription>
                  Chargeable basis and modeled cost by execution path.
                </CardDescription>
              </CardHeader>
              <CardContent className="mt-4 px-0 pb-2">
                {data.providers.length ? (
                  <div className="overflow-x-auto">
                    <table className="w-full min-w-160 text-sm">
                      <thead>
                        <tr className="border-y bg-muted/30 text-left text-xs text-muted-foreground">
                          <th className="px-4 py-2.5 font-medium">Provider</th>
                          <th className="px-4 py-2.5 text-right font-medium">
                            Attempts
                          </th>
                          <th className="px-4 py-2.5 text-right font-medium">
                            Chargeable
                          </th>
                          <th className="px-4 py-2.5 text-right font-medium">
                            Browser time
                          </th>
                          <th className="px-4 py-2.5 text-right font-medium">
                            Modeled cost
                          </th>
                        </tr>
                      </thead>
                      <tbody>
                        {data.providers.map((row) => (
                          <tr
                            key={row.provider}
                            className="border-b last:border-0"
                          >
                            <td className="px-4 py-3">
                              <Badge variant="secondary">
                                {providerLabels[row.provider]}
                              </Badge>
                            </td>
                            <td className="px-4 py-3 text-right tabular-nums">
                              {numberFormatter.format(row.attempt_count)}
                            </td>
                            <td className="px-4 py-3 text-right tabular-nums">
                              {formatMilliseconds(row.chargeable_time_ms)}
                            </td>
                            <td className="px-4 py-3 text-right tabular-nums">
                              {formatMilliseconds(
                                row.browser_connected_time_ms
                              )}
                            </td>
                            <td className="px-4 py-3 text-right font-medium tabular-nums">
                              {numberFormatter.format(row.modeled_cost_units)}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                ) : (
                  <p className="px-4 py-12 text-center text-sm text-muted-foreground">
                    No finalized attempts in this window.
                  </p>
                )}
              </CardContent>
            </Card>

            <Card>
              <CardHeader className="pt-5">
                <CardTitle>Highest-cost sessions</CardTitle>
                <CardDescription>
                  Sessions ordered by finalized attempt cost.
                </CardDescription>
              </CardHeader>
              <CardContent className="mt-3 px-0 pb-2">
                {data.recent_sessions.length ? (
                  data.recent_sessions.map((session) => (
                    <button
                      type="button"
                      key={session.session_id}
                      className="flex w-full items-center justify-between gap-4 border-t px-4 py-3 text-left transition-colors hover:bg-muted/40"
                      onClick={() =>
                        navigate(`/sessions/${session.session_id}`)
                      }
                    >
                      <div className="min-w-0">
                        <p className="truncate text-sm font-medium">
                          {session.client_reference ?? session.session_id}
                        </p>
                        <p className="mt-1 text-xs text-muted-foreground">
                          {session.providers
                            .map((provider) => providerLabels[provider])
                            .join(" → ")}{" "}
                          · {formatDate(session.closed_at)}
                        </p>
                      </div>
                      <div className="flex shrink-0 items-center gap-2">
                        <span className="text-sm font-medium tabular-nums">
                          {numberFormatter.format(
                            session.modeled_cost_units
                          )}
                        </span>
                        <ArrowRight
                          className="size-3.5 text-muted-foreground"
                          aria-hidden
                        />
                      </div>
                    </button>
                  ))
                ) : (
                  <p className="px-4 py-12 text-center text-sm text-muted-foreground">
                    No finalized sessions in this window.
                  </p>
                )}
              </CardContent>
            </Card>
          </div>
        </>
      )}

      <Card className="mt-6">
        <CardHeader className="pt-5">
          <div className="flex flex-wrap items-center gap-2">
            <CardTitle>CDP action cost attribution</CardTitle>
            <Badge variant="outline">All time</Badge>
          </div>
          <CardDescription>
            Cumulative bounded attribution. Connect, idle, disconnect, and
            billing-minimum cost is retained as unattributed session cost.
          </CardDescription>
        </CardHeader>
        <CardContent className="mt-4 pb-5">
          {commandCosts.isLoading ? (
            <div className="h-40 animate-pulse rounded-lg bg-muted/50" />
          ) : commandCosts.error ? (
            <QueryError
              error={commandCosts.error}
              retry={() => void commandCosts.refetch()}
            />
          ) : attributedCommands.length ? (
            <div className="space-y-4">
              {attributedCommands.map((row) => (
                <div
                  key={`${row.provider}:${row.method}`}
                  className="grid gap-2 sm:grid-cols-[minmax(12rem,0.7fr)_minmax(14rem,1fr)_auto]"
                >
                  <div className="min-w-0">
                    <p className="truncate font-mono text-xs">
                      {row.method === "__session_overhead__"
                        ? "Unattributed session"
                        : row.method === "__other__"
                          ? "Other bounded methods"
                          : row.method}
                    </p>
                    <p className="mt-0.5 text-xs text-muted-foreground">
                      {providerLabels[row.provider]} ·{" "}
                      {row.command_count
                        ? `${numberFormatter.format(row.command_count)} commands · ${numberFormatter.format(row.failed_count + row.interrupted_count)} unsuccessful`
                        : "connect, idle, disconnect, and billing minimum"}
                    </p>
                  </div>
                  <div className="flex items-center">
                    <div className="h-2 w-full overflow-hidden rounded-full bg-muted">
                      <div
                        className="h-full rounded-full bg-primary"
                        style={{
                          width: `${Math.max(
                            1,
                            (row.attributed_cost_units / maxCommandCost) *
                              100
                          )}%`,
                        }}
                      />
                    </div>
                  </div>
                  <div className="text-right">
                    <p className="text-sm font-medium tabular-nums">
                      {numberFormatter.format(row.attributed_cost_units)} units
                    </p>
                    <p className="mt-0.5 text-xs text-muted-foreground">
                      {formatMilliseconds(row.attributed_browser_time_ms)}
                    </p>
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <p className="py-12 text-center text-sm text-muted-foreground">
              No command cost attribution has been projected yet.
            </p>
          )}
        </CardContent>
      </Card>

      {data?.finalized_through && (
        <p className="mt-4 text-right text-xs text-muted-foreground">
          Finalized through {formatDate(data.finalized_through)}
        </p>
      )}
    </main>
  )
}
