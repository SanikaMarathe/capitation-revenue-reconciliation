"""Deterministic pre-parse of rate documents into text where every fragment has a reference.

The LLM must cite one of these references for each rate (source_ref), and code then checks
the cited fragment really contains that number. Merged Excel cells are flattened (value sits
in the top-left cell); PDF tables become pipe-separated rows.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

import pdfplumber
from openpyxl import load_workbook

from schemas.enums import DocFormat


@dataclass(frozen=True)
class ParsedDocument:
    filename: str
    format: DocFormat
    sha256: str
    text: str                       # what the LLM sees
    refs: dict[str, str]            # source_ref -> fragment text (for the cross-check)


def sha256_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def detect_format(path: Path) -> DocFormat:
    ext = path.suffix.lower()
    if ext == ".pdf":
        return DocFormat.PDF
    if ext in (".xlsx", ".xlsm"):
        return DocFormat.XLSX
    return DocFormat.EMAIL


def parse(path: Path) -> ParsedDocument:
    fmt = detect_format(path)
    text, refs = {DocFormat.PDF: _pdf, DocFormat.XLSX: _xlsx, DocFormat.EMAIL: _email}[fmt](path)
    return ParsedDocument(path.name, fmt, sha256_of(path), text, refs)


def _pdf(path: Path) -> tuple[str, dict[str, str]]:
    out: list[str] = []
    refs: dict[str, str] = {}
    with pdfplumber.open(path) as pdf:
        for p, page in enumerate(pdf.pages, start=1):
            out.append(f"=== page {p} text ===")
            for n, line in enumerate((page.extract_text() or "").splitlines(), start=1):
                ref = f"p{p}:l{n}"
                refs[ref] = line
                out.append(f"[{ref}] {line}")
            for t, table in enumerate(page.extract_tables(), start=1):
                out.append(f"=== page {p} table {t} ===")
                for r, row in enumerate(table, start=1):
                    cells = []
                    for c, value in enumerate(row, start=1):
                        ref = f"p{p}:t{t}:r{r}:c{c}"
                        refs[ref] = value or ""
                        cells.append(f"[{ref}] {value or ''}")
                    out.append(" | ".join(cells))
    return "\n".join(out), refs


def _xlsx(path: Path) -> tuple[str, dict[str, str]]:
    wb = load_workbook(path, data_only=True)
    out: list[str] = []
    refs: dict[str, str] = {}
    for ws in wb.worksheets:
        out.append(f"=== sheet '{ws.title}' ===")
        for row in ws.iter_rows():
            cells = []
            for cell in row:
                if cell.value is None:
                    continue
                ref = f"{ws.title}!{cell.coordinate}"
                refs[ref] = str(cell.value)
                cells.append(f"[{ref}] {cell.value}")
            if cells:
                out.append(" | ".join(cells))
    return "\n".join(out), refs


def _email(path: Path) -> tuple[str, dict[str, str]]:
    out: list[str] = []
    refs: dict[str, str] = {}
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        ref = f"email:L{n}"
        refs[ref] = line
        out.append(f"[{ref}] {line}")
    return "\n".join(out), refs
