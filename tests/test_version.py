from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

import version as version_tool


def create_git_project(tmp_path: Path, project_version: str = "0.0.1") -> Path:
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    (project_dir / "pyproject.toml").write_text(
        f'[project]\nname = "synthetic"\nversion = "{project_version}"\n',
        encoding="utf-8",
    )
    version_tool.run_git(["init"], cwd=project_dir)
    version_tool.run_git(["config", "user.name", "Version Test"], cwd=project_dir)
    version_tool.run_git(
        ["config", "user.email", "version-test@example.invalid"], cwd=project_dir
    )
    version_tool.run_git(["add", "pyproject.toml"], cwd=project_dir)
    version_tool.run_git(["commit", "-m", "initial"], cwd=project_dir)
    return project_dir


@pytest.mark.parametrize(
    ("current", "release", "expected"),
    [
        ("1.2.3", "major", "2.0.0"),
        ("1.2.3", "minor", "1.3.0"),
        ("1.2.3", "patch", "1.2.4"),
    ],
)
def test_bump_version(current: str, release: str, expected: str) -> None:
    assert version_tool.bump_version(current, release) == expected


@pytest.mark.parametrize(
    "invalid",
    ["", "1", "1.2", "1.2.3.4", "01.2.3", "1.02.3", "1.2.03", "v1.2.3", "1.2.3-rc1"],
)
def test_parse_version_rejects_noncanonical_values(invalid: str) -> None:
    with pytest.raises(version_tool.VersionError, match="Versão inválida"):
        version_tool.parse_version(invalid)


def test_cli_requires_exactly_one_version_choice() -> None:
    with pytest.raises(SystemExit) as missing:
        version_tool.parse_args([])
    assert missing.value.code == 2

    for arguments in (["--major", "--minor"], ["--patch", "1.2.3"]):
        with pytest.raises(SystemExit) as conflicting:
            version_tool.parse_args(arguments)
        assert conflicting.value.code == 2


def test_load_and_replace_version_preserve_comments_and_line_endings(tmp_path: Path) -> None:
    pyproject = tmp_path / "pyproject.toml"
    original = (
        "# comentário preservado\r\n"
        "[project]\r\n"
        'name = "synthetic"\r\n'
        'version = "1.2.3"\r\n'
        "# version remains documented here\r\n"
    )
    with pyproject.open("w", encoding="utf-8", newline="") as pyproject_file:
        pyproject_file.write(original)

    assert version_tool.load_project_version(pyproject) == "1.2.3"
    text = version_tool.read_project_text(pyproject)
    updated = version_tool.replace_version_line(text, "1.2.3", "1.2.4")

    assert updated == original.replace('version = "1.2.3"', 'version = "1.2.4"')
    assert updated.count("\r\n") == original.count("\r\n")


def test_project_version_errors_are_friendly(tmp_path: Path) -> None:
    missing = tmp_path / "missing.toml"
    with pytest.raises(version_tool.VersionError, match="não encontrado"):
        version_tool.load_project_version(missing)

    invalid = tmp_path / "pyproject.toml"
    invalid.write_text("[project\n", encoding="utf-8")
    with pytest.raises(version_tool.VersionError, match="Não foi possível ler"):
        version_tool.load_project_version(invalid)

    invalid.write_text('[project]\nname = "synthetic"\n', encoding="utf-8")
    with pytest.raises(version_tool.VersionError, match="project.*version"):
        version_tool.load_project_version(invalid)


def test_replace_requires_one_exact_version_line() -> None:
    with pytest.raises(version_tool.VersionError, match="encontrei 0"):
        version_tool.replace_version_line('[project]\nversion="1.2.3"\n', "1.2.3", "1.2.4")

    duplicated = '[project]\nversion = "1.2.3"\nversion = "1.2.3"\n'
    with pytest.raises(version_tool.VersionError, match="encontrei 2"):
        version_tool.replace_version_line(duplicated, "1.2.3", "1.2.4")


@pytest.mark.parametrize(
    ("arguments", "expected"),
    [
        (["--major"], "1.0.0"),
        (["--minor"], "0.1.0"),
        (["--patch"], "0.0.2"),
        (["0.0.0"], "0.0.0"),
    ],
)
def test_complete_version_flow_in_temporary_repository(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    arguments: list[str],
    expected: str,
) -> None:
    initial = "0.0.1" if expected != "0.0.0" else "1.0.0"
    project_dir = create_git_project(tmp_path, initial)
    monkeypatch.chdir(project_dir)

    assert version_tool.main(arguments) == 0
    assert capsys.readouterr().out.strip() == f"v{expected}"
    assert version_tool.load_project_version(project_dir / "pyproject.toml") == expected
    assert version_tool.run_git(["status", "--porcelain"], cwd=project_dir).stdout == ""
    assert (
        version_tool.run_git(["log", "-1", "--pretty=%s"], cwd=project_dir).stdout.strip()
        == f"v{expected}"
    )
    assert version_tool.run_git(["tag", "--list"], cwd=project_dir).stdout.strip() == f"v{expected}"


def test_dirty_worktree_is_rejected_before_writing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    project_dir = create_git_project(tmp_path)
    (project_dir / "untracked.txt").write_text("dirty", encoding="utf-8")
    monkeypatch.chdir(project_dir)

    assert version_tool.main(["--patch"]) == 1
    assert "não está limpa" in capsys.readouterr().err
    assert version_tool.load_project_version(project_dir / "pyproject.toml") == "0.0.1"
    assert version_tool.run_git(["tag", "--list"], cwd=project_dir).stdout == ""


def test_existing_tag_and_same_version_are_rejected_without_writing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    project_dir = create_git_project(tmp_path)
    version_tool.run_git(["tag", "v0.0.2"], cwd=project_dir)
    monkeypatch.chdir(project_dir)

    assert version_tool.main(["--patch"]) == 1
    assert "já existe" in capsys.readouterr().err
    assert version_tool.load_project_version(project_dir / "pyproject.toml") == "0.0.1"

    assert version_tool.main(["0.0.1"]) == 1
    assert "já é 0.0.1" in capsys.readouterr().err


def test_commit_failure_restores_file_and_staging(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    pyproject = tmp_path / "pyproject.toml"
    original = '[project]\nname = "synthetic"\nversion = "0.0.1"\n'
    pyproject.write_text(original, encoding="utf-8")
    calls: list[list[str]] = []

    def fake_run_git(
        arguments: list[str] | tuple[str, ...], *, cwd: Path, check: bool = True
    ) -> subprocess.CompletedProcess[str]:
        del cwd, check
        command = list(arguments)
        calls.append(command)
        if command[0] == "status":
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
        if command[0] == "rev-parse":
            return subprocess.CompletedProcess(command, 1, stdout="", stderr="")
        if command[0] == "commit":
            raise subprocess.CalledProcessError(1, command, stderr="commit hook failed")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(version_tool, "run_git", fake_run_git)
    monkeypatch.chdir(tmp_path)

    assert version_tool.main(["--patch"]) == 1
    assert pyproject.read_text(encoding="utf-8") == original
    assert calls == [
        ["status", "--porcelain=v1", "--untracked-files=normal"],
        ["rev-parse", "--verify", "--quiet", "refs/tags/v0.0.2"],
        ["add", "pyproject.toml"],
        ["commit", "-m", "v0.0.2"],
        ["restore", "--staged", "--", "pyproject.toml"],
    ]
    assert "commit hook failed" in capsys.readouterr().err


def test_tag_failure_keeps_commit_result_and_reports_partial_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text('[project]\nversion = "0.0.1"\n', encoding="utf-8")
    calls: list[list[str]] = []

    def fake_run_git(
        arguments: list[str] | tuple[str, ...], *, cwd: Path, check: bool = True
    ) -> subprocess.CompletedProcess[str]:
        del cwd, check
        command = list(arguments)
        calls.append(command)
        if command[0] == "status":
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
        if command[0] == "rev-parse":
            return subprocess.CompletedProcess(command, 1, stdout="", stderr="")
        if command[0] == "tag":
            raise subprocess.CalledProcessError(1, command, stderr="tag failed")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(version_tool, "run_git", fake_run_git)
    monkeypatch.chdir(tmp_path)

    assert version_tool.main(["--patch"]) == 1
    assert version_tool.load_project_version(pyproject) == "0.0.2"
    assert ["restore", "--staged", "--", "pyproject.toml"] not in calls
    error = capsys.readouterr().err
    assert "commit da versão 0.0.2 foi criado" in error
    assert "sem a tag v0.0.2" in error
