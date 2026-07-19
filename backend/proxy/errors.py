class HarborError(Exception):
    """Base class for errors Harbor can render without leaking internals."""


class ConnectionRejected(HarborError):
    close_code = 1011
    reason = "harbor_internal_error"
    status_code = 500


class InvalidHarborSettings(ConnectionRejected):
    close_code = 4400
    reason = "invalid_harbor_settings"
    status_code = 400


class ProviderQueueTimeout(ConnectionRejected):
    close_code = 4408
    reason = "provider_queue_timeout"
    status_code = 503


class GatewayCapacityFull(ConnectionRejected):
    close_code = 1013
    reason = "gateway_capacity_full"
    status_code = 503


class ProviderQueueFull(ConnectionRejected):
    close_code = 4429
    reason = "provider_queue_full"
    status_code = 503


class ProviderUnavailable(ConnectionRejected):
    close_code = 4503
    reason = "provider_unavailable"
    status_code = 503


class ProviderAcquisitionTimeout(ConnectionRejected):
    close_code = 4504
    reason = "provider_acquisition_timeout"
    status_code = 504


class InvalidCdpMessage(ConnectionRejected):
    close_code = 4400
    reason = "invalid_cdp_message"


class SessionLeaseLost(ConnectionRejected):
    close_code = 1011
    reason = "session_lease_lost"


class ProviderConnectionLost(ConnectionRejected):
    close_code = 1011
    reason = "provider_connection_lost"


class ProviderTimeout(ConnectionRejected):
    close_code = 4508
    reason = "provider_timeout"
    status_code = 504


class DomainBlockingUnavailable(ConnectionRejected):
    close_code = 4510
    reason = "domain_blocking_unavailable"
    status_code = 503
