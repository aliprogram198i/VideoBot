import asyncio

import pytest

from jobs.download_manager import DownloadBusyError, DownloadJobManager


@pytest.mark.asyncio
async def test_download_manager_preserves_per_user_serialization():
    manager = DownloadJobManager(max_concurrent=1, per_user_timeout=0.05, queue_timeout=0.2)
    entered = asyncio.Event()
    release = asyncio.Event()

    async def first():
        async with manager.slot(1):
            entered.set()
            await release.wait()

    first_task = asyncio.create_task(first())
    await entered.wait()

    with pytest.raises(DownloadBusyError):
        async with manager.slot(1):
            pass

    release.set()
    await first_task
    await manager.close()


@pytest.mark.asyncio
async def test_download_manager_queues_other_users():
    manager = DownloadJobManager(max_concurrent=1, per_user_timeout=0.2, queue_timeout=0.5)
    entered = asyncio.Event()
    release = asyncio.Event()
    second_entered = asyncio.Event()

    async def first():
        async with manager.slot(1):
            entered.set()
            await release.wait()

    async def second():
        async with manager.slot(2):
            second_entered.set()

    first_task = asyncio.create_task(first())
    await entered.wait()
    second_task = asyncio.create_task(second())
    await asyncio.sleep(0.05)
    assert not second_entered.is_set()
    assert manager.queue_depth >= 1

    release.set()
    await asyncio.gather(first_task, second_task)
    assert second_entered.is_set()
    await manager.close()
