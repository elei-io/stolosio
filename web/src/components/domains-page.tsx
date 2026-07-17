import { useQuery } from "@tanstack/react-query"
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
  DomainMethodCheck,
  DomainPage,
  DomainPlan,
  DomainProbe,
  DomainProbePage,
  DomainProviderEvidence,
  DomainSessionPage,
} from "@/types/api"

const providerLabels: Record<ActivityProvider, string> = {
  http: "HTTP",
  chromium: "Chromium",
  browserless: "Browserless",
  lightpanda: "Lightpanda",
  camoufox: "Camoufox",
}

const supportFilterLabels: Record<string, string> = {
  all: "All support states",
  supported: "Supported",
  checking: "Checking",
  unsupported: "Unsupported",
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
  if (plan.reason === "no_supported_provider") return "No supported provider"
  if (!plan.candidates.length) return "No plan"
  return plan.candidates
    .map((candidate) => providerLabels[candidate.provider])
    .join(" → ")
}

function PlanDescription({ plan }: { plan: DomainPlan }) {
  if (plan.reason === "configured_default") {
    return <span>Configured default</span>
  }
  if (plan.reason === "no_supported_provider") {
    return <span className="text-destructive">No supported path</span>
  }
  return <span>Cheapest supported first</span>
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
  label = "Domain support evidence is unavailable",
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
    <Card className="gap-0 rounded-lg p-5">
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

function SupportSummary({ domain }: { domain: DomainListItem }) {
  const parts = []
  if (domain.support_counts.supported) {
    parts.push(`${domain.support_counts.supported} supported`)
  }
  if (domain.support_counts.checking) {
    parts.push(`${domain.support_counts.checking} checking`)
  }
  if (domain.support_counts.unsupported) {
    parts.push(`${domain.support_counts.unsupported} unsupported`)
  }
  return <span>{parts.length ? parts.join(" · ") : "No support evidence"}</span>
}

function DomainIndex({ navigate }: { navigate: (href: string) => void }) {
  const [search, setSearch] = useState("")
  const [debouncedSearch, setDebouncedSearch] = useState("")
  const [support, setSupport] = useState("all")
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
    if (support !== "all") params.set("support_state", support)
    if (transitionsOnly) params.set("has_transitions", "true")
    if (activeProbesOnly) params.set("has_active_probes", "true")
    if (cursor) params.set("before", cursor)
    return params.toString()
  }, [activeProbesOnly, cursor, debouncedSearch, transitionsOnly, support])
  const domains = useQuery({
    queryKey: ["domains", query],
    queryFn: () => apiRequest<DomainPage>(`/v1/admin/domains?${query}`),
  })
  const resetPagination = () => {
    setCursor(null)
    setCursorHistory([])
  }

  if (domains.isLoading) return <LoadingState label="Loading domain support" />
  if (domains.isError) {
    return <ErrorState error={domains.error} retry={() => void domains.refetch()} />
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
          label="Supported"
          value={page.summary.supported_domains}
          detail="At least one supported provider"
          icon={ShieldCheck}
        />
        <SummaryCard
          label="Checking"
          value={page.summary.checking_domains}
          detail="Support checks in progress"
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
        <div className="flex flex-col gap-4 border-b px-5 py-4 xl:flex-row xl:items-end xl:justify-between">
          <div>
            <h2 className="font-semibold">Observed domains</h2>
            <p className="mt-1 text-sm text-muted-foreground">
              Expected provider plans derived from absolute support evidence.
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
              value={support}
              onValueChange={(value) => {
                setSupport(value ?? "all")
                resetPagination()
              }}
            >
              <SelectTrigger className="h-9 w-full sm:w-44">
                <SelectValue>{supportFilterLabels[support]}</SelectValue>
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">All support states</SelectItem>
                <SelectItem value="supported">Supported</SelectItem>
                <SelectItem value="checking">Checking</SelectItem>
                <SelectItem value="unsupported">Unsupported</SelectItem>
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
              Provider transitions
            </Button>
          </div>
        </div>

        {page.domains.length ? (
          <>
            <div className="hidden min-w-[62rem] grid-cols-[1.4fr_1.45fr_1.25fr_.65fr_.65fr_2.5rem] border-b bg-muted/35 px-5 py-2.5 font-mono text-[0.6875rem] font-medium tracking-wider text-muted-foreground uppercase md:grid">
              <span>Domain</span>
              <span>Expected plan</span>
              <span>Support evidence</span>
              <span>Sessions</span>
              <span>Provider transitions</span>
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
                    className="group block px-5 py-4 transition-colors hover:bg-muted/25 md:grid md:min-w-[62rem] md:grid-cols-[1.4fr_1.45fr_1.25fr_.65fr_.65fr_2.5rem] md:items-center"
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
                      <SupportSummary domain={domain} />
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
              <div className="flex items-center justify-between border-t bg-muted/10 px-5 py-3">
                <p className="text-xs text-muted-foreground">
                  Page {cursorHistory.length + 1} · {page.domains.length} domains
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
      label: "Healthy",
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
    declared: {
      label: "Declared",
      icon: Check,
      className: "text-emerald-700 dark:text-emerald-400",
    },
    missing: {
      label: "Missing",
      icon: X,
      className: "text-destructive",
    },
  }[check.state]
  const Icon = metadata.icon
  return (
    <div>
      <p className={cn("flex items-center gap-1.5 text-sm font-medium", metadata.className)}>
        <Icon className={cn("size-3.5", check.state === "checking" && "animate-spin")} />
        {metadata.label}
      </p>
      <p
        className="mt-1 text-xs text-muted-foreground"
        title={check.checked_at ? formatDate(check.checked_at) : undefined}
      >
        {check.checked_at
          ? `Checked ${relativeTime(check.checked_at)}`
          : check.state === "checking"
            ? "Check in progress"
            : "Not checked yet"}
      </p>
    </div>
  )
}

function MethodCheckCell({ check }: { check: DomainMethodCheck }) {
  if (check.state === "not_checked" || check.state === "checking") {
    return <CheckCell check={check} />
  }
  return (
    <div title={check.unsupported_methods.join(", ")}>
      <p
        className={cn(
          "flex items-center gap-1.5 text-sm font-medium",
          check.state === "declared"
            ? "text-emerald-700 dark:text-emerald-400"
            : "text-destructive"
        )}
      >
        {check.state === "declared" ? (
          <Check className="size-3.5" />
        ) : (
          <X className="size-3.5" />
        )}
        {check.declared_count}/{check.observed_count} declared
      </p>
      <p className="mt-1 text-xs text-muted-foreground">
        Manifest v{check.manifest_version}
      </p>
    </div>
  )
}

function SupportBadge({ evidence }: { evidence: DomainProviderEvidence }) {
  const labels = {
    supported: "Supported",
    unsupported: "Unsupported",
    checking: "Checking",
    unknown: "Unknown",
  }
  return (
    <Badge
      variant="outline"
      className={cn(
        "w-fit font-normal",
        evidence.support_state === "supported" &&
          "border-emerald-500/30 bg-emerald-500/10 text-emerald-700 dark:text-emerald-400",
        evidence.support_state === "unsupported" &&
          "border-destructive/30 bg-destructive/10 text-destructive",
        evidence.support_state === "checking" &&
          "border-amber-500/30 bg-amber-500/10 text-amber-700 dark:text-amber-400",
        evidence.support_state === "unknown" && "text-muted-foreground"
      )}
    >
      {labels[evidence.support_state]}
    </Badge>
  )
}

function ProviderRow({ evidence }: { evidence: DomainProviderEvidence }) {
  return (
    <div className="grid min-w-[88rem] grid-cols-[1.2fr_repeat(5,1fr)_1fr] items-center gap-4 px-5 py-4">
      <div>
        <div className="flex items-center gap-2">
          <p className="font-medium">{providerLabels[evidence.provider]}</p>
          {!evidence.automatic_enabled && (
            <Badge variant="outline" className="font-normal text-muted-foreground">
              Disabled
            </Badge>
          )}
        </div>
        <p className="mt-1 text-xs text-muted-foreground">
          {formatNumber(evidence.average_cost_units)} cost units ·{" "}
          {evidence.cost_is_estimate ? "estimate" : "observed"}
        </p>
      </div>
      <CheckCell check={evidence.checks.navigation} />
      <CheckCell check={evidence.checks.status} />
      <CheckCell check={evidence.checks.headers} />
      <MethodCheckCell check={evidence.checks.method_coverage} />
      <CheckCell check={evidence.checks.content} />
      <div>
        <SupportBadge evidence={evidence} />
        {evidence.failure_reason_code && (
          <p className="mt-1 text-xs text-muted-foreground">
            {evidence.failure_reason_code.replaceAll("_", " ")}
          </p>
        )}
      </div>
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

function ProbeCard({ probe }: { probe: DomainProbe }) {
  const checks = [
    ["Navigation", probe.navigation_state],
    ["HTTP status", probe.status_state],
    ["Headers", probe.headers_state],
    ["Declared methods", probe.method_coverage_state],
    ["Content sanity", probe.content_state],
  ]
  return (
    <div className="p-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <div className="flex items-center gap-2">
            <p className="font-medium">{providerLabels[probe.candidate_provider]}</p>
            <Badge variant="outline" className="font-normal capitalize">
              {probe.outcome ?? probe.state}
            </Badge>
          </div>
          <p className="mt-1 text-xs text-muted-foreground">
            Independent support check · {formatDate(probe.created_at)}
          </p>
        </div>
        {probe.cost_units !== null && (
          <p className="text-xs text-muted-foreground">
            {formatNumber(probe.cost_units)} cost units
          </p>
        )}
      </div>
      <div className="mt-4 grid gap-3 rounded-md border bg-muted/15 p-4 sm:grid-cols-2 lg:grid-cols-5">
        {checks.map(([label, state]) => (
          <div key={label}>
            <p className="text-xs text-muted-foreground">{label}</p>
            <p className="mt-1 text-sm font-medium capitalize">
              {state?.replaceAll("_", " ") ?? "Pending"}
            </p>
          </div>
        ))}
      </div>
      {probe.reason_codes.length > 0 && (
        <p className="mt-3 text-xs text-muted-foreground">
          {probe.reason_codes.map((reason) => reason.replaceAll("_", " ")).join(" · ")}
        </p>
      )}
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
  const detail = useQuery({
    queryKey: ["domain", domainId],
    queryFn: () => apiRequest<DomainDetail>(`/v1/admin/domains/${domainId}`),
  })
  const probes = useQuery({
    queryKey: ["domain-probes", domainId],
    queryFn: () =>
      apiRequest<DomainProbePage>(`/v1/admin/domains/${domainId}/probes`),
  })
  const sessions = useQuery({
    queryKey: ["domain-sessions", domainId],
    queryFn: () =>
      apiRequest<DomainSessionPage>(
        `/v1/admin/domains/${domainId}/sessions?limit=25`
      ),
  })
  if (detail.isLoading) return <LoadingState label="Loading domain support" />
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
      <button
        type="button"
        onClick={() => navigate("/domains")}
        className="mb-5 inline-flex items-center gap-2 text-sm text-muted-foreground hover:text-foreground"
      >
        <ArrowLeft className="size-4" /> All domains
      </button>
      <header className="flex flex-col gap-5 border-b pb-6 lg:flex-row lg:items-end lg:justify-between">
        <div>
          <p className="mb-3 flex items-center gap-2 text-xs font-medium text-muted-foreground">
            <Globe2 className="size-3.5" /> Domain support
          </p>
          <h1 className="text-3xl font-semibold tracking-tight sm:text-4xl">
            {domain.hostname}
          </h1>
          <p className="mt-2 text-sm text-muted-foreground">
            First seen {formatDate(domain.first_seen_at)} · Last seen{" "}
            {relativeTime(domain.last_seen_at)}
          </p>
        </div>
        <div className="rounded-lg border bg-card px-5 py-4 lg:min-w-96">
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

      <section className="mt-6 grid gap-3 sm:grid-cols-3">
        <Card className="gap-0 rounded-lg p-5">
          <Fact label="Observed sessions" value={formatNumber(domain.session_count)} />
        </Card>
        <Card className="gap-0 rounded-lg p-5">
          <Fact label="Active checks" value={formatNumber(domain.active_probe_count)} />
        </Card>
        <Card className="gap-0 rounded-lg p-5">
          <Fact label="Runtime provider transitions" value={formatNumber(domain.transition_count)} />
        </Card>
      </section>

      <section className="mt-6 overflow-hidden rounded-lg border bg-card">
        <div className="border-b px-5 py-4">
          <h2 className="font-semibold">Provider support</h2>
          <p className="mt-1 text-sm text-muted-foreground">
            What Harbor independently believes can support this domain. Cost only
            orders providers already marked supported.
          </p>
        </div>
        <div className="overflow-x-auto">
          <div className="grid min-w-[88rem] grid-cols-[1.2fr_repeat(5,1fr)_1fr] gap-4 border-b bg-muted/35 px-5 py-2.5 font-mono text-[0.6875rem] font-medium tracking-wider text-muted-foreground uppercase">
            <span>Provider</span>
            <span>Navigation</span>
            <span>HTTP status</span>
            <span>Headers</span>
            <span>Declared methods</span>
            <span>Content sanity</span>
            <span>Support</span>
          </div>
          <div className="divide-y">
            {domain.providers.map((evidence) => (
              <ProviderRow key={evidence.provider} evidence={evidence} />
            ))}
          </div>
        </div>
      </section>

      <div className="mt-6 grid gap-6 xl:grid-cols-[1.4fr_.8fr]">
        <section className="overflow-hidden rounded-lg border bg-card">
          <div className="border-b px-5 py-4">
            <h2 className="font-semibold">Support checks</h2>
            <p className="mt-1 text-sm text-muted-foreground">
              Absolute health results and bounded content facts—never comparisons
              with another provider.
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
            <div className="divide-y">
              {probes.data.probes.map((probe) => (
                <ProbeCard key={probe.id} probe={probe} />
              ))}
            </div>
          ) : (
            <div className="p-8 text-center text-sm text-muted-foreground">
              No support checks recorded.
            </div>
          )}
        </section>

        <section className="overflow-hidden rounded-lg border bg-card self-start">
          <div className="border-b px-5 py-4">
            <h2 className="font-semibold">Observed CDP methods</h2>
            <p className="mt-1 text-sm text-muted-foreground">
              Requirements Harbor uses for method coverage.
            </p>
          </div>
          {domain.commands.length ? (
            <div className="divide-y">
              {domain.commands.slice(0, 12).map((command) => (
                <div
                  key={command.method}
                  className="flex items-center justify-between gap-4 px-5 py-3"
                >
                  <code className="truncate text-xs">{command.method}</code>
                  <span className="shrink-0 text-xs text-muted-foreground">
                    {formatNumber(command.command_count)} ·{" "}
                    {formatNumber(command.session_count)} sessions
                  </span>
                </div>
              ))}
            </div>
          ) : (
            <div className="p-5 text-sm text-muted-foreground">
              No CDP method observations recorded.
            </div>
          )}
        </section>
      </div>

      <section className="mt-6 overflow-hidden rounded-lg border bg-card">
        <div className="border-b px-5 py-4">
          <h2 className="font-semibold">Recent sessions</h2>
          <p className="mt-1 text-sm text-muted-foreground">
            Actual provider paths; arrows show runtime transition.
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
          <div className="divide-y">
            {sessions.data.sessions.map((session) => (
              <div
                key={session.id}
                className="grid gap-4 px-5 py-4 md:grid-cols-[1.5fr_1.2fr_1fr_.7fr]"
              >
                <div className="min-w-0">
                  <p className="truncate font-mono text-xs">{session.id}</p>
                  <p className="mt-1 text-xs text-muted-foreground">
                    {formatDate(session.created_at)}
                  </p>
                </div>
                <div>
                  <p className="text-sm">
                    {session.providers.length
                      ? session.providers
                          .map((provider) => providerLabels[provider])
                          .join(" → ")
                      : "No attempt"}
                  </p>
                  <p className="mt-1 text-xs capitalize text-muted-foreground">
                    {session.selection_mode}
                  </p>
                </div>
                <p className="text-sm capitalize">
                  {session.selection_reason?.replaceAll("_", " ") ?? "—"}
                </p>
                <div>
                  <p className="flex items-center gap-2 text-sm capitalize">
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
                  </p>
                  <p className="mt-1 text-xs text-muted-foreground">
                    {formatNumber(session.actual_cost_units)} units
                  </p>
                </div>
              </div>
            ))}
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
    <main className="mx-auto min-h-svh w-full max-w-[100rem] px-4 py-6 sm:px-6 md:py-8 lg:px-10">
      {domainId === undefined && (
        <header className="mb-6 border-b pb-6">
          <p className="mb-3 flex items-center gap-2 text-xs font-medium text-muted-foreground">
            <Globe2 className="size-3.5" /> Routing evidence
          </p>
          <h1 className="text-3xl font-semibold tracking-tight sm:text-4xl">
            Domains
          </h1>
          <p className="mt-2 max-w-2xl text-sm leading-6 text-muted-foreground">
            Harbor picks the cheapest supported provider and transitions through the
            support plan when a journey reveals a new requirement.
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
