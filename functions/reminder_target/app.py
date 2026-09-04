"""Deployable target Lambda for the SAM stack (end-to-end demo).

EventBridge Scheduler invokes this function when a reminder fires. It reads the
task from DynamoDB, skips it if the user already completed it, and publishes the
reminder to an SNS topic — so after deploying and subscribing to the topic, you
receive a real reminder.

This is a self-contained handler (its own boto3 clients, no repo imports) so it
packages cleanly as a Lambda. It mirrors the logic in src/handler.py, which is
the blog's per-user version. Swap SNS for your own notification channel as
needed.

Environment variables (set by template.yaml):
  TASKS_TABLE        - DynamoDB table holding tasks
  REMINDER_TOPIC_ARN - SNS topic to publish reminders to
"""
import json
import logging
import os

import boto3

logger = logging.getLogger()
logger.setLevel(logging.INFO)

TASKS_TABLE = os.environ["TASKS_TABLE"]
REMINDER_TOPIC_ARN = os.environ["REMINDER_TOPIC_ARN"]

dynamodb = boto3.resource("dynamodb")
sns = boto3.client("sns")


def lambda_handler(event, context):
    """Deliver a reminder: read the task, skip if completed, publish to SNS."""
    logger.info("REMINDER FIRED: %s", json.dumps(event))

    task_id = event["task_id"]
    task = dynamodb.Table(TASKS_TABLE).get_item(Key={"task_id": task_id}).get("Item")

    # Skip if the task was completed (or deleted) before the reminder fired.
    if task and task.get("status") == "completed":
        logger.info("Task %s already completed; skipping notification", task_id)
        return {"delivered": False, "reason": "completed"}

    title = event.get("title") or (task or {}).get("title", "your task")
    sns.publish(
        TopicArn=REMINDER_TOPIC_ARN,
        Subject="Reminder",
        Message=f"This is your reminder for: {title}",
    )
    logger.info("Published reminder for task %s to SNS", task_id)
    return {"delivered": True, "task_id": task_id}
