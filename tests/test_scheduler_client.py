"""Mocked tests proving the sample works as the blog describes.

These tests use moto to mock EventBridge Scheduler, so no real AWS resources or
credentials are touched. They verify each capability in the blog: create,
update, cancel, snooze (both branches), recurring, cleanup, the delivery
handler, and the API integration.

Run:  pytest -q
"""
import json
import os
from unittest.mock import patch

import boto3
import pytest
from moto import mock_aws

REGION = "us-east-1"
TASK_GROUP = "task-reminders"
RECURRING_GROUP = "recurring-tasks"
TASKS_TABLE = "test-tasks"

# Dummy push-notification device token used only in tests. Not a credential;
# the value is meaningless. nosec silences Bandit's hardcoded-secret heuristic,
# which flags any literal assigned near an identifier containing "token".
FAKE_DEVICE_TOKEN = "example-device-token"  # nosec B105


@pytest.fixture
def aws_env(monkeypatch):
    """Fake AWS credentials/region so boto3 clients initialize under moto."""
    monkeypatch.setenv("AWS_DEFAULT_REGION", REGION)
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_SECURITY_TOKEN", "testing")
    monkeypatch.setenv("AWS_SESSION_TOKEN", "testing")
    monkeypatch.setenv("TASKS_TABLE", TASKS_TABLE)


@pytest.fixture
def modules(aws_env, monkeypatch):
    """Import the sample modules under an active moto mock and create the
    scheduler groups, the DynamoDB table, and the SNS topic they use.

    Imported inside the mock so the module-level boto3 clients/resources in
    config.py are created against the mocked backend.
    """
    with mock_aws():
        boto3.client("scheduler", region_name=REGION).create_schedule_group(
            Name=TASK_GROUP
        )
        boto3.client("scheduler", region_name=REGION).create_schedule_group(
            Name=RECURRING_GROUP
        )
        boto3.resource("dynamodb", region_name=REGION).create_table(
            TableName=TASKS_TABLE,
            AttributeDefinitions=[{"AttributeName": "task_id", "AttributeType": "S"}],
            KeySchema=[{"AttributeName": "task_id", "KeyType": "HASH"}],
            BillingMode="PAY_PER_REQUEST",
        )
        topic_arn = boto3.client("sns", region_name=REGION).create_topic(
            Name="test-reminders"
        )["TopicArn"]
        monkeypatch.setenv("REMINDER_TOPIC_ARN", topic_arn)

        import importlib

        import src.config as config
        importlib.reload(config)
        import src.scheduler_client as sc
        importlib.reload(sc)
        import src.data_layer as data_layer
        importlib.reload(data_layer)
        import src.handler as handler
        importlib.reload(handler)
        import api.api_handlers as api
        importlib.reload(api)
        import scripts.cleanup as cleanup
        importlib.reload(cleanup)

        yield {
            "config": config, "sc": sc, "data_layer": data_layer,
            "handler": handler, "api": api, "cleanup": cleanup,
            "topic_arn": topic_arn,
        }


def _get_schedule(name, group):
    return boto3.client("scheduler", region_name=REGION).get_schedule(
        Name=name, GroupName=group
    )


# --------------------------------------------------------------------------- #
# Create
# --------------------------------------------------------------------------- #
def test_create_task_creates_schedule(modules):
    sc = modules["sc"]
    with patch.object(sc, "save_task_to_db") as save:
        sc.create_task(
            user_id="u1", task_id="t1", title="Buy milk",
            reminder_time="2026-12-01T09:00:00", timezone="Europe/Helsinki",
        )

    save.assert_called_once_with("u1", "t1", "Buy milk", "2026-12-01T09:00:00")
    sched = _get_schedule("reminder-t1", TASK_GROUP)
    assert sched["ScheduleExpression"] == "at(2026-12-01T09:00:00)"
    assert sched["ScheduleExpressionTimezone"] == "Europe/Helsinki"
    assert sched["ActionAfterCompletion"] == "DELETE"
    payload = json.loads(sched["Target"]["Input"])
    assert payload == {
        "type": "task_reminder", "user_id": "u1",
        "task_id": "t1", "title": "Buy milk",
    }


def test_create_task_without_reminder_time_skips_schedule(modules):
    sc = modules["sc"]
    with patch.object(sc, "save_task_to_db"):
        sc.create_task("u1", "t-none", "No reminder", reminder_time="", timezone="UTC")

    client = boto3.client("scheduler", region_name=REGION)
    with pytest.raises(client.exceptions.ResourceNotFoundException):
        client.get_schedule(Name="reminder-t-none", GroupName=TASK_GROUP)


# --------------------------------------------------------------------------- #
# Update
# --------------------------------------------------------------------------- #
def test_update_task_reminder_changes_time(modules):
    sc = modules["sc"]
    with patch.object(sc, "save_task_to_db"):
        sc.create_task("u1", "t2", "Call dentist", "2026-12-01T09:00:00", "UTC")

    sc.update_task_reminder("u1", "t2", "2026-12-02T15:30:00", "UTC", "Call dentist")

    sched = _get_schedule("reminder-t2", TASK_GROUP)
    assert sched["ScheduleExpression"] == "at(2026-12-02T15:30:00)"


# --------------------------------------------------------------------------- #
# Cancel
# --------------------------------------------------------------------------- #
def test_complete_task_deletes_schedule(modules):
    sc = modules["sc"]
    with patch.object(sc, "save_task_to_db"):
        sc.create_task("u1", "t3", "Submit report", "2026-12-01T09:00:00", "UTC")

    with patch.object(sc, "mark_task_complete_in_db") as mark:
        sc.complete_task("u1", "t3")

    mark.assert_called_once_with("t3")
    client = boto3.client("scheduler", region_name=REGION)
    with pytest.raises(client.exceptions.ResourceNotFoundException):
        client.get_schedule(Name="reminder-t3", GroupName=TASK_GROUP)


def test_complete_task_when_no_schedule_is_silent(modules):
    """Blog behavior: missing schedule is swallowed (already fired / never set)."""
    sc = modules["sc"]
    with patch.object(sc, "mark_task_complete_in_db") as mark:
        sc.complete_task("u1", "never-existed")  # must not raise
    mark.assert_called_once_with("never-existed")


# --------------------------------------------------------------------------- #
# Snooze (both branches)
# --------------------------------------------------------------------------- #
def test_snooze_updates_existing_schedule(modules):
    sc = modules["sc"]
    with patch.object(sc, "save_task_to_db"):
        sc.create_task("u1", "t4", "Water plants", "2026-12-01T09:00:00", "UTC")

    sc.snooze_reminder("u1", "t4", "Water plants", snooze_minutes=10, user_timezone="UTC")

    sched = _get_schedule("reminder-t4", TASK_GROUP)
    assert sched["ScheduleExpression"].startswith("at(")
    assert sched["ScheduleExpressionTimezone"] == "UTC"


def test_snooze_recreates_when_already_fired(modules):
    """If the original fired and auto-deleted, snooze creates a fresh schedule."""
    sc = modules["sc"]
    # No prior schedule exists -> update raises ResourceNotFoundException ->
    # except branch creates a new one.
    sc.snooze_reminder("u1", "t5", "Standup", snooze_minutes=5, user_timezone="UTC")

    sched = _get_schedule("reminder-t5", TASK_GROUP)
    assert sched["ScheduleExpression"].startswith("at(")
    payload = json.loads(sched["Target"]["Input"])
    assert payload["task_id"] == "t5"


# --------------------------------------------------------------------------- #
# Recurring
# --------------------------------------------------------------------------- #
def test_create_recurring_task(modules):
    sc = modules["sc"]
    sc.create_recurring_task("u1", "t6", "Take medication", "0 8 * * ? *", "UTC")

    sched = _get_schedule("recurring-t6", RECURRING_GROUP)
    assert sched["ScheduleExpression"] == "cron(0 8 * * ? *)"
    payload = json.loads(sched["Target"]["Input"])
    assert payload["type"] == "recurring_reminder"


# --------------------------------------------------------------------------- #
# Cleanup
# --------------------------------------------------------------------------- #
def test_delete_all_schedules_in_group(modules):
    sc = modules["sc"]
    cleanup = modules["cleanup"]
    with patch.object(sc, "save_task_to_db"):
        sc.create_task("u1", "c1", "A", "2026-12-01T09:00:00", "UTC")
        sc.create_task("u1", "c2", "B", "2026-12-01T10:00:00", "UTC")

    cleanup.delete_all_schedules_in_group(TASK_GROUP)

    remaining = boto3.client("scheduler", region_name=REGION).list_schedules(
        GroupName=TASK_GROUP
    )["Schedules"]
    assert remaining == []


def test_cleanup_main_deletes_named_groups(modules):
    """The operator CLI wrapper clears every group passed as an argument."""
    sc = modules["sc"]
    cleanup = modules["cleanup"]
    with patch.object(sc, "save_task_to_db"):
        sc.create_task("u1", "c3", "A", "2026-12-01T09:00:00", "UTC")
    sc.create_recurring_task("u1", "c4", "B", "0 8 * * ? *", "UTC")

    rc = cleanup.main([TASK_GROUP, RECURRING_GROUP])

    assert rc == 0
    client = boto3.client("scheduler", region_name=REGION)
    assert client.list_schedules(GroupName=TASK_GROUP)["Schedules"] == []
    assert client.list_schedules(GroupName=RECURRING_GROUP)["Schedules"] == []


def test_cleanup_main_without_args_returns_error(modules):
    cleanup = modules["cleanup"]
    assert cleanup.main([]) == 1


# --------------------------------------------------------------------------- #
# Delivery handler
# --------------------------------------------------------------------------- #
def test_handler_sends_push(modules):
    handler = modules["handler"]
    event = {"user_id": "u1", "task_id": "t1", "title": "Buy milk"}
    with patch.object(handler, "get_user", return_value={
        "notification_preference": "push", "device_token": FAKE_DEVICE_TOKEN,
    }), patch.object(handler, "get_task", return_value={"status": "pending"}), \
            patch.object(handler, "send_push_notification") as push:
        handler.lambda_handler(event, None)
    push.assert_called_once_with(device_token=FAKE_DEVICE_TOKEN, title="Reminder", body="Buy milk")


def test_handler_sends_email(modules):
    handler = modules["handler"]
    event = {"user_id": "u1", "task_id": "t1", "title": "Buy milk"}
    with patch.object(handler, "get_user", return_value={
        "notification_preference": "email", "email": "u1@example.com",
    }), patch.object(handler, "get_task", return_value={"status": "pending"}), \
            patch.object(handler, "send_email") as email:
        handler.lambda_handler(event, None)
    email.assert_called_once()
    assert email.call_args.kwargs["to"] == "u1@example.com"


def test_handler_skips_completed_task(modules):
    handler = modules["handler"]
    event = {"user_id": "u1", "task_id": "t1", "title": "Buy milk"}
    with patch.object(handler, "get_user", return_value={
        "notification_preference": "push", "device_token": FAKE_DEVICE_TOKEN,
    }), patch.object(handler, "get_task", return_value={"status": "completed"}), \
            patch.object(handler, "send_push_notification") as push:
        handler.lambda_handler(event, None)
    push.assert_not_called()


# --------------------------------------------------------------------------- #
# API integration
# --------------------------------------------------------------------------- #
def test_api_create_task(modules):
    api = modules["api"]
    sc = modules["sc"]
    event = {
        "body": json.dumps({"title": "Buy milk", "reminder_time": "2026-12-01T09:00:00"}),
        "requestContext": {"authorizer": {"claims": {"sub": "user-42"}}},
    }
    with patch.object(sc, "save_task_to_db"):
        resp = api.api_create_task(event, None)

    assert resp["statusCode"] == 201
    task_id = json.loads(resp["body"])["task_id"]
    sched = _get_schedule(f"reminder-{task_id}", TASK_GROUP)
    assert json.loads(sched["Target"]["Input"])["user_id"] == "user-42"


def test_api_delete_task(modules):
    api = modules["api"]
    sc = modules["sc"]
    event = {
        "requestContext": {"authorizer": {"claims": {"sub": "user-42"}}},
        "pathParameters": {"taskId": "t-del"},
    }
    with patch.object(sc, "mark_task_complete_in_db"):
        resp = api.api_delete_task(event, None)
    assert resp["statusCode"] == 204


# --------------------------------------------------------------------------- #
# Data layer — DynamoDB (real reference implementation, mocked by moto)
# --------------------------------------------------------------------------- #
def test_save_and_get_task_roundtrip(modules):
    dl = modules["data_layer"]
    dl.save_task_to_db("u1", "d1", "Buy milk", "2026-12-01T09:00:00")

    task = dl.get_task("d1")
    assert task["task_id"] == "d1"
    assert task["user_id"] == "u1"
    assert task["title"] == "Buy milk"
    assert task["status"] == "pending"


def test_mark_task_complete_updates_status(modules):
    dl = modules["data_layer"]
    dl.save_task_to_db("u1", "d2", "Submit report", "2026-12-01T09:00:00")

    dl.mark_task_complete_in_db("d2")

    assert dl.get_task("d2")["status"] == "completed"


def test_get_task_missing_returns_empty(modules):
    dl = modules["data_layer"]
    assert dl.get_task("does-not-exist") == {}


# --------------------------------------------------------------------------- #
# Data layer — SNS delivery (real reference implementation, mocked by moto)
# --------------------------------------------------------------------------- #
def test_send_email_publishes_to_sns(modules):
    dl = modules["data_layer"]
    # moto has no inbox to read, so assert the publish call succeeds and returns
    # a MessageId (moto validates the TopicArn exists).
    with patch.object(dl.sns, "publish", wraps=dl.sns.publish) as pub:
        dl.send_email(to="u1@example.com", subject="Reminder", body="Buy milk")
    pub.assert_called_once()
    assert pub.call_args.kwargs["TopicArn"] == modules["topic_arn"]
    assert pub.call_args.kwargs["Message"] == "Buy milk"


# --------------------------------------------------------------------------- #
# Deployable target Lambda (functions/reminder_target/app.py)
# --------------------------------------------------------------------------- #
def _load_target_app():
    import importlib
    import sys

    sys.path.insert(0, "functions/reminder_target")
    import app as target_app
    importlib.reload(target_app)
    return target_app


def test_target_publishes_reminder(modules):
    app = _load_target_app()
    # seed a pending task
    modules["data_layer"].save_task_to_db("u1", "target-1", "Buy milk", "")

    with patch.object(app.sns, "publish", wraps=app.sns.publish) as pub:
        result = app.lambda_handler(
            {"type": "task_reminder", "user_id": "u1", "task_id": "target-1", "title": "Buy milk"},
            None,
        )
    assert result["delivered"] is True
    pub.assert_called_once()
    assert "Buy milk" in pub.call_args.kwargs["Message"]


def test_target_skips_completed_task(modules):
    app = _load_target_app()
    modules["data_layer"].save_task_to_db("u1", "target-2", "Buy milk", "")
    modules["data_layer"].mark_task_complete_in_db("target-2")

    with patch.object(app.sns, "publish") as pub:
        result = app.lambda_handler(
            {"type": "task_reminder", "user_id": "u1", "task_id": "target-2", "title": "Buy milk"},
            None,
        )
    assert result["delivered"] is False
    pub.assert_not_called()
