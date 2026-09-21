"""Isolated exact-byte payload primitive; not wired into schema 7 repositories.

The caller owns an already validated connection, the registered schema, and the
transaction. This module creates no tables, opens no files, and never commits or
rolls back caller work. The current test-only table is described by ADR 0010.
Payload integrity does not establish factual truth, bundle custody or approval.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from enum import StrEnum
from hashlib import sha256


MAX_PAYLOAD_BYTES = 2 * 1024 * 1024


class MaterialPayloadError(RuntimeError):
    """An exact-byte payload is invalid, missing, or inconsistent."""


class PayloadKind(StrEnum):
    PDF = "pdf"
    LATEX = "latex_utf8"
    TEXT = "extracted_text_utf8"


@dataclass(frozen=True, slots=True)
class PayloadReference:
    kind: PayloadKind
    sha256: str
    byte_length: int

    def __post_init__(self) -> None:
        _validate_reference(self)


@dataclass(frozen=True, slots=True)
class MaterialPayloadReferences:
    pdf: PayloadReference
    latex: PayloadReference
    extracted_text: PayloadReference


@dataclass(frozen=True, slots=True)
class MaterialPayloads:
    pdf: bytes = field(repr=False)
    latex: str = field(repr=False)
    extracted_text: str = field(repr=False)


def _validate_kind(kind: PayloadKind) -> None:
    if type(kind) is not PayloadKind:
        raise MaterialPayloadError("Unsupported material payload kind")


def _validate_reference(reference: PayloadReference) -> None:
    if type(reference) is not PayloadReference:
        raise MaterialPayloadError("Invalid material payload reference")
    _validate_kind(reference.kind)
    if (type(reference.sha256) is not str or len(reference.sha256) != 64
            or any(char not in "0123456789abcdef" for char in reference.sha256)
            or type(reference.byte_length) is not int
            or not 0 <= reference.byte_length <= MAX_PAYLOAD_BYTES):
        raise MaterialPayloadError("Invalid material payload reference")


def _validate_data(kind: PayloadKind, data: bytes) -> None:
    _validate_kind(kind)
    if type(data) is not bytes or len(data) > MAX_PAYLOAD_BYTES:
        raise MaterialPayloadError("Invalid or oversized material payload")
    if kind is not PayloadKind.PDF:
        try:
            data.decode("utf-8", errors="strict")
        except UnicodeError:
            raise MaterialPayloadError("Invalid material payload UTF-8") from None


def _encode_text(value: str) -> bytes:
    # Bound before encoding as well as after: one code point uses at least one
    # UTF-8 byte, and no oversized input requires a second full allocation.
    if type(value) is not str or len(value) > MAX_PAYLOAD_BYTES:
        raise MaterialPayloadError("Invalid or oversized material payload text")
    try:
        data = value.encode("utf-8", errors="strict")
    except UnicodeError:
        raise MaterialPayloadError("Invalid material payload UTF-8") from None
    if len(data) > MAX_PAYLOAD_BYTES:
        raise MaterialPayloadError("Invalid or oversized material payload text")
    return data


def _read_row(connection: sqlite3.Connection, kind: PayloadKind,
              digest: str) -> tuple[object, ...] | None:
    # Inspect SQLite storage types and lengths before returning bytes to Python.
    # The final WHERE clause still selects malformed rows so corruption cannot
    # be mistaken for absence during insertion. One statement holds one snapshot.
    cursor = connection.cursor()
    cursor.row_factory = None
    try:
        cursor.execute(
            "SELECT kind, sha256, "
            "CASE WHEN typeof(byte_length) = 'integer' THEN byte_length ELSE NULL END, "
            "typeof(byte_length), "
            "typeof(payload_bytes), length(payload_bytes), "
            "CASE WHEN typeof(payload_bytes) = 'blob' "
            "AND length(payload_bytes) <= ? THEN payload_bytes ELSE NULL END "
            "FROM material_payloads WHERE kind = ? AND sha256 = ? LIMIT 2",
            (MAX_PAYLOAD_BYTES, kind.value, digest),
        )
        rows = cursor.fetchall()
        if len(rows) > 1:
            raise MaterialPayloadError("Ambiguous material payload reference")
        return rows[0] if rows else None
    finally:
        cursor.close()


def _validated_row(row: tuple[object, ...], reference: PayloadReference) -> bytes:
    kind, digest, byte_length, length_type, payload_type, actual_length, data = row
    if (kind != reference.kind.value or digest != reference.sha256
            or length_type != "integer" or type(byte_length) is not int
            or byte_length != reference.byte_length or payload_type != "blob"
            or type(actual_length) is not int or actual_length != byte_length):
        raise MaterialPayloadError("Inconsistent material payload metadata")
    _validate_data(reference.kind, data)
    if len(data) != byte_length or sha256(data).hexdigest() != reference.sha256:
        raise MaterialPayloadError("Inconsistent material payload bytes")
    return data


def intern_payload(connection: sqlite3.Connection, *, kind: PayloadKind,
                   data: bytes) -> PayloadReference:
    """Intern exact bytes in the caller's active transaction, without repair.

    Callers must roll back their material transaction if any later step fails.
    A digest indexes a candidate shared row; exact byte equality is also required.
    """
    _validate_data(kind, data)
    reference = PayloadReference(kind, sha256(data).hexdigest(), len(data))
    try:
        if not connection.in_transaction:
            raise MaterialPayloadError("Material payload insertion requires a transaction")
        row = _read_row(connection, kind, reference.sha256)
        if row is not None:
            if _validated_row(row, reference) != data:
                raise MaterialPayloadError("Material payload bytes differ")
            return reference
        connection.execute(
            "INSERT INTO material_payloads (kind, sha256, byte_length, payload_bytes) "
            "VALUES (?, ?, ?, ?)",
            (kind.value, reference.sha256, reference.byte_length, data),
        ).close()
        return reference
    except sqlite3.Error:
        raise MaterialPayloadError("Material payload storage failed") from None


def read_payload(connection: sqlite3.Connection, reference: PayloadReference, *,
                 expected_kind: PayloadKind) -> bytes:
    """Revalidate a bounded payload on every read, without changing storage."""
    _validate_reference(reference)
    _validate_kind(expected_kind)
    if reference.kind is not expected_kind:
        raise MaterialPayloadError("Material payload reference has the wrong kind")
    try:
        row = _read_row(connection, reference.kind, reference.sha256)
        if row is None:
            raise MaterialPayloadError("Material payload is missing")
        return _validated_row(row, reference)
    except sqlite3.Error:
        raise MaterialPayloadError("Material payload storage failed") from None


def intern_material_payloads(connection: sqlite3.Connection, *, pdf: bytes,
                             latex: str, extracted_text: str) -> MaterialPayloadReferences:
    """Validate all inputs before interning the three independently typed values."""
    _validate_data(PayloadKind.PDF, pdf)
    latex_bytes = _encode_text(latex)
    text_bytes = _encode_text(extracted_text)
    return MaterialPayloadReferences(
        intern_payload(connection, kind=PayloadKind.PDF, data=pdf),
        intern_payload(connection, kind=PayloadKind.LATEX, data=latex_bytes),
        intern_payload(connection, kind=PayloadKind.TEXT, data=text_bytes),
    )


def read_material_payloads(connection: sqlite3.Connection,
                           references: MaterialPayloadReferences) -> MaterialPayloads:
    """Reconstruct original bytes and strict UTF-8 strings without normalization.

    The caller owns a read transaction for consistency across all three values.
    No rendering, claim resolution, or material-custody validation occurs here.
    """
    if type(references) is not MaterialPayloadReferences:
        raise MaterialPayloadError("Invalid material payload references")
    try:
        if not connection.in_transaction:
            raise MaterialPayloadError("Material payload reconstruction requires a transaction")
    except sqlite3.Error:
        raise MaterialPayloadError("Material payload storage failed") from None
    return MaterialPayloads(
        read_payload(connection, references.pdf, expected_kind=PayloadKind.PDF),
        read_payload(connection, references.latex, expected_kind=PayloadKind.LATEX).decode("utf-8"),
        read_payload(connection, references.extracted_text, expected_kind=PayloadKind.TEXT).decode("utf-8"),
    )
