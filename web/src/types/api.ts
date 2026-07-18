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
  actual_cost_units: number
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
  state: string
  selection_reason: string | null
  transition_trigger: string | null
  plan_version: number | null
  plan_position: number | null
  estimated_cost_units: number | null
  actual_cost_units: number | null
  resolved_setting_keys: string[]
  setting_sources: Record<string, unknown>
  created_at: string
  queued_at: string | null
  acquiring_at: string | null
  active_at: string | null
  finished_at: string | null
  terminal_reason: string | null
}

export type SessionDetail = SessionListItem & {
  requested_setting_keys: string[]
  admitted_at: string | null
  opened_at: string | null
  closing_at: string | null
  lease_expires_at: string | null
  command_count: number
  attempts: SessionAttempt[]
}

export type ManagedProvider =
  "chromium" | "browserless" | "lightpanda" | "camoufox"

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
  required_health_confirmations: number
  health_policy_version: number
  configuration_version: number
}

export type RoutingConfigurationUpdate = {
  default_provider?: ActivityProvider
  existing_domain_probe_rate_basis_points?: number
  required_health_confirmations?: number
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
  "unknown" | "checking" | "healthy" | "unhealthy" | "inconclusive"

export type DomainRuntimeState = "eligible" | "suppressed"

export type DomainPlanCandidate = {
  provider: ActivityProvider
  estimated_cost_units: number
}

export type DomainPlan = {
  reason:
    | "cheapest_eligible"
    | "configured_default_bootstrap"
    | "no_eligible_provider"
  candidates: DomainPlanCandidate[]
}

export type DomainSummary = {
  known_domains: number
  healthy_domains: number
  checking_domains: number
  unhealthy_domains: number
  suppressed_domains: number
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
  suppressed_provider_count: number
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
  runtime_state: DomainRuntimeState
  routing_eligible: boolean
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
  runtime: {
    state: DomainRuntimeState
    suppressed_at: string | null
    suppressed_session_id: string | null
    incompatible_method: string | null
    restored_at: string | null
    restored_session_id: string | null
    last_evidence_at: string | null
  }
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
  actual_cost_units: number
  created_at: string
  closed_at: string | null
  terminal_reason: string | null
}

export type DomainSessionPage = {
  sessions: DomainSession[]
  next_cursor: string | null
}
