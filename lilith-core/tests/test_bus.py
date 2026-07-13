"""Tests for lilith_core.bus — LilithBus pub/sub + role anycast claim.

Covers the 12 tests from the plan-28 spec (test #12 about subscribe_pattern
handler is out of scope for v1 and intentionally omitted):

    1.  monotonic ids
    2.  poll exact-match
    3.  poll wildcard '*' (one segment)
    4.  poll wildcard '**' (recursive)
    5.  claim partitioned by role (5 msgs each, no cross-talk)
    6.  atomic claim under 10 threads (5 msgs → 5 winners, 5 None, no dupes)
    7.  FIFO claim order
    8.  ack excludes from claim; poll still shows historical
    9.  release → another claimer can re-claim
    10. payload JSON roundtrip with nested structures
    11. no cross-topic leak between patterns
"""

from __future__ import annotations

import threading

import pytest

from lilith_core.bus import (
    BusError,
    BusMessage,
    LilithBus,
)


# ---------- 1. monotonic ids ----------


def test_publish_returns_monotonic_ids(tmp_path):
    bus = LilithBus(tmp_path / "bus.db")
    a = bus.publish("a", {})
    b = bus.publish("b", {})
    c = bus.publish("c", {})
    assert a < b < c
    assert isinstance(a, int)


# ---------- 2. poll exact match ----------


def test_poll_exact_topic(tmp_path):
    bus = LilithBus(tmp_path / "bus.db")
    bus.publish("agents.skadi.event", {"k": 1})
    bus.publish("agents.odin.event", {"k": 2})
    out = bus.poll("agents.skadi.event")
    assert len(out) == 1
    assert out[0].topic == "agents.skadi.event"
    assert out[0].payload == {"k": 1}


# ---------- 3. '*' = single segment ----------


def test_poll_wildcard_single_segment(tmp_path):
    bus = LilithBus(tmp_path / "bus.db")
    bus.publish("agents.skadi.event", {"k": "skadi"})
    bus.publish("agents.odin.event", {"k": "odin"})
    bus.publish("agents.skadi.subnet.ping", {"k": "deep"})  # too many segments
    bus.publish("agentz.x.event", {"k": "sibling"})  # different root
    out = bus.poll("agents.*.event")
    topics = sorted(m.topic for m in out)
    assert topics == ["agents.odin.event", "agents.skadi.event"]


# ---------- 4. '**' = recursive ----------


def test_poll_wildcard_recursive(tmp_path):
    bus = LilithBus(tmp_path / "bus.db")
    bus.publish("agents.a", {})
    bus.publish("agents.a.b", {})
    bus.publish("agents.a.b.c", {})
    bus.publish("other.foo", {})

    a_all = sorted(m.topic for m in bus.poll("agents.**"))
    assert a_all == ["agents.a", "agents.a.b", "agents.a.b.c"]

    prefix = [m.topic for m in bus.poll("**.foo")]
    assert prefix == ["other.foo"]

    middle = sorted(m.topic for m in bus.poll("agents.**.c"))
    assert middle == ["agents.a.b.c"]


def test_poll_wildstar_recursive_empty_prefix(tmp_path):
    """'**' must allow the empty match: pattern="**.foo" against topic="foo"."""
    bus = LilithBus(tmp_path / "bus.db")
    bus.publish("foo", {})
    out = [m.topic for m in bus.poll("**.foo")]
    assert out == ["foo"]


# ---------- 5. claim partitioned by role ----------


def test_claim_partitioned_by_role(tmp_path):
    bus = LilithBus(tmp_path / "bus.db")
    role_a_ids = [bus.publish("work", {"r": "A", "i": i}, role="A") for i in range(5)]
    role_b_ids = [bus.publish("work", {"r": "B", "i": i}, role="B") for i in range(5)]

    out_a: list = []
    out_b: list = []
    mutex = threading.Lock()

    def worker(role: str, claimer: str, sink: list) -> None:
        msg = bus.claim_any(role, claimer)
        with mutex:
            sink.append(msg.id if msg is not None else None)

    a_threads = [
        threading.Thread(target=worker, args=("A", f"A-{i}", out_a)) for i in range(5)
    ]
    b_threads = [
        threading.Thread(target=worker, args=("B", f"B-{i}", out_b)) for i in range(5)
    ]
    for t in a_threads + b_threads:
        t.start()
    for t in a_threads + b_threads:
        t.join()

    won_a = [x for x in out_a if x is not None]
    won_b = [x for x in out_b if x is not None]
    assert len(won_a) == 5
    assert len(won_b) == 5
    assert set(won_a) == set(role_a_ids)
    assert set(won_b) == set(role_b_ids)
    # No cross-role bleed
    assert not (set(won_a) & set(won_b))


# ---------- 6. atomic claim under 10 threads ----------


def test_claim_atomic_under_threads(tmp_path):
    bus = LilithBus(tmp_path / "bus.db")
    n_msgs = 5
    n_workers = 10
    published = [bus.publish("work", {"i": i}, role="race") for i in range(n_msgs)]

    results: list = []
    mutex = threading.Lock()

    def worker() -> None:
        msg = bus.claim_any("race", f"w-{threading.get_ident()}")
        with mutex:
            results.append(msg.id if msg is not None else None)

    threads = [threading.Thread(target=worker) for _ in range(n_workers)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    won = [x for x in results if x is not None]
    lost = [x for x in results if x is None]
    assert len(won) == n_msgs
    assert len(lost) == n_workers - n_msgs
    assert set(won) == set(published)
    # No duplicate claims under contention
    assert len(set(won)) == len(won)


# ---------- 7. FIFO claim order ----------


def test_claim_fifo_order(tmp_path):
    bus = LilithBus(tmp_path / "bus.db")
    a = bus.publish("q", {"i": 0}, role="fifo")
    b = bus.publish("q", {"i": 1}, role="fifo")
    c = bus.publish("q", {"i": 2}, role="fifo")

    m1 = bus.claim_any("fifo", "w")
    m2 = bus.claim_any("fifo", "w")
    m3 = bus.claim_any("fifo", "w")
    assert m1 is not None and m1.id == a
    assert m2 is not None and m2.id == b
    assert m3 is not None and m3.id == c


# ---------- 8. ack excludes from claim; poll still sees historical ----------


def test_ack_excludes_from_claim_but_poll_shows_historical(tmp_path):
    bus = LilithBus(tmp_path / "bus.db")
    msg_id = bus.publish("q", {"hi": 1}, role="x")

    claimed = bus.claim_any("x", "alice")
    assert claimed is not None and claimed.id == msg_id

    # Rightful acker wins
    assert bus.ack(msg_id, "alice") is True
    # Second ack from same claimer is a no-op (delivered_at already set)
    assert bus.ack(msg_id, "alice") is False
    # Wrong claimer is rejected
    assert bus.ack(msg_id, "bob") is False

    # No unclaimed rows left for role 'x' — claim_any returns None.
    assert bus.claim_any("x", "bob") is None

    # poll() is fan-out / historical: ack does NOT remove the row,
    # so reading the topic with since_id=0 still surfaces it.
    history = bus.poll("q", limit=100)
    assert any(m.id == msg_id for m in history)


# ---------- 9. release → another claimer can re-claim ----------


def test_release_makes_reclaimable(tmp_path):
    bus = LilithBus(tmp_path / "bus.db")
    msg_id = bus.publish("q", {"v": 1}, role="r")

    first = bus.claim_any("r", "alice")
    assert first is not None and first.id == msg_id

    # Bob can't release Alice's claim.
    assert bus.release(msg_id, "bob") is False

    # Alice releases → row goes back into the unclaimed pool.
    assert bus.release(msg_id, "alice") is True

    # Another worker (or Alice again) can now claim it.
    second = bus.claim_any("r", "bob")
    assert second is not None and second.id == msg_id

    # After ack, release is refused (delivered_at != NULL).
    assert bus.ack(msg_id, "bob") is True
    assert bus.release(msg_id, "bob") is False


# ---------- 10. payload JSON roundtrip ----------


def test_payload_roundtrip_nested(tmp_path):
    bus = LilithBus(tmp_path / "bus.db")
    payload = {
        "str": "héllo",
        "int": 42,
        "float": 3.14,
        "bool_true": True,
        "bool_false": False,
        "none": None,
        "list": [1, 2, [3, "x", None]],
        "nested": {"a": {"b": {"c": [True, False, None, 0, 1]}}},
    }
    bus.publish("rt", payload)
    out = bus.poll("rt", limit=10)
    assert len(out) == 1
    msg = out[0]
    assert isinstance(msg, BusMessage)
    assert msg.payload == payload
    # Type fidelity, not just equality: False must stay False, not 0.
    assert msg.payload["bool_false"] is False
    assert msg.payload["none"] is None
    assert msg.payload["nested"]["a"]["b"]["c"][0] is True


# ---------- 11. no cross-topic leak ----------


def test_no_cross_topic_leak(tmp_path):
    bus = LilithBus(tmp_path / "bus.db")
    bus.publish("alpha.event", {"a": 1})
    bus.publish("alpha.event.deep", {"a": 2})
    bus.publish("beta.event", {"b": 1})

    # Unrelated family returns nothing.
    assert bus.poll("gamma.**") == []

    # Same root, deeper branch: both alpha rows.
    alpha = [m.topic for m in bus.poll("alpha.**")]
    assert sorted(alpha) == ["alpha.event", "alpha.event.deep"]

    # Different root: only beta.
    beta = [m.topic for m in bus.poll("beta.**")]
    assert beta == ["beta.event"]

    # role-scoped claim respects the topic filter (no leak across topics).
    bus.publish("alpha.work", {}, role="jobs")
    bus.publish("beta.work", {}, role="jobs")
    a_msg = bus.claim_any("jobs", "w")
    assert a_msg is not None
    # The first unclaimed 'jobs' row ordered by id wins; both exist but
    # the claimer only ever holds one. Verify the union covers both topics.
    second = bus.claim_any("jobs", "w")
    assert second is not None
    assert {a_msg.topic, second.topic} == {"alpha.work", "beta.work"}


# ---------- defensive: validation ----------


def test_publish_validates_input(tmp_path):
    bus = LilithBus(tmp_path / "bus.db")
    with pytest.raises(BusError):
        bus.publish("", {})
    with pytest.raises(BusError):
        bus.publish("ok", [])


def test_claim_validates_input(tmp_path):
    bus = LilithBus(tmp_path / "bus.db")
    with pytest.raises(BusError):
        bus.claim_any("", "claimer")
    with pytest.raises(BusError):
        bus.claim_any("role", "")
