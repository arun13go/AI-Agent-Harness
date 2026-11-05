# ADR 0026: Document Extraction with Azure AI Document Intelligence

- **Status:** Accepted
- **Date:** 5 November 2026
- **Builds on:** [ADR 0017](0017-deterministic-chunk-identity.md), [ADR 0023](0023-azure-ai-search-retrieval-adapter.md), [ADR 0025](0025-corpus-synchronization.md)

## Purpose and scope

Only Markdown could be ingested. This ADR adds PDF and Word (`.docx`) ingestion by sending each
file to Azure AI Document Intelligence, which returns Markdown that the existing chunker, chunk
identity and scoping rules handle unchanged. It covers the extractor contract, the service client,
the folder loader, the sync behavior that keeps repeat runs from being billed again, and the
optional infrastructure. It does not cover image-only files, other Office formats, or scheduling.

## Decision

- **A managed service parses the files.** PDF and Word files are attacker-controlled, and parsers
  for them have a long record of vulnerabilities. Sending them to the service keeps that parsing
  out of this process, and it needs no new dependency. A local parser was the alternative in the
  plan; it was not chosen because the packages could not be installed from this machine and a
  hand-written PDF parser is not safe to maintain.
- **Contract.** `DocumentExtractor.extract(content, filename)` returns `ExtractedDocument`
  (Markdown and a page count) or raises `ExtractionError` or `InputValidationError`.
  `ExtractionError` is a new typed error with code `extraction_error`. The registration bundle
  and ARM template embed the core types and errors, so both were regenerated.
- **Formats.** `.pdf` and `.docx`, through the `prebuilt-layout` model with Markdown output (API
  `2024-11-30`). Scanned PDFs are handled by the same model. Legacy `.doc` is not supported by the
  service and is rejected with an instruction to save as `.docx`: in a folder it is an error, not
  a skip, because a silently skipped file would later have its chunks removed by a sync. Other
  file types in a folder are ignored as before.
- **Local checks before anything is sent.** The extension must be supported and the first bytes
  must match it (`%PDF-` or the zip signature). A legacy or encrypted Office container, empty
  files and files over the size limit (default 4 MiB, the free tier's limit) are rejected.
- **The client treats the service and the reply as untrusted.** The endpoint must be an
  `https` resource subdomain (Entra authentication requires one) and is validated before any token
  is requested. The operation address returned by the service must have the same host, the layout
  model's result path and only the expected API version, or the call fails before the token is
  sent anywhere else. Redirects are refused, the file name is never sent, waiting honors
  `Retry-After` within bounds and has a deadline, throttled and unavailable responses are retried
  a few times, and the response size is bounded. Errors carry a status and a service error code,
  never a body.
- **The result must be usable.** It must be Markdown with at least one page and non-empty text. A
  document that yields no usable text is an error, never an empty result, since an empty result
  would read as "delete this file's evidence" to a sync.
- **Identity and untrusted text.** `sourceVersion` is a hash of the original bytes, never of the
  extracted text. The Markdown goes through the same chunker, so chunk identity, tenant scoping,
  labels and the untrusted-content marking are identical to Markdown sources.
- **No repeat billing.** Extraction is billed per page, so a sync asks the index which version it
  already holds for each source and does not extract a PDF or Word file whose bytes are
  unchanged. Those chunks stay and are refreshed in place with a partial update (freshness and
  labels only). A partial update of a missing chunk fails, so a refresh can not bring back removed
  evidence, and a sync stops if the index no longer holds a source it was told was unchanged.
- **Explicit opt-in.** `search-index --action sync --extract-documents` extracts documents and
  reports `extractedFiles` and `extractedPages`. Without the flag, a folder that contains PDF or
  Word files is refused rather than skipped, for the same reason as `.doc`. A dry run still
  extracts new and changed files, because chunk identity depends on the extracted structure, so it
  can cost pages.
- **Optional infrastructure.** `infra/document-intelligence.bicep` creates a keyless resource
  (`disableLocalAuth`, Entra only, custom subdomain). `main.bicep` creates it only when
  `documentIntelligenceSku` is `F0` or `S0`, in the optional resource group with search, and
  exports `AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT`. The default creates nothing. The template grants
  no roles; the operator needs "Cognitive Services User" on the resource.
- **Shared HTTP support.** The token cache and the refusal to follow redirects moved from the
  search client into one small module that both Azure REST clients use.

## Live validation (Free tier, 5 November 2026)

A Free (F0), keyless Document Intelligence resource was created in the dedicated resource group
(the what-if showed exactly one new resource) and the operator was granted "Cognitive Services
User" on it. With small generated test files, on the real service:

| Scenario | Result |
|---|---|
| PDF and Word present, flag omitted | refused, nothing indexed |
| First sync (Markdown, PDF, Word) | 2 files and 2 pages extracted, 3 chunks added |
| Resync unchanged | nothing extracted (0 pages), 2 sources refreshed in place |
| Word file edited | 1 file and 1 page extracted, old chunk removed, new added |
| PDF removed | its chunk removed, nothing extracted |
| Legacy `.doc` added | refused before any extraction |
| Corrupt PDF added | `extraction_error`, HTTP 400 `InvalidRequest`, no body, index untouched |
| PDF restored, then unchanged resync | 1 page extracted, then 0 |

The extracted Markdown was clean: a Word Heading 1 and a PDF's larger title font both became `#`
headings, so chunk paths and citations name the section, and a Word table came back as an HTML
table. Through the Azure profile, the PDF question and the Word table question were each
answered from the right document with a citation (the Word answer reflected the edited file),
and an off-topic question was answered with a weak-evidence clarification and no model call.
About 4 of the 500 free pages were used. This validates the plumbing on tiny single-page files;
it does not measure extraction quality on real, multi-page, scanned or table-heavy documents,
which the free tier's two-page limit also prevents.

## Consequences

- The free tier (F0) analyzes only the first two pages of a PDF, accepts files up to 4 MB and
  allows 500 pages a month, so it proves the plumbing but not real corpora. Real use needs S0,
  which is billed per page; check current pricing before a large ingest.
- Document content is sent to the Azure resource you choose. Confirm that its region, retention
  and data handling satisfy your requirements before ingesting sensitive documents.
- The layout model's Markdown can carry page headers and footers, page-break comments and HTML
  tables. They are kept as text, so some chunks contain that noise. Cleaning it is a later change
  if evaluation shows it matters.
- A changed file is extracted and billed again; an unchanged one is not. Chunk identifiers are
  stable for unchanged files because they are not re-extracted.
- Image files, other Office formats, concurrent syncs and scheduled syncs are not covered.
