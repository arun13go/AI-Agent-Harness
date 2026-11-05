# ADR 0025: Corpus Synchronization into Azure AI Search

- **Status:** Accepted
- **Date:** 5 November 2026
- **Builds on:** [ADR 0017](0017-deterministic-chunk-identity.md), [ADR 0020](0020-local-lexical-retrieval-and-corpus-loading.md), [ADR 0023](0023-azure-ai-search-retrieval-adapter.md)

## Purpose and scope

Until now the only way to fill the Azure index was a one-off loader for the evaluation corpus.
This ADR adds a general, repeatable ingestion path: make an index corpus hold exactly the chunks a
folder produces. It covers Markdown folders, since extractors for Word and PDF will feed the same
step later. It does not add scheduling, change detection by file watcher, or per-document
deletion by name.

## Decision

- **A sync owns one `(tenant, corpus)` pair.** Chunks carry a `corpusId`, and a sync reads,
  writes and deletes only chunks of its own tenant and corpus. Folders with different owners or
  labels can share a tenant without deleting each other. The corpus ID is a validated name (1 to
  64 lowercase letters, digits or dashes).
- **Exact set difference.** Chunk identifiers are deterministic and include the source version
  ([ADR 0017](0017-deterministic-chunk-identity.md)), so the sync compares the identifiers a
  folder should produce with those the index holds. An unchanged file adds and removes nothing. An
  edited file's old chunks are removed and its new chunks added. A removed file's chunks are
  removed. A changed label set rewrites the same chunks with the new labels.
- **Freshness is renewed on every sync.** All current chunks are rewritten, which restarts their
  freshness window, because running a sync asserts the sources are still current. The report
  counts these as `refreshed`. Skipping unchanged chunks would let evidence go stale while the
  operator believes it was confirmed.
- **Write before delete.** New and current chunks are written first and stale ones deleted after,
  so a failure leaves extra evidence rather than missing evidence. A later sync repairs it.
- **Never overwrite another corpus.** Chunk identifiers do not include the corpus, so identical
  files at the same path in two corpora of one tenant would collide. The sync looks up every
  chunk it is about to write and refuses if one is owned by another corpus or by none, rather than
  taking it over and later deleting another corpus's evidence. The remedy is a different tenant or
  distinct source paths.
- **Deletion guards.** A sync that would remove every chunk of a corpus, or more than half of a
  corpus of ten or more chunks, is refused unless confirmed (`--allow-large-delete`; the empty
  case is available only to programmatic callers, since an empty folder is already an error).
  A wrong folder or a mistaken mount is the failure these guard against.
- **Dry run.** `--dry-run` lists the index and reports the counts, writes nothing and does not
  create the index.
- **Counts only.** Reports contain numbers, never source names or content.
- **Index changes.** The index gains a filterable `corpusId`, and the key is now filterable and
  sortable so ingestion can walk a corpus in key order with a cursor, without the service's
  offset ceiling. Existing field attributes cannot be changed in place, so indexes created before
  this change must be recreated.
- **Replaces `load`.** The `search-index` `load` action wrote chunks with no corpus, which a sync
  would refuse to overwrite, so `sync` replaces it. The evaluation corpus is synced like any other:
  `--tenant tenant-eval-0001 --corpus-id grounded-relevance`.

## Live validation (Free tier, 5 November 2026)

On the real service: a first sync added all 24 chunks; an unchanged resync added and removed
nothing and refreshed 24; editing one file and deleting another reported the same counts in a dry
run and the real run (1 added, 6 removed) and left the other 18 chunks untouched; restoring the
folder added 6 and removed 1; the same files under another corpus ID were refused; a second corpus
in the same tenant survived resyncs of the first; and the profile-driven evaluation on the synced
index gave the same results as before (answer rate 0.59, no false answers). The isolation check
was rerun on a recreated canary index and passed 8 of 8.

One later command failed with HTTP 401 and succeeded unchanged on retry, with a token valid for
another 75 minutes. With the earlier unexplained failure, that makes two transient failures in
about thirty runs. The REST backend does not retry, so a bounded retry for throttled, unavailable
and (once, after refreshing the token) unauthorized responses is the next change.

## Consequences

- A customer can load their own Markdown corpus into the index with one command, and rerun it to
  pick up edits and removals.
- Concurrent syncs of the same corpus are not coordinated. The last writer wins, and the next sync
  repairs any difference.
- Only Markdown is ingested. Word and PDF extractors will produce chunks for this same step.
- Hosting, scheduling a sync, and removal of a single document by name are not provided.
