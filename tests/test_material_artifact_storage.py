from __future__ import annotations

import sqlite3
import unittest
from dataclasses import replace
from hashlib import sha256
from unittest.mock import patch

from grounded_apply.repositories.material_payloads import (
    MAX_PAYLOAD_BYTES,
    MaterialPayloadError,
    MaterialPayloadReferences,
    MaterialPayloads,
    PayloadKind,
    PayloadReference,
    intern_material_payloads,
    intern_payload,
    read_material_payloads,
    read_payload,
)


# This is deliberately test-only DDL. Production remains schema 7, and these
# primitive tests do not establish conversion or material-custody compatibility.
PAYLOAD_SCHEMA = """
CREATE TABLE material_payloads (
    kind TEXT NOT NULL CHECK(kind IN ('pdf', 'latex_utf8', 'extracted_text_utf8')),
    sha256 TEXT NOT NULL CHECK(length(sha256) = 64),
    byte_length INTEGER NOT NULL CHECK(typeof(byte_length) = 'integer')
        CHECK(byte_length BETWEEN 0 AND 2097152),
    payload_bytes BLOB NOT NULL CHECK(typeof(payload_bytes) = 'blob')
        CHECK(length(payload_bytes) = byte_length),
    PRIMARY KEY(kind, sha256)
)
"""


class MaterialArtifactStorageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.connection = self.new_connection()
        self.connection.execute("BEGIN")

    def new_connection(
        self, *, weak_schema: bool = False,
        factory: type[sqlite3.Connection] = sqlite3.Connection,
    ) -> sqlite3.Connection:
        connection = sqlite3.connect(":memory:", isolation_level=None, factory=factory)
        self.addCleanup(connection.close)
        if weak_schema:
            # No affinity or checks: simulate values a damaged/untrusted table
            # can expose, without depending on a production migration accepting it.
            connection.execute("""CREATE TABLE material_payloads (
                kind, sha256, byte_length, payload_bytes,
                PRIMARY KEY(kind, sha256))""")
        else:
            connection.execute(PAYLOAD_SCHEMA)
        return connection

    def row_count(self, connection: sqlite3.Connection | None = None) -> int:
        return (connection or self.connection).execute(
            "SELECT count(*) FROM material_payloads",
        ).fetchone()[0]

    def reference(self, data: bytes, kind: PayloadKind = PayloadKind.PDF) -> PayloadReference:
        return PayloadReference(kind=kind, sha256=sha256(data).hexdigest(), byte_length=len(data))

    def insert_unchecked(
        self, connection: sqlite3.Connection, reference: PayloadReference,
        *, payload_bytes: object, byte_length: object,
    ) -> None:
        connection.execute(
            "INSERT INTO material_payloads VALUES (?, ?, ?, ?)",
            (reference.kind.value, reference.sha256, byte_length, payload_bytes),
        )

    def test_identical_bytes_share_one_row_per_kind(self) -> None:
        data = b"Fictional exact shared content."
        references = []
        for kind in PayloadKind:
            first = intern_payload(self.connection, kind=kind, data=data)
            second = intern_payload(self.connection, kind=kind, data=data)
            self.assertEqual(first, second)
            self.assertEqual(first, self.reference(data, kind))
            references.append(first)
            self.assertEqual(read_payload(self.connection, first, expected_kind=kind), data)
        self.assertEqual(self.row_count(), 3)
        self.assertEqual(len(set(references)), 3)

    def test_different_bytes_are_never_merged(self) -> None:
        references = [
            intern_payload(self.connection, kind=PayloadKind.PDF, data=data)
            for data in (b"Fictional A", b"Fictional B", b"Fictional A ")
        ]
        self.assertEqual(len(set(references)), 3)
        self.assertEqual(self.row_count(), 3)

    def test_same_digest_and_length_still_require_byte_equality(self) -> None:
        with patch("grounded_apply.repositories.material_payloads.sha256") as digest:
            digest.return_value.hexdigest.return_value = "a" * 64
            first = intern_payload(self.connection, kind=PayloadKind.PDF, data=b"Fictional A")
            with self.assertRaises(MaterialPayloadError):
                intern_payload(self.connection, kind=PayloadKind.PDF, data=b"Fictional B")
            self.assertEqual(read_payload(self.connection, first, expected_kind=PayloadKind.PDF), b"Fictional A")
        self.assertEqual(self.row_count(), 1)
        self.assertTrue(self.connection.in_transaction)

    def test_material_round_trip_preserves_exact_text_and_arbitrary_pdf_bytes(self) -> None:
        latex = "\ufeffFictional résumé\r\nCafe\u0301\x00\\command{}  \n"
        extracted_text = "\ufeffFictional 简历\r\nCafé\x00\t \r\n"
        pdf = b"\x00\xff\xfeFictional binary payload\r\n"
        references = intern_material_payloads(
            self.connection, pdf=pdf, latex=latex, extracted_text=extracted_text,
        )
        self.assertEqual(
            read_material_payloads(self.connection, references),
            MaterialPayloads(pdf=pdf, latex=latex, extracted_text=extracted_text),
        )
        self.assertEqual(references.latex.byte_length, len(latex.encode("utf-8")))
        self.assertEqual(references.extracted_text.byte_length, len(extracted_text.encode("utf-8")))

    def test_unicode_normalization_and_line_endings_remain_distinct(self) -> None:
        texts = ("Fictional Café\n", "Fictional Cafe\u0301\n", "Fictional Café\r\n")
        references = [
            intern_payload(self.connection, kind=PayloadKind.TEXT, data=text.encode("utf-8"))
            for text in texts
        ]
        self.assertEqual(len(set(references)), len(texts))
        self.assertEqual(
            tuple(read_payload(self.connection, ref, expected_kind=PayloadKind.TEXT).decode("utf-8") for ref in references),
            texts,
        )

    def test_empty_payloads_round_trip_without_semantic_rewriting(self) -> None:
        references = intern_material_payloads(self.connection, pdf=b"", latex="", extracted_text="")
        self.assertEqual(read_material_payloads(self.connection, references), MaterialPayloads(b"", "", ""))
        self.assertEqual(self.row_count(), 3)

    def test_exact_maximum_accepts_bytes_and_counts_multibyte_text_as_utf8(self) -> None:
        self.assertEqual(MAX_PAYLOAD_BYTES, 2 * 1024 * 1024)
        text = "é" * (MAX_PAYLOAD_BYTES // 2)
        references = intern_material_payloads(
            self.connection, pdf=b"\xff" * MAX_PAYLOAD_BYTES, latex=text, extracted_text=text,
        )
        self.assertEqual(references.latex.byte_length, MAX_PAYLOAD_BYTES)
        self.assertEqual(read_material_payloads(self.connection, references).latex, text)

    def test_oversized_payload_write_fails_before_any_write(self) -> None:
        for kind in PayloadKind:
            with self.subTest(kind=kind), self.assertRaises(MaterialPayloadError):
                intern_payload(self.connection, kind=kind, data=b"x" * (MAX_PAYLOAD_BYTES + 1))
        self.assertEqual(self.connection.total_changes, 0)

    def test_write_requires_exact_bytes_and_registered_kind(self) -> None:
        for data in ("Fictional text", bytearray(b"x"), memoryview(b"x"), None, 1):
            with self.subTest(data_type=type(data).__name__), self.assertRaises(MaterialPayloadError):
                intern_payload(self.connection, kind=PayloadKind.PDF, data=data)
        for kind in ("pdf", "unknown", None, 1):
            with self.subTest(kind=kind), self.assertRaises(MaterialPayloadError):
                intern_payload(self.connection, kind=kind, data=b"Fictional text")
        self.assertEqual(self.connection.total_changes, 0)

    def test_invalid_utf8_is_rejected_for_both_text_kinds(self) -> None:
        for kind in (PayloadKind.LATEX, PayloadKind.TEXT):
            for data in (b"\xff", b"\xc0\xaf", b"\xed\xa0\x80", b"\xe2\x82"):
                with self.subTest(kind=kind, data=data), self.assertRaises(MaterialPayloadError):
                    intern_payload(self.connection, kind=kind, data=data)
        self.assertEqual(self.connection.total_changes, 0)

    def test_all_material_inputs_are_prevalidated_before_interning(self) -> None:
        valid = dict(pdf=b"Fictional PDF", latex="Fictional LaTeX", extracted_text="Fictional text")
        invalid_inputs = (
            dict(pdf=bytearray(b"not bytes")),
            dict(latex=b"not text"),
            dict(extracted_text=None),
            dict(latex="\ud800"),
            dict(extracted_text="\udfff"),
            dict(latex="é" * (MAX_PAYLOAD_BYTES // 2 + 1)),
            dict(extracted_text="x" * (MAX_PAYLOAD_BYTES + 1)),
        )
        for invalid in invalid_inputs:
            with self.subTest(field=next(iter(invalid))), self.assertRaises(MaterialPayloadError):
                intern_material_payloads(self.connection, **(valid | invalid))
            self.assertEqual(self.connection.total_changes, 0)
            self.assertTrue(self.connection.in_transaction)

    def test_missing_payload_fails_without_creating_rows(self) -> None:
        with self.assertRaises(MaterialPayloadError):
            read_payload(self.connection, self.reference(b"missing"), expected_kind=PayloadKind.PDF)
        self.assertEqual(self.connection.total_changes, 0)

    def test_wrong_kind_reference_fails_even_when_matching_bytes_exist(self) -> None:
        data = b"Fictional text"
        pdf = intern_payload(self.connection, kind=PayloadKind.PDF, data=data)
        intern_payload(self.connection, kind=PayloadKind.TEXT, data=data)
        with self.assertRaises(MaterialPayloadError):
            read_payload(self.connection, pdf, expected_kind=PayloadKind.TEXT)
        text_reference = replace(pdf, kind=PayloadKind.LATEX)
        with self.assertRaises(MaterialPayloadError):
            read_payload(self.connection, text_reference, expected_kind=PayloadKind.LATEX)

    def test_reference_shape_is_validated_before_database_queries(self) -> None:
        reference = self.reference(b"Fictional bytes")
        cases = (
            dict(kind="pdf"), dict(kind=None),
            dict(sha256="A" * 64), dict(sha256="g" * 64), dict(sha256="a" * 63),
            dict(sha256="a" * 65), dict(sha256=None), dict(sha256=b"a" * 64),
            dict(byte_length=True), dict(byte_length=1.0), dict(byte_length="1"),
            dict(byte_length=-1), dict(byte_length=MAX_PAYLOAD_BYTES + 1),
        )
        statements = []
        self.connection.set_trace_callback(statements.append)
        for values in cases:
            with self.subTest(values=values), self.assertRaises(MaterialPayloadError):
                read_payload(self.connection, replace(reference, **values), expected_kind=PayloadKind.PDF)
        for invalid in (None, {}, ("pdf", reference.sha256, reference.byte_length)):
            with self.subTest(reference=invalid), self.assertRaises(MaterialPayloadError):
                read_payload(self.connection, invalid, expected_kind=PayloadKind.PDF)
        for kind in ("pdf", "unknown", None):
            with self.subTest(expected_kind=kind), self.assertRaises(MaterialPayloadError):
                read_payload(self.connection, reference, expected_kind=kind)
        self.assertEqual(statements, [])

    def test_reference_length_must_match_stored_bytes(self) -> None:
        reference = intern_payload(self.connection, kind=PayloadKind.PDF, data=b"Fictional PDF")
        with self.assertRaises(MaterialPayloadError):
            read_payload(self.connection, replace(reference, byte_length=reference.byte_length + 1), expected_kind=PayloadKind.PDF)

    def test_changed_stored_bytes_with_same_length_fail_digest_validation(self) -> None:
        reference = intern_payload(self.connection, kind=PayloadKind.PDF, data=b"Fictional A")
        self.connection.execute("UPDATE material_payloads SET payload_bytes = ?", (b"Fictional B",))
        with self.assertRaises(MaterialPayloadError):
            read_payload(self.connection, reference, expected_kind=PayloadKind.PDF)
        with self.assertRaises(MaterialPayloadError):
            intern_payload(self.connection, kind=PayloadKind.PDF, data=b"Fictional A")
        self.assertEqual(self.row_count(), 1)

    def test_corrupt_stored_lengths_and_sql_storage_types_fail_closed(self) -> None:
        reference = self.reference(b"Fictional PDF")
        corruptions = (
            (reference.byte_length + 1, b"Fictional PDF"),
            (-1, b"Fictional PDF"),
            (float(reference.byte_length), b"Fictional PDF"),
            (str(reference.byte_length), b"Fictional PDF"), (None, b"Fictional PDF"),
            (reference.byte_length, "Fictional PDF"),
            (reference.byte_length, 123), (reference.byte_length, None),
        )
        for length, data in corruptions:
            with self.subTest(length=length, data_type=type(data).__name__):
                connection = self.new_connection(weak_schema=True)
                self.insert_unchecked(connection, reference, payload_bytes=data, byte_length=length)
                with self.assertRaises(MaterialPayloadError):
                    read_payload(connection, reference, expected_kind=PayloadKind.PDF)
                connection.execute("BEGIN")
                with self.assertRaises(MaterialPayloadError):
                    intern_payload(connection, kind=PayloadKind.PDF, data=b"Fictional PDF")
                self.assertEqual(self.row_count(connection), 1)

    def test_oversized_blob_never_crosses_sqlite_read_boundary(self) -> None:
        reference = self.reference(b"Fictional PDF")
        oversized = b"x" * (MAX_PAYLOAD_BYTES + 1)
        observed_rows = []
        test_case = self

        class BoundedCursor(sqlite3.Cursor):
            def fetchall(self) -> list[tuple]:
                rows = super().fetchall()
                for row in rows:
                    observed_rows.append(row)
                    for value in row:
                        if isinstance(value, (bytes, str)):
                            test_case.assertLessEqual(len(value), MAX_PAYLOAD_BYTES, "SQL returned oversized content")
                return rows

        class BoundedConnection(sqlite3.Connection):
            def cursor(self, factory: type[sqlite3.Cursor] = BoundedCursor) -> sqlite3.Cursor:
                return super().cursor(factory)

        for declared_length in (reference.byte_length, len(oversized), "x" * len(oversized)):
            with self.subTest(declared_length_type=type(declared_length).__name__, valid_size=declared_length == reference.byte_length):
                connection = self.new_connection(weak_schema=True, factory=BoundedConnection)
                self.insert_unchecked(connection, reference, payload_bytes=oversized, byte_length=declared_length)
                with self.assertRaises(MaterialPayloadError):
                    read_payload(connection, reference, expected_kind=PayloadKind.PDF)
                connection.execute("BEGIN")
                with self.assertRaises(MaterialPayloadError):
                    intern_payload(connection, kind=PayloadKind.PDF, data=b"Fictional PDF")
                self.assertEqual(self.row_count(connection), 1)
        self.assertEqual(len(observed_rows), 6)

    def test_invalid_utf8_at_rest_fails_even_with_matching_digest_and_length(self) -> None:
        for kind in (PayloadKind.LATEX, PayloadKind.TEXT):
            with self.subTest(kind=kind):
                reference = self.reference(b"\xff", kind)
                self.insert_unchecked(self.connection, reference, payload_bytes=b"\xff", byte_length=1)
                with self.assertRaises(MaterialPayloadError):
                    read_payload(self.connection, reference, expected_kind=kind)

    def test_swapped_material_references_fail_before_returning_payloads(self) -> None:
        references = intern_material_payloads(
            self.connection, pdf=b"Fictional shared text", latex="Fictional shared text", extracted_text="Fictional shared text",
        )
        for corrupted in (
            replace(references, pdf=references.latex),
            replace(references, latex=references.extracted_text),
            replace(references, extracted_text=references.pdf),
            replace(references, extracted_text=replace(references.extracted_text, sha256="0" * 64)),
        ):
            with self.subTest(references=corrupted), self.assertRaises(MaterialPayloadError):
                read_material_payloads(self.connection, corrupted)

    def test_one_corrupt_shared_payload_fails_all_referencing_materials(self) -> None:
        first = intern_material_payloads(self.connection, pdf=b"Fictional A", latex="Source A", extracted_text="Text A")
        second = intern_material_payloads(self.connection, pdf=b"Fictional A", latex="Source B", extracted_text="Text B")
        self.connection.execute(
            "UPDATE material_payloads SET payload_bytes = ? WHERE kind = ? AND sha256 = ?",
            (b"Fictional B", PayloadKind.PDF.value, first.pdf.sha256),
        )
        for references in (first, second):
            with self.subTest(references=references), self.assertRaises(MaterialPayloadError):
                read_material_payloads(self.connection, references)

    def test_writes_require_caller_transaction_and_do_not_begin_one(self) -> None:
        self.connection.rollback()
        for operation in (
            lambda: intern_payload(self.connection, kind=PayloadKind.PDF, data=b"Fictional PDF"),
            lambda: intern_material_payloads(self.connection, pdf=b"Fictional PDF", latex="Source", extracted_text="Text"),
        ):
            with self.assertRaises(MaterialPayloadError):
                operation()
            self.assertFalse(self.connection.in_transaction)
            self.assertEqual(self.connection.total_changes, 0)

    def test_helper_does_not_create_schema_or_commit_caller_work(self) -> None:
        connection = sqlite3.connect(":memory:", isolation_level=None)
        self.addCleanup(connection.close)
        connection.execute("CREATE TABLE fictional_caller_work (id TEXT)")
        connection.execute("BEGIN")
        connection.execute("INSERT INTO fictional_caller_work VALUES ('fictional-pending')")
        with self.assertRaises(MaterialPayloadError):
            intern_payload(connection, kind=PayloadKind.PDF, data=b"Fictional PDF")
        self.assertTrue(connection.in_transaction)
        self.assertEqual(connection.execute("SELECT count(*) FROM fictional_caller_work").fetchone()[0], 1)
        self.assertIsNone(connection.execute("SELECT name FROM sqlite_master WHERE name = 'material_payloads'").fetchone())
        connection.rollback()
        self.assertEqual(connection.execute("SELECT count(*) FROM fictional_caller_work").fetchone()[0], 0)

    def test_closed_connection_errors_hide_raw_sqlite_messages_and_payloads(self) -> None:
        connection = self.new_connection()
        connection.close()
        data = b"Fictional payload detail absent from errors"
        references = MaterialPayloadReferences(
            self.reference(data, PayloadKind.PDF),
            self.reference(data, PayloadKind.LATEX),
            self.reference(data, PayloadKind.TEXT),
        )
        for operation in (
            lambda: intern_payload(connection, kind=PayloadKind.PDF, data=data),
            lambda: read_payload(connection, references.pdf, expected_kind=PayloadKind.PDF),
            lambda: intern_material_payloads(connection, pdf=data, latex=data.decode(), extracted_text=data.decode()),
            lambda: read_material_payloads(connection, references),
        ):
            with self.assertRaises(MaterialPayloadError) as error:
                operation()
            self.assertEqual(str(error.exception), "Material payload storage failed")
            self.assertNotIn(data.decode(), repr(error.exception))
            self.assertTrue(error.exception.__suppress_context__)

    def test_caller_rollback_removes_new_payloads_after_material_constraint_failure(self) -> None:
        shared = intern_payload(self.connection, kind=PayloadKind.PDF, data=b"Fictional shared PDF")
        self.connection.commit()
        self.connection.execute("CREATE TABLE fictional_materials (id TEXT PRIMARY KEY)")
        self.connection.execute("INSERT INTO fictional_materials VALUES ('fictional-existing')")
        self.connection.execute("BEGIN")
        references = intern_material_payloads(
            self.connection, pdf=b"Fictional shared PDF", latex="New source", extracted_text="New text",
        )
        self.assertEqual(references.pdf, shared)
        self.assertEqual(self.row_count(), 3)
        with self.assertRaises(sqlite3.IntegrityError):
            self.connection.execute("INSERT INTO fictional_materials VALUES ('fictional-existing')")
        self.assertTrue(self.connection.in_transaction)
        self.assertEqual(self.row_count(), 3)
        self.connection.rollback()
        self.assertEqual(self.row_count(), 1)
        self.assertEqual(read_payload(self.connection, shared, expected_kind=PayloadKind.PDF), b"Fictional shared PDF")
        with self.assertRaises(MaterialPayloadError):
            read_payload(self.connection, references.latex, expected_kind=PayloadKind.LATEX)

    def test_payload_failure_leaves_unrelated_caller_work_pending(self) -> None:
        self.connection.execute("CREATE TABLE fictional_caller_work (id TEXT)")
        self.connection.execute("INSERT INTO fictional_caller_work VALUES ('fictional-pending')")
        with self.assertRaises(MaterialPayloadError):
            intern_material_payloads(self.connection, pdf=b"Fictional PDF", latex="Source", extracted_text="\ud800")
        self.assertTrue(self.connection.in_transaction)
        self.assertEqual(self.connection.execute("SELECT count(*) FROM fictional_caller_work").fetchone()[0], 1)
        self.connection.rollback()
        self.assertIsNone(self.connection.execute("SELECT name FROM sqlite_master WHERE name = 'fictional_caller_work'").fetchone())

    def test_read_only_caller_connection_reconstructs_without_mutation(self) -> None:
        references = intern_material_payloads(self.connection, pdf=b"Fictional PDF", latex="Source", extracted_text="Text")
        self.connection.commit()
        self.connection.execute("PRAGMA query_only = ON")
        before = self.connection.total_changes
        with self.assertRaises(MaterialPayloadError):
            read_material_payloads(self.connection, references)
        self.assertFalse(self.connection.in_transaction)
        self.connection.execute("BEGIN")
        self.assertEqual(read_material_payloads(self.connection, references), MaterialPayloads(b"Fictional PDF", "Source", "Text"))
        self.assertEqual(self.connection.total_changes, before)
        self.assertTrue(self.connection.in_transaction)

    def test_caller_row_factory_cannot_replace_values_during_verification(self) -> None:
        references = intern_material_payloads(self.connection, pdf=b"Fictional PDF", latex="Source", extracted_text="Text")

        def substitute_row(_cursor: sqlite3.Cursor, _row: tuple) -> tuple:
            return ("Fictional substituted row",)

        self.connection.row_factory = substitute_row
        self.assertEqual(read_material_payloads(self.connection, references), MaterialPayloads(b"Fictional PDF", "Source", "Text"))
        self.assertIs(self.connection.row_factory, substitute_row)

    def test_duplicate_key_rows_in_damaged_table_fail_without_choosing_one(self) -> None:
        connection = sqlite3.connect(":memory:", isolation_level=None)
        self.addCleanup(connection.close)
        connection.execute("CREATE TABLE material_payloads (kind, sha256, byte_length, payload_bytes)")
        reference = self.reference(b"Fictional PDF")
        for _ in range(2):
            self.insert_unchecked(connection, reference, payload_bytes=b"Fictional PDF", byte_length=reference.byte_length)
        with self.assertRaises(MaterialPayloadError):
            read_payload(connection, reference, expected_kind=PayloadKind.PDF)
        connection.execute("BEGIN")
        with self.assertRaises(MaterialPayloadError):
            intern_payload(connection, kind=PayloadKind.PDF, data=b"Fictional PDF")
        self.assertEqual(self.row_count(connection), 2)

    def test_sharing_preserves_distinct_fixture_material_job_and_bundle_identities(self) -> None:
        # These binding rows are synthetic callers of the primitive, not a
        # production material migration or an approval-custody acceptance gate.
        self.connection.execute("""CREATE TABLE fictional_material_bindings (
            material_id TEXT PRIMARY KEY, job_id TEXT, bundle_sha256 TEXT,
            pdf_sha256 TEXT, latex_sha256 TEXT, text_sha256 TEXT)""")
        references: list[MaterialPayloadReferences] = []
        original = []
        for index in range(3):
            reference = intern_material_payloads(
                self.connection, pdf=b"Fictional shared PDF", latex="Shared source", extracted_text="Shared text",
            )
            references.append(reference)
            row = (
                f"fictional-material-{index}", f"fictional-job-{index}",
                sha256(f"fictional-bundle-{index}".encode()).hexdigest(),
                reference.pdf.sha256, reference.latex.sha256, reference.extracted_text.sha256,
            )
            original.append(row)
            self.connection.execute("INSERT INTO fictional_material_bindings VALUES (?, ?, ?, ?, ?, ?)", row)
        self.assertEqual(self.row_count(), 3)
        self.assertEqual(
            self.connection.execute("SELECT * FROM fictional_material_bindings ORDER BY material_id").fetchall(),
            original,
        )
        for reference in references:
            self.assertEqual(
                read_material_payloads(self.connection, reference),
                MaterialPayloads(b"Fictional shared PDF", "Shared source", "Shared text"),
            )


if __name__ == "__main__":
    unittest.main()
