import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import {
  Ban,
  CircleAlert,
  LoaderCircle,
  RefreshCw,
  Save,
  ShieldCheck,
} from "lucide-react"
import { type FormEvent, useState } from "react"
import { toast } from "sonner"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { apiRequest, extractApiError } from "@/lib/api"
import type {
  ActivityProvider,
  NetworkPolicy,
  NetworkPolicyUpdate,
  ProviderCostRate,
} from "@/types/api"

const providerLabels: Record<ActivityProvider, string> = {
  browserless: "Browserless",
  browserless_cloud: "Browserless cloud",
}

const providerDescriptions: Record<ActivityProvider, string> = {
  browserless: "The local browser fleet and /v1/connect's default",
  browserless_cloud: "Paid stealth browsers for challenge resolution",
}

function fetchNetworkPolicy() {
  return apiRequest<NetworkPolicy>("/v1/admin/network")
}

function fetchCostRates() {
  return apiRequest<ProviderCostRate[]>("/v1/admin/costs/rates")
}

function updateNetworkPolicy(update: NetworkPolicyUpdate) {
  return apiRequest<NetworkPolicy>("/v1/admin/network", {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(update),
  })
}

function updateCostRate(
  provider: ActivityProvider,
  costUnitsPerSecond: number
) {
  return apiRequest<ProviderCostRate>(`/v1/admin/costs/rates/${provider}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ cost_units_per_second: costUnitsPerSecond }),
  })
}

function LoadingState() {
  return (
    <div className="flex min-h-96 items-center justify-center rounded-lg border bg-card">
      <div className="text-center">
        <LoaderCircle
          className="mx-auto size-5 animate-spin text-muted-foreground"
          aria-hidden
        />
        <p className="mt-3 text-sm text-muted-foreground">Loading policy</p>
      </div>
    </div>
  )
}

function ErrorState({ error, retry }: { error: unknown; retry: () => void }) {
  return (
    <div className="flex min-h-96 items-center justify-center rounded-lg border bg-card px-6">
      <div className="max-w-md text-center">
        <CircleAlert className="mx-auto size-6 text-destructive" aria-hidden />
        <h2 className="mt-4 font-semibold">Policy is unavailable</h2>
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

function NetworkPolicySettings({ policy }: { policy: NetworkPolicy }) {
  const queryClient = useQueryClient()
  const [patternsText, setPatternsText] = useState(
    policy.blocked_domain_patterns.join("\n")
  )
  const patterns = patternsText
    .split("\n")
    .map((pattern) => pattern.trim())
    .filter(Boolean)
  const dirty =
    JSON.stringify(patterns) !== JSON.stringify(policy.blocked_domain_patterns)
  const valid = patterns.length <= 1_000

  const mutation = useMutation({
    mutationFn: updateNetworkPolicy,
    onSuccess: (value) => {
      queryClient.setQueryData(["network-policy"], value)
      setPatternsText(value.blocked_domain_patterns.join("\n"))
      toast.success("Network blocklist updated")
    },
    onError: (error) => toast.error(extractApiError(error)),
  })

  const submit = (event: FormEvent) => {
    event.preventDefault()
    if (!dirty || !valid) return
    mutation.mutate({ blocked_domain_patterns: patterns })
  }

  return (
    <Card className="gap-0 rounded-lg">
      <form onSubmit={submit}>
        <div className="flex flex-col gap-3 border-b px-4 py-3 sm:flex-row sm:items-center sm:justify-between">
          <div>
            <h2 className="font-semibold">Global domain blocklist</h2>
            <p className="mt-1 text-sm text-muted-foreground">
              Block ad, tracker and other page requests to these hosts in every
              session and capture. Sessions can still navigate to them.
            </p>
          </div>
          <Badge
            variant="outline"
            className="h-7 gap-2 self-start bg-muted/35 px-3 text-muted-foreground sm:self-auto"
          >
            <Ban className="size-3.5" aria-hidden />
            Configuration v{policy.configuration_version}
          </Badge>
        </div>

        <div className="p-4">
          <Label htmlFor="network-blocked-domains">
            Blocked domain patterns
          </Label>
          <span className="mt-1 block text-xs leading-5 text-muted-foreground">
            Enter one hostname per line. A leading wildcard, such as{" "}
            <code className="rounded bg-muted px-1 py-0.5 font-mono text-[0.6875rem] text-foreground">
              *.doubleclick.net
            </code>
            , matches subdomains.
          </span>
          <textarea
            id="network-blocked-domains"
            className="mt-4 min-h-40 w-full resize-y rounded-md border border-input bg-transparent px-3 py-2 font-mono text-sm shadow-xs outline-none placeholder:text-muted-foreground focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/50"
            value={patternsText}
            onChange={(event) => setPatternsText(event.target.value)}
            placeholder={"*.doubleclick.net\nads.example"}
            spellCheck={false}
          />
          <div className="mt-3 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
            <p className="text-xs text-muted-foreground">
              {patterns.length} of 1,000 patterns configured. Changes apply to
              newly acquired sessions.
            </p>
            <Button
              type="submit"
              className="gap-2 self-start sm:self-auto"
              disabled={!dirty || !valid || mutation.isPending}
            >
              {mutation.isPending ? (
                <LoaderCircle className="size-4 animate-spin" aria-hidden />
              ) : (
                <Save className="size-4" aria-hidden />
              )}
              Save blocklist
            </Button>
          </div>
        </div>
      </form>
    </Card>
  )
}

function CostRateRow({ rate }: { rate: ProviderCostRate }) {
  const queryClient = useQueryClient()
  const [cost, setCost] = useState(String(rate.cost_units_per_second))
  const numericCost = Number(cost)
  const valid = Number.isInteger(numericCost) && numericCost >= 0
  const dirty = numericCost !== rate.cost_units_per_second

  const mutation = useMutation({
    mutationFn: (value: number) => updateCostRate(rate.provider, value),
    onSuccess: (value) => {
      queryClient.setQueryData<ProviderCostRate[]>(
        ["cost-rates"],
        (current = []) =>
          current.map((item) =>
            item.provider === value.provider ? value : item
          )
      )
      toast.success(`${providerLabels[rate.provider]} cost rate updated`)
    },
    onError: (error) => toast.error(extractApiError(error)),
  })

  const submit = (event: FormEvent) => {
    event.preventDefault()
    if (!dirty || !valid) return
    mutation.mutate(numericCost)
  }

  return (
    <form
      onSubmit={submit}
      className="grid gap-3 px-4 py-3 md:grid-cols-[minmax(12rem,1.6fr)_minmax(9rem,1fr)_5rem] md:items-center"
    >
      <div>
        <p className="font-medium">{providerLabels[rate.provider]}</p>
        <p className="mt-0.5 text-xs text-muted-foreground">
          {providerDescriptions[rate.provider]}
        </p>
      </div>
      <span className="relative block max-w-40">
        <Input
          type="number"
          min="0"
          step="1"
          required
          value={cost}
          onChange={(event) => setCost(event.target.value)}
          aria-label={`Cost units per second for ${providerLabels[rate.provider]}`}
          className="h-9 pr-16"
        />
        <span className="pointer-events-none absolute top-1/2 right-3 -translate-y-1/2 text-xs text-muted-foreground">
          units/s
        </span>
      </span>
      <Button
        type="submit"
        size="sm"
        variant={dirty ? "default" : "outline"}
        disabled={!dirty || !valid || mutation.isPending}
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

function CostRates({ rates }: { rates: ProviderCostRate[] }) {
  return (
    <Card className="gap-0 rounded-lg">
      <div className="border-b px-4 py-3">
        <h2 className="font-semibold">Cost rates</h2>
        <p className="mt-1 text-sm text-muted-foreground">
          Modeled cost units per second a provider slot is held. New rates apply
          to attempts that finish afterwards.
        </p>
      </div>
      <div className="divide-y">
        {rates.map((rate) => (
          <CostRateRow
            key={`${rate.provider}:${rate.cost_units_per_second}`}
            rate={rate}
          />
        ))}
      </div>
    </Card>
  )
}

export function PolicyPage() {
  const networkPolicy = useQuery({
    queryKey: ["network-policy"],
    queryFn: fetchNetworkPolicy,
  })
  const rates = useQuery({ queryKey: ["cost-rates"], queryFn: fetchCostRates })

  const retry = () => {
    void Promise.all([networkPolicy.refetch(), rates.refetch()])
  }

  return (
    <main className="mx-auto min-h-[calc(100svh-4rem)] w-full max-w-[100rem] px-4 py-6 sm:px-6 md:min-h-svh lg:px-8 lg:py-8">
      <header className="border-b pb-4">
        <div className="mb-3 flex items-center gap-2 text-xs font-medium text-muted-foreground">
          <ShieldCheck className="size-3.5" aria-hidden />
          Control plane
        </div>
        <h1 className="text-3xl font-semibold tracking-tight sm:text-4xl">
          Settings
        </h1>
        <p className="mt-2 max-w-2xl text-sm leading-6 text-muted-foreground">
          Global request blocking and the cost model applied to provider usage.
        </p>
      </header>

      <div className="py-5">
        {networkPolicy.isPending || rates.isPending ? (
          <LoadingState />
        ) : networkPolicy.error ||
          rates.error ||
          !networkPolicy.data ||
          !rates.data ? (
          <ErrorState
            error={networkPolicy.error ?? rates.error}
            retry={retry}
          />
        ) : (
          <div className="space-y-4">
            <NetworkPolicySettings
              key={`${networkPolicy.data.configuration_version}:${networkPolicy.data.blocked_domain_patterns.join(",")}`}
              policy={networkPolicy.data}
            />
            <CostRates rates={rates.data} />
          </div>
        )}
      </div>
    </main>
  )
}
