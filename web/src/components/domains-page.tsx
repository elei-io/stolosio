import { useQuery } from "@tanstack/react-query"
import {
  ArrowLeft,
  ArrowRight,
  Check,
  ChevronRight,
  CircleAlert,
  CircleDot,
  Compass,
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
  DomainComparisonCheck,
  DomainDetail,
  DomainListItem,
  DomainMethodCheck,
  DomainPage,
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

function routeReason(reason: string) {
  return reason === "cheapest_qualified"
    ? "Cheapest qualified"
    : "Default provider"
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
  label = "Domain evidence is unavailable",
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

function EvidenceSummary({ domain }: { domain: DomainListItem }) {
  const parts = []
  if (domain.qualification_counts.qualified) {
    parts.push(`${domain.qualification_counts.qualified} qualified`)
  }
  if (domain.qualification_counts.probing) {
    parts.push(`${domain.qualification_counts.probing} probing`)
  }
  if (domain.qualification_counts.rejected) {
    parts.push(`${domain.qualification_counts.rejected} rejected`)
  }
  if (!parts.length) return <span>No qualifications</span>
  return <span>{parts.join(" · ")}</span>
}

function DomainIndex({
  navigate,
}: {
  navigate: (href: string) => void
}) {
  const [search, setSearch] = useState("")
  const [debouncedSearch, setDebouncedSearch] = useState("")
  const [qualification, setQualification] = useState("all")
  const [promotionsOnly, setPromotionsOnly] = useState(false)
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
    if (qualification !== "all") {
      params.set("qualification_state", qualification)
    }
    if (promotionsOnly) params.set("has_promotions", "true")
    if (activeProbesOnly) params.set("has_active_probes", "true")
    return params.toString()
  }, [
    activeProbesOnly,
    debouncedSearch,
    promotionsOnly,
    qualification,
  ])
  const pageQuery = useMemo(() => {
    const params = new URLSearchParams(query)
    if (cursor) params.set("before", cursor)
    return params.toString()
  }, [cursor, query])

  const domains = useQuery({
    queryKey: ["domains", pageQuery],
    queryFn: () => apiRequest<DomainPage>(`/v1/admin/domains?${pageQuery}`),
  })

  const resetPagination = () => {
    setCursor(null)
    setCursorHistory([])
  }

  const nextPage = () => {
    const nextCursor = domains.data?.next_cursor
    if (!nextCursor) return
    setCursorHistory((current) => [...current, cursor])
    setCursor(nextCursor)
  }

  const previousPage = () => {
    if (!cursorHistory.length) return
    const previous = cursorHistory.at(-1) ?? null
    setCursorHistory((current) => current.slice(0, -1))
    setCursor(previous)
  }

  if (domains.isLoading) return <LoadingState label="Loading domain evidence" />
  if (domains.isError) {
    return <ErrorState error={domains.error} retry={() => void domains.refetch()} />
  }

  const page = domains.data
  if (!page) return null
  const filtersActive =
    Boolean(search) ||
    qualification !== "all" ||
    promotionsOnly ||
    activeProbesOnly

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
          label="Qualified"
          value={page.summary.qualified_domains}
          detail="With qualified provider evidence"
          icon={ShieldCheck}
        />
        <SummaryCard
          label="Probing"
          value={page.summary.probing_domains}
          detail="Qualification in progress"
          icon={FlaskConical}
        />
        <SummaryCard
          label="Default only"
          value={page.summary.default_only_domains}
          detail="No qualified alternative"
          icon={Compass}
        />
        <SummaryCard
          label="Promoted"
          value={page.summary.promoted_domains}
          detail="Required a browser transition"
          icon={Sparkles}
        />
      </section>

      <section className="mt-6 overflow-hidden rounded-lg border bg-card">
        <div className="flex flex-col gap-4 border-b px-5 py-4 xl:flex-row xl:items-end xl:justify-between">
          <div>
            <h2 className="font-semibold">Observed domains</h2>
            <p className="mt-1 text-sm text-muted-foreground">
              Factual routing, qualification, and browser-promotion evidence.
            </p>
          </div>
          <div className="flex flex-col gap-2 sm:flex-row sm:flex-wrap">
            <span className="relative min-w-64">
              <Search
                className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-muted-foreground"
                aria-hidden
              />
              <Input
                value={search}
                onChange={(event) => setSearch(event.target.value)}
                placeholder="Search hostname"
                aria-label="Search domains"
                className="h-9 pl-9"
              />
            </span>
            <Select
              value={qualification}
              onValueChange={(value) => {
                setQualification(value ?? "all")
                resetPagination()
              }}
            >
              <SelectTrigger className="h-9 w-full sm:w-44">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">All evidence</SelectItem>
                <SelectItem value="qualified">Qualified</SelectItem>
                <SelectItem value="probing">Probing</SelectItem>
                <SelectItem value="rejected">Rejected</SelectItem>
                <SelectItem value="unqualified">Unqualified</SelectItem>
              </SelectContent>
            </Select>
            <Button
              variant={activeProbesOnly ? "secondary" : "outline"}
              size="sm"
              className="h-9"
              aria-pressed={activeProbesOnly}
              onClick={() => {
                setActiveProbesOnly((value) => !value)
                resetPagination()
              }}
            >
              Active probes
            </Button>
            <Button
              variant={promotionsOnly ? "secondary" : "outline"}
              size="sm"
              className="h-9"
              aria-pressed={promotionsOnly}
              onClick={() => {
                setPromotionsOnly((value) => !value)
                resetPagination()
              }}
            >
              Promotions
            </Button>
          </div>
        </div>

        {page.domains.length ? (
          <>
            <div className="hidden min-w-[62rem] grid-cols-[1.5fr_1.2fr_1.25fr_.7fr_.7fr_2.5rem] border-b bg-muted/35 px-5 py-2.5 font-mono text-[0.6875rem] font-medium tracking-wider text-muted-foreground uppercase md:grid">
              <span>Domain</span>
              <span>Current route</span>
              <span>Provider evidence</span>
              <span>Sessions</span>
              <span>Promotions</span>
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
                    className="group block px-5 py-4 transition-colors hover:bg-muted/25 md:grid md:min-w-[62rem] md:grid-cols-[1.5fr_1.2fr_1.25fr_.7fr_.7fr_2.5rem] md:items-center"
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
                    <div className="mt-4 md:mt-0">
                      <p className="text-xs text-muted-foreground md:hidden">
                        Current route
                      </p>
                      <p className="mt-1 text-sm font-medium md:mt-0">
                        {providerLabels[domain.current_route.provider]}
                      </p>
                      <p className="mt-1 text-xs text-muted-foreground">
                        {routeReason(domain.current_route.reason)}
                      </p>
                    </div>
                    <div className="mt-4 text-sm text-muted-foreground md:mt-0">
                      <p className="text-xs md:hidden">Provider evidence</p>
                      <p className="mt-1 md:mt-0">
                        <EvidenceSummary domain={domain} />
                      </p>
                      {domain.active_probe_count > 0 && (
                        <p className="mt-1 text-xs text-amber-700 dark:text-amber-400">
                          {domain.active_probe_count} active{" "}
                          {domain.active_probe_count === 1 ? "probe" : "probes"}
                        </p>
                      )}
                    </div>
                    <div className="mt-4 md:mt-0">
                      <p className="text-xs text-muted-foreground md:hidden">
                        Sessions
                      </p>
                      <p className="mt-1 text-sm font-medium md:mt-0">
                        {formatNumber(domain.session_count)}
                      </p>
                    </div>
                    <div className="mt-4 md:mt-0">
                      <p className="text-xs text-muted-foreground md:hidden">
                        Promotions
                      </p>
                      <p className="mt-1 text-sm font-medium md:mt-0">
                        {formatNumber(domain.promotion_count)}
                      </p>
                    </div>
                    <ChevronRight
                      className="hidden size-4 text-muted-foreground transition-transform group-hover:translate-x-0.5 md:block"
                      aria-hidden
                    />
                  </a>
                )
              })}
            </div>
            {(cursorHistory.length > 0 || page.next_cursor) && (
              <div className="flex items-center justify-between gap-4 border-t bg-muted/10 px-5 py-3">
                <p className="text-xs text-muted-foreground">
                  Page {cursorHistory.length + 1} · {page.domains.length}{" "}
                  {page.domains.length === 1 ? "domain" : "domains"}
                </p>
                <div className="flex items-center gap-2">
                  <Button
                    variant="outline"
                    size="sm"
                    className="h-8 gap-1.5"
                    disabled={!cursorHistory.length || domains.isFetching}
                    onClick={previousPage}
                  >
                    <ArrowLeft className="size-3.5" aria-hidden />
                    Previous
                  </Button>
                  <Button
                    variant="outline"
                    size="sm"
                    className="h-8 gap-1.5"
                    disabled={!page.next_cursor || domains.isFetching}
                    onClick={nextPage}
                  >
                    Next
                    <ArrowRight className="size-3.5" aria-hidden />
                  </Button>
                </div>
              </div>
            )}
          </>
        ) : (
          <div className="flex min-h-72 items-center justify-center px-6 text-center">
            <div>
              <Globe2
                className="mx-auto size-6 text-muted-foreground"
                aria-hidden
              />
              <h3 className="mt-4 font-medium">
                {filtersActive ? "No matching domains" : "No domains observed"}
              </h3>
              <p className="mt-2 text-sm text-muted-foreground">
                {filtersActive
                  ? "Adjust the filters to broaden the evidence shown."
                  : "Domains appear after Harbor observes session navigation."}
              </p>
            </div>
          </div>
        )}
      </section>
    </>
  )
}

function CheckCell({ check }: { check: DomainComparisonCheck }) {
  const metadata = {
    matches: {
      label: "Matches",
      icon: Check,
      className: "text-emerald-700 dark:text-emerald-400",
    },
    differs: {
      label: "Differs",
      icon: X,
      className: "text-destructive",
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
      <p className={cn("flex items-center gap-1.5 text-sm font-medium", metadata.className)}>
        <Icon
          className={cn("size-3.5", check.state === "checking" && "animate-spin")}
          aria-hidden
        />
        {metadata.label}
      </p>
      <p
        className="mt-1 text-xs text-muted-foreground"
        title={check.checked_at ? formatDate(check.checked_at) : undefined}
      >
        {check.checked_at
          ? `Checked ${relativeTime(check.checked_at)}`
          : check.state === "checking"
            ? "Probe in progress"
            : "Not checked yet"}
      </p>
    </div>
  )
}

function MethodCheckCell({ check }: { check: DomainMethodCheck }) {
  if (check.state === "not_checked") return <CheckCell check={check} />
  const differs = check.state === "differs"
  const title = differs
    ? `Unsupported: ${check.unsupported_methods.join(", ")}`
    : `Capability manifest v${check.manifest_version}`

  return (
    <div title={title}>
      <p
        className={cn(
          "flex items-center gap-1.5 text-sm font-medium",
          differs
            ? "text-destructive"
            : "text-emerald-700 dark:text-emerald-400"
        )}
      >
        {differs ? (
          <X className="size-3.5" aria-hidden />
        ) : (
          <Check className="size-3.5" aria-hidden />
        )}
        {check.supported_count}/{check.observed_count} covered
      </p>
      <p className="mt-1 text-xs text-muted-foreground">
        Manifest v{check.manifest_version}
      </p>
    </div>
  )
}

function providerDecision(
  evidence: DomainProviderEvidence,
  currentProvider: ActivityProvider,
  currentCost: number
) {
  if (evidence.provider === currentProvider) {
    return { label: "Current route", tone: "current" }
  }
  if (!evidence.automatic_enabled) {
    return { label: "Disabled", tone: "neutral" }
  }
  if (evidence.qualification_state === "qualified") {
    return { label: "Qualified alternative", tone: "qualified" }
  }
  if (
    evidence.qualification_state === "probing" ||
    evidence.probe_state === "checking"
  ) {
    return { label: "Evaluating", tone: "checking" }
  }
  if (evidence.qualification_state === "rejected") {
    return { label: "Ruled out", tone: "rejected" }
  }
  if (evidence.average_cost_units >= currentCost) {
    return { label: "Not cheaper", tone: "neutral" }
  }
  return { label: "Not checked", tone: "neutral" }
}

function ProviderEvidenceRow({
  evidence,
  currentProvider,
  currentCost,
}: {
  evidence: DomainProviderEvidence
  currentProvider: ActivityProvider
  currentCost: number
}) {
  const decision = providerDecision(evidence, currentProvider, currentCost)
  return (
    <div className="grid min-w-[82rem] grid-cols-[1.2fr_repeat(5,1fr)_1.15fr] items-center gap-4 px-5 py-4">
      <div>
        <div className="flex flex-wrap items-center gap-2">
          <p className="font-medium">{providerLabels[evidence.provider]}</p>
          {evidence.is_default && (
            <Badge variant="secondary" className="font-normal">
              Default
            </Badge>
          )}
          {!evidence.automatic_enabled && (
            <Badge variant="outline" className="font-normal text-muted-foreground">
              Disabled
            </Badge>
          )}
        </div>
        <p className="mt-1 text-xs text-muted-foreground">
          {formatNumber(evidence.average_cost_units)} cost units
          {evidence.cost_is_estimate ? " · estimate" : " · observed"}
        </p>
      </div>
      <CheckCell check={evidence.checks.status} />
      <CheckCell check={evidence.checks.headers} />
      <CheckCell check={evidence.checks.console} />
      <MethodCheckCell check={evidence.checks.methods} />
      <CheckCell check={evidence.checks.content} />
      <Badge
        variant="outline"
        className={cn(
          "w-fit font-normal",
          decision.tone === "current" &&
            "border-blue-500/30 bg-blue-500/10 text-blue-700 dark:text-blue-400",
          decision.tone === "qualified" &&
            "border-emerald-500/30 bg-emerald-500/10 text-emerald-700 dark:text-emerald-400",
          decision.tone === "checking" &&
            "border-amber-500/30 bg-amber-500/10 text-amber-700 dark:text-amber-400",
          decision.tone === "rejected" &&
            "border-destructive/30 bg-destructive/10 text-destructive",
          decision.tone === "neutral" && "text-muted-foreground"
        )}
      >
        {decision.label}
      </Badge>
    </div>
  )
}

function Fact({
  label,
  value,
}: {
  label: string
  value: string | number
}) {
  return (
    <div>
      <p className="text-xs text-muted-foreground">{label}</p>
      <p className="mt-1 text-sm font-medium">{value}</p>
    </div>
  )
}

function MatchFact({
  label,
  value,
  detail,
}: {
  label: string
  value: boolean | null
  detail?: string
}) {
  return (
    <div className="flex items-start justify-between gap-3">
      <div>
        <p className="text-xs text-muted-foreground">{label}</p>
        {detail && <p className="mt-1 text-xs">{detail}</p>}
      </div>
      <span
        className={cn(
          "text-xs font-medium",
          value === true && "text-emerald-700 dark:text-emerald-400",
          value === false && "text-destructive",
          value === null && "text-muted-foreground"
        )}
      >
        {value === true ? "Match" : value === false ? "Mismatch" : "Pending"}
      </span>
    </div>
  )
}

function ProbeCard({ probe }: { probe: DomainProbe }) {
  return (
    <div className="p-5">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div>
          <div className="flex flex-wrap items-center gap-2">
            <p className="font-medium">
              {providerLabels[probe.candidate_provider]}
            </p>
            <Badge variant="outline" className="font-normal capitalize">
              {probe.state}
            </Badge>
            {probe.comparison_outcome && (
              <Badge
                variant="secondary"
                className={cn(
                  "font-normal",
                  probe.comparison_outcome === "matched" &&
                    "bg-emerald-500/10 text-emerald-700 dark:text-emerald-400",
                  probe.comparison_outcome !== "matched" &&
                    "bg-destructive/10 text-destructive"
                )}
              >
                {probe.comparison_outcome.replace("_", " ")}
              </Badge>
            )}
          </div>
          <p className="mt-1 text-xs text-muted-foreground">
            {probe.trigger === "new_domain"
              ? "New-domain qualification"
              : "Existing-domain sample"}{" "}
            · {formatDate(probe.created_at)}
          </p>
        </div>
        {probe.cost_units !== null && (
          <p className="text-xs text-muted-foreground">
            {formatNumber(probe.cost_units)} cost units
          </p>
        )}
      </div>
      <div className="mt-5 grid gap-4 rounded-md border bg-muted/15 p-4 sm:grid-cols-2 lg:grid-cols-4">
        <MatchFact
          label="HTTP status"
          value={probe.status_matches}
          detail={`${probe.baseline_status} → ${probe.candidate_status ?? "pending"}`}
        />
        <MatchFact label="Selected headers" value={probe.headers_match} />
        <MatchFact
          label="Console errors"
          value={probe.console_errors_acceptable}
          detail={`${probe.baseline_console_errors} → ${
            probe.candidate_console_errors ?? "pending"
          }`}
        />
        <MatchFact label="Content fingerprint" value={probe.content_matches} />
      </div>
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
    queryFn: () =>
      apiRequest<DomainDetail>(`/v1/admin/domains/${domainId}`),
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

  if (detail.isLoading) return <LoadingState label="Loading domain evidence" />
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
        className="mb-5 inline-flex items-center gap-2 text-sm text-muted-foreground transition-colors hover:text-foreground"
      >
        <ArrowLeft className="size-4" aria-hidden />
        All domains
      </button>

      <header className="flex flex-col gap-5 border-b pb-6 lg:flex-row lg:items-end lg:justify-between">
        <div className="min-w-0">
          <div className="mb-3 flex items-center gap-2 text-xs font-medium text-muted-foreground">
            <Globe2 className="size-3.5" aria-hidden />
            Domain evidence
          </div>
          <h1 className="truncate text-3xl font-semibold tracking-tight sm:text-4xl">
            {domain.hostname}
          </h1>
          <p className="mt-2 text-sm text-muted-foreground">
            First seen {formatDate(domain.first_seen_at)} · Last seen{" "}
            {relativeTime(domain.last_seen_at)}
          </p>
        </div>
        <div className="rounded-lg border bg-card px-5 py-4 lg:min-w-80">
          <p className="text-xs font-medium text-muted-foreground">
            Current automatic route
          </p>
          <div className="mt-2 flex items-center justify-between gap-4">
            <p className="text-lg font-semibold">
              {providerLabels[domain.current_route.provider]}
            </p>
            <Badge variant="secondary" className="font-normal">
              {routeReason(domain.current_route.reason)}
            </Badge>
          </div>
          <p className="mt-2 text-xs text-muted-foreground">
            {formatNumber(domain.current_route.estimated_cost_units)} estimated
            cost units
          </p>
        </div>
      </header>

      <section className="mt-6 grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <Card className="gap-0 rounded-lg p-5">
          <Fact label="Observed sessions" value={formatNumber(domain.session_count)} />
        </Card>
        <Card className="gap-0 rounded-lg p-5">
          <Fact
            label="Eligible acquisitions"
            value={formatNumber(domain.eligible_acquisition_count)}
          />
        </Card>
        <Card className="gap-0 rounded-lg p-5">
          <Fact
            label="Active probes"
            value={formatNumber(domain.active_probe_count)}
          />
        </Card>
        <Card className="gap-0 rounded-lg p-5">
          <Fact
            label="Browser promotions"
            value={formatNumber(domain.promotion?.promotion_count ?? 0)}
          />
        </Card>
      </section>

      <section className="mt-6 overflow-hidden rounded-lg border bg-card">
        <div className="border-b px-5 py-4">
          <h2 className="font-semibold">Provider checks</h2>
          <p className="mt-1 text-sm text-muted-foreground">
            Latest baseline comparison and observed-method coverage for every
            provider.
          </p>
        </div>
        <div className="overflow-x-auto">
          <div className="grid min-w-[82rem] grid-cols-[1.2fr_repeat(5,1fr)_1.15fr] gap-4 border-b bg-muted/35 px-5 py-2.5 font-mono text-[0.6875rem] font-medium tracking-wider text-muted-foreground uppercase">
            <span>Provider</span>
            <span>HTTP status</span>
            <span>Headers</span>
            <span>Console</span>
            <span>Known methods</span>
            <span>Content</span>
            <span>Decision</span>
          </div>
          <div className="divide-y">
            {domain.providers.map((evidence) => (
              <ProviderEvidenceRow
                key={evidence.provider}
                evidence={evidence}
                currentProvider={domain.current_route.provider}
                currentCost={domain.current_route.estimated_cost_units}
              />
            ))}
          </div>
        </div>
      </section>

      <div className="mt-6 grid gap-6 xl:grid-cols-[1.4fr_.8fr]">
        <section className="overflow-hidden rounded-lg border bg-card">
          <div className="border-b px-5 py-4">
            <h2 className="font-semibold">Qualification probes</h2>
            <p className="mt-1 text-sm text-muted-foreground">
              Sanitized comparisons against successful baseline acquisitions.
            </p>
          </div>
          {probes.isError ? (
            <div className="p-5 text-sm text-destructive">
              {extractApiError(probes.error)}
            </div>
          ) : probes.isLoading ? (
            <div className="flex h-32 items-center justify-center">
              <LoaderCircle
                className="size-5 animate-spin text-muted-foreground"
                aria-hidden
              />
            </div>
          ) : probes.data?.probes.length ? (
            <div className="divide-y">
              {probes.data.probes.map((probe) => (
                <ProbeCard key={probe.id} probe={probe} />
              ))}
            </div>
          ) : (
            <div className="p-8 text-center text-sm text-muted-foreground">
              No qualification probes recorded.
            </div>
          )}
        </section>

        <div className="space-y-6">
          <section className="overflow-hidden rounded-lg border bg-card">
            <div className="border-b px-5 py-4">
              <h2 className="font-semibold">Browser requirements</h2>
              <p className="mt-1 text-sm text-muted-foreground">
                Recorded HTTP-to-browser transitions.
              </p>
            </div>
            {domain.promotion ? (
              <div className="space-y-5 p-5">
                <Fact
                  label="Promotion count"
                  value={formatNumber(domain.promotion.promotion_count)}
                />
                <Fact
                  label="Last trigger"
                  value={domain.promotion.last_trigger_method}
                />
                <Fact
                  label="Last observed"
                  value={formatDate(domain.promotion.last_seen_at)}
                />
              </div>
            ) : (
              <div className="p-5 text-sm leading-6 text-muted-foreground">
                Harbor has not recorded a browser promotion for this domain.
              </div>
            )}
          </section>

          <section className="overflow-hidden rounded-lg border bg-card">
            <div className="border-b px-5 py-4">
              <h2 className="font-semibold">Observed CDP methods</h2>
              <p className="mt-1 text-sm text-muted-foreground">
                Most frequent methods associated with this domain.
              </p>
            </div>
            {domain.commands.length ? (
              <div className="divide-y">
                {domain.commands.slice(0, 10).map((command) => (
                  <div
                    key={command.method}
                    className="flex items-center justify-between gap-4 px-5 py-3"
                  >
                    <code className="truncate text-xs">{command.method}</code>
                    <span className="shrink-0 text-xs text-muted-foreground">
                      {formatNumber(command.command_count)} commands ·{" "}
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
      </div>

      <section className="mt-6 overflow-hidden rounded-lg border bg-card">
        <div className="border-b px-5 py-4">
          <h2 className="font-semibold">Recent sessions</h2>
          <p className="mt-1 text-sm text-muted-foreground">
            Acquisition paths and outcomes observed for this domain.
          </p>
        </div>
        {sessions.isError ? (
          <div className="p-5 text-sm text-destructive">
            {extractApiError(sessions.error)}
          </div>
        ) : sessions.isLoading ? (
          <div className="flex h-32 items-center justify-center">
            <LoaderCircle
              className="size-5 animate-spin text-muted-foreground"
              aria-hidden
            />
          </div>
        ) : sessions.data?.sessions.length ? (
          <div className="divide-y">
            {sessions.data.sessions.map((session) => (
              <div
                key={session.id}
                className="grid gap-4 px-5 py-4 md:grid-cols-[1.5fr_1fr_1fr_.7fr]"
              >
                <div className="min-w-0">
                  <p className="truncate font-mono text-xs">{session.id}</p>
                  <p className="mt-1 text-xs text-muted-foreground">
                    {formatDate(session.created_at)}
                  </p>
                </div>
                <div>
                  <p className="text-xs text-muted-foreground md:hidden">
                    Acquisition path
                  </p>
                  <p className="mt-1 text-sm md:mt-0">
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
                <div>
                  <p className="text-xs text-muted-foreground md:hidden">
                    Routing
                  </p>
                  <p className="mt-1 text-sm md:mt-0">
                    {session.routing_reason?.replaceAll("_", " ") ?? "—"}
                  </p>
                </div>
                <div>
                  <p className="text-xs text-muted-foreground md:hidden">
                    Outcome
                  </p>
                  <div className="mt-1 flex items-center gap-2 text-sm capitalize md:mt-0">
                    <CircleDot
                      className={cn(
                        "size-3.5",
                        session.state === "closed"
                          ? "text-emerald-600"
                          : session.state === "failed"
                            ? "text-destructive"
                            : "text-amber-600"
                      )}
                      aria-hidden
                    />
                    {session.state}
                  </div>
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
        <header className="mb-6 flex flex-col gap-4 border-b pb-6 sm:flex-row sm:items-end sm:justify-between">
          <div>
            <div className="mb-3 flex items-center gap-2 text-xs font-medium text-muted-foreground">
              <Globe2 className="size-3.5" aria-hidden />
              Routing evidence
            </div>
            <h1 className="text-3xl font-semibold tracking-tight sm:text-4xl">
              Domains
            </h1>
            <p className="mt-2 max-w-2xl text-sm leading-6 text-muted-foreground">
              Inspect the factual provider evidence Harbor uses for deterministic
              domain routing.
            </p>
          </div>
          <a
            href="/routing"
            onClick={(event) => {
              event.preventDefault()
              navigate("/routing")
            }}
            className="inline-flex items-center gap-2 self-start text-sm font-medium text-muted-foreground transition-colors hover:text-foreground sm:self-auto"
          >
            Routing policy
            <ArrowRight className="size-4" aria-hidden />
          </a>
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
