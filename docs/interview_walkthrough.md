# CT-200 Pipeline: Technical Architecture & Interview Walkthrough Guide

This guide provides an end-to-end technical explanation of the **Tri9T AI CT-200 Regulatory Manual Traceability & QA Suite**. It is designed to prepare you for technical interviews, design defense, and code walkthroughs. Every section is strictly grounded in the actual codebase, referencing concrete modules, database schemas, domain models, and algorithms.

---

## 1. Executive Summary & Problem Context

### The Engineering Challenge
In regulated medical device engineering (governed by FDA 21 CFR Part 820, 21 CFR Part 11, and ISO 13485), devices undergo continuous firmware, hardware, and labeling updates. When a new Instructions for Use (IFU) or Technical Specification manual revision is released:
1. **Historical QA Test Suites must remain immutable**: A test suite created for v1 must never have its assertions or requirements silently mutated by a v2 ingestion.
2. **Requirement Drift must be instantly detectable**: Engineers need to know immediately which test cases are affected by revised numerical tolerances, updated safety timings, or modified error codes.
3. **Auditability & Traceability**: Every generated test case must cite verifiable source sections and cryptographic content hashes.
4. **Reliability & Determinism**: System operations (parsing, diffing, staleness evaluation) must be reproducible, fast, and capable of running offline without relying on non-deterministic LLM calls for state tracking.

### What CT-200 Implements
The CT-200 system is a modular Python backend (built with FastAPI, SQLAlchemy 2.0, SQLite, PyMuPDF, and Pydantic v2) that ingests digital vector manuals, extracts hierarchical AST representations without hallucinating missing nodes, maintains cross-version node lineage, generates schema-validated QA test suites tied to version-pinned selections, and dynamically evaluates staleness at retrieval time using cryptographic hashing and deterministic heuristics.

---

## 2. End-to-End System Flow

```mermaid
flowchart TD
    subgraph Ingestion ["1. Ingestion & Hierarchy Reconstruction"]
        PDF[PDF File: ct200_manual.pdf] --> Extractor[PDFExtractor: PyMuPDF Vector Engine]
        Extractor --> Parser[DocumentParser: Layout & Hierarchy Reconstruction]
        Parser --> AST[DocumentDraft: Typed AST Nodes & Warnings]
        AST --> IngestUC[IngestDocumentUseCase]
    end

    subgraph Storage ["2. Hybrid Persistence & Matching"]
        IngestUC --> SQLite[(SQLite Relational DB)]
        SQLite --> Models[Document, DocumentVersion, LogicalNode, NodeVersion]
        IngestUC --> Matcher[3-Pass Cross-Version Node Matcher]
        Matcher --> VersionMatch[VersionMatchORM & Content Hashing]
    end

    subgraph Selection ["3. Version-Pinned Selection"]
        Engineer[QA Engineer / API Client] --> CreateSel[POST /api/v1/selections]
        CreateSel --> SelValidation[Validate Single Version & Node Ownership]
        SelValidation --> SelRecord[SelectionORM & SelectionNodeORM]
    end

    subgraph Generation ["4. Validated QA Test Synthesis"]
        SelRecord --> GenUC[GenerateQAUseCase]
        GenUC --> Expansion[Section Expansion & Node Deduplication]
        Expansion --> LLM[LLMClient Adapter: Mock / OpenAI]
        LLM --> RetryLoop[Two-Tier Validation & 1-Retry Recovery Loop]
        RetryLoop --> JSONStore[Append-Only JSON Document Store]
        RetryLoop --> GenIndex[SQLite GenerationIndexORM & SourceNodeORM]
    end

    subgraph Staleness ["5. Retrieval-Time Staleness & Impact Reporting"]
        Client[GET /api/v1/generations/{id}] --> GetGenUC[GetGenerationUseCase]
        GetGenUC --> LoadJSON[Load Immutable JSON from Disk]
        GetGenUC --> StalenessSvc[StalenessService.analyze]
        StalenessSvc --> CompareHashes[Compare Pinned Hash vs Latest Version Hash]
        CompareHashes --> Heuristics[ImpactAnalyzer: Deterministic Keyword/Regex Heuristics]
        Heuristics --> Response[GenerationResponse with Dynamic Traceability]
    end
```

### Detailed Phase Breakdown

#### Phase 1: Ingestion & Hierarchy Reconstruction (`src/app/infrastructure/pdf/`)
1. **Extraction (`extractor.py`)**: `PDFExtractor` uses PyMuPDF (`fitz`) to extract text tokens, bounding boxes, font sizes, font flags (bold/regular), and line coordinates. It checks selectable text density; if text is corrupted or absent, it falls back to OCR.
2. **Hierarchy Parsing (`parser.py`)**: `DocumentParser` converts raw text and coordinate streams into a structured `DocumentDraft` containing typed `DocumentNode` objects (`SECTION`, `PARAGRAPH`, `TABLE`, `LIST`, `LIST_ITEM`).
   - Section numbers are extracted via regex (`^(\d+(\.\d+)*)\s+(.*)$`).
   - Declared depth is computed from segment count (`2.1.1.1` $\to$ depth 4).
   - If an intermediate section is skipped (`2.1.1` does not exist), the node attaches to its nearest existing ancestor (`2.1`), preserves its declared depth (4), and records a structured `MISSING_PARENT_SECTION` warning containing the exact node ID.
   - Physical reading order is preserved in `source_order` (e.g., Section 3.4 appears on Page 2 before Section 3.3; an `OUT_OF_ORDER_SECTION` warning is recorded).
   - Tables are extracted into structured rows and headers; multi-line wrapped cells are merged.

#### Phase 2: Persistence & Lineage Tracking (`src/app/application/use_cases/ingest_document.py`)
1. **File Fingerprinting**: `compute_file_sha256` computes the SHA-256 hash of the PDF. If the exact file was already ingested, it returns the existing `DocumentVersion` (`is_duplicate=True`).
2. **Entity Creation**:
   - `Document`: Represents the device manual family (`slug="ct200"`).
   - `DocumentVersion`: Represents the revision (`version_label="v1"` or `"v2"`), storing file hash, warnings, page count, and creation time.
3. **Cross-Version 3-Pass Matching**:
   - **Pass 1 (Strict Triple Match)**: Matches `(section_number, normalized_heading, parent_path)`. Reuses existing `LogicalNode`.
   - **Pass 2 (Structural Match)**: If section numbers shifted but normalized title and parent hierarchy match unambiguously, reuses `LogicalNode`.
   - **Pass 3 (Ambiguity & New Nodes)**: Low-confidence matches are flagged `status="unresolved"` in `VersionMatchORM`. Genuinely new sections (e.g., Section 5.3 Data Export in v2) receive a new `LogicalNode`.
4. **Node Content Hashing**:
   $$\text{content\_hash} = \text{SHA256}(\text{normalized\_heading} + "::" + \text{normalized\_body\_or\_table\_json})$$

#### Phase 3: Version-Pinned Selection (`src/app/application/use_cases/manage_selections.py`)
1. Endpoint `POST /api/v1/selections` receives a selection `name`, a single `document_version_id`, and an ordered list of `node_version_ids`.
2. Validation ensures:
   - All referenced nodes exist in the database.
   - Every node strictly belongs to the specified `document_version_id` (rejecting cross-document or mixed-version contamination with HTTP 422).
   - No duplicate node IDs exist in the payload.
3. The selection is saved in `SelectionORM` and `SelectionNodeORM`, permanently pinned to those specific `NodeVersion` snapshots.

#### Phase 4: Validated QA Generation & Storage (`src/app/application/use_cases/generate_qa.py`)
1. **Idempotency Check**: `Idempotency-Key` HTTP header is required. If a generation with the same key and `selection_id` exists, the cached result is returned immediately without re-invoking the LLM.
2. **Context Expansion & Deduplication**: If a selected node is a `SECTION`, all of its descendant child nodes are gathered. If the selection also directly included a child, it is included in the prompt context only once.
3. **Prompt & LLM Invocation**: The context is passed to the configured `LLMClient` (`MockLLMClient` by default, or `OpenAILLMClient` if `MOCK_LLM=false`).
4. **Two-Tier Validation**:
   - **Tier 1 (Schema Validation)**: Output must parse as JSON matching Pydantic `TestSuiteOutput` with 3–5 test cases, valid preconditions, and numbered steps.
   - **Tier 2 (Reference Validation)**: Every cited ID in `node_references` must exist within the selection's expanded context.
5. **Self-Healing 1-Retry Loop**: If validation fails on attempt 1, the LLM is called a second time with the validation error message. If attempt 2 succeeds, status is `SUCCESS`. If it fails, status is `FAILED` with partial test cases masked.
6. **Dual Storage Write**:
   - The full generation document is written as an immutable JSON file in `data/generations/{generation_id}.json`.
   - Search indices are committed to SQLite (`GenerationIndexORM` and `GenerationSourceNodeORM`).

#### Phase 5: Dynamic Retrieval-Time Staleness (`src/app/application/services/staleness_service.py`)
1. When `GET /api/v1/generations/{generation_id}` is called, the saved JSON file is read from disk. **The stored file is never modified or regenerated.**
2. The service queries SQLite for the latest ingested version of the document (e.g., v2).
3. For every source snapshot in the generation:
   - The service resolves the snapshot's `logical_node_id` against the latest manual revision.
   - **`current`**: Latest node exists and `content_hash` matches original snapshot.
   - **`stale`**: Latest node exists, but `content_hash` differs. Unified text diff is computed.
   - **`removed`**: Node is absent from the latest revision.
   - **`unknown`**: Latest manual is unavailable or matching is flagged `unresolved`.
4. **Aggregate Priority Ladder**: `unknown > removed > stale > current`. A single current node never masks a stale or removed specification.
5. **Deterministic Heuristic Impact Analysis (`impact_analyzer.py`)**: Diff lines are inspected for:
   - `NUMERICAL_SPECIFICATION_CHANGED` (regex numeric token diffs)
   - `SAFETY_OR_ALARM_MODIFIED` (safety keywords: alarm, warning, overpressure, deflation, etc.)
   - `FEATURE_OR_BEHAVIOR_CHANGED` (feature keywords: bluetooth, csv, export, e\d+)
   - `WORDING_ONLY_CHANGED` (editorial text adjustments)
   - Mandatory disclaimer: *"Impact tags are automated heuristic review hints, not medical or regulatory conclusions."*

---

## 3. Key Architectural Decisions & Tradeoffs

### Decision 1: Separating `LogicalNode` from `NodeVersion`
- **Context**: Sections in medical device manuals change text, move around, or get renumbered across versions.
- **Decision**: Decouple persistent semantic identity (`LogicalNode`) from revision-specific snapshots (`NodeVersion`).
- **Why Alternatives Were Rejected**:
  - *In-place mutation*: Overwriting nodes destroys the audit trail and corrupts earlier test case citations.
  - *Embedding UUIDs in PDFs*: Vendor PDFs are external, read-only binary files that cannot be edited.
- **Tradeoff**: Ingestion requires a matching pass and handles unresolved ambiguities, but provides guaranteed historical immutability.

### Decision 2: Version-Pinned Selections
- **Context**: Test cases must verify a specific, approved manual version.
- **Decision**: Selections point strictly to specific `NodeVersion` IDs, capturing immutable hashes.
- **Why Alternatives Were Rejected**:
  - *Dynamic resolution to latest*: If a selection automatically points to "latest", a new manual ingestion could silently invalidate test tolerances or preconditions without the engineer's knowledge.
- **Tradeoff**: Updating a test suite to a new manual release requires an explicit engineering review rather than automatic silent updating. In medical device engineering, this friction is a safety requirement.

### Decision 3: Hybrid Storage (SQLite + Append-Only JSON Files)
- **Context**: Need fast relational queries for section hierarchies and selections, alongside storage for deeply nested LLM test suites.
- **Decision**: SQLite (via SQLAlchemy 2.0) stores relational metadata and indices; individual JSON files on disk store generation documents (compatible with MongoDB collections).
- **Why Alternatives Were Rejected**:
  - *100% Relational SQL (TestCase, TestStep tables)*: Creates schema rigidity, migration friction when prompt formats evolve, and unnecessary multi-table joins to retrieve a single test suite.
  - *100% MongoDB / NoSQL*: Graph-like recursive hierarchy queries (parent-child trees, section traversal) and relational foreign keys are fragile in NoSQL without ACID transactions.
- **Tradeoff**: Coordinated writes across two storage engines. We mitigate this by writing the JSON file first, then committing the SQLite transaction. If the transaction fails, the orphaned JSON file is harmless.

### Decision 4: Deterministic Staleness (Zero LLM Calls for Classification)
- **Context**: Evaluating whether existing test suites are stale against a newer manual revision.
- **Decision**: Staleness and impact classification are computed entirely deterministically using SHA-256 hash comparison, Python's `difflib`, and regex/keyword heuristics.
- **Why Alternatives Were Rejected**:
  - *LLM-as-a-judge*: Calling an LLM to classify diffs introduces non-deterministic outputs, hallucinated severity ratings, network dependency, latency (2–5 seconds per request), and ongoing API costs.
- **Tradeoff**: Regex/keyword heuristics cannot capture subtle semantic shifts in phrasing that do not use known keywords. However, they are instant (<5ms), 100% reproducible, operate completely offline, and are fully auditable.

### Decision 5: Non-Hallucinating Hierarchy Reconstruction (Skipped Headings & Source Order)
- **Context**: In CT-200, Section `2.1.1.1` has declared depth 4, but Section `2.1.1` does not exist. Section `3.4` appears physically before Section `3.3`.
- **Decision**:
  - Preserve `2.1.1.1` declared depth as 4, attach it to nearest ancestor `2.1`, emit `MISSING_PARENT_SECTION` warning referencing the node's persistent ID. **Do not hallucinate a dummy `2.1.1` node.**
  - Store nodes in physical reading order (`source_order`), preserving natural layout rather than sorting artificially by section number.
- **Why Alternatives Were Rejected**:
  - *Inventing dummy section `2.1.1`*: Hallucinating non-existent nodes pollutes test generation context and corrupts cross-version diffs.
- **Tradeoff**: The section tree has non-consecutive depth jumps (`2.1` $\to$ `2.1.1.1`), which tree traversals must support.

### Decision 6: Pluggable LLM Interface with Deterministic Mock Default
- **Context**: Automated test suites, CI/CD, and local demos should never fail due to missing cloud API keys, network outages, or vendor rate limits.
- **Decision**: Provide an abstract `LLMClient` interface with a `MockLLMClient` (default) and `OpenAILLMClient`. If real provider mode is enabled without an API key, fail fast with a `ConfigurationError`.
- **Tradeoff**: Mock client outputs are synthetic and deterministic rather than creatively varied, but ensure reliable testing and zero demo setup friction.

---

## 4. Recognized System Limitations & Production Tradeoffs

1. **Heuristic Impact Tagging Scope**:
   - The impact analyzer uses curated keyword sets (`SAFETY_KEYWORDS`, `FEATURE_KEYWORDS`) and number extractors. Subtle semantic revisions without keyword triggers fall back to `WORDING_ONLY_CHANGED`.
   - *Production evolution*: In a clinical setting, this can be augmented with a medical ontology (SNOMED CT, UMLS, or MedDRA) or a secondary offline embeddings classifier.
2. **SQLite Write Concurrency**:
   - SQLite uses file-level locking for writes. While optimal for single-process architectures and testing, high-concurrency enterprise deployments with hundreds of simultaneous writes should swap the engine to PostgreSQL.
3. **In-Memory Diff Computation**:
   - Diffing is computed on-the-fly during `GET /api/v1/generations/{id}`. For manuals with thousands of pages or large data tables, caching diff results by `(v1_hash, v2_hash)` in Redis would optimize response latency.
4. **Layout Segmentation on Complex Legacy Scans**:
   - The PDF extractor excels on digital vector PDFs. For noisy physical scans or complex multi-column magazines, integrating LayoutLM or a vision-language model would enhance segment classification.

---

## 5. High-Yield Interview Questions & Code-Grounded Answers

### Q1: Can you walk me through the ingestion pipeline from raw PDF bytes to persisted database records?
**Answer:**
1. The client posts the PDF file path, slug, and version label to `POST /api/v1/documents/ingest`.
2. `IngestDocumentUseCase` computes the SHA-256 hash of the PDF file to ensure idempotency. If already ingested, it returns the existing version immediately.
3. `PDFExtractor` uses PyMuPDF (`fitz`) to extract words, bounding boxes, font attributes, and line groupings, checking selectable text density.
4. `DocumentParser` processes the line groups into an AST: identifying numbered and unnumbered headings, tracking declared depth, attaching children to the nearest ancestor, merging multi-line table cells, and recording structural warnings (`MISSING_PARENT_SECTION`, `OUT_OF_ORDER_SECTION`).
5. In `SQLAlchemyDocumentRepository`:
   - It creates or retrieves the `Document`.
   - It creates the `DocumentVersion`.
   - It runs the 3-pass matcher against the baseline version (strict triple match, structural match, ambiguity flagging) to assign persistent `LogicalNode` IDs.
   - It computes SHA-256 content hashes for each node over normalized text and table data, persisting `NodeVersionORM` rows.

### Q2: How did you solve the skipped-level heading problem (Section 2.1.1.1 where 2.1.1 does not exist)?
**Answer:**
- In `src/app/infrastructure/pdf/parser.py`, the parser computes the declared depth directly from the dot-delimited section number: `2.1.1.1` has 4 segments, so its declared depth is 4.
- When searching for its parent in the active section stack, the parser searches backwards for the closest ancestor whose section number is a prefix. Because `2.1.1` is missing, it attaches directly to `2.1 General Specifications`.
- The parser records a structured warning: `MISSING_PARENT_SECTION`, explicitly storing the page number and the persistent node ID of the `2.1.1.1` node.
- Crucially, we **do not invent or hallucinate** a synthetic dummy section `2.1.1`. The AST faithfully reflects the physical document.

### Q3: Why did you separate `LogicalNode` from `NodeVersion`, and how does cross-version matching work?
**Answer:**
- In medical device manuals, a requirement (e.g., Battery Life) has a persistent semantic identity across revisions, but its text, tolerances, or section numbering may change.
- `LogicalNode` stores the stable concept ID (`log_...`).
- `NodeVersion` stores the exact snapshot (`nv_...`) for one manual version, including text, bounding box, page locations, and cryptographic `content_hash`.
- When ingesting v2, we match nodes in three passes:
  1. *Strict Triple Match*: `(section_number, normalized_heading, parent_path)` matches the baseline node.
  2. *Structural Match*: If numbering shifted but heading and parent hierarchy match unambiguously, we reuse the `LogicalNode`.
  3. *Ambiguity Handling*: If candidates conflict, we record `status="unresolved"` in `VersionMatchORM`. If a node is completely new (e.g., Section 5.3 in v2), a new `LogicalNode` is created.

### Q4: Why did you choose a hybrid storage architecture instead of putting everything into SQLite or MongoDB?
**Answer:**
- Medical device manuals have two opposing data profiles:
  1. **Relational & Hierarchical Data**: Section trees, parent-child lineages, version comparisons, and selections require relational integrity, foreign key constraints, and fast index scans. SQLite (via SQLAlchemy 2.0) is ideal for this.
  2. **LLM Test Suites**: Generated test cases have deeply nested, variable schemas (preconditions, steps, validation logs, raw responses). Normalizing them into SQL (`TestCase`, `TestStep`, `StepAction` tables) requires complex migrations and multi-table joins. Storing them as individual JSON documents is append-only, human-auditable, and aligns directly with MongoDB.
- We bridge the two with SQLite lookup tables (`GenerationIndexORM` and `GenerationSourceNodeORM`), which index every generation by `selection_id`, `logical_node_id`, and `node_version_id`. This allows $O(1)$ indexed queries without scanning JSON files on disk.

### Q5: How do you guarantee that generating test cases on v1 is never corrupted or silently mutated when v2 is ingested?
**Answer:**
- Selections are **version-pinned**: `POST /api/v1/selections` accepts `node_version_ids` from a single document version. It validates that all nodes belong to that version and stores them in `SelectionNodeORM`.
- When `GenerateQAUseCase` runs, it captures the exact text and SHA-256 content hashes of the pinned nodes into an immutable `SourceSnapshot` array inside the generation document.
- When v2 is ingested, new `NodeVersionORM` rows are created, but the v1 rows and the selection remain completely untouched.
- At retrieval time, `StalenessService` dynamically compares the pinned snapshot hashes with the latest v2 hashes. The stored generation artifact on disk is **never rewritten or regenerated**.

### Q6: How does the QA test case generation validation and retry loop work?
**Answer:**
- In `src/app/application/use_cases/generate_qa.py`, we implement a two-tier validation mechanism:
  1. *Schema Validation*: The output must parse into `TestSuiteOutput` (Pydantic model) containing between 3 and 5 test cases with non-empty titles, preconditions, and structured steps.
  2. *Reference Validation*: Every node ID cited in `test_case.node_references` must exist in the expanded context of the selection. Any hallucinated external ID causes validation failure.
- If validation fails on attempt 1, the system triggers **at most one retry**, sending the LLM the exact schema and reference error messages.
- If attempt 2 succeeds, the document is saved with `status="SUCCESS"`. If attempt 2 fails, it is saved with `status="FAILED"`, recording the validation errors while masking any partial or corrupt test cases.

### Q7: How is staleness computed, and why are zero LLM calls used?
**Answer:**
- In `src/app/application/services/staleness_service.py`, when a generation is retrieved via `GET /api/v1/generations/{generation_id}`, the service:
  1. Resolves the latest version of the manual family (e.g., v2).
  2. Looks up the matching node in v2 for each saved source snapshot via its `logical_node_id`.
  3. Classifies per-source status:
     - `current`: SHA-256 hashes match.
     - `stale`: SHA-256 hashes differ. A unified diff is generated via Python's `difflib`.
     - `removed`: Node is absent from v2.
     - `unknown`: Node match is unresolved.
  4. Applies the priority ladder for aggregate status: `unknown > removed > stale > current`.
  5. Runs `ImpactAnalyzer` to assign deterministic review tags (`NUMERICAL_SPECIFICATION_CHANGED`, `SAFETY_OR_ALARM_MODIFIED`, etc.).
- **Zero LLM calls** are used because hash comparison and regex heuristics are deterministic, 100% reproducible, take <5ms, cost nothing, and cannot hallucinate.

### Q8: How does section expansion and deduplication work during QA generation?
**Answer:**
- When an engineer creates a selection, they might select a top-level section (e.g., Section 1 Device Overview) and also select a specific sub-paragraph.
- In `src/app/application/use_cases/generate_qa.py` (`_expand_and_deduplicate_nodes`), the use case checks each node:
  - If a node is a `SECTION`, it recursively retrieves all descendant child nodes.
  - It maintains an ordered dictionary of unique node IDs. If a child was also selected directly, it is included in the context **only once**.
- This ensures the LLM receives complete section context without redundant text or duplicate node citations.

### Q9: How does idempotency work in the generation pipeline?
**Answer:**
- The client passes an `Idempotency-Key` header with `POST /api/v1/selections/{id}/generations`.
- `GenerateQAUseCase` queries `GenerationIndexORM` for `(selection_id, idempotency_key)`.
- If an entry exists, the previously generated document is loaded from the JSON store and returned immediately with HTTP 201/200, bypassing the LLM completely.
- If the header is missing or empty, the API immediately rejects the request with HTTP 422.

### Q10: What are the exact verified domain changes between CT-200 v1 and v2?
**Answer:**
In `tests/test_persistence_and_diff.py` (`test_actual_ct200_changes_v1_to_v2`), the suite verifies 5 exact domain changes:
1. **Battery Life (Section 2.1.1.1)**: 300 cycles / 15% threshold $\to$ 250 cycles / 10% threshold (`DiffStatus.CHANGED`).
2. **Inflation Increment (Section 3.2)**: 40 mmHg increment $\to$ 30 mmHg increment (`DiffStatus.CHANGED`).
3. **Overpressure Deflation Timing (Table 4.2)**: 2 seconds $\to$ 1.5 seconds (`DiffStatus.CHANGED`).
4. **New Error Code E6 (Table 4.2)**: Bluetooth sync failure added (`DiffStatus.CHANGED`).
5. **New Section 5.3 (Data Export)**: CSV export via firmware 1.4 added (`DiffStatus.ADDED`).
6. Additionally, a profile paragraph in Section 3.1 was removed/reorganized (`DiffStatus.REMOVED`).
Total diff counts: 49 unchanged, 5 changed, 2 added, 1 removed, 0 unresolved.

---

## 6. Project Codebase Architecture Map

| Layer | Directory / File | Key Responsibility |
| :--- | :--- | :--- |
| **Domain** | `src/app/domain/models/` | Core entities: `document.py`, `diff.py`, `qa.py`, `selection.py` |
| **Domain** | `src/app/domain/exceptions.py` | Clean domain exceptions (`NodeNotFoundError`, `InvalidQueryError`, etc.) |
| **Use Cases** | `src/app/application/use_cases/ingest_document.py` | Orchestrates extraction, matching, and DB persistence |
| **Use Cases** | `src/app/application/use_cases/manage_selections.py` | Selection validation, version pinning, and retrieval |
| **Use Cases** | `src/app/application/use_cases/generate_qa.py` | Expansion, idempotency, prompt generation, retry loop |
| **Use Cases** | `src/app/application/use_cases/diff_versions.py` | Cross-version AST diffing, unified text diffs |
| **Use Cases** | `src/app/application/use_cases/get_generation.py` | Retrieval and indexed single-filter queries |
| **Services** | `src/app/application/services/staleness_service.py` | Dynamic retrieval-time staleness comparison & priority ladder |
| **Services** | `src/app/application/services/impact_analyzer.py` | Deterministic regex/keyword heuristic classification |
| **Infrastructure** | `src/app/infrastructure/pdf/extractor.py` | PyMuPDF text token and spatial bounding box extractor |
| **Infrastructure** | `src/app/infrastructure/pdf/parser.py` | AST reconstruction, skipped heading healing, table merging |
| **Infrastructure** | `src/app/infrastructure/db/models.py` | SQLAlchemy 2.0 ORM models (`DocumentORM`, `NodeVersionORM`, etc.) |
| **Infrastructure** | `src/app/infrastructure/db/repositories/` | Concrete repository implementing relational queries |
| **Infrastructure** | `src/app/infrastructure/storage/json_store.py` | Append-only filesystem JSON document storage |
| **Infrastructure** | `src/app/infrastructure/llm/client.py` | Pluggable `MockLLMClient` and `OpenAILLMClient` adapters |
| **API** | `src/app/api/routes/` | Thin FastAPI transport routes (`documents.py`, `selections.py`, `generations.py`) |
| **Demo** | `scripts/demo_e2e.py` | Full 5-step executable lifecycle walkthrough |
