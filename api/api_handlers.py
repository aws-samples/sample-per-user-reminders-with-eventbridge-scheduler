"""API Gateway + Lambda entry points that drive the scheduling operations.

These handlers sit behind Amazon API Gateway and call the scheduling functions
in src/scheduler_client.py as part of normal CRUD operations.
"""
import json
import uuid

from src.scheduler_client import complete_task, create_task


def generate_id():
    """Generate a unique task ID."""
    return str(uuid.uuid4())


def api_create_task(event, context):
    body = json.loads(event["body"])
    user_id = event["requestContext"]["authorizer"]["claims"]["sub"]
    task_id = generate_id()

    create_task(
        user_id=user_id,
        task_id=task_id,
        title=body["title"],
        reminder_time=body.get("reminder_time"),
        timezone=body.get("timezone", "UTC"),
    )

    return {
        "statusCode": 201,
        "body": json.dumps({"task_id": task_id}),
    }


def api_delete_task(event, context):
    user_id = event["requestContext"]["authorizer"]["claims"]["sub"]
    task_id = event["pathParameters"]["taskId"]

    complete_task(user_id, task_id)

    return {"statusCode": 204}
