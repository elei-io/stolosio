class HarborError(Exception):
    """Base class for errors Harbor can render without leaking internals."""


class ConnectionRejected(HarborError):
    close_code = 1011
    reason = "harbor_internal_error"


class InvalidHarborSettings(ConnectionRejected):
    close_code = 4400
    reason = "invalid_harbor_settings"


class SessionQueueTimeout(ConnectionRejected):
    close_code = 4408
    reason = "session_queue_timeout"


class ProviderQueueFull(ConnectionRejected):
    close_code = 4429
    reason = "provider_queue_full"


class ProviderUnavailable(ConnectionRejected):
    close_code = 4503
    reason = "provider_unavailable"


class ProviderAcquisitionTimeout(ConnectionRejected):
    close_code = 4504
    reason = "provider_acquisition_timeout"


class InvalidCdpMessage(ConnectionRejected):
    close_code = 4400
    reason = "invalid_cdp_message"


class SessionLeaseLost(ConnectionRejected):
    close_code = 1011
    reason = "session_lease_lost"


class ProviderConnectionLost(ConnectionRejected):
    close_code = 1011
    reason = "provider_connection_lost"
