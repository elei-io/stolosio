import { apiRequest } from "@/lib/api"
import type {
  FleetConfiguration,
  FleetConfigurationUpdate,
  ExternalProviderCapacity,
  ExternalProviderCapacityUpdate,
  ManagedProvider,
  ProviderFleetSnapshot,
} from "@/types/api"

export const providerLabels: Record<ManagedProvider, string> = {
  browserless: "Browserless",
}

export const admissionProviderLabels: Record<
  ExternalProviderCapacity["provider"],
  string
> = {
  browserless_cloud: "Browserless cloud",
}

export function fetchFleetSnapshots() {
  return apiRequest<ProviderFleetSnapshot[]>("/v1/fleet/providers")
}

export function fetchFleetConfigurations() {
  return apiRequest<FleetConfiguration[]>("/v1/admin/fleets")
}

export function fetchExternalCapacities() {
  return apiRequest<ExternalProviderCapacity[]>("/v1/admin/providers/capacity")
}

export function updateExternalCapacity(
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

export function updateFleetConfiguration(
  provider: ManagedProvider,
  update: FleetConfigurationUpdate
) {
  return apiRequest<FleetConfiguration>(`/v1/admin/fleets/${provider}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(update),
  })
}
