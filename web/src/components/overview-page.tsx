import { useQuery } from "@tanstack/react-query"
import {
  Activity,
  AlertTriangle,
  ArrowRight,
  Boxes,
  CheckCircle2,
  CircleAlert,
  CircleDot,
  Clock3,
  Globe2,
  LoaderCircle,
  RefreshCw,
  Server,
  ShieldCheck,
  Waves,
} from "lucide-react"
import { useMemo } from "react"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card } from "@/components/ui/card"
import { extractApiError } from "@/lib/api"
import { cn } from "@/lib/utils"
import type {
  ActivityEvent,
  ActivityEventPage,
  ActivityProvider,
  CommandCostStat,
  DomainPage,
  GatewayFleetSnapshot,
  ProviderFleetSnapshot,
} from "@/types/api"

type OverviewPageProps = {
  navigate: (href: string) => void
}

type SystemStatus = {
  detail: string
  label: "Healthy" | "Scaling" | "Under pressure" | "Degraded"
  tone: "success" | "warning" | "danger"
}

const providerLabels: Record<ActivityProvider, string> = {
  http: "HTTP",
  browserbase: "Browserbase",
  browserless: "Browserless",
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

function formatDuration(seconds: number) {
  if (seconds <= 0) return "None"
  if (seconds < 60) return `${Math.round(seconds)}s`
  const roundedSeconds = Math.round(seconds)
  const minutes = Math.floor(roundedSeconds / 60)
  const remainder = roundedSeconds % 60
  return remainder ? `${minutes}m ${remainder}s` : `${minutes}m`
}

function formatMilliseconds(milliseconds: number) {
  if (milliseconds < 1_000) return `${milliseconds}ms`
  return formatDuration(milliseconds / 1_000)
}

function relativeTime(value: string) {
  const seconds = Math.round((new Date(value).getTime() - Date.now()) / 1000)
  const formatter = new Intl.RelativeTimeFormat(undefined, { numeric: "auto" })
  if (Math.abs(seconds) < 60) return formatter.format(seconds, "second")
  const minutes = Math.round(seconds / 60)
  if (Math.abs(minutes) < 60) return formatter.format(minutes, "minute")
  const hours = Math.round(minutes / 60)
  if (Math.abs(hours) < 24) return formatter.format(hours, "hour")
  return formatter.format(Math.round(hours / 24), "day")
}

function eventLabel(event: ActivityEvent) {
  return event.event_type
    .split(".")
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(" ")
}

function eventDetail(event: ActivityEvent) {
  const payload = event.payload
  if (typeof payload.reason === "string") return payload.reason
  if (typeof payload.method === "string") return payload.method
  if (
    typeof payload.from_provider === "string" &&
    typeof payload.to_provider === "string"
  ) {
    return `${payload.from_provider} → ${payload.to_provider}`
  }
  if (typeof payload.status === "number") return `HTTP ${payload.status}`
  return event.provider ? providerLabels[event.provider] : "Stolosio gateway"
}

function statusFor(
  providers: ProviderFleetSnapshot[],
  hasUnavailableData: boolean
): SystemStatus {
  if (
    hasUnavailableData ||
    providers.some((provider) => provider.unhealthy_instances > 0)
  ) {
    return {
      label: "Degraded",
      detail: hasUnavailableData
        ? "Some operational data is unavailable"
        : "One or more provider instances are unhealthy",
      tone: "danger",
    }
  }
  if (
    providers.some(
      (provider) =>
        provider.queued_attempts > 0 && provider.available_slots === 0
    )
  ) {
    return {
      label: "Under pressure",
      detail: "Provider demand is waiting for available capacity",
      tone: "warning",
    }
  }
  if (
    providers.some(
      (provider) =>
        provider.ready_instances < provider.desired_instances ||
        provider.observed_instances < provider.desired_instances
    )
  ) {
    return {
      label: "Scaling",
      detail: "Provider capacity is converging on desired state",
      tone: "warning",
    }
  }
  return {
    label: "Healthy",
    detail: "Gateway and provider capacity are ready",
    tone: "success",
  }
}

function StatusBadge({ status }: { status: SystemStatus }) {
  return (
    <Badge
      variant="outline"
      className={cn(
        "gap-1.5 rounded-full px-2.5 py-1",
        status.tone === "success" &&
          "border-emerald-500/25 bg-emerald-500/8 text-emerald-700 dark:text-emerald-400",
        status.tone === "warning" &&
          "border-amber-500/25 bg-amber-500/8 text-amber-700 dark:text-amber-400",
        status.tone === "danger" &&
          "border-destructive/25 bg-destructive/8 text-destructive"
      )}
    >
      <span
        className={cn(
          "size-1.5 rounded-full",
          status.tone === "success" && "bg-emerald-500",
          status.tone === "warning" && "bg-amber-500",
          status.tone === "danger" && "bg-destructive"
        )}
      />
      {status.label}
    </Badge>
  )
}

function MetricCard({
  detail,
  icon: Icon,
  label,
  value,
}: {
  detail: string
  icon: typeof Activity
  label: string
  value: number
}) {
  return (
    <Card className="gap-0 rounded-lg p-5">
      <div className="flex items-start justify-between">
        <div>
          <p className="text-xs font-medium text-muted-foreground">{label}</p>
          <p className="mt-2 text-2xl font-semibold tracking-tight">
            {numberFormatter.format(value)}
          </p>
        </div>
        <span className="flex size-9 items-center justify-center rounded-md border bg-muted/30">
          <Icon className="size-4 text-muted-foreground" aria-hidden />
        </span>
      </div>
      <p className="mt-3 text-xs text-muted-foreground">{detail}</p>
    </Card>
  )
}

function SectionError({ error, retry }: { error: unknown; retry: () => void }) {
  return (
    <div className="flex min-h-48 items-center justify-center px-6 py-8 text-center">
      <div>
        <CircleAlert className="mx-auto size-5 text-destructive" aria-hidden />
        <p className="mt-3 text-sm font-medium">Data unavailable</p>
        <p className="mt-1 max-w-sm text-xs text-muted-foreground">
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

export function OverviewPage({ navigate }: OverviewPageProps) {
  const gateway = useQuery({
    queryKey: ["gateway-fleet"],
    queryFn: () => apiRequest<GatewayFleetSnapshot>("/v1/fleet/gateway"),
    refetchInterval: 10_000,
  })
  const fleets = useQuery({
    queryKey: ["fleet-snapshots"],
    queryFn: () => apiRequest<ProviderFleetSnapshot[]>("/v1/fleet/providers"),
    refetchInterval: 10_000,
  })
  const activity = useQuery({
    queryKey: ["overview-activity"],
    queryFn: () => apiRequest<ActivityEventPage>("/v1/admin/events?limit=30"),
    refetchInterval: 10_000,
  })
  const domains = useQuery({
    queryKey: ["overview-domains"],
    queryFn: () => apiRequest<DomainPage>("/v1/admin/domains?limit=5"),
    refetchInterval: 30_000,
  })
  const commandCosts = useQuery({
    queryKey: ["command-costs"],
    queryFn: () =>
      apiRequest<CommandCostStat[]>("/v1/admin/command-costs?limit=100"),
    refetchInterval: 30_000,
  })

  const providers = useMemo(() => fleets.data ?? [], [fleets.data])
  const totals = useMemo(
    () =>
      providers.reduce(
        (result, provider) => ({
          activeAttempts: result.activeAttempts + provider.active_attempts,
          availableSlots: result.availableSlots + provider.available_slots,
          queuedAttempts: result.queuedAttempts + provider.queued_attempts,
          totalSlots: result.totalSlots + provider.total_slots,
          unhealthyInstances:
            result.unhealthyInstances + provider.unhealthy_instances,
        }),
        {
          activeAttempts: 0,
          availableSlots: 0,
          queuedAttempts: 0,
          totalSlots: 0,
          unhealthyInstances: 0,
        }
      ),
    [providers]
  )
  const systemStatus = statusFor(
    providers,
    Boolean(gateway.error || fleets.error)
  )
  const recentEvents = (activity.data?.events ?? [])
    .filter((event) => event.event_family !== "command")
    .slice(0, 8)
  const browserCommandCosts = (commandCosts.data ?? [])
    .filter((row) => row.attributed_browser_time_ms > 0)
    .slice(0, 10)

  const attention = providers.flatMap((provider) => {
    const items: {
      detail: string
      label: string
      provider: ActivityProvider
      tone: "danger" | "warning"
    }[] = []
    if (provider.unhealthy_instances > 0) {
      items.push({
        label: `${provider.unhealthy_instances} unhealthy ${
          provider.unhealthy_instances === 1 ? "instance" : "instances"
        }`,
        detail: "Provider capacity may be reduced",
        provider: provider.provider,
        tone: "danger",
      })
    }
    if (provider.queued_attempts > 0 && provider.available_slots === 0) {
      items.push({
        label: `${provider.queued_attempts} queued with no free slots`,
        detail: `${formatDuration(provider.oldest_queued_attempt_seconds)} oldest wait`,
        provider: provider.provider,
        tone: "warning",
      })
    } else if (
      provider.ready_instances < provider.desired_instances ||
      provider.observed_instances < provider.desired_instances
    ) {
      items.push({
        label: `${provider.ready_instances} of ${provider.desired_instances} desired ready`,
        detail: "Fleet is converging on desired capacity",
        provider: provider.provider,
        tone: "warning",
      })
    }
    return items
  })

  const refetchAll = () => {
    void gateway.refetch()
    void fleets.refetch()
    void activity.refetch()
    void domains.refetch()
    void commandCosts.refetch()
  }
  const isRefreshing =
    gateway.isFetching ||
    fleets.isFetching ||
    activity.isFetching ||
    domains.isFetching ||
    commandCosts.isFetching

  return (
    <main className="mx-auto w-full max-w-[100rem] px-4 py-6 sm:px-6 lg:px-8 lg:py-8">
      <div className="flex flex-col justify-between gap-4 sm:flex-row sm:items-start">
        <div>
          <div className="flex items-center gap-3">
            <h1 className="text-2xl font-semibold tracking-tight">Overview</h1>
            <StatusBadge status={systemStatus} />
          </div>
          <p className="mt-2 text-sm text-muted-foreground">
            {systemStatus.detail}
          </p>
        </div>
        <Button
          variant="outline"
          size="sm"
          className="w-fit gap-2"
          onClick={refetchAll}
          disabled={isRefreshing}
        >
          <RefreshCw
            className={cn("size-3.5", isRefreshing && "animate-spin")}
            aria-hidden
          />
          Refresh
        </Button>
      </div>

      <section className="mt-5 grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <MetricCard
          label="Sessions · 24h"
          value={gateway.data?.sessions_last_24h ?? 0}
          detail={`${numberFormatter.format(gateway.data?.active_sessions ?? 0)} active · ${numberFormatter.format(gateway.data?.capacity ?? 0)} capacity`}
          icon={CircleDot}
        />
        <MetricCard
          label="Active attempts"
          value={totals.activeAttempts}
          detail="Acquisitions using provider capacity"
          icon={Activity}
        />
        <MetricCard
          label="Queued attempts"
          value={totals.queuedAttempts}
          detail="Waiting across all providers"
          icon={Clock3}
        />
        <MetricCard
          label="Available slots"
          value={totals.availableSlots}
          detail={`${numberFormatter.format(totals.totalSlots)} total provider slots`}
          icon={Boxes}
        />
      </section>

      <div className="mt-5 grid gap-4 xl:grid-cols-[1.5fr_1fr]">
        <section className="overflow-hidden rounded-lg border bg-card">
          <div className="flex items-start justify-between gap-3 border-b px-4 py-3">
            <div>
              <h2 className="font-semibold">Provider capacity</h2>
              <p className="mt-1 text-sm text-muted-foreground">
                Current demand and readiness by provider.
              </p>
            </div>
            <Button
              variant="ghost"
              size="sm"
              className="gap-1.5 text-muted-foreground"
              onClick={() => navigate("/fleets")}
            >
              View fleets
              <ArrowRight className="size-3.5" aria-hidden />
            </Button>
          </div>
          {fleets.isLoading ? (
            <div className="flex min-h-64 items-center justify-center">
              <LoaderCircle
                className="size-5 animate-spin text-muted-foreground"
                aria-hidden
              />
            </div>
          ) : fleets.error ? (
            <SectionError
              error={fleets.error}
              retry={() => void fleets.refetch()}
            />
          ) : (
            <div className="divide-y">
              {providers.map((provider) => {
                const occupied = Math.max(
                  0,
                  provider.total_slots - provider.available_slots
                )
                const percentage =
                  provider.total_slots === 0
                    ? 0
                    : Math.min(100, (occupied / provider.total_slots) * 100)
                const hasIssue =
                  provider.unhealthy_instances > 0 ||
                  (provider.queued_attempts > 0 &&
                    provider.available_slots === 0)
                return (
                  <button
                    type="button"
                    key={provider.provider}
                    onClick={() => navigate(`/fleets/${provider.provider}`)}
                    className="grid w-full gap-3 px-4 py-3 text-left transition-colors hover:bg-muted/25 sm:grid-cols-[1.15fr_1fr_1fr_auto] sm:items-center"
                  >
                    <div className="flex items-center gap-3">
                      <span className="flex size-9 items-center justify-center rounded-md border bg-background">
                        <Server
                          className="size-4 text-muted-foreground"
                          aria-hidden
                        />
                      </span>
                      <div>
                        <p className="text-sm font-medium">
                          {providerLabels[provider.provider]}
                        </p>
                        <p
                          className={cn(
                            "mt-0.5 text-xs",
                            hasIssue
                              ? "text-amber-700 dark:text-amber-400"
                              : "text-muted-foreground"
                          )}
                        >
                          {provider.ready_instances} of{" "}
                          {provider.desired_instances} desired ready
                        </p>
                      </div>
                    </div>
                    <div>
                      <div className="flex items-center justify-between text-xs">
                        <span className="font-medium">
                          {occupied} / {provider.total_slots} occupied
                        </span>
                        <span className="text-muted-foreground">
                          {provider.available_slots} free
                        </span>
                      </div>
                      <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-muted">
                        <div
                          className={cn(
                            "h-full rounded-full",
                            percentage > 85 ? "bg-amber-500" : "bg-primary"
                          )}
                          style={{ width: `${percentage}%` }}
                        />
                      </div>
                    </div>
                    <div className="flex gap-5 text-xs sm:block">
                      <p>
                        <span className="font-medium">
                          {provider.active_attempts}
                        </span>{" "}
                        <span className="text-muted-foreground">active</span>
                      </p>
                      <p className="sm:mt-1">
                        <span className="font-medium">
                          {provider.queued_attempts}
                        </span>{" "}
                        <span className="text-muted-foreground">queued</span>
                      </p>
                    </div>
                    <ArrowRight
                      className="hidden size-4 text-muted-foreground sm:block"
                      aria-hidden
                    />
                  </button>
                )
              })}
              {!providers.length && (
                <div className="px-5 py-12 text-center text-sm text-muted-foreground">
                  No provider fleet snapshots are available.
                </div>
              )}
            </div>
          )}
        </section>

        <section className="overflow-hidden rounded-lg border bg-card">
          <div className="border-b px-4 py-3">
            <h2 className="font-semibold">Attention required</h2>
            <p className="mt-1 text-sm text-muted-foreground">
              Current operational conditions.
            </p>
          </div>
          <div className="p-3">
            {attention.length ? (
              <div className="space-y-1">
                {attention.map((item) => (
                  <button
                    type="button"
                    key={`${item.provider}-${item.label}`}
                    className="flex w-full items-start gap-3 rounded-md p-3 text-left hover:bg-muted/50"
                    onClick={() => navigate(`/fleets/${item.provider}`)}
                  >
                    {item.tone === "danger" ? (
                      <CircleAlert
                        className="mt-0.5 size-4 shrink-0 text-destructive"
                        aria-hidden
                      />
                    ) : (
                      <AlertTriangle
                        className="mt-0.5 size-4 shrink-0 text-amber-600"
                        aria-hidden
                      />
                    )}
                    <span>
                      <span className="block text-sm font-medium">
                        {providerLabels[item.provider]} · {item.label}
                      </span>
                      <span className="mt-1 block text-xs text-muted-foreground">
                        {item.detail}
                      </span>
                    </span>
                  </button>
                ))}
              </div>
            ) : gateway.error || fleets.error ? (
              <div className="flex items-start gap-3 rounded-md p-3">
                <CircleAlert
                  className="mt-0.5 size-4 shrink-0 text-destructive"
                  aria-hidden
                />
                <div>
                  <p className="text-sm font-medium">
                    Operational snapshot unavailable
                  </p>
                  <p className="mt-1 text-xs text-muted-foreground">
                    Refresh to retry the gateway and fleet requests.
                  </p>
                </div>
              </div>
            ) : (
              <div className="flex min-h-36 flex-col items-center justify-center text-center">
                <CheckCircle2
                  className="size-6 text-emerald-600 dark:text-emerald-400"
                  aria-hidden
                />
                <p className="mt-3 text-sm font-medium">No action needed</p>
                <p className="mt-1 text-xs text-muted-foreground">
                  Capacity is ready and no instances are unhealthy.
                </p>
              </div>
            )}
          </div>
        </section>
      </div>

      <div className="mt-5 grid gap-4 xl:grid-cols-[1.5fr_1fr]">
        <section className="overflow-hidden rounded-lg border bg-card">
          <div className="flex items-start justify-between gap-3 border-b px-4 py-3">
            <div>
              <h2 className="font-semibold">Recent activity</h2>
              <p className="mt-1 text-sm text-muted-foreground">
                Latest session, acquisition, and browser events.
              </p>
            </div>
            <Button
              variant="ghost"
              size="sm"
              className="gap-1.5 text-muted-foreground"
              onClick={() => navigate("/activity")}
            >
              View activity
              <ArrowRight className="size-3.5" aria-hidden />
            </Button>
          </div>
          {activity.isLoading ? (
            <div className="flex min-h-64 items-center justify-center">
              <LoaderCircle
                className="size-5 animate-spin text-muted-foreground"
                aria-hidden
              />
            </div>
          ) : activity.error ? (
            <SectionError
              error={activity.error}
              retry={() => void activity.refetch()}
            />
          ) : recentEvents.length ? (
            <div className="divide-y">
              {recentEvents.map((event) => (
                <div
                  key={event.event_id}
                  className="grid gap-2 px-4 py-3 sm:grid-cols-[1fr_auto] sm:items-center"
                >
                  <div className="flex min-w-0 items-start gap-3">
                    <span
                      className={cn(
                        "mt-1.5 size-2 shrink-0 rounded-full",
                        event.outcome === "failure"
                          ? "bg-destructive"
                          : event.outcome === "interrupted"
                            ? "bg-amber-500"
                            : "bg-emerald-500"
                      )}
                    />
                    <div className="min-w-0">
                      <p className="truncate text-sm font-medium">
                        {eventLabel(event)}
                      </p>
                      <p className="mt-0.5 truncate text-xs text-muted-foreground">
                        {eventDetail(event)} · Session{" "}
                        {event.session_id.slice(0, 8)}
                      </p>
                    </div>
                  </div>
                  <time
                    dateTime={event.occurred_at}
                    className="ml-5 text-xs text-muted-foreground sm:ml-0"
                  >
                    {relativeTime(event.occurred_at)}
                  </time>
                </div>
              ))}
            </div>
          ) : (
            <div className="flex min-h-48 flex-col items-center justify-center text-center">
              <Waves className="size-6 text-muted-foreground" aria-hidden />
              <p className="mt-3 text-sm font-medium">No recent activity</p>
              <p className="mt-1 text-xs text-muted-foreground">
                Session events will appear here as Stolosio handles work.
              </p>
            </div>
          )}
        </section>

        <section className="overflow-hidden rounded-lg border bg-card">
          <div className="flex items-start justify-between gap-3 border-b px-4 py-3">
            <div>
              <h2 className="font-semibold">Domain routing</h2>
              <p className="mt-1 text-sm text-muted-foreground">
                Current health and runtime eligibility.
              </p>
            </div>
            <Button
              variant="ghost"
              size="sm"
              className="gap-1.5 text-muted-foreground"
              onClick={() => navigate("/domains")}
            >
              View
              <ArrowRight className="size-3.5" aria-hidden />
            </Button>
          </div>
          {domains.isLoading ? (
            <div className="flex min-h-64 items-center justify-center">
              <LoaderCircle
                className="size-5 animate-spin text-muted-foreground"
                aria-hidden
              />
            </div>
          ) : domains.error ? (
            <SectionError
              error={domains.error}
              retry={() => void domains.refetch()}
            />
          ) : (
            <div className="p-4">
              <div className="grid grid-cols-2 gap-3">
                <div className="rounded-md border bg-muted/20 p-3">
                  <Globe2
                    className="size-4 text-muted-foreground"
                    aria-hidden
                  />
                  <p className="mt-3 text-xl font-semibold">
                    {numberFormatter.format(
                      domains.data?.summary.known_domains ?? 0
                    )}
                  </p>
                  <p className="mt-1 text-xs text-muted-foreground">
                    Known domains
                  </p>
                </div>
                <div className="rounded-md border bg-muted/20 p-3">
                  <ShieldCheck
                    className="size-4 text-muted-foreground"
                    aria-hidden
                  />
                  <p className="mt-3 text-xl font-semibold">
                    {numberFormatter.format(
                      domains.data?.summary.healthy_domains ?? 0
                    )}
                  </p>
                  <p className="mt-1 text-xs text-muted-foreground">Healthy</p>
                </div>
                <div className="rounded-md border bg-muted/20 p-3">
                  <LoaderCircle
                    className="size-4 text-muted-foreground"
                    aria-hidden
                  />
                  <p className="mt-3 text-xl font-semibold">
                    {numberFormatter.format(
                      domains.data?.summary.checking_domains ?? 0
                    )}
                  </p>
                  <p className="mt-1 text-xs text-muted-foreground">Checking</p>
                </div>
                <div className="rounded-md border bg-muted/20 p-3">
                  <Activity
                    className="size-4 text-muted-foreground"
                    aria-hidden
                  />
                  <p className="mt-3 text-xl font-semibold">
                    {numberFormatter.format(
                      domains.data?.summary.transitioned_domains ?? 0
                    )}
                  </p>
                  <p className="mt-1 text-xs text-muted-foreground">
                    Transitioned
                  </p>
                </div>
              </div>
              {(domains.data?.summary.unhealthy_domains ?? 0) > 0 && (
                <button
                  type="button"
                  className="mt-4 flex w-full items-center justify-between rounded-md border border-amber-500/20 bg-amber-500/5 p-3 text-left"
                  onClick={() => navigate("/domains")}
                >
                  <span className="flex items-center gap-2 text-sm">
                    <AlertTriangle
                      className="size-4 text-amber-600"
                      aria-hidden
                    />
                    {domains.data?.summary.unhealthy_domains ?? 0} domains need
                    review
                  </span>
                  <ArrowRight
                    className="size-4 text-muted-foreground"
                    aria-hidden
                  />
                </button>
              )}
            </div>
          )}
        </section>
      </div>

      <section className="mt-5 overflow-hidden rounded-lg border bg-card">
        <div className="border-b px-4 py-3">
          <h2 className="font-semibold">Browser time attribution</h2>
          <p className="mt-1 text-sm text-muted-foreground">
            Cumulative provider-and-method totals. Stolosio retains these
            aggregates, not individual successful commands.
          </p>
        </div>
        {commandCosts.isLoading ? (
          <div className="flex min-h-48 items-center justify-center">
            <LoaderCircle
              className="size-5 animate-spin text-muted-foreground"
              aria-hidden
            />
          </div>
        ) : commandCosts.error ? (
          <SectionError
            error={commandCosts.error}
            retry={() => void commandCosts.refetch()}
          />
        ) : browserCommandCosts.length ? (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[48rem] text-left">
              <thead className="border-b bg-muted/20 text-xs text-muted-foreground">
                <tr>
                  <th className="px-4 py-2.5 font-medium">Method</th>
                  <th className="px-4 py-2.5 font-medium">Provider</th>
                  <th className="px-4 py-2.5 text-right font-medium">
                    Browser time
                  </th>
                  <th className="px-4 py-2.5 text-right font-medium">
                    Commands
                  </th>
                  <th className="px-4 py-2.5 text-right font-medium">
                    Failed / interrupted
                  </th>
                  <th className="px-4 py-2.5 text-right font-medium">
                    Cost units
                  </th>
                </tr>
              </thead>
              <tbody className="divide-y">
                {browserCommandCosts.map((row) => (
                  <tr key={`${row.provider}-${row.method}`}>
                    <td className="max-w-md px-4 py-3">
                      <code className="text-xs">
                        {row.method === "__session_overhead__"
                          ? "Unattributed session"
                          : row.method === "__other__"
                            ? "Other methods"
                            : row.method}
                      </code>
                    </td>
                    <td className="px-4 py-3">
                      <Badge variant="secondary">
                        {providerLabels[row.provider]}
                      </Badge>
                    </td>
                    <td className="px-4 py-3 text-right text-sm font-medium tabular-nums">
                      {formatMilliseconds(row.attributed_browser_time_ms)}
                    </td>
                    <td className="px-4 py-3 text-right text-sm tabular-nums">
                      {row.method === "__session_overhead__"
                        ? "—"
                        : numberFormatter.format(row.command_count)}
                    </td>
                    <td className="px-4 py-3 text-right text-sm tabular-nums">
                      {numberFormatter.format(row.failed_count)} /{" "}
                      {numberFormatter.format(row.interrupted_count)}
                    </td>
                    <td className="px-4 py-3 text-right text-sm tabular-nums">
                      {numberFormatter.format(row.attributed_cost_units)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <div className="flex min-h-40 flex-col items-center justify-center text-center">
            <Clock3 className="size-6 text-muted-foreground" aria-hidden />
            <p className="mt-3 text-sm font-medium">
              No browser time attributed yet
            </p>
            <p className="mt-1 text-xs text-muted-foreground">
              Browserless and Browserbase command totals will appear after
              attempts complete.
            </p>
          </div>
        )}
      </section>
    </main>
  )
}
