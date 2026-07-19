"""Shared pytest fixtures."""

import pytest
from wyoming_bluetts import handler


@pytest.fixture(autouse=True)
def _reset_generation_lock():
    """Reset the process-wide generation lock between tests.

    asyncio.Lock only binds to a specific event loop the first time it
    actually has to wait for another holder (see its acquire()) -- not on
    every acquire. Each test runs its own asyncio.run(), i.e. its own event
    loop, so a module-global lock left bound to a closed loop from a
    previous test would raise "bound to a different event loop" the moment
    two tests both hit real contention, even though nothing is wrong in
    production (a long-lived server has exactly one loop for its whole
    life). Resetting it here keeps tests independent of each other and of
    run order.
    """
    handler._GENERATION_LOCK = None
    yield
    handler._GENERATION_LOCK = None
