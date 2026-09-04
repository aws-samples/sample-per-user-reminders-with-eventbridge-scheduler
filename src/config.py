"""Shared configuration and AWS clients.

The blog snippets hardcode ROLE_ARN and FUNCTION_ARN for readability. In the
runnable sample we read them (and the DynamoDB table / SNS topic added for the
deployable end-to-end demo) from environment variables so no AWS account IDs are
committed and the same code runs unchanged in dev, test, and production.
"""
import os

import boto3

# One shared EventBridge Scheduler client, reused across all operations.
scheduler = boto3.client("scheduler")

# DynamoDB resource and SNS client for the deployable end-to-end demo: tasks are
# stored in a DynamoDB table and reminders are published to an SNS topic.
dynamodb = boto3.resource("dynamodb")
sns = boto3.client("sns")

# IAM role that grants EventBridge Scheduler permission to invoke the target
# Lambda function. See "Setting up EventBridge Scheduler" in the blog post.
ROLE_ARN = os.environ.get(
    "SCHEDULER_ROLE_ARN",
    "arn:aws:iam::123456789012:role/SchedulerToLambdaRole",
)

# ARN of the Lambda function that delivers the reminder (see src/handler.py).
FUNCTION_ARN = os.environ.get(
    "REMINDER_FUNCTION_ARN",
    "arn:aws:lambda:us-east-1:123456789012:function:deliver-reminder",
)

# DynamoDB table that stores tasks, and the SNS topic reminders publish to.
# Both are created by template.yaml; their names/ARNs come from the stack
# outputs (set as environment variables).
TASKS_TABLE = os.environ.get("TASKS_TABLE", "task-reminders-tasks")
REMINDER_TOPIC_ARN = os.environ.get("REMINDER_TOPIC_ARN", "")

# Schedule groups used to organize schedules by use case.
TASK_REMINDERS_GROUP = "task-reminders"
RECURRING_TASKS_GROUP = "recurring-tasks"
