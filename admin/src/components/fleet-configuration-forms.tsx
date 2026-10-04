import { useMutation, useQueryClient } from "@tanstack/react-query"
import { LoaderCircle, Save, Settings2 } from "lucide-react"
import { type FormEvent, useState } from "react"
import { toast } from "sonner"

import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Switch } from "@/components/ui/switch"
import { extractApiError } from "@/lib/api"
import type {
  FleetConfiguration,
  FleetConfigurationUpdate,
  ExternalProviderCapacity,
  ExternalProviderCapacityUpdate,
} from "@/types/api"

import {
  admissionProviderLabels,
  providerLabels,
  updateExternalCapacity,
  updateFleetConfiguration,
} from "@/lib/fleet-api"

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

export function ConfigurationForm({
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
            help="Warm instances Stolosio keeps available."
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
            help="Idle seconds before Stolosio reduces capacity."
            onChange={updateNumber}
          />
          <NumberField
            label="Maximum queued attempts"
            name="max_queued_attempts"
            value={values.max_queued_attempts ?? 0}
            min={0}
            help="Attempts Stolosio may queue while all Browserless slots are occupied."
            onChange={updateNumber}
          />
        </div>

        <div className="mt-6 flex items-center justify-between border-t pt-5">
          <p className="text-xs text-muted-foreground">
            Changes are versioned and audited by Stolosio.
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

export function ExternalCapacityForm({
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
                updateNumber("max_active_sessions", event.target.valueAsNumber)
              }
              className="mt-2 h-10 bg-background"
            />
            <span className="mt-1.5 block text-xs leading-5 text-muted-foreground">
              Hard Stolosio admission ceiling for this provider.
            </span>
          </label>
          <label className="block">
            <span className="text-sm font-medium">Maximum queued attempts</span>
            <Input
              type="number"
              min={0}
              value={values.max_queued_attempts ?? 0}
              onChange={(event) =>
                updateNumber("max_queued_attempts", event.target.valueAsNumber)
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
            {`Stolosio controls admission only; ${admissionProviderLabels[capacity.provider]} controls instances.`}
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
