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

export type CommandCostStat = {
  provider: ActivityProvider
  method: string
  command_count: number
  failed_count: number
  interrupted_count: number
  total_duration_ms: number
  total_provider_latency_ms: number
  total_harbor_queue_ms: number
  attributed_browser_time_ms: number
  attributed_cost_units: number
  first_seen_at: string
  last_seen_at: string
}

export type CostWindow = "24h" | "7d" | "30d" | "90d"

export type CostTotals = {
  session_count: number
  attempt_count: number
  failed_attempt_count: number
  modeled_cost_units: number
  chargeable_time_ms: number
  browser_connected_time_ms: number
  browserless_slot_time_ms: number
  browserbase_billable_time_ms: number
}

export type ProviderCostSummary = {
  provider: ActivityProvider
  attempt_count: number
  session_count: number
  failed_attempt_count: number
  modeled_cost_units: number
  chargeable_time_ms: number
  browser_connected_time_ms: number
  capacity_occupied_time_ms: number
  estimated_billable_time_ms: number
}

export type CostBucket = {
  started_at: string
  provider: ActivityProvider
  attempt_count: number
  modeled_cost_units: number
  chargeable_time_ms: number
}

export type CostSession = {
  session_id: string
  client_reference: string | null
  closed_at: string | null
  providers: ActivityProvider[]
  modeled_cost_units: number
  chargeable_time_ms: number
}

export type CostOverview = {
  window: CostWindow
  starts_at: string
  ends_at: string
  finalized_through: string | null
  totals: CostTotals
  providers: ProviderCostSummary[]
  buckets: CostBucket[]
  recent_sessions: CostSession[]
}

export type ActivityProvider =
  | "http"
  | "browserless"
  | "browserbase"

export type HarborSessionState =
  | "requested"
  | "admitted"
  | "open"
  | "closing"
  | "closed"
  | "failed"

export type SessionDomainSummary = {
  id: number
  hostname: string
}

export type SessionListItem = {
  id: string
  client_reference: string | null
  state: HarborSessionState
  created_at: string
  closed_at: string | null
  duration_seconds: number | null
  terminal_reason: string | null
  providers: ActivityProvider[]
  selection_mode: "automatic" | "explicit"
  selection_reason: string | null
  transition_triggers: string[]
  modeled_cost_units: number
  total_browser_time_ms: number
  total_capacity_occupied_ms: number
  estimated_billable_ms: number
  domains: SessionDomainSummary[]
}

export type SessionPage = {
  sessions: SessionListItem[]
  next_cursor: string | null
}

export type SessionAttempt = {
  id: string
  ordinal: number
  provider: ActivityProvider
  provider_instance_id: string | null
  provider_session_id: string | null
  state: string
  selection_reason: string | null
  transition_trigger: string | null
  plan_version: number | null
  plan_position: number | null
  estimated_cost_units: number | null
  modeled_cost_units: number | null
  chargeable_time_ms: number | null
  cost_basis: string | null
  cost_rate_units_per_second: number | null
  resolved_setting_keys: string[]
  setting_sources: Record<string, unknown>
  created_at: string
  queued_at: string | null
  acquiring_at: string | null
  active_at: string | null
  finished_at: string | null
  provider_started_at: string | null
  provider_ended_at: string | null
  capacity_occupied_ms: number | null
  browser_connected_ms: number | null
  provider_reported_ms: number | null
  estimated_billable_ms: number | null
  terminal_reason: string | null
}

export type SessionDetail = SessionListItem & {
  requested_setting_keys: string[]
  admitted_at: string | null
  opened_at: string | null
  closing_at: string | null
  lease_expires_at: string | null
  attempts: SessionAttempt[]
}

export type ManagedProvider = "browserless"

export type GatewayFleetSnapshot = {
  active_sessions: number
  capacity: number
}

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
  max_queued_attempts: number
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
  max_queued_attempts?: number
  enabled?: boolean
}

export type ExternalProviderCapacity = {
  provider: "http" | "browserbase"
  enabled: boolean
  max_active_sessions: number
  max_queued_attempts: number
  configuration_version: number
}

export type ExternalProviderCapacityUpdate = {
  enabled?: boolean
  max_active_sessions?: number
  max_queued_attempts?: number
}

export type RoutingConfiguration = {
  default_provider: ActivityProvider
  existing_domain_probe_rate_basis_points: number
  required_health_confirmations: number
  health_policy_version: number
  configuration_version: number
}

export type RoutingConfigurationUpdate = {
  default_provider?: ActivityProvider
  existing_domain_probe_rate_basis_points?: number
  required_health_confirmations?: number
}

export type NetworkPolicy = {
  blocked_domain_patterns: string[]
  configuration_version: number
}

export type NetworkPolicyUpdate = {
  blocked_domain_patterns: string[]
}

export type ProviderRoutingProfile = {
  provider: ActivityProvider
  automatic_enabled: boolean
  cost_units_per_second: number
  provider_contract_version: number
}

export type ProviderRoutingUpdate = {
  automatic_enabled?: boolean
  cost_units_per_second?: number
}

export type DomainHealthState =
  "unknown" | "healthy" | "unhealthy" | "inconclusive"

export type DomainPlanCandidate = {
  provider: ActivityProvider
  estimated_cost_units: number
}

export type DomainPlan = {
  reason:
    | "cheapest_eligible"
    | "adaptive_browser_required"
    | "adaptive_http_exploration"
    | "configured_default_bootstrap"
    | "local_correctness_fallback"
    | "no_eligible_provider"
  candidates: DomainPlanCandidate[]
  paid_fallback_available: boolean
}

export type DomainSummary = {
  known_domains: number
  healthy_domains: number
  checking_domains: number
  unhealthy_domains: number
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
  health_counts: {
    unknown: number
    checking: number
    healthy: number
    unhealthy: number
    inconclusive: number
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
  health_state: DomainHealthState
  routing_eligible: boolean
  paid_fallback_available: boolean
  successful_probe_count: number
  failed_probe_count: number
  inconclusive_probe_count: number
  observed_attempt_count: number
  total_cost_units: number
  average_cost_units: number
  cost_is_estimate: boolean
  last_status_code: number | null
  last_checked_at: string | null
  last_healthy_at: string | null
  failure_reason_code: string | null
  health_policy_version: number | null
  provider_contract_version: number
  checks: {
    navigation: DomainHealthCheck
    status: DomainHealthCheck
    headers: DomainHealthCheck
    content: DomainHealthCheck
  }
}

export type DomainCheckState =
  "healthy" | "unhealthy" | "inconclusive" | "checking" | "not_checked"

export type DomainHealthCheck = {
  state: DomainCheckState
  checked_at: string | null
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
  routing_preference: {
    preferred_provider: "http" | "browserless"
    preference_score: number
    browser_required_count: number
    http_sufficient_count: number
    browser_compatible_count: number
    last_evidence_at: string
  } | null
  expected_plan: DomainPlan
  providers: DomainProviderEvidence[]
}

export type DomainProbe = {
  id: string
  cohort_id: string
  source_session_id: string
  candidate_provider: ActivityProvider
  trigger: "new_domain" | "existing_sample" | "manual"
  state: "queued" | "running" | "completed" | "failed"
  outcome: "healthy" | "unhealthy" | "inconclusive" | null
  navigation_state: string | null
  status_state: string | null
  headers_state: string | null
  content_state: string | null
  status_code: number | null
  reason_codes: string[]
  content_facts: Record<string, number | boolean>
  comparison_state:
    | "pending"
    | "not_applicable"
    | "inconclusive"
    | "comparable"
    | "materially_incomplete"
  cost_units: number | null
  created_at: string
  finished_at: string | null
}

export type DomainProbePage = {
  probes: DomainProbe[]
  next_cursor: string | null
}

export type TriggerDomainProbesResponse = {
  scheduled: { id: string; provider: ActivityProvider }[]
  already_active: { id: string; provider: ActivityProvider }[]
}

export type DomainSession = {
  id: string
  client_reference: string | null
  state: string
  selection_mode: "automatic" | "explicit"
  providers: ActivityProvider[]
  selection_reason: string | null
  transition_triggers: string[]
  modeled_cost_units: number
  created_at: string
  closed_at: string | null
  terminal_reason: string | null
}

export type DomainSessionPage = {
  sessions: DomainSession[]
  next_cursor: string | null
}
