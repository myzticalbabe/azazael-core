# coding: utf-8
"""
Unit tests for MemoryBus (azazael/src/CORE/MemoryBus.py)

Run with: python tests/test_memorybus.py
"""

import sys, os, time, re
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
os.environ['PAMS_URL'] = 'http://127.0.0.1:99999'

from CORE.MemoryBus import MemoryBus, BusMessage


def test_bus_creation():
    """Test bus can be created with default params."""
    bus = MemoryBus(use_local_fallback=True)
    assert bus.session_id is not None
    assert bus.use_local_fallback is True
    stats = bus.get_stats()
    assert stats['published'] == 0
    assert stats['delivered'] == 0
    assert stats['version'] == '1.1.0'
    assert stats['pams_available'] is False  # port 99999 unreachable


def test_bus_creation_no_fallback():
    """Test bus with local fallback disabled."""
    bus = MemoryBus(use_local_fallback=False)
    assert bus.use_local_fallback is False


def test_publish_local_fallback():
    """Test publishing to local fallback queue when PAMS is down."""
    bus = MemoryBus(use_local_fallback=True)
    mid = bus.publish("test.topic", {"key": "value"})
    assert mid is not None
    assert mid > 0
    stats = bus.get_stats()
    assert stats['published'] == 1
    assert stats['local_queue_size'] == 1


def test_publish_no_fallback():
    """Test publish returns None when PAMS is down and fallback is off."""
    bus = MemoryBus(use_local_fallback=False)
    mid = bus.publish("test.topic", "data")
    assert mid is None  # No fallback, PAMS down
    stats = bus.get_stats()
    assert stats['published'] == 1


def test_publish_different_types():
    """Test publishing various content types."""
    bus = MemoryBus(use_local_fallback=True)

    mid1 = bus.publish("t.dict", {"nested": {"a": 1}})
    mid2 = bus.publish("t.str", "hello")
    mid3 = bus.publish("t.int", 42)
    mid4 = bus.publish("t.float", 3.14)
    mid5 = bus.publish("t.bool", True)
    mid6 = bus.publish("t.list", [1, 2, 3])

    assert all(m is not None for m in [mid1, mid2, mid3, mid4, mid5, mid6])
    assert bus.get_stats()['local_queue_size'] == 6


def test_poll_local_fallback():
    """Test polling retrieves messages from local queue."""
    bus = MemoryBus(use_local_fallback=True)
    bus.publish("test.alpha", "first")
    bus.publish("test.beta", "second")

    msgs = bus.poll(topic="test.alpha", since_id=0)
    assert len(msgs) == 1
    assert msgs[0].topic == "test.alpha"
    assert msgs[0].content == "first"

    msgs_all = bus.poll(since_id=0)
    assert len(msgs_all) == 2


def test_poll_with_since_id():
    """Test polling with since_id filtering."""
    bus = MemoryBus(use_local_fallback=True)
    m1 = bus.publish("t.a", "msg1")
    m2 = bus.publish("t.b", "msg2")
    m3 = bus.publish("t.c", "msg3")

    msgs = bus.poll(since_id=m2)
    assert len(msgs) == 1
    assert msgs[0].content == "msg3"


def test_poll_empty():
    """Test polling with no matching messages."""
    bus = MemoryBus(use_local_fallback=True)
    msgs = bus.poll(topic="nonexistent.topic", since_id=0)
    assert msgs == []


def test_poll_limit():
    """Test polling respects limit (local fallback has no hard cap but returns all matching)."""
    bus = MemoryBus(use_local_fallback=True)
    for i in range(10):
        bus.publish("t.limit", f"msg-{i}")

    msgs = bus.poll(topic="t.limit", since_id=0)
    assert len(msgs) == 10


def test_subscribe_deliver():
    """Test subscribing and receiving delivered messages."""
    bus = MemoryBus(use_local_fallback=True)
    received = []

    def callback(msg):
        received.append(msg)

    bus.subscribe("test.status", callback)
    bus.publish("test.status", "running")
    bus.publish("test.status", "completed")

    delivered = bus.poll_and_deliver()
    assert delivered == 2
    assert len(received) == 2
    assert received[0].content == "running"
    assert received[1].content == "completed"


def test_subscribe_wildcard_star():
    """Test single-segment wildcard * matching."""
    bus = MemoryBus(use_local_fallback=True)
    received = []

    def callback(msg):
        received.append(msg.topic)

    bus.subscribe("agent.*.error", callback)
    bus.publish("agent.runtime.error", "err1")
    bus.publish("agent.network.error", "err2")
    bus.publish("agent.error", "err3")  # should NOT match (single segment)
    bus.publish("pool.error", "err4")   # should NOT match

    bus.poll_and_deliver()
    assert len(received) == 2
    assert "agent.runtime.error" in received
    assert "agent.network.error" in received


def test_subscribe_wildcard_gt():
    """Test prefix wildcard > matching."""
    bus = MemoryBus(use_local_fallback=True)
    received = []

    def callback(msg):
        received.append(msg.topic)

    bus.subscribe("pool.>", callback)
    bus.publish("pool.task", "t1")
    bus.publish("pool.task.complete", "t2")
    bus.publish("pool.status.running", "t3")
    bus.publish("agent.task", "t4")  # should NOT match

    bus.poll_and_deliver()
    assert len(received) == 3
    for t in ["pool.task", "pool.task.complete", "pool.status.running"]:
        assert t in received


def test_subscribe_exact():
    """Test exact match subscription."""
    bus = MemoryBus(use_local_fallback=True)
    received = []

    def callback(msg):
        received.append(msg.topic)

    bus.subscribe("agent.status", callback)
    bus.publish("agent.status", "ok")
    bus.publish("agent.status.detail", "detail")  # should NOT match

    bus.poll_and_deliver()
    assert len(received) == 1
    assert received[0] == "agent.status"


def test_subscribe_wildcard_all():
    """Test *.* matches any two-segment topic."""
    bus = MemoryBus(use_local_fallback=True)
    received = []

    def callback(msg):
        received.append(msg.topic)

    bus.subscribe("*.*", callback)
    bus.publish("agent.status", "a")
    bus.publish("pool.task", "b")
    bus.publish("agent.status.running", "c")  # 3 segments — should NOT match

    bus.poll_and_deliver()
    assert len(received) == 2


def test_subscribe_multiple_callbacks():
    """Test multiple callbacks on same pattern."""
    bus = MemoryBus(use_local_fallback=True)
    received1 = []
    received2 = []

    def cb1(msg):
        received1.append(msg.content)

    def cb2(msg):
        received2.append(msg.content)

    bus.subscribe("test.topic", cb1)
    bus.subscribe("test.topic", cb2)
    bus.publish("test.topic", "data")

    bus.poll_and_deliver()
    assert len(received1) == 1
    assert len(received2) == 1


def test_unsubscribe():
    """Test unsubscribing a specific callback."""
    bus = MemoryBus(use_local_fallback=True)
    received = []

    def callback(msg):
        received.append(msg)

    bus.subscribe("test.topic", callback)
    remaining = bus.unsubscribe("test.topic", callback)
    assert remaining == 0

    bus.publish("test.topic", "should not arrive")
    bus.poll_and_deliver()
    assert len(received) == 0


def test_unsubscribe_all():
    """Test unsubscribing all callbacks for a pattern."""
    bus = MemoryBus(use_local_fallback=True)
    bus.subscribe("test.topic", lambda m: None)
    bus.subscribe("test.topic", lambda m: None)
    bus.subscribe("other.topic", lambda m: None)

    remaining = bus.unsubscribe("test.topic")  # remove all
    assert remaining == 0
    stats = bus.get_stats()
    assert stats['subscriptions'] == 1  # only other.topic remains


def test_unsubscribe_nonexistent():
    """Test unsubscribing a pattern that doesn't exist."""
    bus = MemoryBus(use_local_fallback=True)
    remaining = bus.unsubscribe("does.not.exist")
    assert remaining == 0


def test_poll_and_deliver_topic_filter():
    """Test poll_and_deliver with topic filter."""
    bus = MemoryBus(use_local_fallback=True)
    received = []

    def callback(msg):
        received.append(msg.topic)

    bus.subscribe("test.a", callback)
    bus.subscribe("test.b", callback)
    bus.publish("test.a", "x")
    bus.publish("test.b", "y")
    bus.publish("test.a", "z")

    # Only deliver test.a
    delivered = bus.poll_and_deliver(topic="test.a")
    assert delivered == 2
    assert len(received) == 2
    assert all(t == "test.a" for t in received)


def test_get_last_seen():
    """Test tracking last seen message ID per topic."""
    bus = MemoryBus(use_local_fallback=True)
    assert bus.get_last_seen("test.topic") == 0

    mid = bus.publish("test.topic", "data")
    bus.poll(since_id=0)
    assert bus.get_last_seen("test.topic") == mid


def test_stats_tracking():
    """Test all stat counters."""
    bus = MemoryBus(use_local_fallback=True)
    bus.publish("t.a", "x")
    bus.publish("t.b", "y")
    bus.poll(since_id=0)

    stats = bus.get_stats()
    assert stats['published'] == 2
    assert stats['polls'] >= 1
    assert stats['local_fallback_used'] == 2
    assert stats['local_queue_size'] == 2
    assert stats['pams_errors'] == 0  # PAMS never contacted for fallback publishes
    assert stats['version'] == '1.1.0'


def test_bus_repr():
    """Test string representation."""
    bus = MemoryBus(use_local_fallback=True)
    r = repr(bus)
    assert 'MemoryBus' in r
    assert 'DOWN' in r  # PAMS is not available


def test_busmessage_dataclass():
    """Test BusMessage creation and to_dict."""
    import uuid
    mid = 42
    msg = BusMessage(
        message_id=mid,
        topic="test.topic",
        content={"key": "value"},
        metadata={"source": "test"},
        timestamp="2026-01-01T00:00:00",
        session_id="sess-1",
        source="test-source",
    )
    d = msg.to_dict()
    assert d['message_id'] == mid
    assert d['topic'] == "test.topic"
    assert d['content'] == {"key": "value"}
    assert d['source'] == "test-source"


def test_close_does_not_crash():
    """Test close doesn't crash when PAMS is down."""
    bus = MemoryBus(use_local_fallback=True)
    bus.publish("t.a", "x")
    bus.publish("t.b", "y")
    try:
        bus.close()
        cleanup_ok = True
    except Exception:
        cleanup_ok = False
    assert cleanup_ok, 'close() raised an exception'
    # Close should clear subscriptions and local queue
    assert len(bus._subscriptions) == 0
    assert len(bus._local_queue) == 0


def test_poll_and_deliver_empty():
    """Test poll_and_deliver with no messages returns 0."""
    bus = MemoryBus(use_local_fallback=True)
    delivered = bus.poll_and_deliver()
    assert delivered == 0


def test_flush_local_queue():
    """Test flush_local_queue with PAMS down returns 0."""
    bus = MemoryBus(use_local_fallback=True)
    bus.publish("t.a", "x")
    flushed = bus.flush_local_queue()
    assert flushed == 0  # PAMS is down


def test_pattern_matching_static():
    """Test _matches_topic static method directly."""
    assert MemoryBus._matches_topic("agent.status", "agent.status") is True
    assert MemoryBus._matches_topic("agent.status", "agent.status.detail") is False
    assert MemoryBus._matches_topic("agent.*", "agent.status") is True
    assert MemoryBus._matches_topic("agent.*", "agent.status.detail") is False
    assert MemoryBus._matches_topic("agent.>", "agent.status") is True
    assert MemoryBus._matches_topic("agent.>", "agent.status.detail") is True
    assert MemoryBus._matches_topic("agent.>", "pool.status") is False
    assert MemoryBus._matches_topic("*.*", "a.b") is True
    assert MemoryBus._matches_topic("*.*", "a.b.c") is False


def test_topic_segments():
    """Test topic segment generation for PAMS tags."""
    segments = MemoryBus._topic_segments("agent.status.error")
    assert "bus.agent.status" in segments
    assert "bus.agent.status.error" in segments
    assert len(segments) == 2

    # Single-segment should produce no tags (too broad)
    segs = MemoryBus._topic_segments("agent")
    assert segs == []


def test_pattern_matching_wildcard_only():
    """Test single wildcard-only pattern matching."""
    # '*' matches any single segment
    assert MemoryBus._matches_topic("*", "anything") is True
    assert MemoryBus._matches_topic("*", "multi.segment") is False

    # '>' matches everything
    assert MemoryBus._matches_topic(">", "anything") is True
    assert MemoryBus._matches_topic(">", "deeply.nested.topic") is True
    assert MemoryBus._matches_topic(">", "single") is True


def test_publish_with_metadata():
    """Test publishing with metadata (source is overwritten by publish() param)."""
    bus = MemoryBus(use_local_fallback=True)
    meta = {"priority": "high", "custom_tag": "important"}
    mid = bus.publish("test.important", "critical", metadata=meta, source="custom-source")
    assert mid is not None

    msgs = bus.poll(topic="test.important", since_id=0)
    assert len(msgs) == 1
    assert msgs[0].metadata.get("priority") == "high"
    assert msgs[0].metadata.get("custom_tag") == "important"
    assert msgs[0].metadata.get("source") == "custom-source"
    assert msgs[0].metadata.get("topic") == "test.important"


def test_publish_non_serializable():
    """Test publishing non-serializable content (falls back to str conversion)."""
    bus = MemoryBus(use_local_fallback=True)

    class Custom:
        def __str__(self):
            return "custom-obj"

    mid = bus.publish("t.custom", Custom())
    assert mid is not None

    msgs = bus.poll(topic="t.custom", since_id=0)
    assert len(msgs) == 1
    assert isinstance(msgs[0].content, str)


def test_cyclic_publish_no_infinite():
    """Test that publishing during a callback doesn't cause issues."""
    bus = MemoryBus(use_local_fallback=True)
    side_effects = []

    def callback(msg):
        side_effects.append(msg.content)
        # Publish during callback — should not cause issues
        bus.publish("test.nested", "nested-" + str(msg.content))

    bus.subscribe("test.trigger", callback)
    bus.publish("test.trigger", "first")
    bus.poll_and_deliver()

    # The nested publish should NOT be in this delivery cycle
    assert len(side_effects) == 1
    assert side_effects[0] == "first"

    # But it should be in the queue for next poll
    stats = bus.get_stats()
    assert stats['published'] == 2  # original + nested


if __name__ == '__main__':
    tests = [
        ('bus_creation', test_bus_creation),
        ('bus_creation_no_fallback', test_bus_creation_no_fallback),
        ('publish_local_fallback', test_publish_local_fallback),
        ('publish_no_fallback', test_publish_no_fallback),
        ('publish_different_types', test_publish_different_types),
        ('poll_local_fallback', test_poll_local_fallback),
        ('poll_with_since_id', test_poll_with_since_id),
        ('poll_empty', test_poll_empty),
        ('poll_limit', test_poll_limit),
        ('subscribe_deliver', test_subscribe_deliver),
        ('subscribe_wildcard_star', test_subscribe_wildcard_star),
        ('subscribe_wildcard_gt', test_subscribe_wildcard_gt),
        ('subscribe_exact', test_subscribe_exact),
        ('subscribe_wildcard_all', test_subscribe_wildcard_all),
        ('subscribe_multiple_callbacks', test_subscribe_multiple_callbacks),
        ('unsubscribe', test_unsubscribe),
        ('unsubscribe_all', test_unsubscribe_all),
        ('unsubscribe_nonexistent', test_unsubscribe_nonexistent),
        ('poll_and_deliver_topic_filter', test_poll_and_deliver_topic_filter),
        ('get_last_seen', test_get_last_seen),
        ('stats_tracking', test_stats_tracking),
        ('bus_repr', test_bus_repr),
        ('busmessage_dataclass', test_busmessage_dataclass),
        ('close_does_not_crash', test_close_does_not_crash),
        ('poll_and_deliver_empty', test_poll_and_deliver_empty),
        ('flush_local_queue', test_flush_local_queue),
        ('pattern_matching_static', test_pattern_matching_static),
        ('pattern_matching_wildcard_only', test_pattern_matching_wildcard_only),
        ('topic_segments', test_topic_segments),
        ('publish_with_metadata', test_publish_with_metadata),
        ('publish_non_serializable', test_publish_non_serializable),
        ('cyclic_publish_no_infinite', test_cyclic_publish_no_infinite),
    ]
    passed = 0
    failed = 0
    print(f"\n  MemoryBus Tests ({len(tests)} tests)")
    print(f"  {'='*55}")
    for name, fn in tests:
        try:
            fn()
            print(f"  [OK] {name}")
            passed += 1
        except Exception as e:
            import traceback
            traceback.print_exc()
            print(f"  [FAIL] {name}: {e}")
            failed += 1
    print(f"  {'='*55}")
    print(f"  Passed: {passed}, Failed: {failed}")
