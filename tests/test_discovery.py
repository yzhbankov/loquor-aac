"""§3 Discovery: beacon layout, Bloom filter false positives, visibility, directory."""

import random

import pytest

from loquor.protocol.discovery import (
    BEACON_SIZE,
    Beacon,
    PeerDirectory,
    Visibility,
    bloom,
    bloom_may_contain,
    is_beacon,
    prefix_of,
)
from loquor.protocol.identity import Identity


def test_beacon_is_16_bytes_and_round_trips():
    me = Identity.generate()
    beacon = Beacon(prefix=prefix_of(me.agent_id), bloom=bloom(["weather_forecast"]), seq=7)
    data = beacon.encode()
    assert len(data) == BEACON_SIZE and data[:2] == b"AM" and data[2] == 1
    assert is_beacon(data) and not is_beacon(data + b"x") and not is_beacon(b"\x00" * 16)
    assert Beacon.decode(data) == beacon
    assert Beacon.decode(Beacon(beacon.prefix, beacon.bloom, seq=300).encode()).seq == 300 % 256
    with pytest.raises(ValueError, match="version"):
        Beacon.decode(b"AM\x02" + data[3:])
    with pytest.raises(ValueError, match="not a beacon"):
        Beacon.decode(b"XX" + data[2:])


def test_bloom_membership_and_false_positive_rate():
    assert bloom([]) == b"\x00\x00\x00\x00"
    f = bloom(["weather_forecast", "clothing_recommendation", "irrigation", "music"])
    assert all(bloom_may_contain(f, c) for c in ["weather_forecast", "clothing_recommendation", "irrigation", "music"])

    rng = random.Random(42)
    trials = 20000
    false_positives = sum(bloom_may_contain(f, f"cap-{rng.random()}") for _ in range(trials))
    rate = false_positives / trials
    # 32 bits, k=2, n=4: expected (1 - (1 - 1/32)^8)^2 ≈ 5 %.
    assert 0.02 < rate < 0.10, rate

    f8 = bloom([f"c{i}" for i in range(8)])
    rate8 = sum(bloom_may_contain(f8, f"cap-{rng.random()}") for _ in range(trials)) / trials
    assert rate < rate8 < 0.35, rate8  # expected ≈ 15 % at n=8


def test_visibility_policy():
    assert Visibility.VISIBLE.beacons and Visibility.VISIBLE.answers_queries
    assert not Visibility.QUIET.beacons and Visibility.QUIET.answers_queries and Visibility.QUIET.answers_hello
    assert not Visibility.SILENT.beacons and not Visibility.SILENT.answers_queries
    assert not Visibility.SILENT.answers_hello
    assert Visibility("quiet") is Visibility.QUIET


def test_directory_beacons_hellos_and_pruning():
    me = Identity.generate()
    d = PeerDirectory(ttl=0.0)
    card = {"name": "Weather Forecaster", "skills": [{"id": "weather_forecast"}]}
    beacon = Beacon(prefix_of(me.agent_id), bloom(["weather_forecast"]), seq=1)

    # A beacon from someone we never HELLO'd with is an unknown: nothing stored.
    assert d.heard_beacon(beacon, address=1) == (None, False) and len(d) == 0

    peer, new = d.heard_hello(me.agent_id, me.public_key, card, address=1)
    assert new and peer.handshaken and peer.name == "Weather Forecaster"
    assert peer.capabilities == ["weather_forecast"] and peer.has("weather_forecast")
    assert peer.label == f"weather-forecaster-{me.agent_id[:4].hex()}" and peer.uid == me.agent_id.hex()
    assert d.find("weather_forecast") == [peer] and d.find("music") == []
    assert d.by_prefix(prefix_of(me.agent_id)) is peer and me.agent_id in d

    same, changed = d.heard_beacon(beacon, address=2)
    assert same is peer and not changed and peer.address == 2 and peer.seq == 1
    _, changed = d.heard_beacon(Beacon(beacon.prefix, bloom(["x"]), seq=2), address=2)
    assert changed and peer.may_have("x") and not peer.has("x")

    _, new = d.heard_hello(me.agent_id, me.public_key, card, address=3)
    assert not new
    assert d.prune() == [peer] and len(d) == 0
