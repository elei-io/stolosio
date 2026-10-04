"""Capture classifications shared by history and aggregate queries."""

from datetime import timedelta
from typing import Literal

from sqlalchemy import case

from backend.db.models import GatewaySession

CapturePath = Literal["http", "managed", "local_resolution", "challenge_resolution"]

WINDOWS = {"24h": timedelta(hours=24), "7d": timedelta(days=7), "30d": timedelta(days=30)}

REJECTION_REASONS = ("gateway_capacity_full", "provider_queue_full", "provider_queue_timeout")


def capture_outcome_expression():
    return case(
        (
            GatewaySession.capture_summary.is_not(None),
            GatewaySession.capture_summary["outcome"].as_string(),
        ),
        (GatewaySession.state.not_in(("closed", "failed")), "in_progress"),
        (GatewaySession.terminal_reason.in_(REJECTION_REASONS), "rejected"),
        (GatewaySession.state == "failed", "interrupted"),
        else_="unknown",
    )


def capture_path_expression():
    tiers = GatewaySession.capture_summary["tiers"]
    return case(
        (GatewaySession.capture_summary.is_(None), None),
        (tiers.contains(["challenge_resolution"]), "challenge_resolution"),
        (tiers.contains(["local_resolution"]), "local_resolution"),
        (tiers.contains(["managed"]), "managed"),
        else_="http",
    )
