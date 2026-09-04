"""Application data-access and notification layer.

The blog presents these as comments ("your application's data access layer").
This sample provides a **deployable reference implementation** backed by Amazon
DynamoDB (task storage) and Amazon SNS (reminder delivery) so you can deploy the
stack and watch a reminder actually arrive.

This is one concrete implementation, not the only one — swap DynamoDB for your
own store and SNS for your own notification channel (push provider, Amazon SES,
etc.). The two per-user helpers at the bottom (`get_user`,
`send_push_notification`) are intentionally left as stubs, because per-user
channel routing depends on your user model.
"""
from src.config import REMINDER_TOPIC_ARN, TASKS_TABLE, dynamodb, sns


# --------------------------------------------------------------------------- #
# Task storage — Amazon DynamoDB (deployable reference implementation)
# --------------------------------------------------------------------------- #
def save_task_to_db(user_id: str, task_id: str, title: str, reminder_time: str) -> None:
    """Persist a new task to DynamoDB."""
    dynamodb.Table(TASKS_TABLE).put_item(
        Item={
            "task_id": task_id,
            "user_id": user_id,
            "title": title,
            "reminder_time": reminder_time or "",
            "status": "pending",
        }
    )


def mark_task_complete_in_db(task_id: str) -> None:
    """Mark a task complete in DynamoDB."""
    dynamodb.Table(TASKS_TABLE).update_item(
        Key={"task_id": task_id},
        UpdateExpression="SET #s = :completed",
        ExpressionAttributeNames={"#s": "status"},
        ExpressionAttributeValues={":completed": "completed"},
    )


def get_task(task_id: str) -> dict:
    """Look up a task record in DynamoDB. Returns {} if not found."""
    return dynamodb.Table(TASKS_TABLE).get_item(Key={"task_id": task_id}).get("Item", {})


# --------------------------------------------------------------------------- #
# Notification delivery — Amazon SNS (deployable reference implementation)
# --------------------------------------------------------------------------- #
def send_email(to: str, subject: str, body: str) -> None:
    """Deliver a reminder by publishing to an SNS topic.

    Named `send_email` to match the blog. In this reference implementation it
    publishes to SNS; subscribe an email or SMS endpoint to the topic to receive
    it. Replace with Amazon SES or your own email provider for direct email.
    """
    sns.publish(TopicArn=REMINDER_TOPIC_ARN, Subject=subject, Message=body)


# --------------------------------------------------------------------------- #
# Per-user helpers — stubs (implement against your own user model)
# --------------------------------------------------------------------------- #
def get_user(user_id: str) -> dict:
    """Look up a user record (channel preference, email, device token).

    Left as a stub: per-user channel routing depends on your user model. Return
    a dict like {"notification_preference": "email", "email": "..."}.
    """
    raise NotImplementedError("Replace with your application's user store")


def send_push_notification(device_token: str, title: str, body: str) -> None:
    """Deliver a push notification. Replace with your push provider (e.g. a
    mobile push service). Left as a stub in this sample."""
    raise NotImplementedError("Replace with your push notification provider")
