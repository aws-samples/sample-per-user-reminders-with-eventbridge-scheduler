"""Target Lambda function invoked by EventBridge Scheduler when a reminder fires.

This is the deployed function referenced by REMINDER_FUNCTION_ARN. It receives
the schedule's Input payload as `event` and delivers the notification through
the user's preferred channel.
"""
from src.data_layer import get_task, get_user, send_email, send_push_notification


def lambda_handler(event, context):
    """Deliver a reminder notification to the user."""
    # These helpers represent your application's data access layer
    user = get_user(event["user_id"])
    task = get_task(event["task_id"])

    if task["status"] == "completed":
        return

    if user["notification_preference"] == "push":
        send_push_notification(
            device_token=user["device_token"],
            title="Reminder",
            body=event["title"],
        )
    elif user["notification_preference"] == "email":
        send_email(
            to=user["email"],
            subject=f"Reminder: {event['title']}",
            body=f"This is your reminder for: {event['title']}",
        )
