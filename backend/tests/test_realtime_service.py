from __future__ import annotations

import json


def test_sse_frame_uses_event_and_json_data():
    from app.services.realtime_service import sse_frame

    frame = sse_frame({"type": "task.updated", "task_id": "TASK-1"})
    assert frame.startswith("event: team-update\n")
    assert '"type":"task.updated"' in frame
    assert frame.endswith("\n\n")


def test_heartbeat_is_a_comment_frame():
    from app.services.realtime_service import sse_frame

    assert sse_frame({"type": "realtime.heartbeat"}) == ": heartbeat\n\n"


def test_disabled_realtime_emits_explicit_unavailable(monkeypatch):
    monkeypatch.setenv("REALTIME_ENABLED", "false")
    from app.services.realtime_service import iter_events

    event = next(iter_events("user-1"))
    assert event["type"] == "realtime.unavailable"
    assert event["resource_type"] == "system"


def test_publish_uses_team_channel_and_json(monkeypatch):
    published: list[tuple[str, str]] = []

    class FakeClient:
        def publish(self, channel, payload):
            published.append((channel, payload))
            return 1

    monkeypatch.setenv("REALTIME_ENABLED", "true")
    monkeypatch.setenv("REALTIME_REDIS_URL", "redis://localhost:6379/2")
    from app.services import realtime_service

    monkeypatch.setattr(realtime_service, "_client", lambda: FakeClient())
    assert realtime_service.publish_resource_update(
        resource_type="task",
        resource_id="TASK-1",
        event_type="task.updated",
    )
    assert published[0][0] == realtime_service.TEAM_CHANNEL
    assert json.loads(published[0][1])["task_id"] == "TASK-1"


def test_iter_events_subscribes_team_and_user_channels_and_emits_heartbeat(monkeypatch):
    class FakePubSub:
        def __init__(self):
            self.subscribed = None
            self.closed = False
            self.messages = [
                None,
                {"type": "message", "data": '{"type":"notification.created","task_id":"TASK-1"}'},
            ]

        def subscribe(self, *channels):
            self.subscribed = channels

        def get_message(self, timeout):
            if self.messages:
                return self.messages.pop(0)
            return None

        def close(self):
            self.closed = True

    class FakeClient:
        def __init__(self):
            self.pubsub_instance = FakePubSub()
            self.closed = False

        def ping(self):
            return True

        def pubsub(self, ignore_subscribe_messages):
            assert ignore_subscribe_messages is True
            return self.pubsub_instance

        def close(self):
            self.closed = True

    client = FakeClient()
    monkeypatch.setenv("REALTIME_ENABLED", "true")
    monkeypatch.setenv("REALTIME_REDIS_URL", "redis://localhost:6379/2")
    from app.services import realtime_service

    monkeypatch.setattr(realtime_service, "_client", lambda: client)
    events = realtime_service.iter_events("user-7")

    assert next(events)["type"] == "realtime.heartbeat"
    assert next(events)["type"] == "notification.created"
    assert client.pubsub_instance.subscribed == (
        realtime_service.TEAM_CHANNEL,
        f"{realtime_service.USER_CHANNEL_PREFIX}user-7",
    )

    events.close()
    assert client.pubsub_instance.closed is True
    assert client.closed is True


def test_iter_events_reports_redis_connection_failure(monkeypatch):
    class FakePubSub:
        def __init__(self):
            self.closed = False

        def close(self):
            self.closed = True

    class FakeClient:
        def __init__(self):
            self.pubsub_instance = FakePubSub()
            self.closed = False

        def ping(self):
            raise RuntimeError("redis unavailable")

        def pubsub(self, ignore_subscribe_messages):
            return self.pubsub_instance

        def close(self):
            self.closed = True

    client = FakeClient()
    monkeypatch.setenv("REALTIME_ENABLED", "true")
    monkeypatch.setenv("REALTIME_REDIS_URL", "redis://localhost:6379/2")
    from app.services import realtime_service

    monkeypatch.setattr(realtime_service, "_client", lambda: client)
    event = next(realtime_service.iter_events("user-7"))

    assert event["type"] == "realtime.unavailable"
    assert event["reason"] == "Redis 连接失败，实时同步暂不可用"
    assert client.pubsub_instance.closed is True
    assert client.closed is True
