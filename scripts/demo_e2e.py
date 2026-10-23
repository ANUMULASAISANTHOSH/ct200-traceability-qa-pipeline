#!/usr/bin/env python3
"""End-to-End Demonstration Script for Tri9T AI CT-200 Regulatory Manual QA Suite.

Walks through the full regulatory lifecycle:
1. Ingest CT-200 v1 PDF and inspect its reconstructed hierarchy.
2. Create an immutable version-pinned node selection.
3. Synthesize validated QA test cases using the deterministic mock LLM adapter (no API key needed).
4. Ingest CT-200 v2 PDF and inspect detected structural additions, removals, and changes.
5. Retrieve the v1 generation and demonstrate dynamic staleness evaluation and heuristic impact tags.

Usage:
    python scripts/demo_e2e.py
"""

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path
from starlette.testclient import TestClient

# Ensure project root is in sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.app.api.dependencies import (
    get_db,
    get_json_document_store,
    get_llm_client_dependency,
)
from src.app.config import Settings
from src.app.factory import create_app
from src.app.infrastructure.db import get_engine, get_session_factory, init_db
from src.app.infrastructure.llm.client import MockLLMClient
from src.app.infrastructure.storage.json_store import JSONDocumentStore


V1_PDF = "data/ct200_manual.pdf"
V2_PDF = "data/ct200_manual_v2.pdf"


def print_banner(text: str) -> None:
    sep = "=" * 80
    print(f"\n{sep}\n  {text}\n{sep}")


def print_step(num: int, title: str) -> None:
    print(f"\n[{num}/5] >>> {title}")
    print("-" * 80)


def main() -> int:
    print_banner("Tri9T AI CT-200 Regulatory Manual Traceability & QA Suite: End-to-End Demo")

    # Verify input PDFs exist
    v1_path = PROJECT_ROOT / V1_PDF
    v2_path = PROJECT_ROOT / V2_PDF
    if not v1_path.exists() or not v2_path.exists():
        print(f"Error: Missing source PDF manuals at '{v1_path}' or '{v2_path}'", file=sys.stderr)
        return 1

    temp_dir = tempfile.mkdtemp(prefix="ct200_demo_")
    db_path = os.path.join(temp_dir, "demo.db")
    json_store_dir = os.path.join(temp_dir, "json_store")

    try:
        # Initialize SQLite database & session
        engine = get_engine(f"sqlite:///{db_path}")
        init_db(engine)
        session_factory = get_session_factory(engine)
        json_store = JSONDocumentStore(base_path=json_store_dir)
        mock_llm = MockLLMClient()

        # Build application with dependency overrides
        settings = Settings(
            app_name="CT-200 E2E Demo",
            app_env="demo",
            database_url=f"sqlite:///{db_path}",
            json_store_path=json_store_dir,
            mock_llm=True,
        )
        app = create_app(settings)

        def override_get_db():
            session = session_factory()
            try:
                yield session
            finally:
                session.close()

        app.dependency_overrides[get_db] = override_get_db
        app.dependency_overrides[get_json_document_store] = lambda: json_store
        app.dependency_overrides[get_llm_client_dependency] = lambda: mock_llm

        client = TestClient(app)

        # =====================================================================
        # Step 1: Ingest CT-200 v1 and inspect hierarchy
        # =====================================================================
        print_step(1, "Ingest CT-200 v1 and Inspect Extracted Hierarchy")
        ingest_payload_v1 = {
            "file_path": str(v1_path),
            "document_slug": "ct200",
            "document_title": "CT-200 Sphygmomanometer Manual",
            "version_label": "v1",
        }
        res_v1 = client.post("/api/v1/documents/ingest", json=ingest_payload_v1)
        if res_v1.status_code != 201:
            print(f"Ingestion failed: {res_v1.text}")
            return 1
        data_v1 = res_v1.json()
        v1_id = data_v1["version_id"]
        print(f"Ingested Document Slug : {data_v1['document_slug']}")
        print(f"Version ID             : {v1_id} ({data_v1['version_label']})")
        print(f"Source PDF SHA-256     : {data_v1['sha256_hash']}")
        print(f"Total Extracted Nodes  : {data_v1['node_count']}")
        print(f"Parser Warnings Count  : {data_v1['warning_count']}")

        # Browse sections in v1
        sec_res = client.get(f"/api/v1/documents/ct200/sections?version_id={v1_id}")
        sections = sec_res.json()["sections"]
        print("\nExtracted Root Sections:")
        for s in sections:
            sec_num = s['section_number'] or '-'
            heading = s['heading'] or 'Untitled'
            print(f"  * Section {sec_num:4s} | {heading:35s} | Pages {s['page_start']}-{s['page_end']} | {s['child_count']} direct children")

        # Search for key nodes to verify specific extraction requirements
        search_res = client.get(f"/api/v1/documents/ct200/search?version_id={v1_id}&query=battery")
        search_items = search_res.json()["results"]
        print(f"\nKeyword Search ('battery'): Found {len(search_items)} matching nodes.")
        for item in search_items[:2]:
            print(f"  - [{item['node_type'].upper()}] {item['heading'] or item['section_number']}: \"{item['snippet']}\"")

        # =====================================================================
        # Step 2: Select source nodes pinned to the v1 document version
        # =====================================================================
        print_step(2, "Create Version-Pinned Selection of Critical Nodes")
        # Find specific nodes to pin:
        # 1. Section 1 heading node
        # 2. Battery Life paragraph (Sec 2.1.1.1: 300 cycles)
        # 3. Inflation paragraph (Sec 3.2: 40 mmHg)
        # 4. Error Codes table (Sec 4.2: 2.0s deflation)
        # Select nodes to pin:
        # 1. Section 1 heading node
        sec1_node_id = sections[0]["id"]

        # 2. Battery Life paragraph or section
        r_bat = client.get(f"/api/v1/documents/ct200/search?version_id={v1_id}&query=battery")
        bat_node_id = r_bat.json()["results"][0]["node_version_id"]

        # 3. Inflation paragraph (Sec 3.2: 40 mmHg)
        r_inf = client.get(f"/api/v1/documents/ct200/search?version_id={v1_id}&query=inflation")
        inf_node_id = r_inf.json()["results"][0]["node_version_id"]

        # 4. Error Codes table (Sec 4.2)
        r_tbl = client.get(f"/api/v1/documents/ct200/search?version_id={v1_id}&query=error")
        tbl_node_id = r_tbl.json()["results"][0]["node_version_id"]

        selection_payload = {
            "name": "CT-200 v1 Core Regulatory Scope",
            "document_version_id": v1_id,
            "node_version_ids": [sec1_node_id, bat_node_id, inf_node_id, tbl_node_id],
        }
        sel_res = client.post("/api/v1/selections", json=selection_payload)
        sel_data = sel_res.json()
        selection_id = sel_data["id"]
        print(f"Selection Created  : {sel_data['name']}")
        print(f"Selection ID       : {selection_id}")
        print(f"Pinned Version     : {sel_data['document_version_id']}")
        print("Pinned Source Nodes:")
        for node in sel_data["nodes"]:
            print(f"  - [{node['node_type']:9s}] ID: {node['node_version_id']} | Hash: {node['content_hash'][:12]}... | {node['heading'] or 'Paragraph'}")

        # =====================================================================
        # Step 3: Generate QA content using Mock LLM adapter
        # =====================================================================
        print_step(3, "Synthesize Validated QA Test Cases via Mock LLM Adapter")
        gen_res = client.post(
            f"/api/v1/selections/{selection_id}/generations",
            headers={"Idempotency-Key": "demo-run-e2e-001"},
        )
        gen_data = gen_res.json()
        generation_id = gen_data["generation_id"]
        print(f"Generation ID      : {generation_id}")
        print(f"Status             : {gen_data['status']}")
        print(f"Prompt Version     : {gen_data['prompt_version']}")
        print(f"Schema Version     : {gen_data['schema_version']}")
        print(f"LLM Provider/Model : {gen_data['provider']} / {gen_data['model']}")
        print(f"Test Cases Count   : {len(gen_data['test_cases'])}")

        print("\nGenerated Test Cases with Specific Requirement Traceability:")
        for tc in gen_data["test_cases"]:
            print(f"\n  * Test Case [{tc['id']}]: {tc['title']}")
            print(f"    Preconditions: {', '.join(tc['preconditions'])}")
            print(f"    Step 1: Action: {tc['steps'][0]['action']}")
            print(f"            Expected: {tc['steps'][0]['expected_result']}")
            print(f"    Referenced Node IDs: {tc['node_references']}")

        # Show JSON store file path
        doc_path = json_store.get_document_path(generation_id)
        print(f"\nImmutable JSON Artifact Location:\n  {doc_path} (Exists: {doc_path.exists()})")

        # =====================================================================
        # Step 4: Ingest CT-200 v2 and show detected diffs
        # =====================================================================
        print_step(4, "Ingest CT-200 v2, Verify Distinct Version Identity, and Detect Changes")
        ingest_payload_v2 = {
            "file_path": str(v2_path),
            "document_slug": "ct200",
            "document_title": "CT-200 Sphygmomanometer Manual",
            "version_label": "v2",
        }
        res_v2 = client.post("/api/v1/documents/ingest", json=ingest_payload_v2)
        data_v2 = res_v2.json()
        v2_id = data_v2["version_id"]
        print(f"v1 Version ID          : {v1_id} ({data_v1['version_label']})")
        print(f"v2 Version ID          : {v2_id} ({data_v2['version_label']})")
        print(f"v2 Source PDF SHA-256  : {data_v2['sha256_hash']}")
        print(f"v2 Extracted Nodes     : {data_v2['node_count']}")

        # Verify distinct version IDs
        assert v1_id != v2_id, f"ERROR: v1 and v2 must have distinct version IDs, got {v1_id}"
        print(f"\n[OK] Version Identity Verified: Distinct persistent IDs assigned ({v1_id} != {v2_id})")

        # Verify v1 immutability by re-fetching v1 sections after v2 ingestion
        v1_check = client.get(f"/api/v1/documents/ct200/sections?version_id={v1_id}").json()
        assert v1_check["version_id"] == v1_id
        assert len(v1_check["sections"]) == len(sections)
        for s_orig, s_curr in zip(sections, v1_check["sections"]):
            assert s_orig["content_hash"] == s_curr["content_hash"]
        print("[OK] v1 Immutability Verified: v1 hierarchy, section count, and hashes remain 100% unchanged after v2 ingestion.")

        # Full Document Comparison
        diff_res = client.get(f"/api/v1/documents/ct200/diff?v1_version_id={v1_id}&v2_version_id={v2_id}")
        diff_data = diff_res.json()
        counts = diff_data["summary_counts"]
        print("\nCross-Version Semantic Diff Summary:")
        print(f"  - Unchanged Nodes : {counts.get('unchanged', 0)}")
        print(f"  - Changed Nodes   : {counts.get('changed', 0)}")
        print(f"  - Added Nodes     : {counts.get('added', 0)}")
        print(f"  - Removed Nodes   : {counts.get('removed', 0)}")
        print(f"  - Unresolved      : {counts.get('unresolved', 0)}")

        print("\nKey Changed / Added / Removed Specifications Detected:")
        for d in diff_data["node_diffs"]:
            if d["status"] in ("changed", "added", "removed"):
                print(f"\n  [{d['status'].upper()}] Logical Node: {d['logical_node_id']}")
                lines = d["diff_summary"].splitlines()[:5]
                for line in lines:
                    print(f"    {line}")

        # =====================================================================
        # Step 5: Retrieve earlier generation and report staleness & impact
        # =====================================================================
        print_step(5, "Retrieve v1 Generation with Dynamic Staleness & Heuristic Impact Reporting")
        get_gen_res = client.get(f"/api/v1/generations/{generation_id}")
        get_data = get_gen_res.json()

        print(f"Retrieved Generation ID : {generation_id}")
        print(f"Original Pinned Version : {data_v1['version_label']} ({v1_id})")
        print(f"Active Latest Version   : {get_data['latest_version_label']} ({get_data['latest_document_version_id']})")
        print(f"Aggregate Status        : {get_data['aggregate_status'].upper()} (Priority ladder: unknown > removed > stale > current)")
        print(f"Disclaimer              : {get_data['traceability']['disclaimer']}")

        # Build mapping of source reports for test case traceability demonstration
        src_map = {s["pinned_node_version_id"]: s for s in get_data["source_traceability"]}

        print("\nTest Case Traceability to Requirement Staleness Status:")
        for tc in gen_data["test_cases"]:
            ref_id = tc["node_references"][0] if tc["node_references"] else None
            src_info = src_map.get(ref_id)
            status_str = src_info["status"].upper() if src_info else "UNKNOWN"
            tags_str = f" | Tags: {src_info['impact_tags']}" if src_info and src_info["impact_tags"] else ""
            print(f"  * [{tc['id']}] '{tc['title']}'")
            print(f"    Cites Node : {ref_id} -> Status: {status_str}{tags_str}")

        print("\nPer-Source Traceability & Impact Analysis:")
        for src in get_data["source_traceability"]:
            print(f"\n  * Logical Node : {src['logical_node_id']}")
            print(f"    Status       : {src['status'].upper()}")
            print(f"    Pinned Hash  : {src['original_content_hash'][:12]}... (nv: {src['pinned_node_version_id']})")
            print(f"    Latest Hash  : {src['latest_content_hash'][:12] if src['latest_content_hash'] else 'N/A'}... (nv: {src['latest_node_version_id'] or 'None'})")
            print(f"    Impact Tags  : {src['impact_tags']}")
            print(f"    Diff Summary : {src['diff_summary'].splitlines()[0] if src['diff_summary'] else 'None'}")

        # Demonstrate single-filter queries
        print("\nDemonstrating Single-Filter Queries:")
        # 1. Filter by selection_id
        q_sel = client.get(f"/api/v1/generations?selection_id={selection_id}")
        print(f"  * Query by selection_id: Found {len(q_sel.json())} generation(s) (Status: {q_sel.status_code})")
        # 2. Filter by logical_node_id
        target_log_id = get_data["source_traceability"][1]["logical_node_id"]
        q_log = client.get(f"/api/v1/generations?logical_node_id={target_log_id}")
        print(f"  * Query by logical_node_id ('{target_log_id}'): Found {len(q_log.json())} generation(s)")
        # 3. Validation: zero filters -> 422
        q_err = client.get("/api/v1/generations")
        print(f"  * Query with 0 filters: Expected 422 -> Received HTTP {q_err.status_code} ({q_err.json()['detail']})")

        print_banner("Demo Complete: All 5 Verification Steps Executed Successfully!")
        return 0

    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
