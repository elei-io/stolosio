from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram

REGISTRY = CollectorRegistry(auto_describe=True)

GATEWAY_ACTIVE = Gauge(
    "harbor_gateway_active_sessions",
    "Unexpired logical sessions consuming Harbor intake capacity.",
    registry=REGISTRY,
)
GATEWAY_CAPACITY = Gauge(
    "harbor_gateway_capacity",
    "Configured logical session intake capacity.",
    registry=REGISTRY,
)

PROVIDER_ACTIVE = Gauge(
    "harbor_provider_active_attempts",
    "Unexpired acquisition attempts holding provider capacity.",
    ("provider",),
    registry=REGISTRY,
)
PROVIDER_QUEUED = Gauge(
    "harbor_provider_queued_attempts",
    "Unexpired acquisition attempts waiting for provider capacity.",
    ("provider",),
    registry=REGISTRY,
)
PROVIDER_CAPACITY = Gauge(
    "harbor_provider_capacity",
    "Configured concurrent session capacity.",
    ("provider",),
    registry=REGISTRY,
)
PROVIDER_OLDEST_QUEUED = Gauge(
    "harbor_provider_oldest_queued_attempt_seconds",
    "Age of the oldest unexpired queued acquisition attempt.",
    ("provider",),
    registry=REGISTRY,
)
PROVIDER_DESIRED_INSTANCES = Gauge(
    "harbor_provider_desired_instances",
    "Browser instances requested by Harbor fleet policy.",
    ("provider",),
    registry=REGISTRY,
)
PROVIDER_OBSERVED_INSTANCES = Gauge(
    "harbor_provider_observed_instances",
    "Browser instances currently observed by the fleet controller.",
    ("provider",),
    registry=REGISTRY,
)
PROVIDER_READY_INSTANCES = Gauge(
    "harbor_provider_ready_instances",
    "Healthy browser instances contributing provider capacity.",
    ("provider",),
    registry=REGISTRY,
)
PROVIDER_DRAINING_INSTANCES = Gauge(
    "harbor_provider_draining_instances",
    "Browser instances accepting no new acquisition attempts.",
    ("provider",),
    registry=REGISTRY,
)
PROVIDER_UNHEALTHY_INSTANCES = Gauge(
    "harbor_provider_unhealthy_instances",
    "Observed browser instances that are not usable.",
    ("provider",),
    registry=REGISTRY,
)
PROVIDER_TOTAL_SLOTS = Gauge(
    "harbor_provider_total_slots",
    "Session slots exposed by ready browser instances.",
    ("provider",),
    registry=REGISTRY,
)
PROVIDER_AVAILABLE_SLOTS = Gauge(
    "harbor_provider_available_slots",
    "Unassigned session slots on ready browser instances.",
    ("provider",),
    registry=REGISTRY,
)

SESSION_ACQUISITIONS = Counter(
    "harbor_session_acquisitions",
    "Provider acquisition attempts by terminal outcome.",
    ("provider", "outcome"),
    registry=REGISTRY,
)
SESSION_ACQUISITION_DURATION = Histogram(
    "harbor_session_acquisition_duration_seconds",
    "Time spent acquiring an upstream provider session.",
    ("provider",),
    registry=REGISTRY,
)
SESSIONS_COMPLETED = Counter(
    "harbor_sessions_completed",
    "Completed logical sessions by outcome.",
    ("outcome",),
    registry=REGISTRY,
)
PROVIDER_FAILURES = Counter(
    "harbor_provider_failures",
    "Provider failures grouped by stable Harbor reason.",
    ("provider", "reason"),
    registry=REGISTRY,
)
COMMANDS = Counter(
    "harbor_commands",
    "CDP command outcomes.",
    ("provider", "method", "outcome"),
    registry=REGISTRY,
)
COMMAND_DURATION = Histogram(
    "harbor_command_duration_seconds",
    "CDP command duration by outcome.",
    ("provider", "method", "outcome"),
    registry=REGISTRY,
)
NO_BROWSER_PROMOTIONS = Counter(
    "harbor_no_browser_promotions",
    "Live sessions promoted from the no-browser path by bounded trigger class.",
    ("from_provider", "to_provider", "trigger"),
    registry=REGISTRY,
)
EVENT_PUBLICATION_FAILURES = Counter(
    "harbor_event_publication_failures",
    "Normalized events that could not be acknowledged by JetStream.",
    ("event_type",),
    registry=REGISTRY,
)
EVENT_RECORDER_EVENTS = Counter(
    "harbor_event_recorder_events",
    "Recorder inputs by persistence outcome.",
    ("outcome",),
    registry=REGISTRY,
)
EVENT_RECORDER_LAG = Gauge(
    "harbor_event_recorder_lag_seconds",
    "Age of the newest event received by the recorder.",
    registry=REGISTRY,
)
EVENT_RECORDER_PENDING = Gauge(
    "harbor_event_recorder_pending",
    "Events pending on the recorder durable consumer.",
    registry=REGISTRY,
)
EVENT_DEAD_LETTERS = Counter(
    "harbor_event_dead_letters",
    "Events terminated into the sanitized dead-letter stream.",
    ("reason",),
    registry=REGISTRY,
)
RETENTION_DELETED_ROWS = Counter(
    "harbor_retention_deleted_rows",
    "Rows removed by bounded retention work.",
    ("table",),
    registry=REGISTRY,
)
RETENTION_DURATION = Histogram(
    "harbor_retention_duration_seconds",
    "Duration of a complete retention pass.",
    registry=REGISTRY,
)
