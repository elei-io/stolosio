from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram

CONTROLLER_REGISTRY = CollectorRegistry(auto_describe=True)
SCALING_ACTIONS = Counter(
    "harbor_provider_scaling_actions",
    "Fleet scaling actions by direction and stable outcome.",
    ("provider", "direction", "outcome"),
    registry=CONTROLLER_REGISTRY,
)
RECONCILE_ERRORS = Counter(
    "harbor_provider_reconcile_errors",
    "Fleet reconciliation errors by stable reason.",
    ("provider", "reason"),
    registry=CONTROLLER_REGISTRY,
)
LAST_SUCCESSFUL_RECONCILE = Gauge(
    "harbor_provider_last_successful_reconcile_timestamp",
    "Unix timestamp of the last successful fleet reconciliation.",
    ("provider",),
    registry=CONTROLLER_REGISTRY,
)
RECONCILE_DURATION = Histogram(
    "harbor_provider_reconcile_duration_seconds",
    "Fleet reconciliation duration by provider.",
    ("provider",),
    registry=CONTROLLER_REGISTRY,
)
SCALE_REQUEST_TO_READY = Histogram(
    "harbor_provider_scale_request_to_ready_seconds",
    "Time from a successful scale request until a new instance is ready.",
    ("provider",),
    registry=CONTROLLER_REGISTRY,
)
SCALE_REQUEST_TO_FIRST_ASSIGNMENT = Histogram(
    "harbor_provider_scale_request_to_first_assignment_seconds",
    "Time from a successful scale request until a new instance first serves traffic.",
    ("provider",),
    registry=CONTROLLER_REGISTRY,
)
SCALE_REQUEST_UNASSIGNED = Counter(
    "harbor_provider_scale_request_unassigned_total",
    "Requested instances removed before serving any acquisition attempt.",
    ("provider",),
    registry=CONTROLLER_REGISTRY,
)
