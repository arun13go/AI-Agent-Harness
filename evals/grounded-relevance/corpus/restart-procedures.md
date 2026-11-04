# Restart procedures

## API service

Drain traffic from the load balancer before restarting the API service. Wait until active
connections reach zero, then restart the process with the service manager. After the restart,
confirm that the health endpoint returns healthy for five consecutive checks.

## Background worker

Stop the background worker gracefully so that in-flight jobs finish. If a job is still running after
ten minutes, the on-call engineer may terminate it and requeue the job. Restart the worker only
after the queue depth is below one thousand messages.

## Message queue

Never restart the message queue during business hours. Schedule queue restarts in the Sunday
maintenance window and notify the platform channel one hour in advance.
