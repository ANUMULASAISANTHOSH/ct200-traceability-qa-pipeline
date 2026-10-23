"""Append-only JSON document store with atomic writes for LLM generations."""

import json
import os
import tempfile
import uuid
from pathlib import Path
from typing import Optional
from src.app.domain.models.qa import GenerationDocument


class JSONDocumentStore:
    """Filesystem storage manager writing immutable, append-only JSON generation records."""

    def __init__(self, base_path: str) -> None:
        self.base_path = Path(base_path)
        self.base_path.mkdir(parents=True, exist_ok=True)

    def get_document_path(self, generation_id: str) -> Path:
        """Resolve canonical target file path for a generation ID."""
        return self.base_path / f"{generation_id}.json"

    def save_generation(self, document: GenerationDocument) -> str:
        """Atomically persist a GenerationDocument to disk using a temp file and replace.
        
        Write sequence:
        1. Write payload to temporary file in the same directory (.tmp_<gen_id>_<uuid>.json).
        2. Flush and close file.
        3. Atomically replace temp file into target path ({generation_id}.json).
        
        Recovery behavior:
        If the process terminates before replace, the incomplete temp file is discarded.
        If replace completes but database index creation fails, the JSON document remains
        safely on disk as an immutable record that can be reconciled by re-indexing.
        """
        target_path = self.get_document_path(document.generation_id)
        temp_name = f".tmp_{document.generation_id}_{uuid.uuid4().hex[:8]}.tmp"
        temp_path = self.base_path / temp_name

        data = document.model_dump_json(indent=2)

        try:
            with open(temp_path, "w", encoding="utf-8") as f:
                f.write(data)
                f.flush()
                os.fsync(f.fileno())
            os.replace(temp_path, target_path)
        finally:
            if temp_path.exists():
                try:
                    temp_path.unlink()
                except OSError:
                    pass

        return str(target_path.resolve())

    def load_generation(self, generation_id: str) -> Optional[GenerationDocument]:
        """Read and validate a GenerationDocument by its generation ID."""
        path = self.get_document_path(generation_id)
        if not path.is_file():
            return None
        return self.load_from_path(str(path))

    def load_from_path(self, file_path: str) -> Optional[GenerationDocument]:
        """Read and validate a GenerationDocument from a direct file path."""
        p = Path(file_path)
        if not p.is_file():
            return None
        with open(p, "r", encoding="utf-8") as f:
            content = f.read()
        return GenerationDocument.model_validate_json(content)
