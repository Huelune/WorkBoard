from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QWidget,
)

from workboard.domain import NotFound, PermissionDenied, ValidationError
from workboard.services import WorkBoardService

RULE = "비밀번호는 8자 이상이고 ID와 달라야 합니다."


def _secret() -> QLineEdit:
    field = QLineEdit()
    field.setEchoMode(QLineEdit.EchoMode.Password)
    return field


def _message() -> QLabel:
    label = QLabel()
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setWordWrap(True)
    label.setStyleSheet("color: #e5534b;")
    return label


class ChangePasswordDialog(QDialog):
    """Change one's own password. The current password is always asked for."""

    def __init__(self, service: WorkBoardService, user_id: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.service, self.user_id = service, user_id
        self.setWindowTitle("비밀번호 변경")
        self.setMinimumWidth(380)
        self.old, self.new, self.confirm = _secret(), _secret(), _secret()
        self.message = _message()
        save = QPushButton("변경")
        save.setDefault(True)
        form = QFormLayout(self)
        form.addRow("지금 비밀번호", self.old)
        form.addRow("새 비밀번호", self.new)
        form.addRow("새 비밀번호 확인", self.confirm)
        form.addRow(self.message)
        form.addRow(save)
        save.clicked.connect(self.attempt)

    def attempt(self) -> bool:
        if self.new.text() != self.confirm.text():
            self.message.setText("새 비밀번호 확인이 다릅니다.")
            return False
        try:
            self.service.change_password(self.user_id, self.old.text(), self.new.text())
        except PermissionDenied:
            self.message.setText("지금 비밀번호가 맞지 않습니다.")
            return False
        except (ValidationError, NotFound):
            self.message.setText(f"{RULE} 지금 비밀번호와도 달라야 합니다.")
            return False
        self.accept()
        return True


class LoginDialog(QDialog):
    """Asks who is using the app. `user_id` is set only after a successful login or sign-up."""

    def __init__(self, service: WorkBoardService, default_id: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.service = service
        self.user_id: str | None = None
        self.signup = not service.has_accounts()  # first run on this PC: nothing to log in to yet
        self.setWindowTitle("WorkBoard")
        self.setMinimumWidth(380)
        self.user = QLineEdit(default_id)
        self.password, self.confirm = _secret(), _secret()
        self.confirm_label = QLabel("비밀번호 확인")
        self.hint = QLabel()
        self.hint.setWordWrap(True)
        self.message = _message()
        self.submit = QPushButton()
        self.submit.setDefault(True)
        self.toggle = QPushButton()
        self.toggle.setAutoDefault(False)
        buttons = QHBoxLayout()
        buttons.addWidget(self.toggle)
        buttons.addStretch(1)
        buttons.addWidget(self.submit)
        form = QFormLayout(self)
        form.addRow(self.hint)
        form.addRow("ID", self.user)
        form.addRow("비밀번호", self.password)
        form.addRow(self.confirm_label, self.confirm)
        form.addRow(self.message)
        form.addRow(buttons)
        self.submit.clicked.connect(self.attempt)
        self.toggle.clicked.connect(lambda: self.set_mode(not self.signup))
        self.set_mode(self.signup)
        (self.password if default_id else self.user).setFocus()

    def set_mode(self, signup: bool) -> None:
        self.signup = signup
        self.confirm.setVisible(signup)
        self.confirm_label.setVisible(signup)
        self.submit.setText("계정 만들기" if signup else "로그인")
        self.toggle.setText("로그인으로 돌아가기" if signup else "계정 만들기")
        self.hint.setText(f"이 PC에서 쓸 계정을 만듭니다. {RULE}" if signup else "ID와 비밀번호를 입력하세요.")
        self.message.clear()

    def attempt(self) -> bool:
        user_id, password = self.user.text().strip(), self.password.text()
        try:
            if self.signup:
                if password != self.confirm.text():
                    self.message.setText("비밀번호 확인이 다릅니다.")
                    return False
                self.service.create_account(user_id, password)
            else:
                self.service.check_login(user_id, password)
        except ValidationError:
            self.message.setText(f"이미 있는 ID이거나 규칙에 맞지 않습니다. {RULE}")
            return False
        except PermissionDenied:
            self.password.clear()
            self.message.setText(
                "이미 프로젝트에서 쓰이는 ID라서 새로 만들 수 없습니다. 다른 ID로 만든 뒤 편집자에게 추가를 요청하세요."
                if self.signup
                else "ID 또는 비밀번호가 맞지 않습니다."
            )
            return False
        self.user_id = user_id
        self.accept()
        return True
