# Backup and recovery

## Backup schedule

Databases are backed up every night at 02:00 UTC. Backups are kept for thirty-five days and are
stored in a second region.

## Recovery objectives

The recovery point objective is twenty-four hours and the recovery time objective is four hours for
customer-facing systems.

## Restore testing

The platform team performs a full restore test every quarter and records the result in the
operations log.
