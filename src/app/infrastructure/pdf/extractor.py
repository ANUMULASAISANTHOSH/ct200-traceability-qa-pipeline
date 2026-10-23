"""Two-tier PDF text and layout extraction engine."""

import os
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field
import pypdf


class LayoutToken(BaseModel):
    """Spatial token extracted from PDF vector stream."""
    text: str
    x: float
    y: float
    font_size: float
    font_name: str
    is_bold: bool


class RawPageData(BaseModel):
    """Raw extraction data for a single PDF page."""
    page_number: int
    text_content: str
    tokens: List[LayoutToken] = Field(default_factory=list)
    is_ocr: bool = False
    warning: Optional[str] = None


def normalize_text_ligatures(text: str) -> str:
    """Normalize common unicode ligatures and typography characters."""
    replacements = {
        "\ufb00": "ff",
        "\ufb01": "fi",
        "\ufb02": "fl",
        "\ufb03": "ffi",
        "\ufb04": "ffl",
        "\u2011": "-",  # non-breaking hyphen
        "\u00a0": " ",  # non-breaking space
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    return text


class PDFExtractor:
    """Extracts text and layout tokens with selectable-text verification and OCR fallback."""

    def __init__(self, min_char_threshold: int = 40):
        self.min_char_threshold = min_char_threshold

    def extract_document(self, pdf_path: str) -> List[RawPageData]:
        """Extract layout tokens and text across all pages in the document."""
        if not os.path.exists(pdf_path):
            raise FileNotFoundError(f"PDF file not found at: {pdf_path}")

        reader = pypdf.PdfReader(pdf_path)
        pages_data: List[RawPageData] = []

        for page_idx, page in enumerate(reader.pages):
            page_num = page_idx + 1
            tokens: List[LayoutToken] = []

            def visitor(text: str, cm: Any, tm: Any, font_dict: Any, font_size: float) -> None:
                stripped = text.strip()
                if not stripped:
                    return
                font_name = ""
                if font_dict and isinstance(font_dict, dict):
                    font_name = str(font_dict.get("/BaseFont", ""))
                is_bold = "Bold" in font_name or "bold" in font_name.lower()
                clean_text = normalize_text_ligatures(text)

                tokens.append(
                    LayoutToken(
                        text=clean_text,
                        x=round(float(tm[4]), 2),
                        y=round(float(tm[5]), 2),
                        font_size=round(float(font_size), 2),
                        font_name=font_name,
                        is_bold=is_bold,
                    )
                )

            page.extract_text(visitor_text=visitor)
            raw_text = normalize_text_ligatures(page.extract_text() or "")

            # Sort tokens in visual reading order (y ascending from top, x ascending from left)
            tokens.sort(key=lambda tok: (round(tok.y, 1), tok.x))

            # Quality check: verify text length
            if len(raw_text.strip()) < self.min_char_threshold:
                # Page has low density or is image-based. Attempt OCR fallback if possible.
                ocr_text, ocr_tokens = self._fallback_ocr(pdf_path, page_num)
                if ocr_text:
                    pages_data.append(
                        RawPageData(
                            page_number=page_num,
                            text_content=ocr_text,
                            tokens=ocr_tokens,
                            is_ocr=True,
                            warning=f"Low text density ({len(raw_text.strip())} chars) on page {page_num}; OCR fallback triggered",
                        )
                    )
                    continue

            pages_data.append(
                RawPageData(
                    page_number=page_num,
                    text_content=raw_text,
                    tokens=tokens,
                    is_ocr=False,
                )
            )

        return pages_data

    def _fallback_ocr(self, pdf_path: str, page_number: int) -> tuple[str, List[LayoutToken]]:
        """Fallback to OCR using pytesseract when vector text is unavailable."""
        try:
            from pdf2image import convert_from_path
            import pytesseract

            images = convert_from_path(pdf_path, first_page=page_number, last_page=page_number)
            if not images:
                return "", []

            img = images[0]
            data = pytesseract.image_to_data(img, output_type=pytesseract.Output.DICT)
            text_lines = []
            tokens: List[LayoutToken] = []

            for i in range(len(data["text"])):
                w = data["text"][i].strip()
                if not w:
                    continue
                tokens.append(
                    LayoutToken(
                        text=w,
                        x=float(data["left"][i]),
                        y=float(data["top"][i]),
                        font_size=11.0,
                        font_name="OCR",
                        is_bold=False,
                    )
                )
                text_lines.append(w)

            return " ".join(text_lines), tokens
        except Exception:
            # If OCR fails or dependencies are missing, return empty
            return "", []
