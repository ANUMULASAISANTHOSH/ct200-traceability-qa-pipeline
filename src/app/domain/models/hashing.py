"""Deterministic hashing utilities for PDF files and document tree nodes."""

import hashlib
import json
from typing import Any, Dict, Optional, Union
from pathlib import Path


def compute_file_sha256(file_input: Union[str, Path, bytes]) -> str:
    """Compute deterministic SHA-256 hexadecimal digest of a file or bytes buffer."""
    if isinstance(file_input, bytes):
        return hashlib.sha256(file_input).hexdigest()

    path = Path(file_input)
    hasher = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            hasher.update(chunk)
    return hasher.hexdigest()


def compute_node_content_hash(
    node_type: str,
    heading: Optional[str] = None,
    section_number: Optional[str] = None,
    text: str = "",
    structured_data: Optional[Dict[str, Any]] = None,
) -> str:
    """Compute a deterministic SHA-256 digest over normalized text and structured content.
    
    Any meaningful change to the title, section number, body text, or table/list
    data will produce a completely different hash digest.
    """
    norm_type = (node_type or "").strip().lower()
    norm_sec = (section_number or "").strip()
    norm_heading = (heading or "").strip()
    # Normalize line endings and leading/trailing whitespace
    norm_text = "\n".join(line.rstrip() for line in (text or "").strip().splitlines())

    if structured_data:
        # Canonical JSON with sorted keys and no extraneous whitespace
        struct_json = json.dumps(structured_data, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    else:
        struct_json = ""

    payload = (
        f"type:{norm_type}\n"
        f"sec:{norm_sec}\n"
        f"heading:{norm_heading}\n"
        f"text:{norm_text}\n"
        f"struct:{struct_json}"
    )

    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
