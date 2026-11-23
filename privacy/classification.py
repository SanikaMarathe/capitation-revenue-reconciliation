"""Reads the C1–C4 data-class tags declared on schema fields."""

from __future__ import annotations

from pydantic import BaseModel

from schemas.enums import DataClass


def field_classes(model: type[BaseModel]) -> dict[str, DataClass | None]:
    out: dict[str, DataClass | None] = {}
    for name, info in model.model_fields.items():
        extra = info.json_schema_extra if isinstance(info.json_schema_extra, dict) else {}
        tag = extra.get("data_class")
        out[name] = DataClass(tag) if tag else None
    return out


def fields_of_class(model: type[BaseModel], data_class: DataClass) -> set[str]:
    return {f for f, c in field_classes(model).items() if c == data_class}


def unclassified_fields(model: type[BaseModel]) -> set[str]:
    return {f for f, c in field_classes(model).items() if c is None}
