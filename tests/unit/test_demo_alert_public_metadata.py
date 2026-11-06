"""Public expiry metadata must never expose or extend private SMTP settings."""

import json

import pytest
from pydantic import ValidationError

from scripts import demo_alert_email_session as mail
from services.operations.demo_alert_status import EmailMetadata
from tests.unit.test_demo_alert_email_session import ADDRESS, PASSWORD, ram  # noqa: F401


def test_public_expiry_only_no_write_or_ttl_extension(ram, monkeypatch):  # noqa: F811
    mail.configure()
    path = ram / mail.FILENAME
    original = path.read_bytes()
    monkeypatch.setattr(mail.time, "time", lambda: 100123)
    result = mail.public_status()
    assert result == {
        "state": "CONFIGURED_PRIVATE_RAM",
        "expires_unix": 143200,
        "remaining_seconds": 43077,
    }
    assert ADDRESS not in json.dumps(result) and PASSWORD not in json.dumps(result)
    assert path.read_bytes() == original
    assert mail.load().address == ADDRESS
    assert mail.load().password.get_secret_value() == PASSWORD


@pytest.mark.parametrize("fault", ["missing", "expired", "invalid", "permissions"])
def test_public_missing_or_invalid_has_no_private_failure_or_invented_expiry(
    ram,  # noqa: F811
    monkeypatch,
    fault,  # noqa: F811
):  # noqa: F811
    if fault != "missing":
        mail.configure()
        path = ram / mail.FILENAME
        if fault == "expired":
            monkeypatch.setattr(mail.time, "time", lambda: 143200)
        elif fault == "invalid":
            path.write_text(PASSWORD)
        else:
            path.chmod(0o644)
    result = mail.public_status()
    assert result == {
        "state": "NOT_CONFIGURED" if fault == "missing" else "BLOCKED_EXPIRED_OR_INVALID",
        "expires_unix": None,
        "remaining_seconds": None,
    }
    assert ADDRESS not in json.dumps(result) and PASSWORD not in json.dumps(result)


@pytest.mark.parametrize(
    "payload",
    [
        {"state": "CONFIGURED_PRIVATE_RAM"},
        {"state": "NOT_CONFIGURED", "expires_unix": 143200},
        {"state": "CONFIGURED_PRIVATE_RAM", "expires_unix": 143200, "remaining_seconds": 43201},
        {"state": "NOT_CONFIGURED", "address": ADDRESS},
        {"state": "NOT_CONFIGURED", "password": PASSWORD},
    ],
)
def test_public_model_rejects_private_fields_or_invented_validity(payload):
    with pytest.raises(ValidationError):
        EmailMetadata.model_validate(payload)
