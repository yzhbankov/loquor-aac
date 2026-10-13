"""End-to-end collaboration over the HTTP transport on loopback."""

import asyncio

import pytest

from loquor import Agent, PeerInfo
from loquor.agent import NeedInfo, TaskContext
from loquor.discovery import PeerAddress
from loquor.transports import PeerUnreachable

SECRET = "test-pairing-secret"


def peer_for(agent: Agent) -> PeerInfo:
    address = PeerAddress(
        agent_id=agent.agent_id,
        host="127.0.0.1",
        port=agent.port,
        capabilities=tuple(sorted(agent._capabilities)),
    )
    return PeerInfo(agent_id=agent.agent_id, address=address)


def make_server(tmp_path=None, secret=SECRET) -> Agent:
    """An agent that needs one extra measurement before answering."""
    agent = Agent(
        "server-01",
        pairing_secret=secret,
        store_path=str(tmp_path / "server.db") if tmp_path else ":memory:",
    )
    agent.handler_calls = 0

    async def diagnose(task: TaskContext):
        agent.handler_calls += 1
        if "motor_current_a" not in task.measurements:
            return NeedInfo(needed=["motor_current_a"], reason="need the current draw")
        return {
            "hypothesis": "shaft_misalignment",
            "based_on": {
                "vibration": task.observations["vibration_mm_s"],
                "current": task.measurements["motor_current_a"],
            },
        }

    agent.add_capability("maintenance_diagnostics", diagnose)
    return agent


def make_client(secret=SECRET) -> Agent:
    agent = Agent("client-01", pairing_secret=secret)

    async def provide(task_id, needed, reason):
        assert needed == ["motor_current_a"]
        return {"motor_current_a": 14.9}

    agent.info_provider = provide
    return agent


async def test_multi_turn_collaboration():
    server, client = make_server(), make_client()
    await server.start()
    await client.start()
    try:
        result = await client.ask(
            peer_for(server), "maintenance_diagnostics", {"vibration_mm_s": 8.2}
        )
        assert result["hypothesis"] == "shaft_misalignment"
        assert result["based_on"] == {"vibration": 8.2, "current": 14.9}
        assert server.handler_calls == 2  # once blocked on info, once concluded
    finally:
        await client.stop()
        await server.stop()


async def test_retry_is_idempotent():
    """A retried task_request returns the stored result without redoing work."""
    server, client = make_server(), make_client()
    await server.start()
    await client.start()
    try:
        peer = peer_for(server)
        first = await client.ask(peer, "maintenance_diagnostics",
                                 {"vibration_mm_s": 8.2}, task_id="task-fixed")
        calls_after_first = server.handler_calls
        second = await client.ask(peer, "maintenance_diagnostics",
                                  {"vibration_mm_s": 8.2}, task_id="task-fixed")
        assert second == first
        assert server.handler_calls == calls_after_first  # answered from the store
    finally:
        await client.stop()
        await server.stop()


async def test_interrupt_and_resume(tmp_path):
    """Test 2 from the PoC plan: disconnect mid-task, reconnect, resume."""
    server, client = make_server(tmp_path), make_client()
    await server.start()
    await client.start()

    original_provider = client.info_provider

    async def provider_then_kill(task_id, needed, reason):
        measurements = await original_provider(task_id, needed, reason)
        await server.transport.stop()  # peer vanishes before the answer lands
        return measurements

    client.info_provider = provider_then_kill
    with pytest.raises(PeerUnreachable):
        await client.ask(peer_for(server), "maintenance_diagnostics",
                         {"vibration_mm_s": 8.2}, task_id="task-resume")

    # Reconnect: a new server process with the same persistent store.
    client.info_provider = original_provider
    restarted = make_server(tmp_path)
    await restarted.start()
    try:
        results = await client.resume_pending(peer_for(restarted))
        assert len(results) == 1
        assert results[0]["hypothesis"] == "shaft_misalignment"
        # And a second resume produces nothing new — no duplicate results.
        assert await client.resume_pending(peer_for(restarted)) == []
    finally:
        await restarted.stop()
        await client.stop()
        await server.stop()


async def test_unauthenticated_peer_is_rejected():
    server = make_server()
    intruder = make_client(secret="wrong-secret")
    await server.start()
    await intruder.start()
    try:
        with pytest.raises(RuntimeError, match="signature verification|peer error"):
            await intruder.ask(peer_for(server), "maintenance_diagnostics", {"vibration_mm_s": 8.2})
        assert server.handler_calls == 0  # handler never ran for the intruder
    finally:
        await intruder.stop()
        await server.stop()


async def test_unknown_capability_returns_error():
    server, client = make_server(), make_client()
    await server.start()
    await client.start()
    try:
        with pytest.raises(RuntimeError, match="unknown_capability"):
            await client.ask(peer_for(server), "time_travel", {})
    finally:
        await client.stop()
        await server.stop()


async def test_find_peers_waits_for_matching_capability():
    server, client = make_server(), make_client()
    await server.start()
    await client.start()
    try:
        async def announce_later():
            await asyncio.sleep(0.1)
            await client.discovery.add_peer(peer_for(server).address)

        task = asyncio.ensure_future(announce_later())
        peers = await client.find_peers(capability="maintenance_diagnostics", timeout=5)
        await task
        assert [p.agent_id for p in peers] == ["server-01"]
        assert await client.find_peers(capability="nonexistent", timeout=0.2) == []
    finally:
        await client.stop()
        await server.stop()
