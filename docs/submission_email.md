# Assignment Submission Email Draft

**Subject:** Submission: Tri9T AI Engineering Assignment – CT-200 Regulatory Manual Traceability & QA Suite – [Your Name]

---

**Dear Tri9T AI Hiring Team,**

I am excited to submit my completed implementation for the **Tri9T AI CT-200 Regulatory Manual Traceability & QA Suite** assignment.

This submission delivers a complete, modular, and production-ready backend that solves the core challenge of medical device verification: maintaining immutable, verifiable QA test cases across evolving regulatory manuals, and detecting requirement staleness dynamically when newer manual revisions are released.

---

### Key Deliverables & Implemented Architecture

1. **Two-Tier PDF Extraction & Hierarchy Reconstruction**:
   - Uses PyMuPDF layout analysis for vector text, coordinate bounding boxes, font styling, and multi-line wrapped table cells (e.g., Table 4.2 Error Codes).
   - Resolves structural anomalies: correctly attaches skipped-level heading `2.1.1.1` (depth 4) to `2.1` with an explicit `MISSING_PARENT_SECTION` warning without hallucinating phantom nodes, and preserves physical reading order (Section `3.4` before `3.3`).
   - Normalizes unicode characters accurately (e.g., en-dash `0–300 mmHg` in specifications).

2. **Immutable Document Versioning & Cross-Version Lineage**:
   - Every ingested PDF and node snapshot is cryptographically fingerprinted using SHA-256 hashes.
   - Decouples persistent `LogicalNode` identities from immutable `NodeVersion` snapshots using a 3-pass matching heuristic.

3. **Version-Pinned Selections & Validated QA Generation**:
   - Engineers define test boundaries pinned to specific `NodeVersion` records, strictly preventing cross-document contamination.
   - Implements a pluggable `LLMClient` interface with a deterministic `MockLLMClient` default (no API keys required).
   - Validates generated test suites against strict Pydantic schemas and enforces that all cited references belong to the selection context, backed by an automatic 1-retry recovery loop.

4. **Hybrid Dual Storage Architecture**:
   - Relational metadata, parent-child hierarchies, and cross-store indices are managed via **SQLite (SQLAlchemy 2.0)**.
   - Generated test cases and validation attempts are stored in an **append-only JSON Document Store** (structured for 1-to-1 compatibility with MongoDB).
   - High-performance indexed lookups enable querying generations by selection, logical node, or node version without scanning JSON files on disk.

5. **Retrieval-Time Staleness & Deterministic Impact Reporting**:
   - Evaluates staleness dynamically when `GET /api/v1/generations/{generation_id}` is requested, leaving saved test artifacts completely unmutated.
   - Enforces an aggregate priority ladder: `unknown > removed > stale > current`.
   - Generates transparent, deterministic heuristic impact tags (`NUMERICAL_SPECIFICATION_CHANGED`, `SAFETY_OR_ALARM_MODIFIED`, `FEATURE_OR_BEHAVIOR_CHANGED`, `WORDING_ONLY_CHANGED`) accompanied by regulatory advisory disclaimers.

---

### Quick Verification & Demonstration

The entire test suite and end-to-end demo can be run immediately without internet access or API credentials:

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Run the complete test suite (53 tests passing)
pytest -v

# 3. Run the complete end-to-end demonstration script
python scripts/demo_e2e.py
```

### Repository & Documentation Links
- **Repository URL**: https://github.com/ANUMULASAISANTHOSH/ct200-traceability-qa-pipeline
- **Video Walkthrough (Optional)**: `[Insert Loom / Drive Link if recorded, or leave blank]`
- **Key Documentation**:
  - `README.md`: Setup, configuration, server commands, and API reference examples.
  - `docs/architecture.md`: System architecture, data flow diagrams, and layer contracts.
  - `docs/approach.md`: Technical deep-dive into PDF parsing, matching algorithms, and staleness rules.
  - `docs/decision-log.md`: Detailed engineering tradeoffs and answers to design questions.

Thank you for reviewing my submission. I look forward to discussing the technical design and architectural decisions with your team.

Sincerely,  
**[Your Name]**  
[Your Email Address]  
[Your Phone Number]  
[Your LinkedIn Profile]
