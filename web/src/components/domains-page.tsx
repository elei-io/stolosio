import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import {
  ArrowLeft,
  ArrowRight,
  Check,
  ChevronRight,
  CircleAlert,
  CircleDot,
  FlaskConical,
  Globe2,
  LoaderCircle,
  Minus,
  RefreshCw,
  Search,
  ShieldCheck,
  Sparkles,
  X,
} from "lucide-react"
import { useEffect, useMemo, useState } from "react"
import { toast } from "sonner"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card } from "@/components/ui/card"
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
  ActivityProvider,
  DomainHealthCheck,
  DomainDetail,
  DomainListItem,
  DomainPage,
  DomainPlan,
  DomainProbePage,
  DomainProviderEvidence,
  DomainSessionPage,
  TriggerDomainProbesResponse,
} from "@/types/api"

const providerLabels: Record<ActivityProvider, string> = {
  http: "HTTP",
  browserbase: "Browserbase",
  browserless: "Browserless",
}

const eligibilityFilterLabels: Record<string, string> = {
  all: "All routing states",
  eligible: "Eligible",
  checking: "Checking",
  unhealthy: "Unhealthy",
  inconclusive: "Inconclusive",
  unknown: "Unknown",
}

async function apiRequest<T>(url: string): Promise<T> {
  const response = await fetch(url)
  if (!response.ok) {
    const body: unknown = await response.json().catch(() => undefined)
    throw new Error(extractApiError(body))
  }
  return (await response.json()) as T
}

async function triggerDomainProbes({
  domainId,
  providers,
}: {
  domainId: number
  providers?: ActivityProvider[]
}): Promise<TriggerDomainProbesResponse> {
  const response = await fetch(`/v1/admin/domains/${domainId}/probes`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(providers ? { providers } : {}),
  })
  const body: unknown = await response.json().catch(() => undefined)
  if (!response.ok) {
    throw new Error(extractApiError(body))
  }
  return body as TriggerDomainProbesResponse
}

function formatNumber(value: number) {
  return new Intl.NumberFormat().format(value)
}

function formatDate(value: string) {
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(new Date(value))
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

function planLabel(plan: DomainPlan) {
  if (plan.reason === "no_eligible_provider") return "No eligible provider"
  if (!plan.candidates.length) return "No plan"
  return plan.candidates
    .map((candidate) => providerLabels[candidate.provider])
    .join(" → ")
}

function PlanDescription({ plan }: { plan: DomainPlan }) {
  if (plan.reason === "configured_default_bootstrap") {
    return <span>Configured bootstrap provider</span>
  }
  if (plan.reason === "adaptive_browser_required") {
    return <span>Browser evidence currently favors Browserless</span>
  }
  if (plan.reason === "adaptive_http_exploration") {
    return <span>Bounded HTTP canary for adaptive routing</span>
  }
  if (plan.reason === "local_correctness_fallback") {
    return <span>Local correctness attempt required before paid fallback</span>
  }
  if (plan.reason === "no_eligible_provider") {
    return <span className="text-destructive">No eligible path</span>
  }
  return (
    <span>
      Cheapest eligible first
      {plan.paid_fallback_available ? " · paid fallback opt-in available" : ""}
    </span>
  )
}

function LoadingState({ label }: { label: string }) {
  return (
    <div className="flex min-h-96 items-center justify-center rounded-lg border bg-card">
      <div className="text-center">
        <LoaderCircle
          className="mx-auto size-5 animate-spin text-muted-foreground"
          aria-hidden
        />
        <p className="mt-3 text-sm text-muted-foreground">{label}</p>
      </div>
    </div>
  )
}

function ErrorState({
  error,
  retry,
  label = "Domain routing evidence is unavailable",
}: {
  error: unknown
  retry: () => void
  label?: string
}) {
  return (
    <div className="flex min-h-96 items-center justify-center rounded-lg border bg-card px-6">
      <div className="max-w-md text-center">
        <CircleAlert className="mx-auto size-6 text-destructive" aria-hidden />
        <h2 className="mt-4 font-semibold">{label}</h2>
        <p className="mt-2 text-sm leading-6 text-muted-foreground">
          {extractApiError(error)}
        </p>
        <Button variant="outline" className="mt-5 gap-2" onClick={retry}>
          <RefreshCw className="size-4" aria-hidden />
          Try again
        </Button>
      </div>
    </div>
  )
}

function SummaryCard({
  label,
  value,
  detail,
  icon: Icon,
}: {
  label: string
  value: number
  detail: string
  icon: typeof Globe2
}) {
  return (
    <Card className="gap-0 rounded-lg p-4">
      <div className="flex items-start justify-between">
        <div>
          <p className="text-xs font-medium text-muted-foreground">{label}</p>
          <p className="mt-2 text-2xl font-semibold tracking-tight">
            {formatNumber(value)}
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

function HealthSummary({ domain }: { domain: DomainListItem }) {
  const parts = []
  if (domain.health_counts.healthy) {
    parts.push(`${domain.health_counts.healthy} healthy`)
  }
  if (domain.health_counts.checking) {
    parts.push(`${domain.health_counts.checking} checking`)
  }
  if (domain.health_counts.unhealthy) {
    parts.push(`${domain.health_counts.unhealthy} unhealthy`)
  }
  return <span>{parts.length ? parts.join(" · ") : "No health evidence"}</span>
}

function DomainIndex({ navigate }: { navigate: (href: string) => void }) {
  const [search, setSearch] = useState("")
  const [debouncedSearch, setDebouncedSearch] = useState("")
  const [eligibility, setEligibility] = useState("all")
  const [transitionsOnly, setTransitionsOnly] = useState(false)
  const [activeProbesOnly, setActiveProbesOnly] = useState(false)
  const [cursor, setCursor] = useState<string | null>(null)
  const [cursorHistory, setCursorHistory] = useState<(string | null)[]>([])

  useEffect(() => {
    const timer = window.setTimeout(() => {
      const value = search.trim()
      setDebouncedSearch((current) => {
        if (current !== value) {
          setCursor(null)
          setCursorHistory([])
        }
        return value
      })
    }, 250)
    return () => window.clearTimeout(timer)
  }, [search])

  const query = useMemo(() => {
    const params = new URLSearchParams({ limit: "100" })
    if (debouncedSearch) params.set("search", debouncedSearch)
    if (eligibility !== "all") params.set("eligibility_state", eligibility)
    if (transitionsOnly) params.set("has_transitions", "true")
    if (activeProbesOnly) params.set("has_active_probes", "true")
    if (cursor) params.set("before", cursor)
    return params.toString()
  }, [activeProbesOnly, cursor, debouncedSearch, transitionsOnly, eligibility])
  const domains = useQuery({
    queryKey: ["domains", query],
    queryFn: () => apiRequest<DomainPage>(`/v1/admin/domains?${query}`),
  })
  const resetPagination = () => {
    setCursor(null)
    setCursorHistory([])
  }

  if (domains.isLoading) return <LoadingState label="Loading domain health" />
  if (domains.isError) {
    return (
      <ErrorState error={domains.error} retry={() => void domains.refetch()} />
    )
  }
  const page = domains.data
  if (!page) return null

  return (
    <>
      <section className="grid gap-3 sm:grid-cols-2 xl:grid-cols-5">
        <SummaryCard
          label="Known domains"
          value={page.summary.known_domains}
          detail="Observed by Harbor"
          icon={Globe2}
        />
        <SummaryCard
          label="Healthy"
          value={page.summary.healthy_domains}
          detail="At least one healthy provider"
          icon={ShieldCheck}
        />
        <SummaryCard
          label="Checking"
          value={page.summary.checking_domains}
          detail="Health verification in progress"
          icon={FlaskConical}
        />
        <SummaryCard
          label="No evidence"
          value={page.summary.no_evidence_domains}
          detail="Will use the configured default"
          icon={CircleDot}
        />
        <SummaryCard
          label="Transitioned"
          value={page.summary.transitioned_domains}
          detail="Runtime requirements changed"
          icon={Sparkles}
        />
      </section>

      <section className="mt-6 overflow-hidden rounded-lg border bg-card">
        <div className="flex flex-col gap-3 border-b px-4 py-3 xl:flex-row xl:items-end xl:justify-between">
          <div>
            <h2 className="font-semibold">Observed domains</h2>
            <p className="mt-1 text-sm text-muted-foreground">
              Health evidence and the resulting cheapest route.
            </p>
          </div>
          <div className="flex flex-col gap-2 sm:flex-row sm:flex-wrap">
            <span className="relative min-w-64">
              <Search className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-muted-foreground" />
              <Input
                value={search}
                onChange={(event) => setSearch(event.target.value)}
                placeholder="Search hostname"
                aria-label="Search domains"
                className="h-9 pl-9"
              />
            </span>
            <Select
              value={eligibility}
              onValueChange={(value) => {
                setEligibility(value ?? "all")
                resetPagination()
              }}
            >
              <SelectTrigger className="h-9 w-full sm:w-44">
                <SelectValue>
                  {eligibilityFilterLabels[eligibility]}
                </SelectValue>
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">All routing states</SelectItem>
                <SelectItem value="eligible">Eligible</SelectItem>
                <SelectItem value="checking">Checking</SelectItem>
                <SelectItem value="unhealthy">Unhealthy</SelectItem>
                <SelectItem value="inconclusive">Inconclusive</SelectItem>
                <SelectItem value="unknown">Unknown</SelectItem>
              </SelectContent>
            </Select>
            <Button
              variant={activeProbesOnly ? "secondary" : "outline"}
              size="sm"
              className="h-9"
              onClick={() => {
                setActiveProbesOnly((value) => !value)
                resetPagination()
              }}
            >
              Active checks
            </Button>
            <Button
              variant={transitionsOnly ? "secondary" : "outline"}
              size="sm"
              className="h-9"
              onClick={() => {
                setTransitionsOnly((value) => !value)
                resetPagination()
              }}
            >
              Live escalations
            </Button>
          </div>
        </div>

        {page.domains.length ? (
          <>
            <div className="hidden min-w-[54rem] grid-cols-[1.4fr_1.45fr_1.25fr_.65fr_.65fr_2rem] gap-2 border-b bg-muted/35 px-4 py-2 font-mono text-[0.625rem] font-medium tracking-wider text-muted-foreground uppercase md:grid">
              <span>Domain</span>
              <span>Expected plan</span>
              <span>Provider evidence</span>
              <span>Sessions</span>
              <span>Live escalations</span>
              <span />
            </div>
            <div className="divide-y">
              {page.domains.map((domain) => {
                const href = `/domains/${domain.id}`
                return (
                  <a
                    key={domain.id}
                    href={href}
                    onClick={(event) => {
                      event.preventDefault()
                      navigate(href)
                    }}
                    className="group block px-4 py-3 transition-colors hover:bg-muted/25 md:grid md:min-w-[54rem] md:grid-cols-[1.4fr_1.45fr_1.25fr_.65fr_.65fr_2rem] md:items-center md:gap-2"
                  >
                    <div className="min-w-0">
                      <p className="truncate font-medium">{domain.hostname}</p>
                      <p
                        className="mt-1 text-xs text-muted-foreground"
                        title={formatDate(domain.last_seen_at)}
                      >
                        Last seen {relativeTime(domain.last_seen_at)}
                      </p>
                    </div>
                    <div className="mt-4 min-w-0 md:mt-0">
                      <p className="truncate text-sm font-medium">
                        {planLabel(domain.expected_plan)}
                      </p>
                      <p className="mt-1 text-xs text-muted-foreground">
                        <PlanDescription plan={domain.expected_plan} />
                      </p>
                    </div>
                    <div className="mt-4 text-sm text-muted-foreground md:mt-0">
                      <HealthSummary domain={domain} />
                      {domain.active_probe_count > 0 && (
                        <p className="mt-1 text-xs text-amber-700 dark:text-amber-400">
                          {domain.active_probe_count} active
                        </p>
                      )}
                    </div>
                    <p className="mt-4 text-sm font-medium md:mt-0">
                      {formatNumber(domain.session_count)}
                    </p>
                    <p className="mt-4 text-sm font-medium md:mt-0">
                      {formatNumber(domain.transition_count)}
                    </p>
                    <ChevronRight className="hidden size-4 text-muted-foreground transition-transform group-hover:translate-x-0.5 md:block" />
                  </a>
                )
              })}
            </div>
            {(cursorHistory.length > 0 || page.next_cursor) && (
              <div className="flex items-center justify-between border-t bg-muted/10 px-4 py-2.5">
                <p className="text-xs text-muted-foreground">
                  Page {cursorHistory.length + 1} · {page.domains.length}{" "}
                  domains
                </p>
                <div className="flex gap-2">
                  <Button
                    variant="outline"
                    size="sm"
                    disabled={!cursorHistory.length || domains.isFetching}
                    onClick={() => {
                      setCursor(cursorHistory.at(-1) ?? null)
                      setCursorHistory((current) => current.slice(0, -1))
                    }}
                  >
                    <ArrowLeft className="size-3.5" /> Previous
                  </Button>
                  <Button
                    variant="outline"
                    size="sm"
                    disabled={!page.next_cursor || domains.isFetching}
                    onClick={() => {
                      setCursorHistory((current) => [...current, cursor])
                      setCursor(page.next_cursor)
                    }}
                  >
                    Next <ArrowRight className="size-3.5" />
                  </Button>
                </div>
              </div>
            )}
          </>
        ) : (
          <div className="flex min-h-72 items-center justify-center text-sm text-muted-foreground">
            No matching domains.
          </div>
        )}
      </section>
    </>
  )
}

function CheckCell({ check }: { check: DomainHealthCheck }) {
  const metadata = {
    healthy: {
      label: "OK",
      icon: Check,
      className: "text-emerald-700 dark:text-emerald-400",
    },
    unhealthy: {
      label: "Unhealthy",
      icon: X,
      className: "text-destructive",
    },
    inconclusive: {
      label: "Inconclusive",
      icon: CircleAlert,
      className: "text-amber-700 dark:text-amber-400",
    },
    checking: {
      label: "Checking",
      icon: LoaderCircle,
      className: "text-amber-700 dark:text-amber-400",
    },
    not_checked: {
      label: "Not checked",
      icon: Minus,
      className: "text-muted-foreground",
    },
  }[check.state]
  const Icon = metadata.icon
  return (
    <div>
      <p
        className={cn(
          "flex items-center gap-1 text-xs font-medium",
          metadata.className
        )}
      >
        <Icon
          className={cn("size-3", check.state === "checking" && "animate-spin")}
        />
        {metadata.label}
      </p>
      <p
        className="mt-0.5 text-[0.6875rem] whitespace-nowrap text-muted-foreground"
        title={check.checked_at ? formatDate(check.checked_at) : undefined}
      >
        {check.checked_at
          ? relativeTime(check.checked_at)
          : check.state === "checking"
            ? "In progress"
            : "Never"}
      </p>
    </div>
  )
}

function RoutingBadge({ evidence }: { evidence: DomainProviderEvidence }) {
  const label = evidence.routing_eligible
    ? "Eligible"
    : evidence.paid_fallback_available
      ? "Paid fallback"
    : evidence.health_state === "healthy"
      ? "Disabled"
      : evidence.health_state.replaceAll("_", " ")
  return (
    <Badge
      variant="outline"
      className={cn(
        "w-fit font-normal capitalize",
        evidence.routing_eligible &&
          "border-emerald-500/30 bg-emerald-500/10 text-emerald-700 dark:text-emerald-400",
        !evidence.routing_eligible && "text-muted-foreground"
      )}
    >
      {label}
    </Badge>
  )
}

function ProviderRow({
  evidence,
  pending,
  onCheck,
}: {
  evidence: DomainProviderEvidence
  pending: boolean
  onCheck: () => void
}) {
  return (
    <div className="grid min-w-[50rem] grid-cols-[minmax(7rem,1.15fr)_repeat(4,minmax(5rem,.8fr))_minmax(6rem,.8fr)_auto] items-center gap-2 px-4 py-3">
      <div>
        <div className="flex items-center gap-1.5">
          <p className="text-sm font-medium">
            {providerLabels[evidence.provider]}
          </p>
          {!evidence.automatic_enabled && (
            <Badge
              variant="outline"
              className="px-1.5 py-0 text-[0.625rem] font-normal text-muted-foreground"
            >
              Disabled
            </Badge>
          )}
        </div>
        <p className="mt-0.5 text-[0.6875rem] text-muted-foreground">
          {formatNumber(evidence.average_cost_units)} units ·{" "}
          {evidence.cost_is_estimate ? "estimate" : "observed"}
        </p>
      </div>
      <CheckCell check={evidence.checks.navigation} />
      <CheckCell check={evidence.checks.status} />
      <CheckCell check={evidence.checks.headers} />
      <CheckCell check={evidence.checks.content} />
      <div>
        <RoutingBadge evidence={evidence} />
        {evidence.failure_reason_code && (
          <p className="mt-0.5 truncate text-[0.6875rem] text-muted-foreground">
            {evidence.failure_reason_code.replaceAll("_", " ")}
          </p>
        )}
      </div>
      <Button
        variant="outline"
        size="sm"
        className="h-8 w-fit gap-1.5 px-2.5"
        disabled={pending}
        onClick={onCheck}
      >
        {pending ? (
          <LoaderCircle className="size-3 animate-spin" aria-hidden />
        ) : (
          <FlaskConical className="size-3" aria-hidden />
        )}
        {pending
          ? "Checking"
          : evidence.provider === "browserbase"
            ? "Paid check"
            : evidence.last_checked_at
              ? "Recheck"
              : "Check"}
      </Button>
    </div>
  )
}

function Fact({ label, value }: { label: string; value: string | number }) {
  return (
    <div>
      <p className="text-xs text-muted-foreground">{label}</p>
      <p className="mt-1 text-sm font-medium">{value}</p>
    </div>
  )
}

function DomainDetailPage({
  domainId,
  navigate,
}: {
  domainId: number
  navigate: (href: string) => void
}) {
  const queryClient = useQueryClient()
  const detail = useQuery({
    queryKey: ["domain", domainId],
    queryFn: () => apiRequest<DomainDetail>(`/v1/admin/domains/${domainId}`),
    refetchInterval: (query) =>
      query.state.data?.active_probe_count ? 1_000 : false,
  })
  const probes = useQuery({
    queryKey: ["domain-probes", domainId],
    queryFn: () =>
      apiRequest<DomainProbePage>(
        `/v1/admin/domains/${domainId}/probes?limit=8`
      ),
    refetchInterval: detail.data?.active_probe_count ? 1_000 : false,
  })
  const sessions = useQuery({
    queryKey: ["domain-sessions", domainId],
    queryFn: () =>
      apiRequest<DomainSessionPage>(
        `/v1/admin/domains/${domainId}/sessions?limit=8`
      ),
  })
  const trigger = useMutation({
    mutationFn: triggerDomainProbes,
    onSuccess: (result) => {
      void queryClient.invalidateQueries({ queryKey: ["domain", domainId] })
      void queryClient.invalidateQueries({
        queryKey: ["domain-probes", domainId],
      })
      const count = result.scheduled.length
      if (count) {
        toast.success(
          `${count} health ${count === 1 ? "check" : "checks"} queued`
        )
      } else {
        toast.info("The requested health checks are already in progress")
      }
    },
    onError: (error) => toast.error(extractApiError(error)),
  })
  if (detail.isLoading) return <LoadingState label="Loading domain health" />
  if (detail.isError) {
    return (
      <ErrorState
        error={detail.error}
        retry={() => void detail.refetch()}
        label="Domain is unavailable"
      />
    )
  }
  const domain = detail.data
  if (!domain) return null

  return (
    <>
      <Button
        variant="ghost"
        size="sm"
        type="button"
        onClick={() => navigate("/domains")}
        className="mb-5 -ml-3 gap-2 text-muted-foreground hover:text-foreground"
      >
        <ArrowLeft className="size-4" /> All domains
      </Button>
      <header className="flex flex-col gap-4 border-b pb-4 lg:flex-row lg:items-end lg:justify-between">
        <div>
          <p className="mb-3 flex items-center gap-2 text-xs font-medium text-muted-foreground">
            <Globe2 className="size-3.5" /> Domain routing
          </p>
          <h1 className="text-3xl font-semibold tracking-tight sm:text-4xl">
            {domain.hostname}
          </h1>
          <p className="mt-2 text-sm text-muted-foreground">
            First seen {formatDate(domain.first_seen_at)} · Last seen{" "}
            {relativeTime(domain.last_seen_at)}
          </p>
        </div>
        <div className="rounded-lg border bg-card px-4 py-3 lg:min-w-80">
          <p className="text-xs font-medium text-muted-foreground">
            Expected automatic plan
          </p>
          <p className="mt-2 text-lg font-semibold">
            {planLabel(domain.expected_plan)}
          </p>
          <p className="mt-1 text-xs text-muted-foreground">
            <PlanDescription plan={domain.expected_plan} />
          </p>
        </div>
      </header>

      <section className="mt-5 grid gap-3 sm:grid-cols-3">
        <Card className="gap-0 rounded-lg p-4">
          <Fact
            label="Observed sessions"
            value={formatNumber(domain.session_count)}
          />
        </Card>
        <Card className="gap-0 rounded-lg p-4">
          <Fact
            label="Active checks"
            value={formatNumber(domain.active_probe_count)}
          />
        </Card>
        <Card className="gap-0 rounded-lg p-4">
          <Fact
            label="Live provider escalations"
            value={formatNumber(domain.transition_count)}
          />
        </Card>
      </section>

      <section className="mt-5 overflow-hidden rounded-lg border bg-card">
        <div className="flex flex-col gap-3 border-b px-4 py-3 sm:flex-row sm:items-center sm:justify-between">
          <div>
            <h2 className="font-semibold">Provider eligibility</h2>
            <p className="mt-1 text-sm text-muted-foreground">
              Synchronized health checks establish safe acquisition and compare
              primary content. Cost orders providers with current healthy
              evidence; Browserbase remains the configured terminal fallback.
            </p>
          </div>
          <Button
            variant="outline"
            size="sm"
            className="shrink-0 gap-2"
            disabled={trigger.isPending}
            onClick={() => trigger.mutate({ domainId })}
          >
            {trigger.isPending && !trigger.variables?.providers ? (
              <LoaderCircle className="size-3.5 animate-spin" aria-hidden />
            ) : (
              <FlaskConical className="size-3.5" aria-hidden />
            )}
            Check HTTP + Browserless
          </Button>
        </div>
        <div className="overflow-x-auto">
          <div className="grid min-w-[50rem] grid-cols-[minmax(7rem,1.15fr)_repeat(4,minmax(5rem,.8fr))_minmax(6rem,.8fr)_auto] gap-2 border-b bg-muted/35 px-4 py-2 font-mono text-[0.625rem] font-medium tracking-wider text-muted-foreground uppercase">
            <span>Provider</span>
            <span>Navigation</span>
            <span>HTTP</span>
            <span>Headers</span>
            <span>Content</span>
            <span>Routing</span>
            <span>Action</span>
          </div>
          <div className="divide-y">
            {domain.providers.map((evidence) => (
              <ProviderRow
                key={evidence.provider}
                evidence={evidence}
                pending={
                  evidence.checks.navigation.state === "checking" ||
                  (trigger.isPending &&
                    ((!trigger.variables?.providers &&
                      evidence.provider !== "browserbase") ||
                      trigger.variables?.providers?.includes(
                        evidence.provider
                      ) === true))
                }
                onCheck={() =>
                  trigger.mutate({
                    domainId,
                    providers: [evidence.provider],
                  })
                }
              />
            ))}
          </div>
        </div>
      </section>

      <div className="mt-5 grid gap-4 xl:grid-cols-[1.15fr_.85fr]">
        <section className="overflow-hidden rounded-lg border bg-card">
          <div className="border-b px-4 py-3">
            <h2 className="font-semibold">Recent probes</h2>
            <p className="mt-1 text-sm text-muted-foreground">
              Absolute response health plus bounded primary-content comparison
              within each provider cohort.
            </p>
          </div>
          {probes.isError ? (
            <div className="p-5 text-sm text-destructive">
              {extractApiError(probes.error)}
            </div>
          ) : probes.isLoading ? (
            <div className="flex h-32 items-center justify-center">
              <LoaderCircle className="size-5 animate-spin text-muted-foreground" />
            </div>
          ) : probes.data?.probes.length ? (
            <div className="overflow-x-auto">
              <table className="w-full min-w-[38rem] table-fixed text-left text-sm">
                <colgroup>
                  <col className="w-[17%]" />
                  <col className="w-[13%]" />
                  <col className="w-[14%]" />
                  <col className="w-[12%]" />
                  <col className="w-[12%]" />
                  <col className="w-[14%]" />
                  <col className="w-[18%]" />
                </colgroup>
                <thead className="border-b bg-muted/35 font-mono text-[0.6875rem] tracking-wider text-muted-foreground uppercase">
                  <tr>
                    <th className="px-3 py-2 font-medium">Provider</th>
                    <th className="px-2 py-2 font-medium">Result</th>
                    <th className="px-2 py-2 font-medium">Nav</th>
                    <th className="px-2 py-2 font-medium">HTTP</th>
                    <th className="px-2 py-2 font-medium">Headers</th>
                    <th className="px-2 py-2 font-medium">Content</th>
                    <th className="px-3 py-2 text-right font-medium">When</th>
                  </tr>
                </thead>
                <tbody className="divide-y">
                  {probes.data.probes.map((probe) => (
                    <tr key={probe.id}>
                      <td className="truncate px-3 py-2 font-medium">
                        {providerLabels[probe.candidate_provider]}
                      </td>
                      <td className="px-2 py-2">
                        <Badge
                          variant="outline"
                          className="font-normal capitalize"
                        >
                          {probe.outcome === "healthy"
                            ? "OK"
                            : (probe.outcome ?? probe.state)}
                        </Badge>
                      </td>
                      {[
                        probe.navigation_state,
                        probe.status_state,
                        probe.headers_state,
                        probe.content_state,
                      ].map((state, index) => (
                        <td
                          key={index}
                          className="truncate px-2 py-2 text-xs capitalize"
                        >
                          {state === "healthy"
                            ? "OK"
                            : (state?.replaceAll("_", " ") ?? "Pending")}
                        </td>
                      ))}
                      <td className="px-3 py-2 text-right text-xs whitespace-nowrap text-muted-foreground">
                        {relativeTime(probe.created_at)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <div className="p-8 text-center text-sm text-muted-foreground">
              No health checks recorded.
            </div>
          )}
        </section>

      </div>

      <section className="mt-5 overflow-hidden rounded-lg border bg-card">
        <div className="border-b px-4 py-3">
          <h2 className="font-semibold">Recent sessions</h2>
          <p className="mt-1 text-sm text-muted-foreground">
            Actual provider paths; arrows show live escalation.
          </p>
        </div>
        {sessions.isError ? (
          <div className="p-5 text-sm text-destructive">
            {extractApiError(sessions.error)}
          </div>
        ) : sessions.isLoading ? (
          <div className="flex h-32 items-center justify-center">
            <LoaderCircle className="size-5 animate-spin text-muted-foreground" />
          </div>
        ) : sessions.data?.sessions.length ? (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[42rem] text-left text-sm">
              <thead className="border-b bg-muted/35 font-mono text-[0.6875rem] tracking-wider text-muted-foreground uppercase">
                <tr>
                  <th className="px-4 py-2 font-medium">Session</th>
                  <th className="px-3 py-2.5 font-medium">Provider path</th>
                  <th className="px-3 py-2.5 font-medium">Reason</th>
                  <th className="px-3 py-2.5 font-medium">State</th>
                  <th className="px-4 py-2 text-right font-medium">Cost</th>
                </tr>
              </thead>
              <tbody className="divide-y">
                {sessions.data.sessions.map((session) => (
                  <tr key={session.id}>
                    <td className="max-w-64 px-4 py-2">
                      <p className="truncate font-mono text-xs">{session.id}</p>
                      <p className="mt-0.5 text-xs text-muted-foreground">
                        {relativeTime(session.created_at)}
                      </p>
                    </td>
                    <td className="px-3 py-2.5">
                      {session.providers.length
                        ? session.providers
                            .map((provider) => providerLabels[provider])
                            .join(" → ")
                        : "No attempt"}
                    </td>
                    <td className="px-3 py-2.5 capitalize">
                      {session.selection_reason?.replaceAll("_", " ") ?? "—"}
                    </td>
                    <td className="px-3 py-2.5">
                      <span className="flex items-center gap-2 capitalize">
                        <CircleDot
                          className={cn(
                            "size-3.5",
                            session.state === "closed"
                              ? "text-emerald-600"
                              : session.state === "failed"
                                ? "text-destructive"
                                : "text-amber-600"
                          )}
                        />
                        {session.state}
                      </span>
                    </td>
                    <td className="px-4 py-2 text-right whitespace-nowrap text-muted-foreground">
                      {formatNumber(session.modeled_cost_units)} units
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <div className="p-8 text-center text-sm text-muted-foreground">
            No retained sessions for this domain.
          </div>
        )}
      </section>
    </>
  )
}

export function DomainsPage({
  domainId,
  navigate,
}: {
  domainId?: number
  navigate: (href: string) => void
}) {
  return (
    <main className="mx-auto min-h-svh w-full max-w-[100rem] px-4 py-6 sm:px-6 md:py-8 lg:px-8">
      {domainId === undefined && (
        <header className="mb-5 border-b pb-4">
          <p className="mb-3 flex items-center gap-2 text-xs font-medium text-muted-foreground">
            <Globe2 className="size-3.5" /> Routing evidence
          </p>
          <h1 className="text-3xl font-semibold tracking-tight sm:text-4xl">
            Domains
          </h1>
          <p className="mt-2 max-w-2xl text-sm leading-6 text-muted-foreground">
            Harbor picks the cheapest provider with current health evidence and
            uses Browserbase as the configured terminal fallback.
          </p>
        </header>
      )}
      {domainId === undefined ? (
        <DomainIndex navigate={navigate} />
      ) : (
        <DomainDetailPage domainId={domainId} navigate={navigate} />
      )}
    </main>
  )
}
