# Architecture Decision Log

This document records the architectural and design decisions made throughout the Tri9T AI CT-200 assignment. Every decision directly reflects the code implemented in the repository, explaining the rationale, evaluated alternatives, and deliberate tradeoffs.

---

## Decision 1: Cross-Version Identity via Separate `LogicalNode` and `NodeVersion` Entities

### Context & Problem
In medical device documentation, engineering manuals evolve across firmware versions. Sections may be reworded (e.g., battery life cycle estimates), renumbered, reordered, or split. If a database uses section numbers or heading text as primary keys, any revision breaks historical references or overwrites baseline requirements.

### Decision
Decouple persistent semantic identity from physical revision snapshots:
- **`LogicalNode`**: Represents the ongoing semantic identity of a concept or section across versions (e.g., `specs.battery_life`, `safety.error_codes`).
- **`NodeVersion`**: Represents the immutable parsed snapshot of that node in one specific `DocumentVersion`, capturing its exact text, structured table rows, bounding boxes, page locations, and SHA-256 content hash.
- Cross-version matching is performed during ingestion using a 3-pass heuristic:
  1. Strict Triple Match: `(section_number, normalized_heading, parent_path)`
  2. Structural Match: `(normalized_heading, parent_path)`
  3. Ambiguity & Unresolved Flagging: Flags ambiguous nodes (`status="unresolved"`) rather than making unverified assumptions.

### Tradeoffs & Alternatives Considered
- *Alternative 1: Single Node table mutated in-place.* Rejected because updating a node destroys historical records and invalidates earlier QA test traceability.
- *Alternative 2: Global UUIDs embedded into the PDF.* Impossible because input PDFs are read-only external vendor artifacts.
- *Tradeoff*: Ingestion requires a matching pass, and uncertain matches must be flagged for human review. However, this guarantees that historical records remain permanently immutable.

---

## Decision 2: Immutability of Version-Pinned Selections and Test Suites

### Context & Problem
Quality engineers define test scopes based on specific requirements in a specific manual revision. If a selection automatically points to "latest", a new manual ingestion could silently change test preconditions, tolerance bounds, or safety limits without engineer awareness.

### Decision
Selections are version-pinned and strictly validated:
- `POST /api/v1/selections` accepts an ordered list of `node_version_ids` belonging to a single `document_version_id`.
- Selections reject mixed versions, cross-document contamination, and duplicate nodes with `HTTP 422`.
- When QA test cases are generated, the exact source node text and SHA-256 hashes are captured in an immutable `SourceSnapshot` list inside the generation document.

### Tradeoffs & Alternatives Considered
- *Alternative 1: Selections dynamically resolving to the latest version of logical nodes.* Rejected because silent requirement drift violates regulatory audit principles (such as FDA 21 CFR Part 11 / ISO 13485).
- *Tradeoff*: Engineers must explicitly review stale test suites rather than having them magically updated. This deliberate friction is an intentional safety feature for medical device verification.

---

## Decision 3: Hybrid Storage: SQLite Relational Metadata + Append-Only JSON Document Store

### Context & Problem
The system handles two fundamentally different data profiles:
1. Highly relational, hierarchical document metadata (documents, versions, parent-child section trees, logical node lineages, cross-version matches, selections).
2. Deeply nested, schema-varying LLM test suites (multiple test cases, dynamic preconditions, multi-step actions and expected results, retry validation logs, and raw LLM responses).

### Decision
Implement a hybrid persistent storage model:
- **SQLite (via SQLAlchemy 2.0)**: Stores document entities, version metadata, logical nodes, node versions, cross-version match records, selections, and generation lookup indices. This provides ACID transactions, relational integrity, and fast indexed lookups (`O(1)` access by ID, selection, or node).
- **Append-Only JSON Document Store**: Stores generated LLM artifacts as individual JSON files in an append-only directory (e.g., `data/generations/{generation_id}.json`). The JSON document schema is designed for 1-to-1 parity with MongoDB collections.
- **Cross-Store Linkage**: SQLite `GenerationIndexORM` and `GenerationSourceNodeORM` index every generation by `idempotency_key`, `selection_id`, `logical_node_id`, and `node_version_id`, preventing any full-disk scans when querying generations.

### Tradeoffs & Alternatives Considered
- *Alternative 1: Storing LLM test cases in normalized relational SQL tables (TestCase, TestStep, Precondition tables).* Rejected due to high relational overhead, complex joins for reading a single test suite, and schema rigidity when LLM prompt templates evolve.
- *Alternative 2: Storing everything in MongoDB / NoSQL.* Rejected because hierarchical section trees, parent-child recursion, and referential integrity are cumbersome in pure document stores without relational constraints.
- *Tradeoff*: Two storage targets require coordinated writes (write JSON document, then commit SQLite index transaction). If a failure occurs before SQL commit, the orphaned JSON file is harmless and easily pruned.

---

## Decision 4: Deterministic Staleness & Heuristic Impact Reporting (Zero LLM for Classification)

### Context & Problem
When a newer manual release is ingested, existing QA test suites must be evaluated to identify which test cases are impacted by revised requirements. Many modern workflows pass the diff to an LLM for classification.

### Decision
Use **zero LLM calls** for staleness and impact assessment. Staleness is computed deterministically at retrieval time:
- **Status Classification**:
  - `current`: Node exists in latest version with identical SHA-256 `content_hash`.
  - `stale`: Node exists in latest version, but `content_hash` differs.
  - `removed`: Node is confidently absent from latest version.
  - `unknown`: Latest version is missing or node match is ambiguous (`status="unresolved"`).
- **Aggregate Priority Ladder**: `unknown > removed > stale > current`. A single current node never hides a stale or removed specification.
- **Deterministic Impact Heuristics**:
  - `NUMERICAL_SPECIFICATION_CHANGED`: Regex numeric token extractor detects changed values, decimals, percentages, or units (e.g., 300 to 250 cycles, 40 to 30 mmHg, 2.0 to 1.5 s).
  - `SAFETY_OR_ALARM_MODIFIED`: Keyword matcher identifies safety-critical terms (`alarm`, `warning`, `overpressure`, `error`, `deflation`, `abort`).
  - `FEATURE_OR_BEHAVIOR_CHANGED`: Regex detects protocol/interface keywords (`bluetooth`, `sync`, `csv`) or new error codes (`e\d+`).
  - `WORDING_ONLY_CHANGED`: Editorial fallback tag.
- All tags are explicitly framed with an automated review hint disclaimer, not clinical or regulatory conclusions.

### Tradeoffs & Alternatives Considered
- *Alternative: Calling an LLM to evaluate staleness and severity.* Rejected because LLM calls introduce non-deterministic results, hallucinations, latency (seconds per request), API costs, and dependency on external network services.
- *Tradeoff*: Keyword and regex heuristics cannot detect subtle contextual rephrasings that alter meaning without using predefined keywords. However, they are 100% reproducible, instant (<5ms), completely offline, and auditable.

---

## Decision 5: Skipped-Level Heading Attachment & Natural Source Order Preservation

### Context & Problem
In the CT-200 manual:
1. Heading `2.1.1.1 Battery Life Under Typical Use` has declared depth 4 (`2.1.1.1`), but parent `2.1.1` does not exist in the document.
2. Section `3.4 Pre-Measurement Checklist` physically appears on Page 2 *before* Section `3.3 Measurement Procedure`.

### Decision
- **For Skipped-Level Headings**:
  - Preserve the declared depth (depth 4).
  - Attach the node to the nearest existing numbered ancestor (`2.1`).
  - Record an explicit `MISSING_PARENT_SECTION` extraction warning referencing the node's persistent ID.
  - **Do not hallucinate or invent an artificial `2.1.1` section node**.
- **For Natural Source Order**:
  - Store nodes in physical reading order (`source_order` counter incrementing per page element).
  - Do not artificially sort nodes by section number. The AST accurately reflects the printed physical manual.

### Tradeoffs & Alternatives Considered
- *Alternative 1: Invent an empty dummy node for `2.1.1`.* Rejected because inventing data creates ghost nodes that confuse test generation and diffing.
- *Alternative 2: Coerce `2.1.1.1` to depth 3.* Rejected because modifying declared hierarchy discards engineering manual specifications.
- *Tradeoff*: The section hierarchy tree contains non-consecutive depth transitions (`2.1` -> `2.1.1.1`), which tree traversals must support. This is handled cleanly by attaching children to their closest existing ancestor.

---

## Decision 6: Pluggable LLMClient Interface with Default Deterministic Mock Adapter

### Context & Problem
Running tests, CI/CD pipelines, and local developer demonstrations should never depend on active cloud API credentials, paid rate limits, or internet connectivity. However, production deployments require connecting to real LLM providers (e.g., OpenAI, Anthropic).

### Decision
Implement the Dependency Inversion Principle with a pluggable `LLMClient` port:
- `MockLLMClient`: The default adapter. Deterministically synthesizes schema-compliant medical device test cases citing valid source node IDs without calling external APIs.
- Real provider adapter: Enabled by setting `MOCK_LLM=false` and providing `OPENAI_API_KEY`. If real provider mode is enabled without credentials, the system fails fast with a clear configuration error.
- Generation incorporates a self-healing 1-retry loop: on malformed JSON or schema violations, it retries once with structured validation feedback before failing gracefully.

### Tradeoffs & Alternatives Considered
- *Alternative: Hardcoding OpenAI API calls in the use case.* Rejected because it prevents offline testing and exposes API keys.
- *Tradeoff*: Mock adapter outputs are synthetic rather than creatively generated by frontier models, but they guarantee 100% test reproducibility, zero setup barriers, and zero financial cost.
