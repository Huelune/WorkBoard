from __future__ import annotations

import unittest

from workboard.domain import (
    ProgressValues,
    ReviewState,
    ValidationError,
    history_hash,
    review_state,
    validate_due_date,
    validate_progress,
)


class ValidateProgressTests(unittest.TestCase):
    def test_valid_values_per_status(self) -> None:
        for values in (
            ProgressValues("not_started", 0),
            ProgressValues("in_progress", 0),
            ProgressValues("in_progress", 99),
            ProgressValues("on_hold", 40, hold_reason="자료 대기"),
            ProgressValues("done", 100, evidence_ref="REF-1"),
            ProgressValues("done", 100, result="pass", evidence_ref="REF-1", evidence_note="확인"),
        ):
            with self.subTest(values=values):
                self.assertEqual(validate_progress(values), values)

    def test_rule_violations_are_rejected(self) -> None:
        for values in (
            ProgressValues("unknown", 0),
            ProgressValues("not_started", 1),
            ProgressValues("in_progress", 100),
            ProgressValues("in_progress", -1),
            ProgressValues("on_hold", 100, hold_reason="사유"),
            ProgressValues("on_hold", 10),
            ProgressValues("done", 99, evidence_ref="REF-1"),
            ProgressValues("done", 100),
            ProgressValues("done", 100, result="great", evidence_ref="REF-1"),
        ):
            with self.subTest(values=values), self.assertRaises(ValidationError):
                validate_progress(values)

    def test_blank_required_text_is_rejected(self) -> None:
        for values in (
            ProgressValues("done", 100, evidence_ref="   "),
            ProgressValues("on_hold", 10, hold_reason="\t\n"),
            ProgressValues("done", 100, evidence_ref=123),  # type: ignore[arg-type]
        ):
            with self.subTest(values=values), self.assertRaises(ValidationError):
                validate_progress(values)

    def test_non_integer_progress_is_rejected(self) -> None:
        for progress in (True, 50.0, "50", None):
            with self.subTest(progress=progress), self.assertRaises(ValidationError):
                validate_progress(ProgressValues("in_progress", progress))  # type: ignore[arg-type]

    def test_values_are_trimmed_and_irrelevant_fields_cleared(self) -> None:
        done = validate_progress(
            ProgressValues("done", 100, evidence_ref="  REF-1 ", evidence_note="  ", hold_reason="x")
        )
        self.assertEqual(done, ProgressValues("done", 100, evidence_ref="REF-1"))
        working = validate_progress(
            ProgressValues("in_progress", 30, result="pass", hold_reason="x", evidence_ref="REF-1")
        )
        self.assertEqual(working, ProgressValues("in_progress", 30))


class DueDateTests(unittest.TestCase):
    def test_valid_and_missing_due_dates(self) -> None:
        self.assertEqual(validate_due_date("2026-01-15"), "2026-01-15")
        self.assertIsNone(validate_due_date(None))

    def test_invalid_due_dates_are_rejected(self) -> None:
        for value in ("2026-1-5", "2026-02-30", "01/15", "", "20260115", 20260115):
            with self.subTest(value=value), self.assertRaises(ValidationError):
                validate_due_date(value)  # type: ignore[arg-type]


class HistoryHashTests(unittest.TestCase):
    def test_hash_is_stable_and_sensitive(self) -> None:
        fields = {"status": "done", "progress": 100, "evidence_ref": "근거-1"}
        first = history_hash("", fields)
        self.assertEqual(first, history_hash("", dict(reversed(list(fields.items())))))
        self.assertEqual(len(first), 64)
        self.assertNotEqual(first, history_hash("abc", fields))
        self.assertNotEqual(first, history_hash("", {**fields, "progress": 99}))


class ReviewStateTests(unittest.TestCase):
    def test_review_state(self) -> None:
        self.assertIs(review_state("in_progress", "confirmed"), ReviewState.NONE)
        self.assertIs(review_state("done", None), ReviewState.PENDING)
        self.assertIs(review_state("done", "confirmed"), ReviewState.CONFIRMED)
        self.assertIs(review_state("done", "rejected"), ReviewState.REJECTED)


if __name__ == "__main__":
    unittest.main()
