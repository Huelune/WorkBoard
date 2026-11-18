from __future__ import annotations

import os
import sqlite3
import unittest

from support import Clock, temp_db_path
from workboard.domain import NotFound, PermissionDenied, ValidationError
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
        self.assertFalse(self.svc.check_login("kim", GOOD))
        self.assertFalse(self.svc.check_login(" kim ", GOOD))
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
        self.assertFalse(self.svc.check_login("kim", OTHER))
        with self.assertRaises(PermissionDenied):
            self.svc.check_login("kim", GOOD)

    def test_editor_resets_a_member_password_and_it_must_be_changed(self) -> None:
        project = self.svc.create_project("ed1", "P")
        self.svc.add_editor("ed1", project, "ed2")
        self.svc.add_member("ed1", project, "kim")
        self.svc.add_member("ed1", project, "no-account")
        for user_id in ("ed1", "ed2", "kim", "outsider"):
            self.svc.create_account(user_id, GOOD)
        refusals = (
            (PermissionDenied, "kim", "kim"),  # not an editor
            (PermissionDenied, "ed1", "ed2"),  # never another editor: that would let one editor act as two
            (PermissionDenied, "ed1", "ed1"),
            (ValidationError, "ed1", "outsider"),  # not in this project
            (NotFound, "ed1", "no-account"),
        )
        for error, actor, target in refusals:
            with self.subTest(actor=actor, target=target), self.assertRaises(error):
                self.svc.reset_password(actor, project, target, TEMP)
        with self.assertRaises(ValidationError):
            self.svc.reset_password("ed1", project, "kim", "short")
        self.assertFalse(self.svc.check_login("kim", GOOD))

        self.svc.reset_password("ed1", project, "kim", TEMP)
        with self.assertRaises(PermissionDenied):
            self.svc.check_login("kim", GOOD)
        self.assertTrue(self.svc.check_login("kim", TEMP))  # must change
        self.svc.change_password("kim", TEMP, OTHER)
        self.assertFalse(self.svc.check_login("kim", OTHER))

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
        self.assertFalse(svc.check_login("kim", GOOD))


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
        self.assertFalse(self.svc.check_login("kim", TEMP))

    def test_forced_change_after_a_reset(self) -> None:
        project = self.svc.create_project("ed1", "P")
        self.svc.add_member("ed1", project, "kim")
        self.svc.create_account("kim", GOOD)
        self.svc.reset_password("ed1", project, "kim", TEMP)
        asked: list[ChangePasswordDialog] = []

        def answer(change: ChangePasswordDialog) -> bool:
            asked.append(change)
            self.assertTrue(change.old.isHidden())
            change.new.setText(OTHER)
            change.confirm.setText(OTHER)
            return change.attempt()

        dialog = self.dialog()
        dialog.run_change = answer  # type: ignore[method-assign]
        dialog.user.setText("kim")
        dialog.password.setText(TEMP)
        self.assertTrue(dialog.attempt())
        self.assertEqual(len(asked), 1)
        self.assertFalse(self.svc.check_login("kim", OTHER))

    def test_reset_button_is_only_for_non_editor_members(self) -> None:
        project = self.svc.create_project("ed1", "P")
        self.svc.add_member("ed1", project, "kim")
        self.svc.create_account("kim", GOOD)
        errors: list[str] = []
        window = MainWindow(self.svc, "ed1")
        self.addCleanup(window.deleteLater)
        window.show_error = errors.append  # type: ignore[method-assign]
        page = window.manage_page
        page.members.selectRow(0)
        self.assertFalse(page.password_reset.isEnabled())
        page.members.selectRow(1)
        self.assertTrue(page.password_reset.isEnabled())
        self.assertFalse(window.reset_password("kim", "short"))
        self.assertTrue(window.reset_password("kim", TEMP))
        self.assertTrue(self.svc.check_login("kim", TEMP))
        self.assertFalse(window.reset_password("ed1", TEMP))
        self.assertEqual(len(errors), 2)


if __name__ == "__main__":
    unittest.main()
