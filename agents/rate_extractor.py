"""Rate Ingestion Agent: document -> structured, validated DRAFT rate version.

1. deterministic parse (text with a reference on every fragment)
2. one LLM call -> RateNotice (schema-validated, retried once, else rejected)
3. deterministic validation + source cross-check
4. diff vs. the rates currently in force
5. saved as a DRAFT; only a human approval (governance.approvals) makes it usable
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from agents.doc_parse import ParsedDocument, parse
from engine.pricing import RateBook
from engine.rate_validation import CellDiff, ValidationReport, diff, month_start, validate
from governance.audit import append_event
from llm.client import LLMClient
from llm.prompts import load_prompt
from schemas.enums import ActorType, Category, RateVersionStatus, Region
from schemas.rates import RateNotice, RateRow, RateVersion
from storage import repo
from storage.db import transaction

AGENT = "rate_extractor"
TASK = "rate_extraction"


@dataclass
class ExtractionResult:
    version_id: str
    document_id: str
    notice: RateNotice
    rows: list[RateRow]
    report: ValidationReport
    diff: list[CellDiff]
    cache_hit: bool
    duplicate_of: str | None = None   # same file already ingested


def notice_to_rows(notice: RateNotice, version_id: str) -> list[RateRow]:
    return [RateRow(version_id=version_id, region=r.region, category=r.category, effective_from=r.effective_from,
                    effective_to=month_start(r.effective_to) if r.effective_to else None,
                    pmpm=Decimal(r.pmpm), source_ref=r.source_ref) for r in notice.rates]


def _prior_rates(book: RateBook, as_of: datetime, month: date) -> list[dict]:
    out = []
    for region in Region:
        for category in Category:
            kr = book.lookup(region.value, category.value, month, as_of)
            if kr:
                out.append({"region": region.value, "category": category.value, "pmpm": str(kr.row.pmpm),
                            "version": kr.row.version_id})
    return out


def extract(conn, client: LLMClient, path: Path, *, received: date, version_id: str,
            as_of: datetime | None = None, flag_pct: Decimal = Decimal("15")) -> ExtractionResult:
    as_of = as_of or datetime.now()
    doc: ParsedDocument = parse(path)
    existing = conn.execute("SELECT document_id FROM source_documents WHERE sha256=?", [doc.sha256]).fetchone()

    book = RateBook(repo.rate_rows(conn), repo.rate_versions(conn))
    payload = {
        "document_text": doc.text,
        "document_format": doc.format.value,
        "prior_rates": _prior_rates(book, as_of, month_start(received)),
        "known_regions": [r.value for r in Region],
        "known_categories": [c.value for c in Category],
    }
    result = client.call(agent=AGENT, task=TASK, prompt=load_prompt("rate_extraction"), payload=payload,
                         output_model=RateNotice)
    notice = result.output
    rows = notice_to_rows(notice, version_id)
    report = validate(rows, kind=notice.kind, is_retroactive=notice.is_retroactive, published=notice.published_date,
                      confidence=notice.confidence, refs=doc.refs, book=book, as_of=as_of, change_flag_pct=flag_pct)
    cell_diff = diff(rows, book, as_of)

    if existing:
        return ExtractionResult(version_id, existing[0], notice, rows, report, cell_diff, result.cache_hit,
                                duplicate_of=existing[0])

    document_id = f"DOC-{doc.sha256[:12]}"
    version = RateVersion(version_id=version_id, kind=notice.kind, published_date=notice.published_date,
                          source_document_id=document_id, status=RateVersionStatus.DRAFT,
                          notes=" | ".join([notice.notes] + report.errors + report.flags).strip(" |"))
    with transaction(conn):
        conn.execute("INSERT INTO source_documents VALUES (?,?,?,?,?,?)",
                     [document_id, doc.filename, doc.format.value, received, doc.sha256,
                      "extracted" if report.approvable else "needs_fix"])
        repo.insert_rate_version(conn, version, rows)
        append_event(conn, actor_type=ActorType.AGENT, actor_id=AGENT, action="rate_version.drafted",
                     entity="rate_version", entity_id=version_id, ts=as_of,
                     payload={"document": doc.filename, "sha256": doc.sha256, "cells": len(rows),
                              "errors": report.errors, "flags": report.flags, "cache_hit": result.cache_hit,
                              "prompt_hash": result.prompt_hash})
    return ExtractionResult(version_id, document_id, notice, rows, report, cell_diff, result.cache_hit)
