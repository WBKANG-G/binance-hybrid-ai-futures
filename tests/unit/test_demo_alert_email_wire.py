"""Real loopback verified TLS/SMTP AUTH/DATA; no actual NAVER connection."""

import base64
import email
import socketserver
import ssl
import subprocess
import threading
from types import SimpleNamespace
from uuid import uuid4

import pytest
from pydantic import SecretStr

from scripts.demo_alert_email_session import EmailSettings
from services.operations.demo_alert_delivery import DeliveryFailure
from services.operations.demo_alert_email import DeadlineSMTP, NaverEmail


@pytest.fixture
def smtp_server(tmp_path):
    cert, key = tmp_path / "smtp-cert.pem", tmp_path / "smtp-key.pem"
    subprocess.run(
        [
            "openssl",
            "req",
            "-x509",
            "-nodes",
            "-newkey",
            "rsa:2048",
            "-days",
            "1",
            "-keyout",
            str(key),
            "-out",
            str(cert),
            "-subj",
            "/CN=127.0.0.1",
            "-addext",
            "subjectAltName=IP:127.0.0.1",
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert, key)
    trusted = ssl.create_default_context(cafile=str(cert))
    state = SimpleNamespace(
        messages=[],
        senders=[],
        recipients=[],
        accept_auth=True,
        authenticated=0,
        connection_arguments=[],
    )

    class Handler(socketserver.StreamRequestHandler):
        def reply(self, value):
            self.wfile.write(value.encode() + b"\r\n")
            self.wfile.flush()

        def handle(self):
            self.reply("220 local-test ESMTP")
            while line := self.rfile.readline(8192):
                text = line.decode().strip()
                command = text.split(" ", 1)[0].upper()
                if command in {"EHLO", "HELO"}:
                    self.reply("250-local-test\r\n250-AUTH PLAIN\r\n250 8BITMIME")
                elif command == "AUTH":
                    value = base64.b64decode(text.split(" ", 2)[2]).decode().split("\0")
                    assert value[-2] == "fixture-user" and value[-1] == "SYNTHETIC_APP_PASSWORD"
                    if state.accept_auth:
                        state.authenticated += 1
                        self.reply("235 Authentication successful")
                    else:
                        self.reply("535 SYNTHETIC_SECRET_ERROR_DO_NOT_LOG")
                elif command == "MAIL":
                    state.senders.append(text)
                    self.reply("250 OK")
                elif command == "RCPT":
                    state.recipients.append(text)
                    self.reply("250 OK")
                elif command == "DATA":
                    self.reply("354 Send message")
                    content = []
                    while (part := self.rfile.readline()) != b".\r\n":
                        if not part:
                            return
                        content.append(part)
                    state.messages.append(email.message_from_bytes(b"".join(content)))
                    self.reply("250 Accepted")
                elif command == "QUIT":
                    self.reply("221 Bye")
                    return
                elif command == "RSET":
                    self.reply("250 Reset")
                else:
                    self.reply("500 Unsupported")

    class Server(socketserver.ThreadingTCPServer):
        daemon_threads = True

        def handle_error(self, *args):
            pass

    server = Server(("127.0.0.1", 0), Handler)
    server.socket = context.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    def factory(host, port, *, timeout, context):
        assert host == "smtp.naver.com" and port == 465
        assert context.check_hostname and context.verify_mode == ssl.CERT_REQUIRED
        state.connection_arguments.append((host, port))
        return DeadlineSMTP("127.0.0.1", server.server_address[1], timeout=timeout, context=trusted)

    state.factory = factory
    state.address = "fixture-user@naver.com"
    state.password = SecretStr("SYNTHETIC_APP_PASSWORD")
    try:
        yield state
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_naver_self_test_auth_tls_and_single_recipient(smtp_server):
    state = smtp_server
    identifier = uuid4()
    settings = EmailSettings(state.address, state.password)
    NaverEmail(settings, factory=state.factory).send_test(identifier)
    assert state.authenticated == 1 and len(state.messages) == 1
    message = state.messages[0]
    assert message["From"] == message["To"] == state.address
    assert message["Message-ID"] == f"<demo-alert-{identifier}@naver.com>"
    assert "SYNTHETIC_APP_PASSWORD" not in message.as_string()
    assert len(state.recipients) == 1


def test_smtp_auth_failure_has_fixed_code_and_no_data(smtp_server):
    state = smtp_server
    state.accept_auth = False
    with pytest.raises(DeliveryFailure, match="SMTP_AUTH_FAILED") as error:
        NaverEmail(EmailSettings(state.address, state.password), factory=state.factory).send_test(
            uuid4()
        )
    assert "SECRET" not in str(error.value) and not state.messages and not state.recipients
