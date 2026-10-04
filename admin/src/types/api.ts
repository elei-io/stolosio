import type { CapturePath, CaptureTier, CaptureDecisionCode, CaptureDecision } from "./contracts"

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
  total_stolosio_queue_ms: number
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
}

export type CostBucket = {
  started_at: string
  provider: ActivityProvider
  attempt_count: number
  modeled_cost_units: number
  chargeable_time_ms: number
}

export type CostSession = {
  workload: "automation" | "capture"
  capture_hostname: string | null
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

export type ActivityProvider = "browserless" | "browserless_cloud"

export type StolosioSessionState =
  "requested" | "admitted" | "open" | "closing" | "closed" | "failed"

export type SessionDomainSummary = {
  id: number
  hostname: string
}

export type SessionListItem = {
  workload: "automation" | "capture"
  capture_hostname: string | null
  capture: CaptureSummary | null
  capture_outcome:
    | "captured"
    | "failed"
    | "rejected"
    | "interrupted"
    | "unknown"
    | "in_progress"
    | null
  capture_path: CapturePath | null
  id: string
  client_reference: string | null
  state: StolosioSessionState
  created_at: string
  closed_at: string | null
  duration_seconds: number | null
  terminal_reason: string | null
  providers: ActivityProvider[]
  modeled_cost_units: number
  total_browser_time_ms: number
  total_capacity_occupied_ms: number
  domains: SessionDomainSummary[]
}

export type SessionPage = {
  sessions: SessionListItem[]
  next_cursor: string | null
}

export type AttemptPhaseSummary = {
  measurement_version: number
  observed_session_ms: number
  command_active_ms: number
  no_command_in_flight_ms: number
  pre_first_command_ms: number | null
  post_last_command_ms: number | null
  provider_bootstrap_ms: number
  provider_close_ms: number
}

export type SessionAttempt = {
  id: string
  ordinal: number
  provider: ActivityProvider
  provider_instance_id: string | null
  provider_session_id: string | null
  state: string
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
  phase_summary: AttemptPhaseSummary | null
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
  sessions_last_24h: number
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
  provider: "browserless_cloud"
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

export type NetworkPolicy = {
  blocked_domain_patterns: string[]
  configuration_version: number
}

export type NetworkPolicyUpdate = {
  blocked_domain_patterns: string[]
}

export type ProviderCostRate = {
  provider: ActivityProvider
  cost_units_per_second: number
  updated_at: string
}

export type CaptureAcquisitionOutcome = "default" | "internally_resolved" | "externally_resolved" | "total_failure"
export type CaptureStats = {
  total: number
  counts: Record<CaptureAcquisitionOutcome, number>
  rates: Record<CaptureAcquisitionOutcome, number | null>
  local_attempts: number
  external_attempts: number
  paid_captures: number
  local_seconds: number
  external_seconds: number
  mean_duration_ms: number | null
}
export type AcquisitionOverview = {
  window: string
  starts_at: string
  ends_at: string
  tracking_since: string | null
  all_captures: CaptureStats
  challenged_opt_in: CaptureStats
}

export type CaptureAttempt = {
  path: "http" | "browser"
  tier: CaptureTier
  status_code?: number
  duration_ms: number
  assessment?: string
  decision: CaptureDecision
  reason: CaptureDecisionCode
  http_coverage?: number
  http_sufficient?: boolean
}

export type CaptureSummary = {
  outcome: "captured" | "failed"
  failure_code?: string
  failure_category?: string
  representation?: "response_body" | "rendered_html"
  tiers: string[]
  duration_ms: number
  browser_seconds: number
  paid: boolean
  bytes: number
  attempts: CaptureAttempt[]
}

export type OverviewWindow = "24h" | "7d" | "30d"
export type OutcomeBucket = {
  at: string
  success: number
  failed: number
  other: number
}
export type WorkloadOverview = {
  active: number
  counts: Record<string, number>
  median_duration_ms: number | null
  p95_duration_ms: number | null
  capacity_ms: number
  browser_ms: number
  modeled_cost_units: number
  series: OutcomeBucket[]
  failures: { reason: string; count: number }[]
}
export type AutomationOverview = WorkloadOverview & {
  command_count: number
  failed_commands: number
  interrupted_commands: number
  mean_command_ms: number | null
}
export type CaptureOverview = WorkloadOverview & {
  paths: Record<string, number>
  browser_seconds: number
  paid: number
}
export type OperationsOverview = {
  window: OverviewWindow
  starts_at: string
  ends_at: string
  automation: AutomationOverview
  capture: CaptureOverview
}
