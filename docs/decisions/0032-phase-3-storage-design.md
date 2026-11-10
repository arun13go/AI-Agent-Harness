# ADR 0032: Phase 3 Storage Design

- **Status:** Accepted (design; nothing built or deployed yet)
- **Date:** 10 November 2026
- **Builds on:** [ADR 0005](0005-cosmos-db-reference-run-state.md), [ADR 0006](0006-cosmos-db-reference-memory.md), [ADR 0029](0029-defer-episodic-memory-and-summarize-compaction.md), [ADR 0031](0031-lightweight-by-design.md)
- **Changes:** ADR 0005's hierarchical partition key and Blob artifacts are replaced by the choices below.

## Decision

- **One new service: Azure Cosmos DB for NoSQL.** No Blob Storage, cache or second database.
- **One account and database, two containers.** `records` holds proposals, checkpoints, artifacts,
  run state, approvals and idempotency records (memory joins it later, ADR 0029). `audit` holds run
  events. They are separate so `audit` can have a create-and-read-only role.
- **Partition key `/tenantScope`.** ADR 0005's tenant/subject/run key cannot serve
  `get_request(tenant_scope, approval_id)`, which has no subject or run. A tenant partition keeps
  every lookup a point read.
- **Structural scoping.** A document's `id` is the SHA-256 of kind, subject scope and record id, so
  a read with the wrong subject finds nothing. Approvals hash tenant and approval id only, and
  reads then check identity as the in-memory store does. The typed record is stored whole as
  `payload`, with `kind`, `tenantScope` and, for records that expire, `ttl`.
- **Port to operation.**

  | Port | Operations |
  |---|---|
  | Proposal, checkpoint, artifact | create (conflict raises the existing error); point read |
  | Run state | read, then replace with `If-Match` ETag; a mismatch raises `ConcurrencyError` |
  | Idempotency | claim is create (conflict returns false); complete is read, then replace with `If-Match` |
  | Approval | request is create; decision is a second document, so a repeat conflicts |
  | Audit | create with id `run:sequence`, so a duplicate sequence conflicts; unique key on `/eventId` rejects duplicate events |

  `append_next` takes the next sequence from a count of the run's events (a query in the
  partition), under a per-run lock, and retries once on conflict. It does not cache sequences.
  A duplicate event id conflicts on the unique key; the adapter reads the sequence slot to tell it
  from a taken sequence.
- **One backend port, four methods** (create, read, replace with ETag, find in a partition).
  `delete` joins it with the memory store (ADR 0029), which is the first thing that deletes. One
  module, `cosmos_stores.py`, builds every store on it, so Cosmos details live in one place. The
  stored JSON is built with pydantic's `TypeAdapter`, so there is no hand-written codec. Use
  `azure-cosmos` if it can be locked and installed; otherwise a small REST client, as for Search
  (ADR 0023). The in-memory stores stay as they are.
- **Expiry and deletion.** `ttl` reclaims space only. Code still enforces `expires_at` on read.
  Records expire a fixed grace after their own expiry; audit retention is a deployment setting
  (default 90 days, to be confirmed). Tenant deletion queries the partition and deletes in
  bounded batches. No cross-partition delete.
- **Access.** Entra only (`disableLocalAuth`). The agent identity gets the built-in data
  contributor role on `records` and a custom create-and-read role on `audit`.
- **Opt-in infrastructure.** `infra/cosmos.bicep`, wired into `main.bicep` as `cosmosSku`
  (`none` by default, `free` or `serverless`) and `cosmosAuditRetentionDays` (default 90, to be
  confirmed). It shares the optional resource group with search. `free` gives the one free-tier
  account per subscription at 400 shared RU/s; `serverless` has no throughput setting. Payload is
  excluded from the index. `azd up` is unchanged, and outputs are `AZURE_COSMOS_ENDPOINT` and
  `AZURE_COSMOS_NAME`.
- **Data roles.** The template defines `harness-audit-append` (read metadata, create, read, query;
  no replace, upsert or delete). Assignments are made outside the template, as for Search: the
  built-in Data Contributor on the account for the operator, and `harness-audit-append` on the
  audit container for the agent identity. Cosmos RBAC has no deny rule, so the role lists only what
  is allowed.
- **REST backend.** `cosmos_rest.py` sends `type=aad&ver=1.0&sig=<token>` with scope
  `https://<account>.documents.azure.com/.default`, a tenant partition key header and the ETag in
  `If-Match`. It retries 429, 503 and a rejected token through the helper shared with Search
  (`http_support.py`). Reads and queries also retry network failures. Creates and replaces do not,
  because a retried write that had already succeeded would look like a conflict.
- **Conformance.** One shared suite (isolation, concurrency, duplicate delivery, expiry, deletion,
  append-only order) runs over the in-memory stores, then a fake backend, then live Cosmos.

## Not built

Blob artifacts (an item over 2 MB is refused), Azure cache, memory store, change feed or analytics,
hierarchical partition keys, multi-region and private networking (production overlay guidance).

## Limits and to verify at build

- A tenant partition holds at most 20 GB. Move to a hierarchical key only if a tenant nears it.
- Confirm the custom `audit` role actions, free-tier limits and unique-key scope against current
  Cosmos documentation before writing Bicep.
- Nothing is live: cost, RBAC and behavior under real conflicts are unproven.
