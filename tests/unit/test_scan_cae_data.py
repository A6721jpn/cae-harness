from __future__ import annotations

import json
import os
import subprocess
import sys
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from febio_cae.cli import scan_cae_data as scanner_module

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
SCANNER = REPOSITORY_ROOT / "scripts" / "scan_cae_data.py"


def _run_scanner(
    root: Path, environment: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    environment = (environment or os.environ.copy()).copy()
    source_root = REPOSITORY_ROOT / "src"
    pythonpath = environment.get("PYTHONPATH")
    environment["PYTHONPATH"] = (
        f"{source_root}{os.pathsep}{pythonpath}" if pythonpath else str(source_root)
    )
    return subprocess.run(
        [sys.executable, str(SCANNER), "--root", str(root), "--json"],
        cwd=REPOSITORY_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )


def _init_git_repo(root: Path) -> None:
    subprocess.run(
        ["git", "init", "--quiet", str(root)],
        capture_output=True,
        check=True,
        text=True,
    )


def test_clean_repository_boundary_scan_returns_structured_pass() -> None:
    completed = _run_scanner(REPOSITORY_ROOT)

    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["schema_version"] == "1"
    assert payload["status"] == "PASS"
    assert payload["issues"] == []
    assert ".local" in payload["excluded_paths"]
    assert payload["git_tracking"]["available"] is True


def test_boundary_scan_rejects_cae_data_and_reports_reason(tmp_path: Path) -> None:
    forbidden_file = tmp_path / "02_CAE" / "synthetic-result.feb"
    forbidden_file.parent.mkdir()
    forbidden_file.write_text("synthetic fixture; not a real CAE result", encoding="utf-8")
    _init_git_repo(tmp_path)

    completed = _run_scanner(tmp_path)

    assert completed.returncode == 4, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["status"] == "REJECT"
    assert any(issue["code"] == "FORBIDDEN_CAE_DATA" for issue in payload["issues"])
    assert any(issue["path"].endswith("synthetic-result.feb") for issue in payload["issues"])


def test_boundary_scan_excludes_local_execution_artifacts(tmp_path: Path) -> None:
    generated_file = tmp_path / ".local" / "verification" / "old-result.xplt"
    generated_file.parent.mkdir(parents=True)
    generated_file.write_text("synthetic local artifact", encoding="utf-8")
    _init_git_repo(tmp_path)

    completed = _run_scanner(tmp_path)

    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["status"] in {"PASS", "PASS_WITH_WARNINGS"}
    assert payload["issues"] == []
    assert ".local" in payload["excluded_paths"]


def test_boundary_scan_rejects_non_git_root(tmp_path: Path) -> None:
    completed = _run_scanner(tmp_path)

    assert completed.returncode == 4, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["status"] == "INCOMPLETE"
    assert any(
        issue["code"] in {"GIT_TRACKING_UNAVAILABLE", "GIT_ROOT_MISMATCH"}
        for issue in payload["issues"]
    )


def test_boundary_scan_rejects_when_git_is_unavailable(tmp_path: Path) -> None:
    _init_git_repo(tmp_path)
    (tmp_path / "README.md").write_text("synthetic", encoding="utf-8")
    environment = os.environ.copy()
    environment["PATH"] = ""

    completed = _run_scanner(tmp_path, environment)

    assert completed.returncode == 4, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["status"] == "INCOMPLETE"
    assert any(issue["code"] == "GIT_TRACKING_UNAVAILABLE" for issue in payload["issues"])


def test_boundary_scan_inspects_staged_febio_xml_even_if_worktree_is_benign(
    tmp_path: Path,
) -> None:
    _init_git_repo(tmp_path)
    candidate = tmp_path / "notes.txt"
    candidate.write_text(
        '<?xml version="1.0"?><febio_spec version="4.0"></febio_spec>',
        encoding="utf-8",
    )
    subprocess.run(["git", "-C", str(tmp_path), "add", "notes.txt"], check=True)
    candidate.write_text("ordinary engineering notes", encoding="utf-8")

    completed = _run_scanner(tmp_path)

    assert completed.returncode == 4, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["status"] == "REJECT"
    assert any(
        issue["code"] == "FORBIDDEN_CAE_CONTENT" and issue["path"] == "notes.txt"
        for issue in payload["issues"]
    )


@pytest.mark.parametrize(
    ("fixture_name", "content"),
    [
        (
            "xml-comment",
            (
                b'<?xml version="1.0"?>\n'
                b"<!-- Synthetic generated CAE input -->\n"
                b'<febio_spec version="4.0"><Module type="solid"/></febio_spec>'
            ),
        ),
        (
            "xml-processing-instruction",
            (
                b'<?xml version="1.0"?>\n'
                b'<?febio generated="synthetic"?>\n'
                b'<febio_spec version="4.0"><Module type="solid"/></febio_spec>'
            ),
        ),
        (
            "xml-utf16-le",
            '<?xml version="1.0" encoding="UTF-16"?><febio_spec version="4.0">'
            '<Module type="solid"/></febio_spec>'.encode("utf-16"),
        ),
        (
            "xml-utf16-be",
            b"\xfe\xff"
            + '<?xml version="1.0" encoding="UTF-16"?><febio_spec version="4.0">'
            '<Module type="solid"/></febio_spec>'.encode("utf-16-be"),
        ),
    ],
)
def test_boundary_scan_rejects_staged_febio_xml_prologs_and_utf16(
    tmp_path: Path, fixture_name: str, content: bytes
) -> None:
    _init_git_repo(tmp_path)
    candidate = tmp_path / f"{fixture_name}.txt"
    candidate.write_bytes(content)
    subprocess.run(["git", "-C", str(tmp_path), "add", candidate.name], check=True)
    candidate.write_text("ordinary worktree notes", encoding="utf-8")

    completed = _run_scanner(tmp_path)

    assert completed.returncode == 4, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["status"] == "REJECT"
    assert any(
        issue["code"] == "FORBIDDEN_CAE_CONTENT" and issue["path"] == candidate.name
        for issue in payload["issues"]
    )


def test_boundary_scan_rejects_staged_step_signature_with_neutral_name(tmp_path: Path) -> None:
    _init_git_repo(tmp_path)
    candidate = tmp_path / "artifact.txt"
    content = b"ISO-10303-21;\nHEADER;\nENDSEC;\nDATA;\nENDSEC;\nEND-ISO-10303-21;\n"
    candidate.write_bytes(content)
    subprocess.run(["git", "-C", str(tmp_path), "add", candidate.name], check=True)
    candidate.write_text("ordinary worktree notes", encoding="utf-8")

    completed = _run_scanner(tmp_path)

    assert completed.returncode == 4, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["status"] == "REJECT"
    assert any(
        issue["code"] == "FORBIDDEN_CAE_CONTENT" and issue["path"] == candidate.name
        for issue in payload["issues"]
    )


def test_boundary_scan_rejects_staged_xplt_signature_with_neutral_name(tmp_path: Path) -> None:
    _init_git_repo(tmp_path)
    candidate = tmp_path / "artifact.txt"
    content = b"BEF" + b"\x00\x00\x00\x01\x00\x00\x00\x00"
    candidate.write_bytes(content)
    subprocess.run(["git", "-C", str(tmp_path), "add", candidate.name], check=True)
    candidate.write_text("ordinary worktree notes", encoding="utf-8")

    completed = _run_scanner(tmp_path)

    assert completed.returncode == 4, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["status"] == "REJECT"
    assert any(
        issue["code"] == "FORBIDDEN_CAE_CONTENT" and issue["path"] == candidate.name
        for issue in payload["issues"]
    )


def test_boundary_scan_rejects_staged_encrypted_rsa_pem_with_neutral_name(
    tmp_path: Path,
) -> None:
    _init_git_repo(tmp_path)
    candidate = tmp_path / "artifact.txt"
    content = (
        b"-----BEGIN RSA PRIVATE KEY-----\n"
        b"Proc-Type: 4,ENCRYPTED\n"
        b"DEK-Info: AES-128-CBC,00000000000000000000000000000000\n\n"
        + (b"A" * 96)
        + b"\n-----END RSA PRIVATE KEY-----\n"
    )
    candidate.write_bytes(content)
    subprocess.run(["git", "-C", str(tmp_path), "add", candidate.name], check=True)
    candidate.write_text("ordinary worktree notes", encoding="utf-8")

    completed = _run_scanner(tmp_path)

    assert completed.returncode == 4, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["status"] == "REJECT"
    assert any(
        issue["code"] == "FORBIDDEN_SENSITIVE_CONTENT" and issue["path"] == candidate.name
        for issue in payload["issues"]
    )


def test_boundary_scan_marks_unsupported_xml_encoding_incomplete(tmp_path: Path) -> None:
    _init_git_repo(tmp_path)
    candidate = tmp_path / "model.txt"
    candidate.write_bytes(
        b'<?xml version="1.0" encoding="UTF-32"?><febio_spec version="4.0"></febio_spec>'
    )
    subprocess.run(["git", "-C", str(tmp_path), "add", candidate.name], check=True)
    candidate.write_text("ordinary worktree notes", encoding="utf-8")

    completed = _run_scanner(tmp_path)

    assert completed.returncode == 4, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["status"] == "INCOMPLETE"
    assert any(
        issue["code"] == "UNSUPPORTED_CONTENT_ENCODING" and issue["path"] == candidate.name
        for issue in payload["issues"]
    )


def test_boundary_scan_rejects_worktree_xml_with_comment(tmp_path: Path) -> None:
    _init_git_repo(tmp_path)
    candidate = tmp_path / "current.txt"
    candidate.write_text("ordinary staged notes", encoding="utf-8")
    subprocess.run(["git", "-C", str(tmp_path), "add", candidate.name], check=True)
    candidate.write_bytes(
        b'<?xml version="1.0"?>\n<!-- current worktree -->\n<febio_spec version="4.0"></febio_spec>'
    )

    completed = _run_scanner(tmp_path)

    assert completed.returncode == 4, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["status"] == "REJECT"
    assert any(
        issue["code"] == "FORBIDDEN_CAE_CONTENT" and issue["path"] == candidate.name
        for issue in payload["issues"]
    )


def test_boundary_scan_rejects_staged_private_key_content(tmp_path: Path) -> None:
    _init_git_repo(tmp_path)
    candidate = tmp_path / "notes.txt"
    candidate.write_text(
        "-----BEGIN PRIVATE KEY-----\n" + ("A" * 96) + "\n-----END PRIVATE KEY-----\n",
        encoding="ascii",
    )
    subprocess.run(["git", "-C", str(tmp_path), "add", "notes.txt"], check=True)

    completed = _run_scanner(tmp_path)

    assert completed.returncode == 4, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["status"] == "REJECT"
    assert any(
        issue["code"] == "FORBIDDEN_SENSITIVE_CONTENT" and issue["path"] == "notes.txt"
        for issue in payload["issues"]
    )


def test_boundary_scan_allows_benign_documentation_mentions(tmp_path: Path) -> None:
    _init_git_repo(tmp_path)
    candidate = tmp_path / "README.md"
    candidate.write_text(
        "This guide mentions <febio_spec> and -----BEGIN PRIVATE KEY----- "
        "as prohibited examples, without a payload.",
        encoding="utf-8",
    )
    subprocess.run(["git", "-C", str(tmp_path), "add", "README.md"], check=True)

    completed = _run_scanner(tmp_path)

    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["status"] == "PASS"
    assert payload["issues"] == []


def test_boundary_scan_reports_filesystem_read_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _init_git_repo(tmp_path)

    def failing_walk(
        root: Path,
        *,
        topdown: bool,
        followlinks: bool,
        onerror: Callable[[OSError], None],
    ) -> Iterator[tuple[str, list[str], list[str]]]:
        del root, topdown, followlinks
        onerror(PermissionError(13, "permission denied", str(tmp_path / "blocked")))
        yield str(tmp_path), [], []

    monkeypatch.setattr(scanner_module.os, "walk", failing_walk)
    report = scanner_module.scan_root(tmp_path)

    assert report["status"] == "INCOMPLETE"
    issues = report["issues"]
    assert isinstance(issues, list)
    assert any(
        isinstance(issue, dict) and issue.get("code") == "FILESYSTEM_READ_ERROR" for issue in issues
    )


def test_boundary_scan_rejects_reparse_points_without_traversal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _init_git_repo(tmp_path)
    reparse_directory = tmp_path / "junction-like"
    reparse_directory.mkdir()
    (reparse_directory / "hidden.feb").write_text("synthetic", encoding="utf-8")
    monkeypatch.setattr(
        scanner_module,
        "_is_reparse_point",
        lambda path: Path(path) == reparse_directory,
    )

    report = scanner_module.scan_root(tmp_path)

    assert report["status"] == "INCOMPLETE"
    issues = report["issues"]
    assert isinstance(issues, list)
    assert any(
        isinstance(issue, dict) and issue.get("code") == "UNSAFE_REPARSE_POINT" for issue in issues
    )


def test_boundary_scan_main_rejects_lstat_metadata_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _init_git_repo(tmp_path)
    (tmp_path / "notes.txt").write_text("ordinary notes", encoding="utf-8")

    def failing_lstat(path: object) -> object:
        raise PermissionError(13, "synthetic metadata failure", str(path))

    monkeypatch.setattr(scanner_module.os, "lstat", failing_lstat)

    exit_code = scanner_module.main(["--root", str(tmp_path), "--json"])
    captured = capsys.readouterr()
    payload = json.loads(captured.out)

    assert exit_code == 4
    assert payload["status"] == "INCOMPLETE"
    assert any(issue["code"] == "FILESYSTEM_METADATA_ERROR" for issue in payload["issues"])
