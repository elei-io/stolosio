from unittest.mock import AsyncMock

import pytest

from backend.proxy.attempts import AttemptLease
from backend.proxy.contracts import AttemptState, ProviderAttempt, ProviderName


class TransientDatabaseError(RuntimeError):
    sqlstate = "40P01"


def attempt() -> ProviderAttempt:
    return ProviderAttempt(
        attempt_id="00000000-0000-4000-8000-000000000002",
        session_id="00000000-0000-4000-8000-000000000001",
        ordinal=1,
        provider=ProviderName.HTTP,
        state=AttemptState.ACTIVE,
    )


@pytest.mark.asyncio
async def test_attempt_release_retries_a_transient_database_failure() -> None:
    repository = AsyncMock()
    repository.finish.side_effect = [TransientDatabaseError("deadlock"), True]
    notifier = AsyncMock()
    lease = AttemptLease(attempt(), repository, notifier)

    await lease.release(
        command_summary={"methods": {}},
        phase_summary={"measurement_version": 1},
    )
    await lease.release()

    assert repository.finish.await_count == 2
    assert repository.finish.await_args.kwargs["phase_summary"] == {
        "measurement_version": 1
    }
    notifier.notify.assert_awaited_once_with(ProviderName.HTTP)


@pytest.mark.asyncio
async def test_attempt_release_remains_retryable_after_nontransient_failure() -> None:
    repository = AsyncMock()
    repository.finish.side_effect = [RuntimeError("database unavailable"), True]
    notifier = AsyncMock()
    lease = AttemptLease(attempt(), repository, notifier)

    with pytest.raises(RuntimeError, match="database unavailable"):
        await lease.release()
    await lease.release()

    assert repository.finish.await_count == 2
    notifier.notify.assert_awaited_once_with(ProviderName.HTTP)
