"""CLI never logs supplied credentials, touches DB by default, or grants permission."""

import hashlib
import json
from uuid import UUID

import pytest

from packages.persistence.demo_order_quota import LocalQuotaStopped
from packages.persistence.demo_response_evidence import _line
from scripts import review_wire_restore_reference as cli
from tests.unit.test_wire_restore_reference_schema import value


@pytest.fixture
def reference(tmp_path):
    tmp_path.chmod(0o700)
    path = tmp_path / "reference.json"
    body = _line(value())
    path.write_bytes(body)
    path.chmod(0o600)
    return path, body, hashlib.sha256(body).hexdigest()


def args(reference):
    return ["--reference-file", str(reference[0]), "--retained-sha256", reference[2]]


def target(reference):
    data = json.loads(reference[1])
    return args(reference) + [
        "--mode",
        "compare",
        "--enable-synthetic-review",
        "--source-directory",
        "/synthetic/source",
        "--account",
        data["binding"]["account"],
        "--scope",
        data["binding"]["scope"],
        "--journal",
        data["binding"]["journal"],
    ]


def result(capsys):
    output = capsys.readouterr()
    assert not output.err
    return json.loads(output.out)


def test_default_inspect_never_reads_database_environment_or_creates_engine(
    reference, capsys, monkeypatch
):
    def forbidden(*args, **kw):
        raise AssertionError("DATABASE_ACCESS_FORBIDDEN")

    monkeypatch.setattr(cli, "create_engine", forbidden)
    monkeypatch.setenv(cli.DATABASE_ENV, "INVALID_PRIVATE_DATABASE_CONFIGURATION")
    assert cli.main(args(reference)) == 0
    report = result(capsys)
    assert report["database_accessed"] is False and report["source_snapshot_compared"] is False
    assert report["supplied_reference_sha256"] == reference[2]
    assert not any(
        report[k]
        for k in (
            "reference_is_latest_proven",
            "external_reference_authenticity_verified",
            "execution_approved",
            "orders_enabled",
            "restore_performed",
            "process_restart_resumption_supported",
        )
    )
    assert "INVALID_PRIVATE" not in str(report)


@pytest.mark.parametrize(
    "variant",
    [
        "missing-flag",
        "missing-target",
        "inspect-with-target",
        "mismatched-binding",
        "missing-env",
        "unsupported-driver",
    ],
)
def test_compare_requires_explicit_complete_target_and_dedicated_environment(
    reference, capsys, monkeypatch, variant
):
    monkeypatch.delenv(cli.DATABASE_ENV, raising=False)
    monkeypatch.setenv("DATABASE_URL", "DO_NOT_USE_THIS_FALLBACK")
    supplied = target(reference)
    if variant == "missing-flag":
        supplied.remove("--enable-synthetic-review")
    elif variant == "missing-target":
        index = supplied.index("--account")
        del supplied[index : index + 2]
    elif variant == "inspect-with-target":
        supplied[supplied.index("--mode") + 1] = "inspect"
    elif variant == "mismatched-binding":
        supplied[supplied.index("--account") + 1] = "DEMO-other"
    elif variant == "unsupported-driver":
        monkeypatch.setenv(cli.DATABASE_ENV, "sqlite:///PRIVATE_DATABASE_LOCATION")
    assert cli.main(supplied) == 2
    report = result(capsys)
    assert report["reference_cli_review"] == "BLOCKED_KEEP_PAUSED"
    assert "PRIVATE_DATABASE_LOCATION" not in str(report)
    assert "DO_NOT_USE_THIS_FALLBACK" not in str(report)


@pytest.mark.parametrize(
    "variant", ["bad-digest", "public-file", "symlink", "tampered-body", "missing-file"]
)
def test_bad_reference_has_sanitized_errors_without_repair(reference, capsys, variant):
    supplied = args(reference)
    path = reference[0]
    if variant == "bad-digest":
        supplied[-1] = "b" * 64
    elif variant == "public-file":
        path.chmod(0o644)
    elif variant == "symlink":
        path.unlink()
        path.symlink_to(path.parent / "MISSING_PRIVATE_TARGET")
    elif variant == "tampered-body":
        path.write_bytes(b"PRIVATE_REFERENCE_CONTENT")
    else:
        path.unlink()
    assert cli.main(supplied) == 2
    report = result(capsys)
    assert not report["restore_performed"] and not report["orders_enabled"]
    assert "PRIVATE_REFERENCE_CONTENT" not in str(report) and "MISSING_PRIVATE_TARGET" not in str(
        report
    )


@pytest.mark.parametrize(
    "supplied",
    [
        [],
        ["--database-url", "PRIVATE_PASSWORD_DO_NOT_ECHO"],
        ["--mode", "PRIVATE_PASSWORD_DO_NOT_ECHO"],
        ["--retained-sha256", "PRIVATE_PASSWORD_DO_NOT_ECHO"],
    ],
)
def test_argument_errors_never_echo_rejected_values_or_argv(capsys, supplied):
    with pytest.raises(SystemExit) as exit_info:
        cli.main(supplied)
    assert exit_info.value.code == 2
    output = capsys.readouterr()
    assert "PRIVATE_PASSWORD_DO_NOT_ECHO" not in output.out + output.err
    assert json.loads(output.out)["failure_code"] == "WIRE_REVIEW_ARGUMENTS_INVALID"


@pytest.mark.parametrize(
    "error",
    [
        RuntimeError("PRIVATE_PASSWORD_FROM_DATABASE_DRIVER"),
        OSError("PRIVATE_REFERENCE_PATH"),
        LocalQuotaStopped("PRIVATE_ERROR_NOT_ALLOWLISTED"),
        LocalQuotaStopped("WIRE_PRIVATE_TOKEN_SHOULD_NOT_ECHO"),
    ],
)
def test_exception_messages_outside_static_codes_are_never_output(
    reference, capsys, monkeypatch, error
):
    monkeypatch.setenv(
        cli.DATABASE_ENV, "postgresql+psycopg://private:PRIVATE_PASSWORD@127.0.0.1/private"
    )

    def fail(*args, **kw):
        raise error

    monkeypatch.setattr(cli, "create_engine", fail)
    assert cli.main(target(reference)) == 2
    report = result(capsys)
    assert "PRIVATE" not in json.dumps(report)


@pytest.mark.parametrize("matches", [False, True])
def test_compare_exit_code_means_exact_match_only_and_engine_is_disposed(
    reference, capsys, monkeypatch, matches
):
    monkeypatch.setenv(cli.DATABASE_ENV, "postgresql+psycopg://dev:dev@127.0.0.1/private")
    disposed = []

    class FakeEngine:
        def dispose(self):
            disposed.append(True)

    engine = FakeEngine()
    monkeypatch.setattr(cli, "create_engine", lambda *args, **kw: engine)
    monkeypatch.setattr(cli, "WireCheckpointStore", lambda value: value)

    def compare(store, source, scope, journal, account, path, *, retained_sha256):
        assert isinstance(journal, UUID) and retained_sha256 == reference[2]
        return dict(
            matches_supplied_retained_reference=matches,
            execution_approved=False,
            orders_enabled=False,
            restore_performed=False,
            reference_is_latest_proven=False,
        )

    monkeypatch.setattr(cli, "review_reference_file", compare)
    assert cli.main(target(reference)) == (0 if matches else 2)
    report = result(capsys)
    assert disposed == [True]
    assert report["mode"] == "compare" and report["matches_supplied_retained_reference"] is matches
    assert not report["execution_approved"] and not report["restore_performed"]


def test_engine_cleanup_failure_is_sanitized_and_blocks_result(reference, capsys, monkeypatch):
    monkeypatch.setenv(cli.DATABASE_ENV, "postgresql+psycopg://dev:dev@127.0.0.1/private")

    class FakeEngine:
        def dispose(self):
            raise RuntimeError("PRIVATE_DATABASE_CLOSE_MESSAGE")

    monkeypatch.setattr(cli, "create_engine", lambda *args, **kw: FakeEngine())
    monkeypatch.setattr(cli, "WireCheckpointStore", lambda value: value)
    monkeypatch.setattr(
        cli,
        "review_reference_file",
        lambda *args, **kw: dict(matches_supplied_retained_reference=True),
    )
    assert cli.main(target(reference)) == 2
    assert result(capsys)["failure_code"] == "WIRE_REVIEW_SOURCE_CLEANUP_FAILED"
