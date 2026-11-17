from __future__ import annotations

import unittest

from workboard.domain import WorkItemId


class WorkItemIdTests(unittest.TestCase):
    def test_repeated_title_can_have_distinct_work_items(self) -> None:
        first = WorkItemId.new()
        second = WorkItemId.new()

        self.assertNotEqual(first, second)
        self.assertEqual(WorkItemId(first.value), first)

    def test_invalid_and_non_v4_ids_are_rejected(self) -> None:
        for value in ("", "same-title", "00000000-0000-0000-0000-000000000000"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                WorkItemId(value)


if __name__ == "__main__":
    unittest.main()
