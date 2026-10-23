# Engineering Approach & Pipeline Specification

## 1. Overview & Core Philosophy

The Tri9T AI CT-200 system is designed to solve a foundational challenge in medical device software engineering: **maintaining traceable, verifiable quality assurance test suites across evolving regulatory manuals without corrupting historical test records or silently overlooking critical specification changes**.

The system adheres to three non-negotiable architectural principles:
1. **Immutability of Ingested and Generated History**: Once a manual version or QA generation is accepted, it is permanently frozen. Later manual releases do not rewrite history or overwrite prior test suites.
2. **Deterministic, Traceable Staleness**: Comparing a test suite to a newer manual version is computed dynamically at retrieval time using cryptographic hashes, AST lineage, and unified text diffs—never by re-prompting an LLM or relying on non-deterministic heuristic models for state classification.
3. **Hexagonal Modularity (Clean Architecture)**: API routes remain thin transport adapters. Business rules reside entirely in domain models and application use cases, isolated behind port interfaces for database persistence, document storage, and LLM inference.

---

## 2. PDF Extraction & Layout-Aware AST Reconstruction

### 2.1 Two-Tier Extraction Strategy
Real-world medical device manuals are predominantly digital vector PDFs containing selectable text, fonts, layout coordinates, and vector vector shapes. However, scanned pages or rasterized revisions occasionally occur.

The extraction pipeline implements a two-tier strategy:
- **Tier 1 (Vector Text & Spatial Layout - Primary)**: Uses PyMuPDF (`fitz`) to extract raw character streams, word bounding boxes, font weights, font sizes, and line coordinates. This preserves precise reading order, indentation, and table cell boundaries without OCR distortions.
- **Tier 2 (OCR Fallback)**: If a page contains insufficient selectable text density or corrupted glyph encodings, an OCR pipeline is engaged as a fallback.

### 2.2 Hierarchy Reconstruction & Depth Tracking
The document AST is reconstructed into typed `DocumentNode` objects (`SECTION`, `PARAGRAPH`, `TABLE`, `LIST`, `LIST_ITEM`):
- **Heading Identification**: Regular expressions detect standard numbered hierarchical prefixes (`^(\d+(\.\d+)*)\s+(.*)$`) combined with font-weight/size heuristics for unnumbered headings.
- **Declared Depth Calculation**: The number of dot-separated segments directly determines declared depth (e.g., `1` is depth 1; `2.1` is depth 2; `2.1.1.1` is depth 4).
- **Skipped-Level Parent Attachment**: In regulatory manuals, numbering occasionally omits intermediary levels. For heading `2.1.1.1 Battery Life Under Typical Use` (depth 4) where parent `2.1.1` does not exist:
  - The node's **declared depth (4) is strictly preserved**.
  - The node is attached to the **nearest existing numbered ancestor (`2.1`)**.
  - A structured warning (`MISSING_PARENT_SECTION`) is recorded, explicitly referencing the node's persistent ID.
  - **No synthetic or hallucinated section nodes are invented**.
- **Preservation of Source Order**: Sections are stored in physical reading order rather than artificial numerical sort order. For example, Section `3.4 Pre-Measurement Checklist` physically precedes Section `3.3 Measurement Procedure` on Page 2 of the CT-200 manual; the parser preserves this natural source order (`3.4` at `source_order=15`, `3.3` at `source_order=18`).

### 2.3 Verified PDF Edge Cases
- **Character Encoding & Dash Normalization**:
  In the Specifications table (Table 1, Section 2.1), the pressure range is written as `0–300 mmHg` using an en-dash (`\u2013`, `–`). The extractor faithfully captures and normalizes the unicode representation rather than truncating or corrupting it to an ASCII hyphen (`-`), ensuring that downstream assertions and diff comparisons reflect the actual printed manual.
- **Multi-Line Table Cell Reconstruction**:
  In Table 4.2 (*Error Codes*), cells wrap across multiple visual lines (e.g., `"Motion artifact detected during measurement"`, `"Aborts measurement, initiates rapid deflation within 2 seconds"`). The table extractor detects column x-coordinate boundaries, groups fragments by row bounding box, and merges wrapped lines with space normalization into atomic cell values.
- **Context-Scoped Section Titles**:
  Certain headings recur across manuals (e.g., `"Error Codes"` or `"Maintenance"`). The hierarchy tracks full ancestry paths (`parent_logical_path`) so that identical titles under different parents remain strictly distinct logical entities.

---

## 3. Versioning, Cryptographic Hashing & Cross-Version Matching

### 3.1 Content Hashing & Immutability
- **Document Level**: Each ingested PDF file is fingerprinted with a SHA-256 digest (`compute_file_sha256`). Re-ingesting an identical PDF returns the existing `DocumentVersion` without duplicating rows.
- **Node Level**: Each `NodeVersion` receives a SHA-256 content hash computed over its normalized heading, text body, and structured table data:
  $$\text{content\_hash} = \text{SHA256}(\text{normalized\_heading} + "::" + \text{normalized\_content\_or\_json})$$
- If body text or table rows change between manual revisions, the `content_hash` changes deterministically.

### 3.2 Persistent Logical Nodes vs. Versioned Nodes
To track requirements across manual revisions without conflating versions:
- **`LogicalNode`**: A stable semantic identity across versions (e.g., `sec_battery_life`, `table_error_codes`).
- **`NodeVersion`**: An immutable snapshot tied to a specific `DocumentVersion`, capturing text, structured table rows, bounding boxes, page locations, and content hash at that exact point in time.

### 3.3 3-Pass Cross-Version Matching Heuristic
When ingesting a new version $V_{k}$ relative to existing baseline $V_{k-1}$:
1. **Pass 1 — Strict Triple Match**: Exact match on `(section_number, normalized_heading, parent_logical_path)`. Confidently reuses the existing `logical_node_id`.
2. **Pass 2 — Structural Heading & Parent Path**: If section numbers change but the normalized heading and parent ancestry match unambiguously, the existing `logical_node_id` is assigned.
3. **Pass 3 — Ambiguity & Unresolved Flagging**:
   - If candidate matches have low confidence or conflicting parent lineages, the node is flagged with `status="unresolved"` in `VersionMatchORM`.
   - Truly new sections (e.g., Section `5.3 Data Export` in v2) receive a freshly generated `LogicalNode`.

---

## 4. Version-Pinned Selections & QA Test Case Generation

### 4.1 Immutability of Test Boundaries
Testing medical devices requires specifying exact requirement scopes:
- `POST /api/v1/selections`: Creates a named selection tied to a single `document_version_id` and an ordered list of `node_version_ids`.
- **Validation**: Rejects cross-document nodes, mixed document versions, and duplicate IDs with `HTTP 422`.
- **Pinning**: The selection references immutable `NodeVersion` records. Even when v2 is subsequently ingested, the selection continues pointing to the exact v1 snapshots, text, and hashes that existed at creation time.

### 4.2 QA Test Case Synthesis & Context Expansion
- **Context Expansion & Deduplication**:
  If a selection includes a section node, all of its descendant nodes are included in the prompt context. If a user also directly selected a child node, it is deduplicated so each node appears in context exactly once.
- **Prompt Synthesis**:
  Generates a versioned prompt containing:
  - System instructions specifying strict JSON schema output.
  - Contextual requirements formatted with node IDs, headings, and text.
  - Requirement that every generated test case cite a non-empty subset of valid `node_version_ids`.

### 4.3 Two-Tier Validation & Recovery Loop
1. **Pydantic Schema Validation**:
   Enforces:
   - Root object matching `TestSuiteOutput`.
   - Between 3 and 5 test cases.
   - Non-empty `id`, `title`, `preconditions`, `steps` (`step_number`, `action`, `expected_result`).
2. **Semantic Node Reference Validation**:
   - Every cited node in `node_references` must exist within the selection's expanded context.
   - Hallucinated or external node IDs are rejected.
3. **Self-Healing Recovery Loop**:
   - If attempt 1 fails schema or reference validation, the system executes **at most one retry**.
   - The retry prompt includes concise, structured validation feedback detailing the exact schema violations or invalid references.
   - If attempt 2 succeeds, the result is saved as `SUCCESS`.
   - If attempt 2 fails, the record is stored as `FAILED` with error messages recorded, masking partial or corrupt test cases.

---

## 5. Dual Storage Architecture: SQLite + Append-Only JSON Store

### 5.1 Storage Distribution & Tradeoffs
| Responsibility | Storage Technology | Justification |
| :--- | :--- | :--- |
| **Relational Hierarchy & Metadata** | **SQLite (via SQLAlchemy 2.0)** | Documents, versions, logical nodes, node versions, and selections require fast hierarchical querying, foreign key integrity, ACID transactions, and indexed lookup by version or node. |
| **Generated LLM Outputs** | **Append-Only JSON Document Store** | LLM generations are structured JSON documents (3–5 test cases with nested steps, preconditions, and execution metadata). JSON storage is append-only, human-auditable, zero-migration, and directly compatible with MongoDB document models. |
| **Cross-System Indexing** | **`GenerationIndexORM` & `GenerationSourceNodeORM`** | Normalized SQLite tables index each generation by `selection_id`, `logical_node_id`, and `node_version_id`, enabling instant discovery without full-disk JSON scans. |

---

## 6. Retrieval-Time Staleness & Deterministic Impact Reporting

### 6.1 Dynamic Evaluation (Zero Mutation of Test Artifacts)
When `GET /api/v1/generations/{generation_id}` is invoked:
- The saved JSON file on disk is **never modified, regenerated, or deleted**.
- The service dynamically queries SQLite for the latest ingested version of the same manual.
- For every source snapshot saved in the generation, the system matches its `logical_node_id` against the latest manual revision.

### 6.2 Per-Source Staleness Rules
- **`current`**: Latest logical node exists and its SHA-256 `content_hash` matches the pinned generation snapshot.
- **`stale`**: Latest logical node exists, but its `content_hash` differs (text or table cells modified).
- **`removed`**: Node was present in baseline, but its logical node is absent from the latest revision.
- **`unknown`**: Latest manual version is unavailable or cross-version matching was flagged as `unresolved`.

### 6.3 Strict Aggregate Priority Ladder
To ensure mixed-source states are never masked by a single current node:
$$\textbf{unknown} > \textbf{removed} > \textbf{stale} > \textbf{current}$$
Detailed per-source reports are always returned so engineering teams can review individual requirements.

### 6.4 Deterministic Heuristic Impact Tags
Instead of invoking an LLM for classification, the system applies transparent, rule-based heuristics over unified text diffs:
- **`NUMERICAL_SPECIFICATION_CHANGED`**: Numeric values, decimals, percentages, or units differ (e.g., $300 \to 250$ cycles, $40 \to 30\text{ mmHg}$, $2.0 \to 1.5\text{ s}$).
- **`SAFETY_OR_ALARM_MODIFIED`**: Text diff matches safety-critical keywords (`alarm`, `warning`, `caution`, `hazard`, `danger`, `safety`, `overpressure`, `error`, `fault`, `cutoff`, `deflation`, `emergency`, `stop`, `interlock`, `abort`).
- **`FEATURE_OR_BEHAVIOR_CHANGED`**: Text diff indicates interface/protocol changes or error codes (`bluetooth`, `sync`, `export`, `csv`, `firmware`, `e\d+`).
- **`WORDING_ONLY_CHANGED`**: Editorial phrasing changes without altering numbers or safety terms.
- **Regulatory Disclaimer**: *“Impact tags are automated heuristic review hints, not medical or regulatory conclusions.”*

---

## 7. Current Limitations & Future Roadmap

1. **Deterministic Heuristics Scope**: Impact tags use keyword and regex heuristics. While deterministic and fast, complex linguistic nuances or non-English terminology fall back to `WORDING_ONLY_CHANGED`. Future work could integrate specialized medical ontologies (e.g., SNOMED CT or MedDRA).
2. **In-Memory Diff Computation**: Differencing large tables is performed dynamically at retrieval time. For enterprise manuals containing thousands of tables, an intermediate diff-cache keyed by `(v1_hash, v2_hash)` would optimize throughput.
3. **Multi-Column Complex Layouts**: Complex magazine-style multi-column text flows with interlaced figures currently rely on PyMuPDF block ordering. Adding layout-segmentation neural models (e.g., LayoutLM) would enhance robustness on arbitrary legacy PDFs.
