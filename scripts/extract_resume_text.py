#!/usr/bin/env python3
"""Extract plain text from resume PDF for LLM matching."""

from pathlib import Path

import pdfplumber


def extract_resume_text(pdf_path: Path, output_path: Path) -> str:
    text_parts = []
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            page_text = page.extract_text()
            if page_text:
                text_parts.append(page_text)
    full_text = "\n\n".join(text_parts)
    output_path.write_text(full_text, encoding="utf-8")
    print(f"Extracted {len(full_text)} characters from {pdf_path}")
    print(f"Saved to {output_path}")
    return full_text


if __name__ == "__main__":
    root = Path(__file__).resolve().parent.parent
    pdf = root / "config" / "resume.pdf"
    out = root / "config" / "resume_text.txt"
    extract_resume_text(pdf, out)
