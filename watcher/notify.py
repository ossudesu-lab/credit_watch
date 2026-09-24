"""メールで通知する（Gmail の SMTP）。

パスワードは Gmail のアプリパスワード。普段のログイン用とは別物で、個別に取り消せる。
"""

from __future__ import annotations

import smtplib
from email.message import EmailMessage

SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 465
TIMEOUT_SEC = 20


class GmailSender:
    def __init__(self, user: str, password: str, to: str):
        self._user = user
        self._password = password
        self._to = to

    def send(self, subject: str, body: str) -> None:
        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = self._user
        msg["To"] = self._to
        msg.set_content(body)
        with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=TIMEOUT_SEC) as smtp:
            smtp.login(self._user, self._password)
            smtp.send_message(msg)


class PrintSender:
    """--dry-run 用。送らずに画面に出すだけ。"""

    def send(self, subject: str, body: str) -> None:
        print(f"===== {subject}\n{body}\n")
