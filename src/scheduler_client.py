"""Client-side scheduling operations for per-user reminders.

This module runs wherever your application creates and manages tasks (for
example, behind your API). It creates, updates, snoozes, and cancels
Amazon EventBridge Scheduler schedules. The target Lambda that fires when a
schedule is due lives in src/handler.py. One-time teardown of leftover
schedules lives in scripts/cleanup.py.

Consolidated from blog snippets: create, update, cancel, snooze, recurring.
"""
import json
from datetime import datetime, timedelta, timezone

from src.config import (
    FUNCTION_ARN,
    RECURRING_TASKS_GROUP,
    ROLE_ARN,
    TASK_REMINDERS_GROUP,
    scheduler,
)
from src.data_layer import mark_task_complete_in_db, save_task_to_db


def create_task(user_id: str, task_id: str, title: str,
                reminder_time: str, timezone: str):
    """Create a task and schedule its reminder.

    reminder_time uses the at() format: yyyy-mm-ddThh:mm:ss
    """
    # Your application's data access layer
    save_task_to_db(user_id, task_id, title, reminder_time)

    if reminder_time:
        scheduler.create_schedule(
            Name=f"reminder-{task_id}",
            GroupName=TASK_REMINDERS_GROUP,
            ScheduleExpression=f"at({reminder_time})",
            ScheduleExpressionTimezone=timezone,
            FlexibleTimeWindow={"Mode": "OFF"},
            ActionAfterCompletion="DELETE",
            Target={
                "Arn": FUNCTION_ARN,
                "RoleArn": ROLE_ARN,
                "Input": json.dumps({
                    "type": "task_reminder",
                    "user_id": user_id,
                    "task_id": task_id,
                    "title": title,
                }),
            },
        )


def update_task_reminder(user_id: str, task_id: str,
                         new_reminder_time: str, timezone: str, title: str):
    """Update the reminder time for an existing task.

    update_schedule replaces the entire schedule definition, so every field to
    keep must be included; omitted optional fields revert to their defaults.
    """
    scheduler.update_schedule(
        Name=f"reminder-{task_id}",
        GroupName=TASK_REMINDERS_GROUP,
        ScheduleExpression=f"at({new_reminder_time})",
        ScheduleExpressionTimezone=timezone,
        FlexibleTimeWindow={"Mode": "OFF"},
        ActionAfterCompletion="DELETE",
        Target={
            "Arn": FUNCTION_ARN,
            "RoleArn": ROLE_ARN,
            "Input": json.dumps({
                "type": "task_reminder",
                "user_id": user_id,
                "task_id": task_id,
                "title": title,
            }),
        },
    )


def complete_task(user_id: str, task_id: str):
    """Mark task complete and cancel any pending reminder."""
    # Your application's data access layer
    mark_task_complete_in_db(task_id)

    try:
        scheduler.delete_schedule(
            Name=f"reminder-{task_id}",
            GroupName=TASK_REMINDERS_GROUP,
        )
    except scheduler.exceptions.ResourceNotFoundException:
        pass  # Reminder already fired or was never set


def snooze_reminder(user_id: str, task_id: str, title: str,
                    snooze_minutes: int, user_timezone: str):
    """Snooze a reminder by rescheduling it to fire after a delay.

    Snoozing is effectively a reschedule. If the original schedule already fired
    (and auto-deleted via ActionAfterCompletion: DELETE), the update raises
    ResourceNotFoundException and we create a new schedule instead.
    """
    new_time = datetime.now(timezone.utc) + timedelta(minutes=snooze_minutes)
    schedule_expression = f"at({new_time.strftime('%Y-%m-%dT%H:%M:%S')})"

    target = {
        "Arn": FUNCTION_ARN,
        "RoleArn": ROLE_ARN,
        "Input": json.dumps({
            "type": "task_reminder",
            "user_id": user_id,
            "task_id": task_id,
            "title": title,
        }),
    }

    try:
        scheduler.update_schedule(
            Name=f"reminder-{task_id}",
            GroupName=TASK_REMINDERS_GROUP,
            ScheduleExpression=schedule_expression,
            ScheduleExpressionTimezone="UTC",
            FlexibleTimeWindow={"Mode": "OFF"},
            ActionAfterCompletion="DELETE",
            Target=target,
        )
    except scheduler.exceptions.ResourceNotFoundException:
        # Original reminder already fired; create a new schedule
        scheduler.create_schedule(
            Name=f"reminder-{task_id}",
            GroupName=TASK_REMINDERS_GROUP,
            ScheduleExpression=schedule_expression,
            ScheduleExpressionTimezone="UTC",
            FlexibleTimeWindow={"Mode": "OFF"},
            ActionAfterCompletion="DELETE",
            Target=target,
        )


def create_recurring_task(user_id: str, task_id: str, title: str,
                          cron_expression: str, timezone: str):
    """Create a recurring reminder using a cron expression."""
    scheduler.create_schedule(
        Name=f"recurring-{task_id}",
        GroupName=RECURRING_TASKS_GROUP,
        ScheduleExpression=f"cron({cron_expression})",
        ScheduleExpressionTimezone=timezone,
        FlexibleTimeWindow={"Mode": "OFF"},
        Target={
            "Arn": FUNCTION_ARN,
            "RoleArn": ROLE_ARN,
            "Input": json.dumps({
                "type": "recurring_reminder",
                "user_id": user_id,
                "task_id": task_id,
                "title": title,
            }),
        },
    )


# Cron examples:
# "Take medication" -- every day at 8 AM:        "0 8 * * ? *"
# "Weekly report"   -- every Friday at 4 PM:     "0 16 ? * FRI *"
# "Monthly review"  -- 1st of each month, 9 AM:  "0 9 1 * ? *"
