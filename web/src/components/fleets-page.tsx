import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import {
  Activity,
  ArrowLeft,
  ArrowRight,
  Boxes,
  CircleAlert,
  Cloud,
  Clock3,
  LoaderCircle,
  RefreshCw,
  Save,
  Server,
  Settings2,
} from "lucide-react"
import { type FormEvent, useMemo, useState } from "react"
import { toast } from "sonner"

import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Switch } from "@/components/ui/switch"
import { extractApiError } from "@/lib/api"
import { cn } from "@/lib/utils"
import type {
  FleetConfiguration,
  FleetConfigurationUpdate,
  ExternalProviderCapacity,
  ExternalProviderCapacityUpdate,
  ManagedProvider,
  ProviderFleetSnapshot,
} from "@/types/api"

type FleetPageProps = {
  provider?: string
  navigate: (href: string) => void
}

type FleetRow = {
  configuration: FleetConfiguration
  snapshot?: ProviderFleetSnapshot
}

const providerLabels: Record<ManagedProvider, string> = {
  browserless: "Browserless",
}

const admissionProviderLabels: Record<
  ExternalProviderCapacity["provider"],
  string
> = {
  http: "Direct HTTP",
  browserbase: "Browserbase",
}

const numberFormatter = new Intl.NumberFormat()

async function apiRequest<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, init)
  if (!response.ok) {
    const body: unknown = await response.json().catch(() => undefined)
    throw new Error(extractApiError(body))
  }
  return (await response.json()) as T
}

function fetchFleetSnapshots() {
  return apiRequest<ProviderFleetSnapshot[]>("/v1/fleet/providers")
}

function fetchFleetConfigurations() {
  return apiRequest<FleetConfiguration[]>("/v1/admin/fleets")
}

function fetchExternalCapacities() {
  return apiRequest<ExternalProviderCapacity[]>(
    "/v1/admin/providers/capacity"
  )
}

function updateExternalCapacity(
  provider: ExternalProviderCapacity["provider"],
  update: ExternalProviderCapacityUpdate
) {
  return apiRequest<ExternalProviderCapacity>(
    `/v1/admin/providers/${provider}/capacity`,
    {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(update),
    }
  )
}

function updateFleetConfiguration(
  provider: ManagedProvider,
  update: FleetConfigurationUpdate
) {
  return apiRequest<FleetConfiguration>(`/v1/admin/fleets/${provider}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(update),
  })
}

function fleetStatus(row: FleetRow) {
  const { configuration, snapshot } = row
  if (!configuration.enabled) {
    return {
      label: "Disabled",
      tone: "muted",
      detail: "Not accepting automatic demand",
    } as const
  }
  if (!snapshot) {
    return {
      label: "Unavailable",
      tone: "danger",
      detail: "No current fleet observation",
    } as const
  }
  if (snapshot.unhealthy_instances > 0) {
    return {
      label: "Degraded",
      tone: "danger",
      detail: `${snapshot.unhealthy_instances} unhealthy ${
        snapshot.unhealthy_instances === 1 ? "instance" : "instances"
      }`,
    } as const
  }
  if (snapshot.queued_attempts > 0 && snapshot.available_slots === 0) {
    return {
      label: "Under pressure",
      tone: "warning",
      detail: `${snapshot.queued_attempts} queued with no free slots`,
    } as const
  }
  if (
    snapshot.ready_instances < snapshot.desired_instances ||
    snapshot.observed_instances < snapshot.desired_instances
  ) {
    return {
      label: "Scaling",
      tone: "warning",
      detail: `${snapshot.ready_instances} of ${snapshot.desired_instances} desired ready`,
    } as const
  }
  return {
    label: "Healthy",
    tone: "success",
    detail: "Capacity is ready",
  } as const
}

function StatusBadge({ row }: { row: FleetRow }) {
  const status = fleetStatus(row)
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs font-medium",
        status.tone === "success" &&
          "border-emerald-500/20 bg-emerald-500/8 text-emerald-700 dark:text-emerald-400",
        status.tone === "warning" &&
          "border-amber-500/25 bg-amber-500/8 text-amber-700 dark:text-amber-400",
        status.tone === "danger" &&
          "border-destructive/20 bg-destructive/8 text-destructive",
        status.tone === "muted" && "bg-muted/50 text-muted-foreground"
      )}
    >
      <span
        className={cn(
          "size-1.5 rounded-full",
          status.tone === "success" && "bg-emerald-500",
          status.tone === "warning" && "bg-amber-500",
          status.tone === "danger" && "bg-destructive",
          status.tone === "muted" && "bg-muted-foreground"
        )}
      />
      {status.label}
    </span>
  )
}

function formatDuration(seconds: number) {
  if (seconds <= 0) return "None"
  if (seconds < 60) return `${Math.round(seconds)}s`
  const minutes = Math.floor(seconds / 60)
  const remainder = Math.round(seconds % 60)
  return remainder ? `${minutes}m ${remainder}s` : `${minutes}m`
}

function MetricCard({
  label,
  value,
  detail,
  icon: Icon,
}: {
  label: string
  value: number
  detail: string
  icon: typeof Activity
}) {
  return (
    <div className="rounded-lg border bg-card p-4">
      <div className="flex items-center justify-between">
        <p className="text-sm font-medium text-muted-foreground">{label}</p>
        <Icon className="size-4 text-muted-foreground" aria-hidden />
      </div>
      <p className="mt-3 text-2xl font-semibold tracking-tight">
        {numberFormatter.format(value)}
      </p>
      <p className="mt-1 text-xs text-muted-foreground">{detail}</p>
    </div>
  )
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
          Loading managed fleets
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
        <h2 className="mt-4 font-semibold">Fleet data is unavailable</h2>
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

function FleetList({
  rows,
  admissionCapacities,
  snapshots,
  navigate,
}: {
  rows: FleetRow[]
  admissionCapacities: ExternalProviderCapacity[]
  snapshots: ProviderFleetSnapshot[]
  navigate: (href: string) => void
}) {
  const totals = useMemo(
    () =>
      rows.reduce(
        (current, row) => {
          const snapshot = row.snapshot
          if (!snapshot) return current
          current.active += snapshot.active_attempts
          current.queued += snapshot.queued_attempts
          current.available += snapshot.available_slots
          current.totalSlots += snapshot.total_slots
          current.ready += snapshot.ready_instances
          current.observed += snapshot.observed_instances
          return current
        },
        {
          active: 0,
          queued: 0,
          available: 0,
          totalSlots: 0,
          ready: 0,
          observed: 0,
        }
      ),
    [rows]
  )

  return (
    <>
      <section className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <MetricCard
          label="Active attempts"
          value={totals.active}
          detail="Acquisitions using provider capacity"
          icon={Activity}
        />
        <MetricCard
          label="Queued attempts"
          value={totals.queued}
          detail="Waiting across managed providers"
          icon={Clock3}
        />
        <MetricCard
          label="Available slots"
          value={totals.available}
          detail={`${totals.totalSlots} total managed slots`}
          icon={Boxes}
        />
        <MetricCard
          label="Ready instances"
          value={totals.ready}
          detail={`${totals.observed} instances currently observed`}
          icon={Server}
        />
      </section>

      <section className="mt-5 overflow-hidden rounded-lg border bg-card">
        <div className="border-b px-4 py-3">
          <h2 className="font-semibold">Managed providers</h2>
          <p className="mt-1 text-sm text-muted-foreground">
            Browser fleets with instance-backed session capacity.
          </p>
        </div>
        <div className="hidden min-w-[52rem] grid-cols-[1.35fr_1fr_1fr_1fr_1fr_2rem] gap-2 border-b bg-muted/35 px-4 py-2 font-mono text-[0.625rem] font-medium tracking-wider text-muted-foreground uppercase md:grid">
          <span>Provider</span>
          <span>Instances</span>
          <span>Slots</span>
          <span>Attempts</span>
          <span>Queue</span>
          <span />
        </div>
        <div className="divide-y">
          {rows.map((row) => {
            const { configuration, snapshot } = row
            const status = fleetStatus(row)
            const href = `/fleets/${configuration.provider}`
            const occupied = snapshot
              ? Math.max(0, snapshot.total_slots - snapshot.available_slots)
              : 0
            return (
              <a
                key={configuration.provider}
                href={href}
                onClick={(event) => {
                  event.preventDefault()
                  navigate(href)
                }}
                className="group block px-4 py-3 transition-colors hover:bg-muted/25 md:grid md:min-w-[52rem] md:grid-cols-[1.35fr_1fr_1fr_1fr_1fr_2rem] md:items-center md:gap-2"
              >
                <div>
                  <div className="flex items-center gap-2">
                    <span className="flex size-8 items-center justify-center rounded-md border bg-background">
                      <Server
                        className="size-4 text-muted-foreground"
                        aria-hidden
                      />
                    </span>
                    <div>
                      <p className="font-medium">
                        {providerLabels[configuration.provider]}
                      </p>
                      <div className="mt-1 md:hidden">
                        <StatusBadge row={row} />
                      </div>
                      <p className="mt-1 hidden text-xs text-muted-foreground md:block">
                        {status.detail}
                      </p>
                    </div>
                  </div>
                </div>
                <div className="mt-4 grid grid-cols-2 gap-4 text-sm md:mt-0 md:block">
                  <p className="text-xs text-muted-foreground md:hidden">
                    Instances
                  </p>
                  <p>
                    <span className="font-medium">
                      {snapshot?.ready_instances ?? "—"}
                    </span>
                    <span className="text-muted-foreground">
                      {" "}
                      / {configuration.desired_instances} ready
                    </span>
                  </p>
                </div>
                <div className="mt-3 grid grid-cols-2 gap-4 text-sm md:mt-0 md:block">
                  <p className="text-xs text-muted-foreground md:hidden">
                    Slots
                  </p>
                  <p>
                    <span className="font-medium">{occupied}</span>
                    <span className="text-muted-foreground">
                      {" "}
                      / {snapshot?.total_slots ?? "—"} occupied
                    </span>
                  </p>
                </div>
                <div className="mt-3 grid grid-cols-2 gap-4 text-sm md:mt-0 md:block">
                  <p className="text-xs text-muted-foreground md:hidden">
                    Attempts
                  </p>
                  <p>
                    <span className="font-medium">
                      {snapshot?.active_attempts ?? "—"}
                    </span>
                    <span className="text-muted-foreground"> active</span>
                  </p>
                </div>
                <div className="mt-3 grid grid-cols-2 gap-4 text-sm md:mt-0 md:block">
                  <p className="text-xs text-muted-foreground md:hidden">
                    Queue
                  </p>
                  <p>
                    <span className="font-medium">
                      {snapshot?.queued_attempts ?? "—"}
                    </span>
                    <span className="text-muted-foreground">
                      {snapshot?.queued_attempts
                        ? ` · ${formatDuration(
                            snapshot.oldest_queued_attempt_seconds
                          )} oldest`
                        : " waiting"}
                    </span>
                  </p>
                </div>
                <ArrowRight
                  className="hidden size-4 text-muted-foreground transition-transform group-hover:translate-x-0.5 md:block"
                  aria-hidden
                />
              </a>
            )
          })}
        </div>
      </section>

      {admissionCapacities.length > 0 && (
        <section className="mt-5 overflow-hidden rounded-lg border bg-card">
          <div className="border-b px-4 py-3">
            <h2 className="font-semibold">Admission-controlled providers</h2>
            <p className="mt-1 text-sm text-muted-foreground">
              Concurrency and queue limits enforced by Harbor admission.
            </p>
          </div>
          <div className="divide-y">
            {admissionCapacities.map((capacity) => {
              const snapshot = snapshots.find(
                (candidate) => candidate.provider === capacity.provider
              )
              const href = `/fleets/${capacity.provider}`
              return (
                <a
                  key={capacity.provider}
                  href={href}
                  onClick={(event) => {
                    event.preventDefault()
                    navigate(href)
                  }}
                  className="group block px-4 py-4 transition-colors hover:bg-muted/25 md:grid md:grid-cols-[1.35fr_1fr_1fr_1fr_2rem] md:items-center md:gap-3"
                >
                  <div className="flex items-center gap-3">
                    <span className="flex size-8 items-center justify-center rounded-md border bg-background">
                      <Cloud className="size-4 text-muted-foreground" aria-hidden />
                    </span>
                    <div>
                      <p className="font-medium">
                        {admissionProviderLabels[capacity.provider]}
                      </p>
                      <p className="mt-1 text-xs text-muted-foreground">
                        {capacity.enabled
                          ? "Admission enabled"
                          : "Admission disabled"}
                      </p>
                    </div>
                  </div>
                  <p className="mt-4 text-sm md:mt-0">
                    <span className="font-medium">
                      {snapshot?.active_attempts ?? 0}
                    </span>
                    <span className="text-muted-foreground">
                      {" "}
                      / {capacity.max_active_sessions} concurrent
                    </span>
                  </p>
                  <p className="mt-3 text-sm md:mt-0">
                    <span className="font-medium">
                      {snapshot?.queued_attempts ?? 0}
                    </span>
                    <span className="text-muted-foreground">
                      {" "}
                      / {capacity.max_queued_attempts} queued
                    </span>
                  </p>
                  <p className="mt-3 text-sm text-muted-foreground md:mt-0">
                    Version {capacity.configuration_version}
                  </p>
                  <ArrowRight
                    className="hidden size-4 text-muted-foreground transition-transform group-hover:translate-x-0.5 md:block"
                    aria-hidden
                  />
                </a>
              )
            })}
          </div>
        </section>
      )}
    </>
  )
}

function CapacityBar({
  available,
  total,
}: {
  available: number
  total: number
}) {
  const occupied = Math.max(0, total - available)
  const percentage = total === 0 ? 0 : Math.min(100, (occupied / total) * 100)
  return (
    <div>
      <div className="mb-2 flex items-center justify-between text-sm">
        <span className="font-medium">{occupied} occupied</span>
        <span className="text-muted-foreground">{available} available</span>
      </div>
      <div
        className="h-2 overflow-hidden rounded-full bg-muted"
        role="progressbar"
        aria-label="Occupied fleet slots"
        aria-valuemin={0}
        aria-valuemax={total}
        aria-valuenow={occupied}
      >
        <div
          className="h-full rounded-full bg-primary transition-[width]"
          style={{ width: `${percentage}%` }}
        />
      </div>
      <p className="mt-2 text-xs text-muted-foreground">
        {total} total session slots across ready instances
      </p>
    </div>
  )
}

function NumberField({
  label,
  name,
  value,
  min,
  max,
  help,
  onChange,
}: {
  label: string
  name: keyof FleetConfigurationUpdate
  value: number
  min: number
  max?: number
  help: string
  onChange: (name: keyof FleetConfigurationUpdate, value: number) => void
}) {
  return (
    <label className="block">
      <span className="text-sm font-medium">{label}</span>
      <Input
        type="number"
        name={name}
        min={min}
        max={max}
        value={value}
        onChange={(event) => onChange(name, event.target.valueAsNumber)}
        className="mt-2 h-10 bg-background"
      />
      <span className="mt-1.5 block text-xs leading-5 text-muted-foreground">
        {help}
      </span>
    </label>
  )
}

function ConfigurationForm({
  configuration,
}: {
  configuration: FleetConfiguration
}) {
  const queryClient = useQueryClient()
  const [values, setValues] = useState<FleetConfigurationUpdate>({
    enabled: configuration.enabled,
    minimum_instances: configuration.minimum_instances,
    maximum_instances: configuration.maximum_instances,
    session_capacity_per_instance: configuration.session_capacity_per_instance,
    scale_down_cooldown_seconds: configuration.scale_down_cooldown_seconds,
    max_queued_attempts: configuration.max_queued_attempts,
  })

  const mutation = useMutation({
    mutationFn: (update: FleetConfigurationUpdate) =>
      updateFleetConfiguration(configuration.provider, update),
    onSuccess: async () => {
      await queryClient.invalidateQueries({
        queryKey: ["fleet-configurations"],
      })
      toast.success(`${providerLabels[configuration.provider]} fleet updated`)
    },
    onError: (error) => toast.error(extractApiError(error)),
  })

  const dirty =
    values.enabled !== configuration.enabled ||
    values.minimum_instances !== configuration.minimum_instances ||
    values.maximum_instances !== configuration.maximum_instances ||
    values.session_capacity_per_instance !==
      configuration.session_capacity_per_instance ||
    values.scale_down_cooldown_seconds !==
      configuration.scale_down_cooldown_seconds ||
    values.max_queued_attempts !== configuration.max_queued_attempts

  const updateNumber = (
    name: keyof FleetConfigurationUpdate,
    value: number
  ) => {
    setValues((current) => ({
      ...current,
      [name]: Number.isNaN(value) ? 0 : value,
    }))
  }

  const submit = (event: FormEvent) => {
    event.preventDefault()
    const minimum = values.minimum_instances ?? 0
    const maximum = values.maximum_instances ?? 0
    if (minimum > maximum) {
      toast.error("Minimum instances cannot exceed maximum instances")
      return
    }
    mutation.mutate(values)
  }

  return (
    <form onSubmit={submit} className="rounded-lg border bg-card">
      <div className="flex items-start justify-between gap-4 border-b px-5 py-4">
        <div>
          <h2 className="font-semibold">Scaling configuration</h2>
          <p className="mt-1 text-sm text-muted-foreground">
            Version {configuration.configuration_version}
          </p>
        </div>
        <Settings2 className="size-4 text-muted-foreground" aria-hidden />
      </div>
      <div className="p-5">
        <label className="flex items-center justify-between gap-4 rounded-lg border bg-muted/20 p-4">
          <span>
            <span className="block text-sm font-medium">Fleet enabled</span>
            <span className="mt-1 block text-xs leading-5 text-muted-foreground">
              Allow this provider fleet to contribute managed capacity.
            </span>
          </span>
          <Switch
            checked={values.enabled ?? false}
            onCheckedChange={(checked) =>
              setValues((current) => ({
                ...current,
                enabled: checked,
              }))
            }
          />
        </label>

        <div className="mt-5 grid gap-5 sm:grid-cols-2">
          <NumberField
            label="Minimum instances"
            name="minimum_instances"
            value={values.minimum_instances ?? 0}
            min={0}
            max={100}
            help="Warm instances Harbor keeps available."
            onChange={updateNumber}
          />
          <NumberField
            label="Maximum instances"
            name="maximum_instances"
            value={values.maximum_instances ?? 0}
            min={0}
            max={100}
            help="Hard administrative scaling limit."
            onChange={updateNumber}
          />
          <NumberField
            label="Sessions per instance"
            name="session_capacity_per_instance"
            value={values.session_capacity_per_instance ?? 1}
            min={1}
            help="Concurrent sessions configured on each Browserless worker."
            onChange={updateNumber}
          />
          <NumberField
            label="Scale-down cooldown"
            name="scale_down_cooldown_seconds"
            value={values.scale_down_cooldown_seconds ?? 1}
            min={1}
            help="Idle seconds before Harbor reduces capacity."
            onChange={updateNumber}
          />
          <NumberField
            label="Maximum queued attempts"
            name="max_queued_attempts"
            value={values.max_queued_attempts ?? 0}
            min={0}
            help="Attempts Harbor may queue while all Browserless slots are occupied."
            onChange={updateNumber}
          />
        </div>

        <div className="mt-6 flex items-center justify-between border-t pt-5">
          <p className="text-xs text-muted-foreground">
            Changes are versioned and audited by Harbor.
          </p>
          <Button
            type="submit"
            disabled={!dirty || mutation.isPending}
            className="gap-2"
          >
            {mutation.isPending ? (
              <LoaderCircle className="size-4 animate-spin" aria-hidden />
            ) : (
              <Save className="size-4" aria-hidden />
            )}
            Save changes
          </Button>
        </div>
      </div>
    </form>
  )
}

function FleetDetail({
  row,
  navigate,
}: {
  row: FleetRow
  navigate: (href: string) => void
}) {
  const { configuration, snapshot } = row
  const status = fleetStatus(row)

  return (
    <>
      <Button
        variant="ghost"
        size="sm"
        type="button"
        onClick={() => navigate("/fleets")}
        className="mb-5 -ml-3 gap-2 text-muted-foreground hover:text-foreground"
      >
        <ArrowLeft className="size-4" aria-hidden />
        All fleets
      </Button>

      <header className="flex flex-col gap-4 border-b pb-4 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <div className="mb-3 flex items-center gap-3">
            <StatusBadge row={row} />
            <span className="text-xs text-muted-foreground">
              {status.detail}
            </span>
          </div>
          <h1 className="text-3xl font-semibold tracking-tight sm:text-4xl">
            {providerLabels[configuration.provider]}
          </h1>
          <p className="mt-2 text-sm leading-6 text-muted-foreground">
            Managed browser capacity, demand, and scaling policy.
          </p>
        </div>
        <div className="text-left sm:text-right">
          <p className="text-xs font-medium tracking-wide text-muted-foreground uppercase">
            Controller
          </p>
          <p className="mt-1 text-sm font-medium">
            {configuration.controller_status ?? "No status reported"}
          </p>
        </div>
      </header>

      <section className="mt-5 grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <MetricCard
          label="Active attempts"
          value={snapshot?.active_attempts ?? 0}
          detail={`${snapshot?.queued_attempts ?? 0} currently queued`}
          icon={Activity}
        />
        <MetricCard
          label="Available slots"
          value={snapshot?.available_slots ?? 0}
          detail={`${snapshot?.total_slots ?? 0} total provider slots`}
          icon={Boxes}
        />
        <MetricCard
          label="Ready instances"
          value={snapshot?.ready_instances ?? 0}
          detail={`${configuration.desired_instances} desired by policy`}
          icon={Server}
        />
        <MetricCard
          label="Oldest queued"
          value={Math.round(snapshot?.oldest_queued_attempt_seconds ?? 0)}
          detail="Seconds waiting for provider capacity"
          icon={Clock3}
        />
      </section>

      <div className="mt-5 grid gap-4 xl:grid-cols-[minmax(0,1.15fr)_minmax(20rem,0.85fr)]">
        <div className="space-y-6">
          <section className="rounded-lg border bg-card">
            <div className="border-b px-4 py-3">
              <h2 className="font-semibold">Capacity</h2>
              <p className="mt-1 text-sm text-muted-foreground">
                Current usable capacity comes only from ready, healthy
                instances.
              </p>
            </div>
            <div className="p-4">
              <CapacityBar
                available={snapshot?.available_slots ?? 0}
                total={snapshot?.total_slots ?? 0}
              />
              <dl className="mt-4 grid grid-cols-2 gap-x-4 gap-y-3 border-t pt-4 sm:grid-cols-4">
                {[
                  ["Observed", snapshot?.observed_instances ?? 0],
                  ["Ready", snapshot?.ready_instances ?? 0],
                  ["Draining", snapshot?.draining_instances ?? 0],
                  ["Unhealthy", snapshot?.unhealthy_instances ?? 0],
                ].map(([label, value]) => (
                  <div key={label}>
                    <dt className="text-xs text-muted-foreground">{label}</dt>
                    <dd className="mt-1 text-lg font-semibold">{value}</dd>
                  </div>
                ))}
              </dl>
            </div>
          </section>

          <section className="rounded-lg border border-dashed bg-muted/15 p-4">
            <div className="flex items-start gap-4">
              <span className="flex size-10 shrink-0 items-center justify-center rounded-lg border bg-background">
                <Server className="size-4 text-muted-foreground" aria-hidden />
              </span>
              <div>
                <h2 className="font-semibold">Instance inventory</h2>
                <p className="mt-2 max-w-xl text-sm leading-6 text-muted-foreground">
                  Harbor reports aggregate instance state today. Individual
                  instance IDs, observation freshness, and slot assignments will
                  appear here when the instance read API is available.
                </p>
              </div>
            </div>
          </section>
        </div>

        <ConfigurationForm
          key={configuration.configuration_version}
          configuration={configuration}
        />
      </div>
    </>
  )
}

function ExternalCapacityForm({
  capacity,
}: {
  capacity: ExternalProviderCapacity
}) {
  const queryClient = useQueryClient()
  const [values, setValues] = useState<ExternalProviderCapacityUpdate>({
    enabled: capacity.enabled,
    max_active_sessions: capacity.max_active_sessions,
    max_queued_attempts: capacity.max_queued_attempts,
  })
  const mutation = useMutation({
    mutationFn: (update: ExternalProviderCapacityUpdate) =>
      updateExternalCapacity(capacity.provider, update),
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({
          queryKey: ["external-provider-capacities"],
        }),
        queryClient.invalidateQueries({ queryKey: ["fleet-snapshots"] }),
      ])
      toast.success(
        `${admissionProviderLabels[capacity.provider]} capacity updated`
      )
    },
    onError: (error) => toast.error(extractApiError(error)),
  })
  const dirty =
    values.enabled !== capacity.enabled ||
    values.max_active_sessions !== capacity.max_active_sessions ||
    values.max_queued_attempts !== capacity.max_queued_attempts

  const updateNumber = (
    name: "max_active_sessions" | "max_queued_attempts",
    value: number
  ) => {
    setValues((current) => ({
      ...current,
      [name]: Number.isNaN(value) ? 0 : value,
    }))
  }

  return (
    <form
      onSubmit={(event) => {
        event.preventDefault()
        mutation.mutate(values)
      }}
      className="rounded-lg border bg-card"
    >
      <div className="flex items-start justify-between gap-4 border-b px-5 py-4">
        <div>
          <h2 className="font-semibold">Admission limits</h2>
          <p className="mt-1 text-sm text-muted-foreground">
            Version {capacity.configuration_version}
          </p>
        </div>
        <Settings2 className="size-4 text-muted-foreground" aria-hidden />
      </div>
      <div className="p-5">
        <label className="flex items-center justify-between gap-4 rounded-lg border bg-muted/20 p-4">
          <span>
            <span className="block text-sm font-medium">
              {admissionProviderLabels[capacity.provider]} enabled
            </span>
            <span className="mt-1 block text-xs leading-5 text-muted-foreground">
              Allow automatic and explicit sessions to consume this capacity.
            </span>
          </span>
          <Switch
            checked={values.enabled ?? false}
            onCheckedChange={(enabled) =>
              setValues((current) => ({ ...current, enabled }))
            }
          />
        </label>
        <div className="mt-5 grid gap-5 sm:grid-cols-2">
          <label className="block">
            <span className="text-sm font-medium">
              Maximum concurrent sessions
            </span>
            <Input
              type="number"
              min={0}
              value={values.max_active_sessions ?? 0}
              onChange={(event) =>
                updateNumber(
                  "max_active_sessions",
                  event.target.valueAsNumber
                )
              }
              className="mt-2 h-10 bg-background"
            />
            <span className="mt-1.5 block text-xs leading-5 text-muted-foreground">
              Hard Harbor admission ceiling for this provider.
            </span>
          </label>
          <label className="block">
            <span className="text-sm font-medium">
              Maximum queued attempts
            </span>
            <Input
              type="number"
              min={0}
              value={values.max_queued_attempts ?? 0}
              onChange={(event) =>
                updateNumber(
                  "max_queued_attempts",
                  event.target.valueAsNumber
                )
              }
              className="mt-2 h-10 bg-background"
            />
            <span className="mt-1.5 block text-xs leading-5 text-muted-foreground">
              Requests beyond this waiting limit fail admission immediately.
            </span>
          </label>
        </div>
        <div className="mt-6 flex items-center justify-between border-t pt-5">
          <p className="text-xs text-muted-foreground">
            {capacity.provider === "browserbase"
              ? "Harbor controls admission only; Browserbase controls instances."
              : "Harbor enforces this limit before starting direct HTTP work."}
          </p>
          <Button
            type="submit"
            disabled={!dirty || mutation.isPending}
            className="gap-2"
          >
            {mutation.isPending ? (
              <LoaderCircle className="size-4 animate-spin" aria-hidden />
            ) : (
              <Save className="size-4" aria-hidden />
            )}
            Save limits
          </Button>
        </div>
      </div>
    </form>
  )
}

function ExternalCapacityDetail({
  capacity,
  snapshot,
  navigate,
}: {
  capacity: ExternalProviderCapacity
  snapshot?: ProviderFleetSnapshot
  navigate: (href: string) => void
}) {
  const available = Math.max(
    0,
    capacity.max_active_sessions - (snapshot?.active_attempts ?? 0)
  )
  return (
    <>
      <Button
        variant="ghost"
        size="sm"
        type="button"
        onClick={() => navigate("/fleets")}
        className="mb-5 -ml-3 gap-2 text-muted-foreground hover:text-foreground"
      >
        <ArrowLeft className="size-4" aria-hidden />
        All capacity
      </Button>
      <header className="border-b pb-4">
        <div className="mb-3 flex items-center gap-2">
          <span
            className={cn(
              "size-2 rounded-full",
              capacity.enabled ? "bg-emerald-500" : "bg-muted-foreground"
            )}
          />
          <span className="text-xs font-medium text-muted-foreground">
            {capacity.enabled ? "Admission enabled" : "Admission disabled"}
          </span>
        </div>
        <h1 className="text-3xl font-semibold tracking-tight sm:text-4xl">
          {admissionProviderLabels[capacity.provider]}
        </h1>
        <p className="mt-2 text-sm leading-6 text-muted-foreground">
          {capacity.provider === "browserbase"
            ? "External browser concurrency and Harbor admission limits. There are no Harbor-managed instances for this provider."
            : "Direct HTTP concurrency and queue limits enforced by Harbor admission."}
        </p>
      </header>
      <section className="mt-5 grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <MetricCard
          label="Active sessions"
          value={snapshot?.active_attempts ?? 0}
          detail={`${capacity.max_active_sessions} maximum concurrent`}
          icon={Activity}
        />
        <MetricCard
          label="Available admission"
          value={available}
          detail="Concurrent sessions remaining"
          icon={Cloud}
        />
        <MetricCard
          label="Queued attempts"
          value={snapshot?.queued_attempts ?? 0}
          detail={`${capacity.max_queued_attempts} maximum queued`}
          icon={Clock3}
        />
        <MetricCard
          label="Configured limit"
          value={capacity.max_active_sessions}
          detail={
            capacity.provider === "browserbase"
              ? "Subscription or cost ceiling"
              : "Direct request ceiling"
          }
          icon={Settings2}
        />
      </section>
      <div className="mt-5 max-w-3xl">
        <ExternalCapacityForm
          key={capacity.configuration_version}
          capacity={capacity}
        />
      </div>
    </>
  )
}

export function FleetsPage({ provider, navigate }: FleetPageProps) {
  const snapshots = useQuery({
    queryKey: ["fleet-snapshots"],
    queryFn: fetchFleetSnapshots,
    refetchInterval: 5_000,
  })
  const configurations = useQuery({
    queryKey: ["fleet-configurations"],
    queryFn: fetchFleetConfigurations,
  })
  const externalCapacities = useQuery({
    queryKey: ["external-provider-capacities"],
    queryFn: fetchExternalCapacities,
  })

  const rows = useMemo(() => {
    const snapshotsByProvider = new Map(
      (snapshots.data ?? []).map((snapshot) => [snapshot.provider, snapshot])
    )
    return (configurations.data ?? []).map((configuration) => ({
      configuration,
      snapshot: snapshotsByProvider.get(configuration.provider),
    }))
  }, [configurations.data, snapshots.data])

  const selected = rows.find((row) => row.configuration.provider === provider)
  const admissionCapacities = externalCapacities.data ?? []
  const selectedExternal = admissionCapacities.find(
    (capacity) => capacity.provider === provider
  )
  const selectedExternalSnapshot = snapshots.data?.find(
    (snapshot) => snapshot.provider === selectedExternal?.provider
  )
  const invalidProvider =
    provider !== undefined &&
    configurations.isSuccess &&
    externalCapacities.isSuccess &&
    selected === undefined &&
    selectedExternal === undefined
  const loading =
    snapshots.isPending ||
    configurations.isPending ||
    externalCapacities.isPending
  const error =
    snapshots.error ?? configurations.error ?? externalCapacities.error
  const retry = () => {
    void snapshots.refetch()
    void configurations.refetch()
    void externalCapacities.refetch()
  }

  return (
    <main className="mx-auto min-h-[calc(100svh-4rem)] w-full max-w-[100rem] px-4 py-6 sm:px-6 md:min-h-svh lg:px-8 lg:py-8">
      {!provider && (
        <header className="mb-5 flex flex-col gap-4 border-b pb-4 sm:flex-row sm:items-end sm:justify-between">
          <div>
            <div className="mb-3 flex items-center gap-2 text-xs font-medium text-muted-foreground">
              <span className="size-2 rounded-full bg-emerald-500" />
              Live capacity
            </div>
            <h1 className="text-3xl font-semibold tracking-tight sm:text-4xl">
              Fleets
            </h1>
            <p className="mt-2 max-w-2xl text-sm leading-6 text-muted-foreground">
              Monitor provider demand, usable browser capacity, and Harbor’s
              managed scaling limits.
            </p>
          </div>
          <Button
            variant="outline"
            className="gap-2 self-start sm:self-auto"
            onClick={retry}
            disabled={snapshots.isFetching || configurations.isFetching}
          >
            <RefreshCw
              className={cn(
                "size-4",
                (snapshots.isFetching || configurations.isFetching) &&
                  "animate-spin"
              )}
              aria-hidden
            />
            Refresh
          </Button>
        </header>
      )}

      {loading && <LoadingState />}
      {!loading && error && <ErrorState error={error} retry={retry} />}
      {!loading && !error && !provider && (
        <FleetList
          rows={rows}
          admissionCapacities={admissionCapacities}
          snapshots={snapshots.data ?? []}
          navigate={navigate}
        />
      )}
      {!loading && !error && selected && (
        <FleetDetail row={selected} navigate={navigate} />
      )}
      {!loading && !error && selectedExternal && (
        <ExternalCapacityDetail
          capacity={selectedExternal}
          snapshot={selectedExternalSnapshot}
          navigate={navigate}
        />
      )}
      {!loading && !error && invalidProvider && (
        <div className="flex min-h-96 items-center justify-center rounded-lg border bg-card px-6 text-center">
          <div>
            <CircleAlert
              className="mx-auto size-6 text-muted-foreground"
              aria-hidden
            />
            <h1 className="mt-4 text-xl font-semibold">Fleet not found</h1>
            <p className="mt-2 text-sm text-muted-foreground">
              This is not a managed Harbor provider.
            </p>
            <Button
              variant="outline"
              className="mt-5 gap-2"
              onClick={() => navigate("/fleets")}
            >
              <ArrowLeft className="size-4" aria-hidden />
              All fleets
            </Button>
          </div>
        </div>
      )}
    </main>
  )
}
