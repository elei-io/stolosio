export type ApiErrorResponse = {
  detail?: string
  message?: string
}

export type ActivityEvent = {
  event_id: string
  schema_version: number
  event_type: string
  event_family: string
  outcome: "success" | "failure" | "interrupted" | null
  session_id: string
  occurred_at: string
  provider: ActivityProvider | null
  attempt_id: string | null
  payload: Record<string, unknown>
}

export type ActivityEventPage = {
  events: ActivityEvent[]
  next_cursor: string | null
}

export type ActivityProvider =
  "http" | "chromium" | "browserless" | "lightpanda" | "camoufox"

export type ManagedProvider =
  "chromium" | "browserless" | "lightpanda" | "camoufox"

export type ProviderFleetSnapshot = {
  provider: ActivityProvider
  active_attempts: number
  queued_attempts: number
  capacity: number
  oldest_queued_attempt_seconds: number
  desired_instances: number
  observed_instances: number
  ready_instances: number
  draining_instances: number
  unhealthy_instances: number
  total_slots: number
  available_slots: number
}

export type FleetConfiguration = {
  provider: ManagedProvider
  minimum_instances: number
  maximum_instances: number
  session_capacity_per_instance: number
  scale_down_cooldown_seconds: number
  desired_instances: number
  configuration_version: number
  enabled: boolean
  controller_status: string | null
}

export type FleetConfigurationUpdate = {
  minimum_instances?: number
  maximum_instances?: number
  session_capacity_per_instance?: number
  scale_down_cooldown_seconds?: number
  enabled?: boolean
}

export type RoutingConfiguration = {
  default_provider: ActivityProvider
  existing_domain_probe_rate_basis_points: number
  required_support_confirmations: number
  support_policy_version: number
  configuration_version: number
}

export type RoutingConfigurationUpdate = {
  default_provider?: ActivityProvider
  existing_domain_probe_rate_basis_points?: number
  required_support_confirmations?: number
}

export type ProviderRoutingProfile = {
  provider: ActivityProvider
  automatic_enabled: boolean
  cost_units_per_second: number
  capability_manifest_version: number
}

export type ProviderRoutingUpdate = {
  automatic_enabled?: boolean
  cost_units_per_second?: number
}

export type DomainSupportState =
  | "unknown"
  | "checking"
  | "supported"
  | "unsupported"

export type DomainPlanCandidate = {
  provider: ActivityProvider
  estimated_cost_units: number
}

export type DomainPlan = {
  reason:
    | "cheapest_supported"
    | "configured_default"
    | "no_supported_provider"
  candidates: DomainPlanCandidate[]
}

export type DomainSummary = {
  known_domains: number
  supported_domains: number
  checking_domains: number
  unsupported_domains: number
  no_evidence_domains: number
  transitioned_domains: number
}

export type DomainListItem = {
  id: number
  hostname: string
  first_seen_at: string
  last_seen_at: string
  session_count: number
  eligible_acquisition_count: number
  transition_count: number
  active_probe_count: number
  support_counts: {
    unknown: number
    checking: number
    supported: number
    unsupported: number
  }
  expected_plan: DomainPlan
}

export type DomainPage = {
  domains: DomainListItem[]
  summary: DomainSummary
  next_cursor: string | null
}

export type DomainProviderEvidence = {
  provider: ActivityProvider
  automatic_enabled: boolean
  support_state: DomainSupportState
  successful_probe_count: number
  failed_probe_count: number
  inconclusive_probe_count: number
  observed_session_count: number
  total_cost_units: number
  average_cost_units: number
  cost_is_estimate: boolean
  last_status_code: number | null
  last_checked_at: string | null
  last_supported_at: string | null
  failure_reason_code: string | null
  support_policy_version: number | null
  capability_manifest_version: number
  checks: {
    navigation: DomainHealthCheck
    status: DomainHealthCheck
    headers: DomainHealthCheck
    method_coverage: DomainMethodCheck
    content: DomainHealthCheck
  }
}

export type DomainCheckState =
  | "healthy"
  | "unhealthy"
  | "inconclusive"
  | "checking"
  | "not_checked"
  | "declared"
  | "missing"

export type DomainHealthCheck = {
  state: DomainCheckState
  checked_at: string | null
}

export type DomainMethodCheck = DomainHealthCheck & {
  observed_count: number
  declared_count: number
  unsupported_methods: string[]
  manifest_version: number
}

export type DomainCommandStat = {
  method: string
  command_count: number
  session_count: number
  first_seen_at: string
  last_seen_at: string
}

export type DomainDetail = {
  id: number
  hostname: string
  first_seen_at: string
  last_seen_at: string
  session_count: number
  eligible_acquisition_count: number
  active_probe_count: number
  transition_count: number
  expected_plan: DomainPlan
  providers: DomainProviderEvidence[]
  commands: DomainCommandStat[]
}

export type DomainProbe = {
  id: string
  source_session_id: string
  candidate_provider: ActivityProvider
  trigger: "new_domain" | "existing_sample"
  state: "queued" | "running" | "completed" | "failed"
  outcome: "supported" | "unsupported" | "inconclusive" | null
  navigation_state: string | null
  status_state: string | null
  headers_state: string | null
  method_coverage_state: string | null
  content_state: string | null
  status_code: number | null
  reason_codes: string[]
  method_observed_count: number
  method_declared_count: number
  unsupported_methods: string[]
  content_facts: Record<string, number | boolean>
  cost_units: number | null
  created_at: string
  finished_at: string | null
}

export type DomainProbePage = {
  probes: DomainProbe[]
  next_cursor: string | null
}

export type DomainSession = {
  id: string
  client_reference: string | null
  state: string
  selection_mode: "automatic" | "explicit"
  providers: ActivityProvider[]
  selection_reason: string | null
  transition_triggers: string[]
  actual_cost_units: number
  created_at: string
  closed_at: string | null
  terminal_reason: string | null
}

export type DomainSessionPage = {
  sessions: DomainSession[]
  next_cursor: string | null
}
