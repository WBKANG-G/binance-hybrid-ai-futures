"""Diagnostic metadata is bounded and never contains SQL, paths or exception bodies."""

import json
import sys

import pytest
from sqlalchemy.exc import DBAPIError

from scripts import review_demo_read_code_install as guard


@pytest.mark.parametrize(
    "code,category",
    [
        ("57014", "DATABASE_TIMEOUT"),
        ("55P03", "DATABASE_LOCK_TIMEOUT"),
        ("42501", "DATABASE_PERMISSION"),
        ("42P01", "DATABASE_RELATION_MISSING"),
    ],
)
def test_sqlstate_is_safe_without_statement_parameters_or_original_message(code, category):
    original = RuntimeError("PRIVATE_DB_URL")
    original.sqlstate = code
    error = DBAPIError("SELECT PRIVATE_QUERY", {"key": "PRIVATE_VALUE"}, original)
    report = guard.safe_failure(error)
    assert report["sqlstate"] == code and report["failure_category"] == category
    assert "PRIVATE" not in json.dumps(report)


@pytest.mark.parametrize(
    "error,category",
    [
        (PermissionError(13, "PRIVATE_MESSAGE", "PRIVATE_PATH"), "FILESYSTEM_PERMISSION"),
        (FileNotFoundError(2, "PRIVATE_MESSAGE", "PRIVATE_PATH"), "FILE_NOT_FOUND"),
        (TypeError("PRIVATE_TYPE"), "TYPE_SHAPE"),
        (ValueError("PRIVATE_VALUE"), "VALUE_SHAPE"),
    ],
)
def test_safe_type_categories_never_expose_message_or_path(error, category):
    report = guard.safe_failure(error)
    assert report["failure_category"] == category and "PRIVATE" not in json.dumps(report)


def test_main_reports_last_stage_and_opaque_resource_without_state_leak(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["-c", "a" * 64, "pre"])

    def fail(*args, **kwargs):
        guard.mark("STORAGE_FILE_READ", "STORAGE_FILE", "PRIVATE_PATH")
        raise PermissionError(13, "PRIVATE_MESSAGE", "PRIVATE_PATH")

    monkeypatch.setattr(guard, "review", fail)
    assert guard.main() == 1
    report = json.loads(capsys.readouterr().out)
    assert report["operation_stage"] == "STORAGE_FILE_READ" and report["os_errno"] == 13
    assert len(report["resource_id"]) == 16 and "PRIVATE" not in json.dumps(report)
    assert guard.DIAGNOSTIC.get() is None


def test_later_stage_clears_previous_resource_identity():
    token = guard.DIAGNOSTIC.set({})
    try:
        guard.mark("DATABASE_TABLE_READ", "DATABASE_TABLE", "PRIVATE_TABLE")
        guard.mark("STORAGE_ROOT_VALIDATE")
        assert guard.DIAGNOSTIC.get() == {"operation_stage": "STORAGE_ROOT_VALIDATE"}
    finally:
        guard.DIAGNOSTIC.reset(token)
