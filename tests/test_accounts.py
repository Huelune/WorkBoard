from __future__ import annotations

import os
import sqlite3
import unittest

from support import Clock, temp_db_path
from workboard.domain import PermissionDenied, ValidationError
from workboard.domain.passwords import hash_password, verify_password
from workboard.services import WorkBoardService
from workboard.storage import open_database
from workboard.storage.database import MIGRATIONS

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
try:
    from PySide6.QtWidgets import QApplication, QLineEdit

    from workboard.ui.login import ChangePasswordDialog, LoginDialog
    from workboard.ui.main_window import MainWindow
except ImportError:
    QApplication = None

# Test-only values; nothing here is a real credential.
GOOD, OTHER, TEMP = "test-pass-one", "test-pass-two", "temp-pass-123"


class PasswordHashTests(unittest.TestCase):
    def test_hash_verifies_and_never_contains_the_password(self) -> None:
        stored = hash_password(GOOD)
        self.assertTrue(stored.startswith("scrypt$"))
        self.assertNotIn(GOOD, stored)
        self.assertTrue(verify_password(GOOD, stored))
        self.assertFalse(verify_password(OTHER, stored))
        self.assertNotEqual(stored, hash_password(GOOD))  # fresh salt every time

    def test_broken_or_foreign_hashes_never_verify(self) -> None:
        for stored in ("", "plain", "md5$1$2$3$aa$bb", "scrypt$x$8$1$zz$zz", None, 5, "scrypt$16384$8$1$00"):
            with self.subTest(stored=stored):
                self.assertFalse(verify_password(GOOD, stored))  # type: ignore[arg-type]
        self.assertFalse(verify_password(None, hash_password(GOOD)))  # type: ignore[arg-type]


class AccountCase(unittest.TestCase):
    def setUp(self) -> None:
        self.conn = open_database(temp_db_path(self))
        self.addCleanup(self.conn.close)
        self.svc = WorkBoardService(self.conn, Clock())


class AccountTests(AccountCase):
    def test_sign_up_then_log_in(self) -> None:
        self.assertFalse(self.svc.has_accounts())
        self.svc.create_account(" kim ", GOOD)
        self.assertTrue(self.svc.has_accounts())
        self.svc.check_login("kim", GOOD)
        self.svc.check_login(" kim ", GOOD)
        stored = self.conn.execute("SELECT password_hash FROM account").fetchone()[0]
        self.assertNotIn(GOOD, stored)

    def test_wrong_password_and_unknown_id_fail_the_same_way(self) -> None:
        self.svc.create_account("kim", GOOD)
        messages = set()
        for user_id, password in (("kim", OTHER), ("nobody", GOOD), ("kim", ""), (None, GOOD), ("kim", None)):
            with self.subTest(user_id=user_id), self.assertRaises(PermissionDenied) as caught:
                self.svc.check_login(user_id, password)  # type: ignore[arg-type]
            messages.add(str(caught.exception))
        self.assertEqual(len(messages), 1)

    def test_sign_up_rules(self) -> None:
        self.svc.create_account("kim", GOOD)
        for user_id, password in (("kim", OTHER), ("lee", "short"), ("lee-long-id", "lee-long-id"), (" ", GOOD), ("lee", None), ("lee", " " * 10)):
            with self.subTest(user_id=user_id), self.assertRaises(ValidationError):
                self.svc.create_account(user_id, password)  # type: ignore[arg-type]
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM account").fetchone()[0], 1)

    def test_change_password(self) -> None:
        self.svc.create_account("kim", GOOD)
        with self.assertRaises(PermissionDenied):
            self.svc.change_password("kim", OTHER, TEMP)
        for new in ("short", GOOD, "kim"):
            with self.subTest(new=new), self.assertRaises(ValidationError):
                self.svc.change_password("kim", GOOD, new)
        self.svc.change_password("kim", GOOD, OTHER)
        self.svc.check_login("kim", OTHER)
        with self.assertRaises(PermissionDenied):
            self.svc.check_login("kim", GOOD)

    def test_nobody_can_set_another_persons_password(self) -> None:
        """Regression: an editor could add anyone as a member and then reset their password."""
        self.svc.create_account("victim", GOOD)
        project = self.svc.create_project("attacker", "P")
        self.svc.add_member("attacker", project, "victim")
        self.assertFalse(hasattr(self.svc, "reset_password"))
        with self.assertRaises(PermissionDenied):
            self.svc.change_password("victim", TEMP, OTHER)  # only the current password opens it
        self.svc.check_login("victim", GOOD)

    def test_an_id_a_project_already_trusts_cannot_be_claimed_by_signing_up(self) -> None:
        """Regression: anyone could sign up as an existing editor or member that had no account yet."""
        project = self.svc.create_project("ed1", "P")
        self.svc.add_member("ed1", project, "kim")
        item = self.svc.create_work_item("ed1", project, "T")
        self.svc.assign("ed1", item, "lee")
        self.svc.remove_member("ed1", project, "lee")  # still the assignee of that work
        self.svc.create_account("ed1", GOOD)  # first account on this PC: the owner of the file claims their ID
        for taken in ("kim", "lee", " kim "):
            with self.subTest(taken=taken), self.assertRaises(PermissionDenied):
                self.svc.create_account(taken, GOOD)
        self.svc.create_account("park", GOOD)  # an ID no project names is free
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM account").fetchone()[0], 2)

    def test_projects_only_name_people_who_have_an_account(self) -> None:
        project = self.svc.create_project("ed1", "P")
        scope = self.svc.create_scope("ed1", project, "v1")
        category = self.svc.create_category("ed1", project, "C")
        item = self.svc.create_work_item("ed1", project, "T")
        self.svc.add_member("ed1", project, "before-accounts")  # nothing to check against yet
        self.svc.create_account("ed1", GOOD)
        self.svc.create_account("kim", GOOD)
        members = len(self.svc.list_members(project))
        attempts = (
            lambda: self.svc.add_member("ed1", project, "ghost"),
            lambda: self.svc.add_editor("ed1", project, "ghost"),
            lambda: self.svc.assign("ed1", item, "ghost"),
            lambda: self.svc.add_work_items("ed1", project, [{"title": "N"}], "ghost", scope_id=scope, category_id=category),
        )
        for index, attempt in enumerate(attempts):
            with self.subTest(index=index), self.assertRaises(ValidationError):
                attempt()
        self.assertEqual(len(self.svc.list_members(project)), members)
        self.assertEqual(self.svc.list_assignments(project), [])
        self.svc.add_member("ed1", project, "kim")
        self.svc.assign("ed1", item, "kim")
        self.assertTrue(self.svc.has_account(" kim "))
        self.assertFalse(self.svc.has_account("ghost"))
        self.assertFalse(self.svc.has_account(None))  # type: ignore[arg-type]

    def test_version_four_database_gets_the_account_table(self) -> None:
        path = temp_db_path(self)
        old = sqlite3.connect(path)
        old.executescript("".join(MIGRATIONS[:4]) + "PRAGMA user_version = 4;")
        old.close()
        conn = open_database(path)
        self.addCleanup(conn.close)
        svc = WorkBoardService(conn, Clock())
        self.assertFalse(svc.has_accounts())
        svc.create_account("kim", GOOD)
        svc.check_login("kim", GOOD)


@unittest.skipIf(QApplication is None, "PySide6 is not installed")
class LoginUiTests(AccountCase):
    def setUp(self) -> None:
        super().setUp()
        self.app = QApplication.instance() or QApplication([])

    def dialog(self, default_id: str = "") -> LoginDialog:
        dialog = LoginDialog(self.svc, default_id)
        self.addCleanup(dialog.deleteLater)
        return dialog

    def test_first_run_starts_with_sign_up(self) -> None:
        dialog = self.dialog("kim")
        self.assertTrue(dialog.signup)
        self.assertFalse(dialog.confirm.isHidden())
        self.assertEqual(dialog.user.text(), "kim")
        self.assertEqual(dialog.password.echoMode(), QLineEdit.EchoMode.Password)
        self.assertEqual(dialog.confirm.echoMode(), QLineEdit.EchoMode.Password)
        dialog.password.setText(GOOD)
        dialog.confirm.setText(OTHER)
        self.assertFalse(dialog.attempt())
        self.assertIn("확인", dialog.message.text())
        dialog.confirm.setText(GOOD)
        self.assertTrue(dialog.attempt())
        self.assertEqual(dialog.user_id, "kim")
        self.assertTrue(self.svc.has_accounts())

    def test_log_in_and_refusals(self) -> None:
        self.svc.create_account("kim", GOOD)
        dialog = self.dialog()
        self.assertFalse(dialog.signup)
        self.assertTrue(dialog.confirm.isHidden())
        dialog.user.setText("kim")
        dialog.password.setText(OTHER)
        self.assertFalse(dialog.attempt())
        self.assertEqual(dialog.message.text(), "ID 또는 비밀번호가 맞지 않습니다.")
        self.assertEqual((dialog.user_id, dialog.password.text()), (None, ""))
        dialog.password.setText(GOOD)
        self.assertTrue(dialog.attempt())
        self.assertEqual(dialog.user_id, "kim")

    def test_sign_up_from_the_login_screen(self) -> None:
        self.svc.create_account("kim", GOOD)
        dialog = self.dialog()
        dialog.toggle.click()
        self.assertTrue(dialog.signup)
        dialog.user.setText("kim")
        dialog.password.setText(OTHER)
        dialog.confirm.setText(OTHER)
        self.assertFalse(dialog.attempt())  # ID already taken
        dialog.user.setText("lee")
        dialog.password.setText(OTHER)
        dialog.confirm.setText(OTHER)
        self.assertTrue(dialog.attempt())
        self.assertEqual(dialog.user_id, "lee")

    def test_sign_up_refuses_an_id_in_use_and_says_what_to_do(self) -> None:
        project = self.svc.create_project("ed1", "P")
        self.svc.add_member("ed1", project, "kim")
        self.svc.create_account("ed1", GOOD)
        dialog = self.dialog()
        dialog.toggle.click()
        dialog.user.setText("kim")
        dialog.password.setText(OTHER)
        dialog.confirm.setText(OTHER)
        self.assertFalse(dialog.attempt())
        self.assertIn("이미 프로젝트에서 쓰이는 ID", dialog.message.text())
        self.assertIsNone(dialog.user_id)
        errors: list[str] = []
        window = MainWindow(self.svc, "ed1")
        self.addCleanup(window.deleteLater)
        window.show_error = errors.append  # type: ignore[method-assign]
        self.assertFalse(window.add_member("ghost"))
        self.assertIn("계정이 없습니다", errors[-1])

    def test_change_password_dialog(self) -> None:
        self.svc.create_account("kim", GOOD)
        dialog = ChangePasswordDialog(self.svc, "kim")
        self.addCleanup(dialog.deleteLater)
        dialog.old.setText(OTHER)
        dialog.new.setText(TEMP)
        dialog.confirm.setText(TEMP)
        self.assertFalse(dialog.attempt())
        dialog.old.setText(GOOD)
        dialog.new.setText(TEMP)
        dialog.confirm.setText(OTHER)
        self.assertFalse(dialog.attempt())
        dialog.confirm.setText(TEMP)
        self.assertTrue(dialog.attempt())
        self.svc.check_login("kim", TEMP)

    def test_manage_page_offers_no_password_reset(self) -> None:
        self.svc.create_project("ed1", "P")
        window = MainWindow(self.svc, "ed1")
        self.addCleanup(window.deleteLater)
        self.assertFalse(hasattr(window.manage_page, "password_reset"))
        self.assertFalse(hasattr(window, "reset_password"))


if __name__ == "__main__":
    unittest.main()
