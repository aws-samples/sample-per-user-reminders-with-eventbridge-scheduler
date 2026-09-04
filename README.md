# Per-user reminders with Amazon EventBridge Scheduler

Sample code for the AWS blog post **"Implement per-user reminders with Amazon
EventBridge Scheduler."** It shows how to create one schedule per reminder
instead of polling a database, covering reminder creation, updates,
cancellation, snooze, and recurring tasks.

> **This is sample code for demonstration and educational purposes.** It has not
> undergone production hardening and is not intended for production use as-is.
> The repo includes a deployable end-to-end example backed by Amazon DynamoDB
> (task storage) and Amazon SNS (reminder delivery) so you can deploy and watch a
> reminder actually arrive. These are one reference implementation — swap in your
> own data store and notification channel. See
> [What to do after deploying](#what-to-do-after-deploying).

## The problem

Many applications need to "do X at time Y" per user — remind me about this task,
notify me before my trial ends, follow up after a meeting. The common approach
is a batch poller: a Lambda runs every few minutes, scans the database for items
that are now due, and sends notifications. That works at small scale but its
cost scales with *total* items (you scan the whole table to find the few due
now), it runs continuously even when nothing is due, and mid-batch failures
require idempotency and checkpointing.

## The solution

Create **one schedule per reminder** with Amazon EventBridge Scheduler and let
the service invoke your target Lambda at the exact time. There is no polling
loop and no idle compute — you pay only when a schedule fires. One-time
schedules auto-delete after firing (`ActionAfterCompletion: DELETE`), and the
service supports time zones and cron-based recurring schedules.

## Architecture

Your application API creates, updates, and deletes schedules in Amazon
EventBridge Scheduler as part of normal CRUD operations. When a schedule fires,
it invokes a target AWS Lambda function that delivers the reminder through the
user's preferred channel (push or email). One-time schedules auto-delete after
firing via `ActionAfterCompletion: DELETE`.

## Repository layout

| Path | Runs where | Blog snippet |
|------|-----------|--------------|
| `src/config.py` | client + Lambda | shared client / ARNs / groups |
| `src/scheduler_client.py` | your app / API backend | Create, Update, Cancel, Snooze, Recurring |
| `src/handler.py` | deployed Lambda (schedule target) | Delivering the notification |
| `api/api_handlers.py` | API Gateway + Lambda | Integrating with your API layer |
| `scripts/cleanup.py` | operator, one-time teardown | Clean up |
| `src/data_layer.py` | your app | DynamoDB task storage + SNS delivery (reference impl); `get_user`/push stubs |
| `functions/reminder_target/app.py` | deployed Lambda (SAM) | target: reads task from DynamoDB, publishes to SNS |
| `template.yaml` | AWS (CloudFormation/SAM) | infrastructure: Lambda + role + groups + DynamoDB + SNS |
| `tests/test_scheduler_client.py` | local | mocked tests (moto) |

The code falls into **3 logical divisions**: (1) scheduling operations
(`scheduler_client.py`, driven by `api_handlers.py`), (2) the delivery Lambda
(`handler.py` / the deployable `functions/reminder_target/app.py`), and (3) the
operator-run cleanup utility (`scripts/cleanup.py`).

> **Reference implementation vs. stubs.** `src/data_layer.py` implements task
> storage on **DynamoDB** and reminder delivery on **SNS** so the stack works
> end-to-end. These are illustrative — swap them for your own store and channel.
> The per-user helpers `get_user` and `send_push_notification` remain stubs
> (they raise `NotImplementedError`), because per-user channel routing depends on
> your user model.

## Prerequisites

- An AWS account
- AWS CLI installed and configured
- [AWS SAM CLI](https://docs.aws.amazon.com/serverless-application-model/latest/developerguide/install-sam-cli.html)
- Python 3.9+ with boto3

## Deploy the infrastructure with AWS SAM

`template.yaml` provisions the end-to-end demo: the target Lambda, the two
schedule groups, a least-privilege IAM role that lets EventBridge Scheduler
invoke only that function, a DynamoDB table for tasks, and an SNS topic for
reminder delivery.

```bash
sam build
sam deploy --guided        # first-time deploy; prompts for stack name, region, etc.
```

On the first deploy, `--guided` walks you through the settings (stack name,
Region, IAM role creation) and offers to save them to a `samconfig.toml` so
later deploys can use plain `sam deploy`. Answer **Y** to "Allow SAM CLI IAM role
creation" — the stack creates the scheduler execution role.

After deploy, note the stack outputs and point the scheduling code at them:

```bash
export SCHEDULER_ROLE_ARN="<SchedulerRoleArn output>"
export REMINDER_FUNCTION_ARN="<ReminderFunctionArn output>"
export TASKS_TABLE="<TasksTableName output>"
export REMINDER_TOPIC_ARN="<ReminderTopicArn output>"
export AWS_DEFAULT_REGION="us-east-2"    # match your deploy region
```

Subscribe an endpoint to the SNS topic so you actually receive reminders:

```bash
aws sns subscribe --topic-arn "$REMINDER_TOPIC_ARN" \
  --protocol email --notification-endpoint you@example.com
# then confirm via the email that SNS sends you
```

> **Scope of the deployable stack.** The stack deploys a working end-to-end demo:
> EventBridge Scheduler invokes the target Lambda, which reads the task from
> **DynamoDB** and publishes the reminder to **SNS**. The per-user delivery logic
> in `src/handler.py` (channel routing via `get_user`) stays illustrative — extend
> it with your own user model to route push vs. email per user.

## How the trigger works: groups vs. schedules

A common question: the template creates schedule **groups** but no schedule, and
there is no Lambda "trigger" defined in `template.yaml`. That is intentional.

- A **schedule group** is a container (like a folder) that organizes schedules.
  It does not invoke anything. There are only two of them, so they are static
  infrastructure and live in the template.
- A **schedule** is the timer that fires and invokes the target. In this pattern
  there is **one schedule per reminder** — potentially millions — each created
  at runtime by your application (`scheduler_client.create_task`, etc.) inside a
  group.
- The **trigger** is the `Target` on each schedule (`{"Arn": <lambda>,
  "RoleArn": <role>}`), set when the schedule is created — not at deploy time.

So the template provides the durable pieces (the groups plus the IAM role that
*allows* invocation), and the application code creates the ephemeral schedules
that actually invoke the Lambda. One-time schedules auto-delete after firing.

## Validate the deployment

Confirm the core mechanism — a schedule fires and invokes the Lambda — directly
against your deployed stack. Substitute the ARNs from your stack outputs.

```bash
# 1. A one-time schedule ~2 minutes out (macOS date syntax; on Linux use
#    date -u -d '+2 minutes' +%Y-%m-%dT%H:%M:%S)
FIRE=$(date -u -v+2M +%Y-%m-%dT%H:%M:%S); echo "FIRE=$FIRE"

aws scheduler create-schedule \
  --name reminder-validate-1 \
  --group-name task-reminders \
  --schedule-expression "at($FIRE)" \
  --schedule-expression-timezone "UTC" \
  --flexible-time-window '{"Mode":"OFF"}' \
  --action-after-completion "DELETE" \
  --target '{
    "Arn":"<ReminderFunctionArn>",
    "RoleArn":"<SchedulerRoleArn>",
    "Input":"{\"type\":\"task_reminder\",\"user_id\":\"user-1\",\"task_id\":\"validate-1\",\"title\":\"Buy milk\"}"
  }'

# 2. After the fire time (+ up to ~1 min dispatch), check the Lambda logs.
#    The function name is auto-generated; read it from the stack output.
FUNCTION_NAME=$(aws cloudformation describe-stacks --stack-name <your-stack> \
  --query "Stacks[0].Outputs[?OutputKey=='ReminderFunctionName'].OutputValue" --output text)
aws logs tail "/aws/lambda/$FUNCTION_NAME" --since 10m
# Expect: REMINDER FIRED: {...}  and  "Published reminder for task validate-1 to SNS"
# If you subscribed an endpoint to the topic, you also receive the reminder.

# 3. Confirm the one-time schedule auto-deleted (ActionAfterCompletion: DELETE)
aws scheduler get-schedule --name reminder-validate-1 --group-name task-reminders
# Expect: ResourceNotFoundException
```

If the log line appears, the SNS message is delivered, and the schedule is gone
afterward, the deployment is working exactly as the blog describes.

> The target Lambda looks the task up in DynamoDB by `task_id` and skips
> delivery if its `status` is `completed`. For this raw-CLI check the task row
> need not exist (it falls back to the payload `title`); to exercise the
> DynamoDB path too, `put-item` a task with `task_id=validate-1` first, or drive
> it through `scheduler_client.create_task`, which writes the task for you.

## What to do after deploying

The stack deploys a **working end-to-end demo** — EventBridge Scheduler, the
target Lambda, DynamoDB, and SNS all wired together. It is still a **starting
point**: the DynamoDB/SNS pieces are a reference implementation to adapt, and a
production reminder feature needs a few more steps:

1. **Wire the code to the stack** — set `REMINDER_FUNCTION_ARN`,
   `SCHEDULER_ROLE_ARN`, `TASKS_TABLE`, and `REMINDER_TOPIC_ARN` (from the
   outputs) in your app's configuration.
2. **Swap in your own data store / notification channel (optional)** — the
   sample uses DynamoDB (`src/data_layer.py`) and SNS. Replace them with RDS,
   Amazon SES, a mobile push provider, etc. if they fit your app better; update
   the matching resources in `template.yaml`.
3. **Implement per-user routing** — `get_user` and `send_push_notification` in
   `src/data_layer.py` are still stubs (they raise `NotImplementedError`).
   Implement them against your user model to route push vs. email per user, as
   `src/handler.py` illustrates.
4. **Call the scheduling functions** from your application — `create_task`,
   `update_task_reminder`, `complete_task`, `snooze_reminder`,
   `create_recurring_task` (`api/api_handlers.py` shows the API Gateway pattern).
5. **Add API Gateway + auth** if you want an HTTP front door (the diagram's API
   layer); `api/api_handlers.py` is the handler pattern, not wired in the template.
6. **Test and clean up** — see the sections above.

### What this sample includes vs. what you add

| Component | In this sample | You provide |
|-----------|:--------------:|:-----------:|
| EventBridge Scheduler groups | ✅ (template) | |
| Target Lambda + IAM role | ✅ (template) | |
| Scheduling logic (create/update/cancel/snooze/recurring) | ✅ (code) | |
| **DynamoDB** table + task storage | ✅ (template + code) | adapt to your store |
| **Amazon SNS** topic + delivery | ✅ (template + code) | swap for your channel |
| Per-user channel routing (`get_user`, push) | stubs | ✅ your user model |
| API Gateway + auth | pattern only (`api_handlers.py`) | ✅ template + wiring |

The DynamoDB and SNS pieces are a deployable **reference implementation** so the
sample works out of the box; the blog's focus remains the EventBridge Scheduler
pattern. Swap the data store and notification channel to fit your application.

## Running the tests

The tests mock EventBridge Scheduler with [moto](https://github.com/getmoto/moto),
so they touch **no** real AWS resources or credentials.

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
pytest -q
```

## Clean up

A schedule group cannot be deleted while it still contains schedules, so clear
any remaining schedules first, then tear down the stack:

```bash
# 1. Delete any remaining schedules in your groups (operator-run utility)
python -m scripts.cleanup task-reminders recurring-tasks

# 2. Delete the SAM stack (removes the Lambda, IAM role, schedule groups,
#    the DynamoDB table, and the SNS topic — including its subscriptions)
sam delete
```

One-time schedules that already fired are removed automatically by
`ActionAfterCompletion: DELETE`, so `scripts/cleanup.py` mainly clears recurring
schedules and any still-pending one-time ones. `sam delete` removes the DynamoDB
table (and its data) and the SNS topic with its subscriptions.

## Security and compliance

This is illustrative sample code. Before production use:

- **Validate input** on `reminder_time` and `cron_expression` before passing
  them into schedule expressions.
- **Enforce ownership** — confirm a `task_id` belongs to the authenticated
  `user_id` in `complete_task` / `update_task_reminder` before acting.
- **Keep IAM least-privilege** — the SAM template's scheduler role is already
  scoped to invoking only the target function; scope any DynamoDB/SNS
  permissions you add the same way.
- **Protect PII** — the task title and user details flow through the schedule
  payload and logs; review what you store and log.

## Conclusion

Instead of polling a database on a fixed interval, you create one schedule per
reminder and let Amazon EventBridge Scheduler invoke your Lambda at the right
time. This eliminates idle compute, simplifies failure handling, and scales with
your user base. The same pattern applies to snooze, recurring tasks, and other
time-based features.

- [Amazon EventBridge Scheduler User Guide](https://docs.aws.amazon.com/scheduler/latest/UserGuide/what-is-scheduler.html)
- [Setting up the execution role](https://docs.aws.amazon.com/scheduler/latest/UserGuide/setting-up.html)
- [Serverless Land](https://serverlessland.com/)
