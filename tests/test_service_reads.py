from __future__ import annotations

import unittest
from datetime import datetime, timezone

from support import Clock, temp_db_path
from workboard.domain import NotFound, ValidationError
from workboard.services import WorkBoardService
from workboard.storage import open_database

START = datetime(2026, 1, 5, tzinfo=timezone.utc)
END = datetime(2026, 1, 19, tzinfo=timezone.utc)
TODAY = "2026-01-20"


def day(number: int) -> datetime:
    return datetime(2026, 1, number, 9, tzinfo=timezone.utc)


class ReadCase(unittest.TestCase):
    def setUp(self) -> None:
        self.clock = Clock()
        self.conn = open_database(temp_db_path(self))
        self.addCleanup(self.conn.close)
        self.svc = WorkBoardService(self.conn, self.clock)

    def at(self, number: int) -> None:
        self.clock.now = day(number)

    def add(self, project: str, assignee: str, due: str | None = None, category: str | None = None) -> str:
        item = self.svc.create_work_item("ed1", project, f"업무-{assignee}", category_id=category)
        return self.svc.assign("ed1", item, assignee, due)

    def done(self, assignment: str, assignee: str, ref: str = "REF") -> None:
        self.svc.update_progress(assignee, assignment, "done", 100, evidence_ref=ref)

    def state(self, assignment: str) -> str:
        return self.svc.assignment_detail(assignment)["row"]["review_state"]


class ReviewStateFlowTests(ReadCase):
    def setUp(self) -> None:
        super().setUp()
        self.project = self.svc.create_project("ed1", "P")
        self.svc.add_editor("ed1", self.project, "ed2")
        self.a = self.add(self.project, "u1")

    def test_pending_then_confirmed_then_edited(self) -> None:
        self.assertEqual(self.state(self.a), "none")
        self.done(self.a, "u1")
        self.assertEqual(self.state(self.a), "pending")
        self.svc.review_completion("ed2", self.a, "confirmed")
        self.assertEqual(self.state(self.a), "confirmed")
        self.done(self.a, "u1", ref="REF-수정")
        self.assertEqual(self.state(self.a), "pending")

    def test_rejected_then_resubmitted(self) -> None:
        self.done(self.a, "u1")
        self.svc.review_completion("ed2", self.a, "rejected", "근거 부족")
        self.assertEqual(self.state(self.a), "rejected")
        self.done(self.a, "u1", ref="REF-2")
        self.assertEqual(self.state(self.a), "pending")

    def test_reopened_confirmed_work_needs_review_again(self) -> None:
        self.done(self.a, "u1")
        self.svc.review_completion("ed2", self.a, "confirmed")
        self.svc.update_progress("u1", self.a, "in_progress", 80)
        self.assertEqual(self.state(self.a), "none")
        self.done(self.a, "u1")
        self.assertEqual(self.state(self.a), "pending")

    def test_latest_review_wins_and_corrected_rejection_is_not_counted(self) -> None:
        self.done(self.a, "u1")
        self.svc.review_completion("ed2", self.a, "rejected", "실수")
        self.svc.review_completion("ed1", self.a, "confirmed")
        self.assertEqual(self.state(self.a), "confirmed")
        pace = self.svc.assignee_pace(START, END, self.project)
        self.assertEqual((pace[0]["confirmed"], pace[0]["rejected"]), (1, 0))

    def test_detail_lists_history_and_reviews(self) -> None:
        self.svc.update_progress("u1", self.a, "in_progress", 10)
        self.done(self.a, "u1")
        self.svc.review_completion("ed2", self.a, "rejected", "근거 부족")
        detail = self.svc.assignment_detail(self.a)
        self.assertEqual([h["progress"] for h in detail["history"]], [10, 100])
        self.assertEqual([(r["decision"], r["reason"]) for r in detail["reviews"]], [("rejected", "근거 부족")])
        self.assertEqual(detail["row"]["evidence_ref"], "REF")
        with self.assertRaises(NotFound):
            self.svc.assignment_detail("missing")

    def test_pending_reviews_hide_own_work(self) -> None:
        own = self.add(self.project, "ed1")
        self.done(self.a, "u1")
        self.done(own, "ed1")
        self.assertEqual([r["assignment_id"] for r in self.svc.pending_reviews("ed1", self.project)], [self.a])
        self.assertEqual(
            [r["assignment_id"] for r in self.svc.pending_reviews("ed2", self.project)], [self.a, own]
        )


class AggregateTests(ReadCase):
    """Two editors, two assignees, six live assignments in P1; counts below are hand-counted."""

    def setUp(self) -> None:
        super().setUp()
        svc = self.svc
        self.p1 = svc.create_project("ed1", "P1")
        svc.add_editor("ed1", self.p1, "ed2")
        self.unit = svc.create_category("ed1", self.p1, "UNIT")
        self.integ = svc.create_category("ed1", self.p1, "INTEG", 1)
        a1 = self.add(self.p1, "u1", "2026-01-10", self.unit)
        a2 = self.add(self.p1, "u1", "2026-01-20", self.unit)
        a3 = self.add(self.p1, "u1", "2026-01-07", self.integ)
        a4 = self.add(self.p1, "u2", "2026-01-30", self.integ)
        a5 = self.add(self.p1, "u2", None, self.unit)
        a6 = self.add(self.p1, "u2", "2026-01-08", self.unit)
        cancelled = self.add(self.p1, "u2", "2026-01-01", self.unit)
        deleted = self.add(self.p1, "u1", "2026-01-01", self.unit)
        self.p2 = svc.create_project("ed1", "P2")
        b1 = self.add(self.p2, "u1")
        self.p3 = svc.create_project("ed1", "P3")
        self.add(self.p3, "u3")

        self.at(6)
        self.done(a1, "u1")
        self.done(b1, "u1")
        self.done(cancelled, "u2")
        self.done(deleted, "u1")
        svc.update_progress("u1", a3, "in_progress", 50)
        svc.update_progress("u2", a5, "on_hold", 20, hold_reason="자료 대기")
        self.at(7)
        self.done(a4, "u2")
        svc.review_completion("ed2", a1, "confirmed")
        svc.review_completion("ed1", b1, "confirmed")
        svc.review_completion("ed1", cancelled, "confirmed")
        svc.review_completion("ed2", deleted, "rejected", "근거 없음")
        self.at(8)
        svc.review_completion("ed1", a4, "rejected", "근거 부족")
        svc.cancel_assignment("ed1", cancelled)
        svc.delete_work_item("ed1", svc.assignment_detail(deleted)["row"]["work_item_id"])
        self.at(13)
        self.done(a2, "u1")
        self.at(14)
        self.done(a6, "u2")
        self.at(15)
        svc.review_completion("ed1", a6, "confirmed")
        self.a2, self.a5 = a2, a5

    def test_lists_and_filters(self) -> None:
        self.assertEqual([p["name"] for p in self.svc.list_projects()], ["P1", "P2", "P3"])
        self.assertEqual([c["name"] for c in self.svc.list_categories(self.p1)], ["UNIT", "INTEG"])
        self.assertEqual(self.svc.list_scopes(self.p1), [])
        self.assertEqual(len(self.svc.list_assignments(self.p1)), 6)
        self.assertEqual(len(self.svc.list_assignments(self.p1, category_id=self.integ)), 2)
        self.assertEqual(len(self.svc.list_assignments(self.p1, assignee_id="u2")), 3)
        mine = self.svc.my_assignments("u1")
        self.assertEqual(len(mine), 4)
        self.assertEqual({r["project_id"] for r in mine}, {self.p1, self.p2})
        self.assertEqual([r["assignment_id"] for r in self.svc.pending_reviews("ed2", self.p1)], [self.a2])

    def test_group_summary_by_assignee(self) -> None:
        self.assertEqual(
            self.svc.group_summary(self.p1, "assignee", TODAY),
            [
                {"key": "u1", "count": 3, "average_progress": 83.3, "confirmed": 1, "pending": 1, "rejected": 0, "overdue": 1},
                {"key": "u2", "count": 3, "average_progress": 73.3, "confirmed": 1, "pending": 0, "rejected": 1, "overdue": 0},
            ],
        )

    def test_group_summary_by_category(self) -> None:
        self.assertEqual(
            self.svc.group_summary(self.p1, "category", TODAY),
            [
                {"key": self.unit, "count": 4, "average_progress": 80.0, "confirmed": 2, "pending": 1, "rejected": 0, "overdue": 0},
                {"key": self.integ, "count": 2, "average_progress": 75.0, "confirmed": 0, "pending": 0, "rejected": 1, "overdue": 1},
            ],
        )
        with self.assertRaises(ValidationError):
            self.svc.group_summary(self.p1, "title", TODAY)
        with self.assertRaises(ValidationError):
            self.svc.group_summary(self.p1, "assignee", "오늘")

    def test_project_status(self) -> None:
        self.assertEqual(
            self.svc.project_status(self.p1, TODAY),
            {
                "total": 6,
                "confirmed": 2,
                "pending": 1,
                "rejected": 1,
                "on_hold": 1,
                "overdue": 1,
                "by_category": {self.unit: 4, self.integ: 2},
            },
        )
        with self.assertRaises(NotFound):
            self.svc.project_status("missing", TODAY)

    def test_assignee_pace_in_one_project(self) -> None:
        self.assertEqual(
            self.svc.assignee_pace(START, END, self.p1),
            [
                {"assignee_id": "u1", "assigned": 3, "reported": 2, "confirmed": 1, "rejected": 0, "per_week": 0.5, "weekly": [1, 0]},
                {"assignee_id": "u2", "assigned": 3, "reported": 2, "confirmed": 1, "rejected": 1, "per_week": 0.5, "weekly": [0, 1]},
            ],
        )

    def test_assignee_pace_across_projects(self) -> None:
        pace = {row["assignee_id"]: row for row in self.svc.assignee_pace(START, END)}
        self.assertEqual(
            pace["u1"],
            {"assignee_id": "u1", "assigned": 4, "reported": 3, "confirmed": 2, "rejected": 0, "per_week": 1.0, "weekly": [2, 0]},
        )
        self.assertEqual(pace["u3"]["assigned"], 1)
        self.assertEqual(pace["u3"]["weekly"], [0, 0])

    def test_pace_respects_period_bounds(self) -> None:
        first_week = self.svc.assignee_pace(START, datetime(2026, 1, 12, tzinfo=timezone.utc), self.p1)
        self.assertEqual([(r["reported"], r["confirmed"], r["weekly"]) for r in first_week], [(1, 1, [1]), (1, 0, [0])])
        exactly_at_report = self.svc.assignee_pace(day(6), day(13), self.p1)
        self.assertEqual(exactly_at_report[0]["reported"], 1)  # start is inclusive, end exclusive
        ten_days = self.svc.assignee_pace(START, datetime(2026, 1, 15, tzinfo=timezone.utc), self.p1)
        self.assertEqual(ten_days[1]["weekly"], [0, 1])
        self.assertEqual(ten_days[1]["per_week"], 0.7)

    def test_project_pace(self) -> None:
        self.assertEqual(
            self.svc.project_pace(START, END),
            [
                {"project_id": self.p1, "name": "P1", "total": 6, "confirmed_total": 2, "remaining": 4, "confirmed": 2, "per_week": 1.0, "weeks_left": 4.0, "weekly": [1, 1]},
                {"project_id": self.p2, "name": "P2", "total": 1, "confirmed_total": 1, "remaining": 0, "confirmed": 1, "per_week": 0.5, "weeks_left": 0.0, "weekly": [1, 0]},
                {"project_id": self.p3, "name": "P3", "total": 1, "confirmed_total": 0, "remaining": 1, "confirmed": 0, "per_week": 0.0, "weeks_left": None, "weekly": [0, 0]},
            ],
        )

    def test_invalid_periods_are_rejected(self) -> None:
        naive = datetime(2026, 1, 5)
        for start, end in ((END, START), (START, START), (naive, END), (START, "2026-01-19")):
            with self.subTest(start=start, end=end), self.assertRaises(ValidationError):
                self.svc.assignee_pace(start, end)  # type: ignore[arg-type]
            with self.subTest(start=start, end=end, kind="project"), self.assertRaises(ValidationError):
                self.svc.project_pace(start, end)  # type: ignore[arg-type]


if __name__ == "__main__":
    unittest.main()
