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
  required_successful_probes: number
  comparison_policy_version: number
  configuration_version: number
}

export type RoutingConfigurationUpdate = {
  default_provider?: ActivityProvider
  existing_domain_probe_rate_basis_points?: number
  required_successful_probes?: number
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

export type DomainQualificationState =
  | "unqualified"
  | "probing"
  | "qualified"
  | "rejected"

export type DomainRoute = {
  provider: ActivityProvider
  reason: "cheapest_qualified" | "unqualified_domain_default"
  estimated_cost_units: number
}

export type DomainSummary = {
  known_domains: number
  qualified_domains: number
  probing_domains: number
  default_only_domains: number
  promoted_domains: number
}

export type DomainListItem = {
  id: number
  hostname: string
  first_seen_at: string
  last_seen_at: string
  session_count: number
  eligible_acquisition_count: number
  promotion_count: number
  active_probe_count: number
  qualification_counts: {
    qualified: number
    probing: number
    rejected: number
  }
  current_route: DomainRoute
}

export type DomainPage = {
  domains: DomainListItem[]
  summary: DomainSummary
  next_cursor: string | null
}

export type DomainProviderEvidence = {
  provider: ActivityProvider
  automatic_enabled: boolean
  is_default: boolean
  qualification_state: DomainQualificationState
  successful_probe_count: number
  failed_probe_count: number
  observed_session_count: number
  total_cost_units: number
  average_cost_units: number
  cost_is_estimate: boolean
  last_status_code: number | null
  last_verified_at: string | null
  comparison_policy_version: number | null
  last_probe_at: string | null
  probe_state: "checking" | "completed" | "failed" | "not_checked"
  checks: {
    status: DomainComparisonCheck
    headers: DomainComparisonCheck
    console: DomainComparisonCheck
    methods: DomainMethodCheck
    content: DomainComparisonCheck
  }
}

export type DomainCheckState =
  | "matches"
  | "differs"
  | "checking"
  | "not_checked"

export type DomainComparisonCheck = {
  state: DomainCheckState
  checked_at: string | null
}

export type DomainMethodCheck = DomainComparisonCheck & {
  observed_count: number
  supported_count: number
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
  current_route: DomainRoute
  providers: DomainProviderEvidence[]
  promotion: {
    promotion_count: number
    last_trigger_method: string
    first_seen_at: string
    last_seen_at: string
  } | null
  commands: DomainCommandStat[]
}

export type DomainProbe = {
  id: string
  source_session_id: string
  candidate_provider: ActivityProvider
  trigger: "new_domain" | "existing_sample"
  state: "queued" | "running" | "completed" | "failed"
  comparison_outcome: "matched" | "mismatched" | "execution_failed" | null
  baseline_status: number
  candidate_status: number | null
  status_matches: boolean | null
  headers_match: boolean | null
  baseline_console_errors: number
  candidate_console_errors: number | null
  console_errors_acceptable: boolean | null
  content_matches: boolean | null
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
  routing_reason: string | null
  actual_cost_units: number
  created_at: string
  closed_at: string | null
  terminal_reason: string | null
}

export type DomainSessionPage = {
  sessions: DomainSession[]
  next_cursor: string | null
}
