import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import {
  ArrowDown,
  Check,
  CircleAlert,
  CircleDollarSign,
  Gauge,
  GitCompareArrows,
  Globe2,
  Info,
  LoaderCircle,
  Network,
  RefreshCw,
  Save,
  ShieldCheck,
} from "lucide-react"
import { type FormEvent, useMemo, useState } from "react"
import { toast } from "sonner"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Switch } from "@/components/ui/switch"
import { extractApiError } from "@/lib/api"
import { cn } from "@/lib/utils"
import type {
  ActivityProvider,
  ProviderRoutingProfile,
  ProviderRoutingUpdate,
  RoutingConfiguration,
  RoutingConfigurationUpdate,
} from "@/types/api"

const providerLabels: Record<ActivityProvider, string> = {
  http: "HTTP",
  chromium: "Chromium",
  browserless: "Browserless",
  lightpanda: "Lightpanda",
  camoufox: "Camoufox",
}

const providerDescriptions: Record<ActivityProvider, string> = {
  http: "No-browser acquisition",
  chromium: "Harbor-managed Chromium",
  browserless: "Managed Chromium lifecycle",
  lightpanda: "Lightweight CDP browser",
  camoufox: "Firefox-based stealth browser",
}

async function apiRequest<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, init)
  if (!response.ok) {
    const body: unknown = await response.json().catch(() => undefined)
    throw new Error(extractApiError(body))
  }
  return (await response.json()) as T
}

function fetchRoutingConfiguration() {
  return apiRequest<RoutingConfiguration>("/v1/admin/routing")
}

function fetchProviderProfiles() {
  return apiRequest<ProviderRoutingProfile[]>("/v1/admin/routing/providers")
}

function updateRoutingConfiguration(update: RoutingConfigurationUpdate) {
  return apiRequest<RoutingConfiguration>("/v1/admin/routing", {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(update),
  })
}

function updateProviderProfile(
  provider: ActivityProvider,
  update: ProviderRoutingUpdate
) {
  return apiRequest<ProviderRoutingProfile>(
    `/v1/admin/routing/providers/${provider}`,
    {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(update),
    }
  )
}

function formatPercentage(basisPoints: number) {
  return new Intl.NumberFormat(undefined, {
    maximumFractionDigits: 2,
  }).format(basisPoints / 100)
}

function policySummary(
  configuration: RoutingConfiguration,
  enabledProviders: ProviderRoutingProfile[]
) {
  const fallback = providerLabels[configuration.default_provider]
  const fallbackCost = enabledProviders.find(
    (profile) => profile.provider === configuration.default_provider
  )?.cost_units_per_second
  const probeRate = formatPercentage(
    configuration.existing_domain_probe_rate_basis_points
  )
  const matches = configuration.required_successful_probes
  const cheaperProviders = enabledProviders.filter(
    (profile) =>
      profile.provider !== configuration.default_provider &&
      fallbackCost !== undefined &&
      profile.cost_units_per_second < fallbackCost
  ).length

  return `Unknown and unqualified domains use ${fallback}. Harbor evaluates ${
    cheaperProviders === 1
      ? "one cheaper enabled alternative"
      : `${cheaperProviders} cheaper enabled alternatives`
  } and qualifies an acquisition path after ${matches} matching ${
    matches === 1 ? "probe" : "probes"
  }. ${probeRate}% of later eligible sessions are rechecked.`
}

function LoadingState() {
  return (
    <div className="flex min-h-96 items-center justify-center rounded-lg border bg-card">
      <div className="text-center">
        <LoaderCircle
          className="mx-auto size-5 animate-spin text-muted-foreground"
          aria-hidden
        />
        <p className="mt-3 text-sm text-muted-foreground">
          Loading routing policy
        </p>
      </div>
    </div>
  )
}

function ErrorState({ error, retry }: { error: unknown; retry: () => void }) {
  return (
    <div className="flex min-h-96 items-center justify-center rounded-lg border bg-card px-6">
      <div className="max-w-md text-center">
        <CircleAlert className="mx-auto size-6 text-destructive" aria-hidden />
        <h2 className="mt-4 font-semibold">Routing policy is unavailable</h2>
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

function PolicySettings({
  configuration,
  profiles,
}: {
  configuration: RoutingConfiguration
  profiles: ProviderRoutingProfile[]
}) {
  const queryClient = useQueryClient()
  const [defaultProvider, setDefaultProvider] = useState(
    configuration.default_provider
  )
  const [probeRate, setProbeRate] = useState(
    String(configuration.existing_domain_probe_rate_basis_points / 100)
  )
  const [requiredProbes, setRequiredProbes] = useState(
    String(configuration.required_successful_probes)
  )

  const basisPoints = Math.round(Number(probeRate) * 100)
  const requiredProbeCount = Number(requiredProbes)
  const valid =
    Number.isFinite(basisPoints) &&
    basisPoints >= 0 &&
    basisPoints <= 10_000 &&
    Number.isInteger(requiredProbeCount) &&
    requiredProbeCount >= 1 &&
    requiredProbeCount <= 100
  const dirty =
    defaultProvider !== configuration.default_provider ||
    basisPoints !== configuration.existing_domain_probe_rate_basis_points ||
    requiredProbeCount !== configuration.required_successful_probes

  const mutation = useMutation({
    mutationFn: updateRoutingConfiguration,
    onSuccess: (value) => {
      queryClient.setQueryData(["routing-configuration"], value)
      toast.success("Routing policy updated")
    },
    onError: (error) => toast.error(extractApiError(error)),
  })

  const submit = (event: FormEvent) => {
    event.preventDefault()
    if (!dirty || !valid) return

    const update: RoutingConfigurationUpdate = {}
    if (defaultProvider !== configuration.default_provider) {
      update.default_provider = defaultProvider
    }
    if (basisPoints !== configuration.existing_domain_probe_rate_basis_points) {
      update.existing_domain_probe_rate_basis_points = basisPoints
    }
    if (requiredProbeCount !== configuration.required_successful_probes) {
      update.required_successful_probes = requiredProbeCount
    }
    mutation.mutate(update)
  }

  const enabledProfiles = profiles.filter(
    (profile) => profile.automatic_enabled
  )

  return (
    <Card className="gap-0 rounded-lg">
      <form onSubmit={submit}>
        <div className="flex flex-col gap-3 border-b px-5 py-4 sm:flex-row sm:items-center sm:justify-between">
          <div>
            <h2 className="font-semibold">Automatic routing policy</h2>
            <p className="mt-1 text-sm text-muted-foreground">
              Configure fallback behavior and qualification evidence.
            </p>
          </div>
          <Badge
            variant="outline"
            className="h-7 gap-2 self-start bg-muted/35 px-3 text-muted-foreground sm:self-auto"
          >
            <GitCompareArrows className="size-3.5" aria-hidden />
            Configuration v{configuration.configuration_version}
          </Badge>
        </div>

        <div className="grid divide-y lg:grid-cols-3 lg:divide-x lg:divide-y-0">
          <div className="p-5">
            <Label htmlFor="routing-fallback">
              <ShieldCheck
                className="size-4 text-muted-foreground"
                aria-hidden
              />
              Unknown-domain fallback
            </Label>
            <span className="mt-1 block text-xs leading-5 text-muted-foreground">
              Used until a cheaper provider has been qualified.
            </span>
            <Select
              value={defaultProvider}
              onValueChange={(value) =>
                setDefaultProvider(value as ActivityProvider)
              }
            >
              <SelectTrigger id="routing-fallback" className="mt-4 h-10 w-full">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {enabledProfiles.map((profile) => (
                  <SelectItem key={profile.provider} value={profile.provider}>
                    {providerLabels[profile.provider]}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          <div className="p-5">
            <Label htmlFor="routing-probe-rate">
              <Gauge className="size-4 text-muted-foreground" aria-hidden />
              Existing-domain probe rate
            </Label>
            <span className="mt-1 block text-xs leading-5 text-muted-foreground">
              Percentage of later eligible sessions rechecked.
            </span>
            <span className="relative mt-4 block">
              <Input
                id="routing-probe-rate"
                type="number"
                min="0"
                max="100"
                step="0.01"
                required
                value={probeRate}
                onChange={(event) => setProbeRate(event.target.value)}
                className="h-10 pr-9"
              />
              <span className="pointer-events-none absolute top-1/2 right-3 -translate-y-1/2 text-sm text-muted-foreground">
                %
              </span>
            </span>
            <span className="mt-2 block text-xs text-muted-foreground">
              New domains are still evaluated when this is 0%.
            </span>
          </div>

          <div className="p-5">
            <Label htmlFor="routing-required-probes">
              <Check className="size-4 text-muted-foreground" aria-hidden />
              Required matching probes
            </Label>
            <span className="mt-1 block text-xs leading-5 text-muted-foreground">
              Evidence needed before a provider is qualified.
            </span>
            <Input
              id="routing-required-probes"
              type="number"
              min="1"
              max="100"
              step="1"
              required
              value={requiredProbes}
              onChange={(event) => setRequiredProbes(event.target.value)}
              className="mt-4 h-10"
            />
            <span className="mt-2 block text-xs text-muted-foreground">
              A mismatch or execution failure rejects the candidate.
            </span>
          </div>
        </div>

        <div className="flex flex-col gap-3 border-t bg-muted/15 px-5 py-4 sm:flex-row sm:items-center sm:justify-between">
          <p className="text-xs text-muted-foreground">
            Comparison policy v{configuration.comparison_policy_version}
          </p>
          <Button
            type="submit"
            className="gap-2"
            disabled={!dirty || !valid || mutation.isPending}
          >
            {mutation.isPending ? (
              <LoaderCircle className="size-4 animate-spin" aria-hidden />
            ) : (
              <Save className="size-4" aria-hidden />
            )}
            Save policy
          </Button>
        </div>
      </form>
    </Card>
  )
}

function ProviderRow({
  profile,
  rank,
  isDefault,
}: {
  profile: ProviderRoutingProfile
  rank?: number
  isDefault: boolean
}) {
  const queryClient = useQueryClient()
  const [enabled, setEnabled] = useState(profile.automatic_enabled)
  const [cost, setCost] = useState(String(profile.cost_units_per_second))

  const numericCost = Number(cost)
  const validCost = Number.isInteger(numericCost) && numericCost >= 0
  const dirty =
    enabled !== profile.automatic_enabled ||
    numericCost !== profile.cost_units_per_second

  const mutation = useMutation({
    mutationFn: (update: ProviderRoutingUpdate) =>
      updateProviderProfile(profile.provider, update),
    onSuccess: (value) => {
      queryClient.setQueryData<ProviderRoutingProfile[]>(
        ["routing-providers"],
        (current = []) =>
          current.map((item) =>
            item.provider === value.provider ? value : item
          )
      )
      void queryClient.invalidateQueries({
        queryKey: ["routing-configuration"],
      })
      toast.success(`${providerLabels[profile.provider]} routing updated`)
    },
    onError: (error) => toast.error(extractApiError(error)),
  })

  const submit = (event: FormEvent) => {
    event.preventDefault()
    if (!dirty || !validCost) return
    const update: ProviderRoutingUpdate = {}
    if (enabled !== profile.automatic_enabled) {
      update.automatic_enabled = enabled
    }
    if (numericCost !== profile.cost_units_per_second) {
      update.cost_units_per_second = numericCost
    }
    mutation.mutate(update)
  }

  return (
    <form
      onSubmit={submit}
      className="grid gap-4 px-5 py-4 md:grid-cols-[minmax(12rem,1.4fr)_minmax(10rem,1fr)_minmax(11rem,1fr)_minmax(8rem,0.8fr)_6rem] md:items-center"
    >
      <div className="flex items-center gap-3">
        <span
          className={cn(
            "flex size-9 shrink-0 items-center justify-center rounded-md border",
            profile.provider === "http" ? "bg-blue-500/8" : "bg-background"
          )}
        >
          {profile.provider === "http" ? (
            <Globe2 className="size-4 text-blue-600" aria-hidden />
          ) : (
            <Network className="size-4 text-muted-foreground" aria-hidden />
          )}
        </span>
        <div>
          <div className="flex flex-wrap items-center gap-2">
            <p className="font-medium">{providerLabels[profile.provider]}</p>
            {isDefault && (
              <Badge
                variant="outline"
                className="h-5 bg-muted/50 text-[0.625rem] text-muted-foreground"
              >
                Fallback
              </Badge>
            )}
          </div>
          <p className="mt-0.5 text-xs text-muted-foreground">
            {providerDescriptions[profile.provider]}
          </p>
        </div>
      </div>

      <div>
        <span className="mb-2 block text-xs text-muted-foreground md:hidden">
          Automatic selection
        </span>
        <div className="inline-flex items-center gap-2.5">
          <Switch
            checked={enabled}
            onCheckedChange={setEnabled}
            aria-label={`Automatic routing for ${providerLabels[profile.provider]}`}
            disabled={isDefault}
            className="data-checked:bg-emerald-600"
          />
          <span className="text-sm">{enabled ? "Enabled" : "Excluded"}</span>
        </div>
        {isDefault && (
          <p className="mt-1 text-[0.6875rem] text-muted-foreground">
            Change the fallback before disabling.
          </p>
        )}
      </div>

      <div>
        <span className="mb-2 block text-xs text-muted-foreground md:hidden">
          Cost per second
        </span>
        <span className="relative block max-w-40">
          <Input
            type="number"
            min="0"
            step="1"
            required
            value={cost}
            onChange={(event) => setCost(event.target.value)}
            aria-label={`Cost units per second for ${providerLabels[profile.provider]}`}
            className="h-9 pr-16"
          />
          <span className="pointer-events-none absolute top-1/2 right-3 -translate-y-1/2 text-xs text-muted-foreground">
            units/s
          </span>
        </span>
      </div>

      <div>
        <span className="mb-1 block text-xs text-muted-foreground md:hidden">
          Effective order
        </span>
        {enabled && rank ? (
          <span className="inline-flex items-center gap-1.5 text-sm">
            <ArrowDown className="size-3.5 text-muted-foreground" aria-hidden />
            {rank === 1 ? "Cheapest" : `Rank ${rank}`}
          </span>
        ) : (
          <span className="text-sm text-muted-foreground">Excluded</span>
        )}
        <p className="mt-1 text-[0.6875rem] text-muted-foreground">
          Capability v{profile.capability_manifest_version}
        </p>
      </div>

      <Button
        type="submit"
        size="sm"
        variant={dirty ? "default" : "outline"}
        disabled={!dirty || !validCost || mutation.isPending}
        className="gap-1.5 md:justify-self-end"
      >
        {mutation.isPending ? (
          <LoaderCircle className="size-3.5 animate-spin" aria-hidden />
        ) : (
          <Save className="size-3.5" aria-hidden />
        )}
        Save
      </Button>
    </form>
  )
}

function ProviderPolicy({
  configuration,
  profiles,
}: {
  configuration: RoutingConfiguration
  profiles: ProviderRoutingProfile[]
}) {
  const ranks = useMemo(() => {
    const enabled = profiles
      .filter((profile) => profile.automatic_enabled)
      .toSorted(
        (left, right) =>
          left.cost_units_per_second - right.cost_units_per_second ||
          left.provider.localeCompare(right.provider)
      )
    return new Map(
      enabled.map((profile, index) => [profile.provider, index + 1])
    )
  }, [profiles])

  const sortedProfiles = useMemo(
    () =>
      profiles.toSorted(
        (left, right) =>
          Number(right.automatic_enabled) - Number(left.automatic_enabled) ||
          left.cost_units_per_second - right.cost_units_per_second ||
          left.provider.localeCompare(right.provider)
      ),
    [profiles]
  )

  return (
    <Card className="gap-0 rounded-lg">
      <div className="flex flex-col gap-3 border-b px-5 py-4 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h2 className="font-semibold">Provider policy</h2>
          <p className="mt-1 text-sm text-muted-foreground">
            Set automatic eligibility and relative acquisition cost.
          </p>
        </div>
        <span className="inline-flex items-center gap-2 self-start text-xs text-muted-foreground sm:self-auto">
          <CircleDollarSign className="size-4" aria-hidden />
          Relative Harbor cost units
        </span>
      </div>

      <div className="hidden grid-cols-[minmax(12rem,1.4fr)_minmax(10rem,1fr)_minmax(11rem,1fr)_minmax(8rem,0.8fr)_6rem] border-b bg-muted/35 px-5 py-2.5 font-mono text-[0.6875rem] font-medium tracking-wider text-muted-foreground uppercase md:grid">
        <span>Provider</span>
        <span>Automatic</span>
        <span>Cost per second</span>
        <span>Effective order</span>
        <span />
      </div>

      <div className="divide-y">
        {sortedProfiles.map((profile) => (
          <ProviderRow
            key={`${profile.provider}:${profile.automatic_enabled}:${profile.cost_units_per_second}:${profile.capability_manifest_version}`}
            profile={profile}
            rank={ranks.get(profile.provider)}
            isDefault={profile.provider === configuration.default_provider}
          />
        ))}
      </div>

      <div className="flex gap-3 border-t bg-muted/15 px-5 py-4 text-xs leading-5 text-muted-foreground">
        <Info className="mt-0.5 size-4 shrink-0" aria-hidden />
        <p>
          Routing eligibility controls automatic selection and qualification. It
          does not disable a managed fleet or prevent explicit{" "}
          <code className="rounded bg-muted px-1 py-0.5 font-mono text-[0.6875rem] text-foreground">
            harbor.provider.slug
          </code>{" "}
          selection.
        </p>
      </div>
    </Card>
  )
}

export function RoutingPage() {
  const configuration = useQuery({
    queryKey: ["routing-configuration"],
    queryFn: fetchRoutingConfiguration,
  })
  const profiles = useQuery({
    queryKey: ["routing-providers"],
    queryFn: fetchProviderProfiles,
  })

  const loading = configuration.isPending || profiles.isPending
  const error = configuration.error ?? profiles.error
  const enabledProfiles =
    profiles.data?.filter((profile) => profile.automatic_enabled) ?? []

  const retry = () => {
    void Promise.all([configuration.refetch(), profiles.refetch()])
  }

  return (
    <main className="mx-auto min-h-[calc(100svh-4rem)] w-full max-w-7xl px-4 py-6 sm:px-6 md:min-h-svh lg:px-10 lg:py-10">
      <header className="flex flex-col gap-4 border-b pb-6 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <div className="mb-3 flex items-center gap-2 text-xs font-medium text-muted-foreground">
            <Network className="size-3.5" aria-hidden />
            Control plane
          </div>
          <h1 className="text-3xl font-semibold tracking-tight sm:text-4xl">
            Routing
          </h1>
          <p className="mt-2 max-w-2xl text-sm leading-6 text-muted-foreground">
            Control automatic provider selection, qualification evidence, and
            relative acquisition cost.
          </p>
        </div>
        {configuration.data && (
          <Badge
            variant="outline"
            className="h-8 gap-2 self-start bg-card px-3 text-muted-foreground sm:self-auto"
          >
            <span className="size-1.5 rounded-full bg-emerald-500" />
            Policy active
          </Badge>
        )}
      </header>

      <div className="py-6">
        {loading ? (
          <LoadingState />
        ) : error || !configuration.data || !profiles.data ? (
          <ErrorState error={error} retry={retry} />
        ) : (
          <div className="space-y-6">
            <Card className="relative block overflow-hidden rounded-lg border-l-4 border-l-primary px-5 py-5 sm:px-6">
              <div className="absolute -top-16 -right-10 size-40 rounded-full border border-primary/8" />
              <div className="absolute -top-7 -right-3 size-24 rounded-full border border-primary/8" />
              <div className="relative flex max-w-4xl gap-4">
                <span className="mt-0.5 flex size-9 shrink-0 items-center justify-center rounded-md bg-primary/10 text-primary">
                  <Network className="size-4" aria-hidden />
                </span>
                <div>
                  <p className="text-xs font-medium tracking-wider text-muted-foreground uppercase">
                    Effective policy
                  </p>
                  <p className="mt-2 text-sm leading-6 text-foreground sm:text-base">
                    {policySummary(configuration.data, enabledProfiles)}
                  </p>
                </div>
              </div>
            </Card>

            <PolicySettings
              key={`${configuration.data.configuration_version}:${configuration.data.default_provider}:${configuration.data.existing_domain_probe_rate_basis_points}:${configuration.data.required_successful_probes}`}
              configuration={configuration.data}
              profiles={profiles.data}
            />

            <ProviderPolicy
              configuration={configuration.data}
              profiles={profiles.data}
            />
          </div>
        )}
      </div>
    </main>
  )
}
