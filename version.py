from __future__ import annotations

import argparse
import re
import subprocess
import sys
import tomllib
from collections.abc import Sequence
from pathlib import Path

VERSION_PATTERN = re.compile(
    r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)"
)
PYPROJECT_NAME = "pyproject.toml"


class VersionError(Exception):
    """Expected error that can be presented directly to the user."""


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Atualiza project.version, cria um commit e adiciona uma tag Git."
    )
    version_group = parser.add_mutually_exclusive_group(required=True)
    version_group.add_argument("--major", action="store_true", help="Incrementa a versão major")
    version_group.add_argument("--minor", action="store_true", help="Incrementa a versão minor")
    version_group.add_argument("--patch", action="store_true", help="Incrementa a versão patch")
    version_group.add_argument(
        "version",
        nargs="?",
        metavar="X.Y.Z",
        help="Define diretamente uma versão numérica",
    )
    return parser.parse_args(argv)


def parse_version(value: str) -> tuple[int, int, int]:
    match = VERSION_PATTERN.fullmatch(value)
    if match is None:
        raise VersionError(
            f"Versão inválida: {value!r}. Use o formato numérico X.Y.Z, como 1.2.3."
        )
    major, minor, patch = (int(part) for part in match.groups())
    return major, minor, patch


def bump_version(current: str, release: str) -> str:
    major, minor, patch = parse_version(current)
    if release == "major":
        return f"{major + 1}.0.0"
    if release == "minor":
        return f"{major}.{minor + 1}.0"
    if release == "patch":
        return f"{major}.{minor}.{patch + 1}"
    raise VersionError(f"Tipo de incremento desconhecido: {release!r}.")


def load_project_version(pyproject_path: Path) -> str:
    try:
        with pyproject_path.open("rb") as pyproject_file:
            document = tomllib.load(pyproject_file)
    except FileNotFoundError as exc:
        raise VersionError(f"Arquivo {PYPROJECT_NAME} não encontrado no diretório atual.") from exc
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise VersionError(f"Não foi possível ler {PYPROJECT_NAME}: {exc}") from exc

    project = document.get("project")
    current = project.get("version") if isinstance(project, dict) else None
    if not isinstance(current, str):
        raise VersionError("A chave [project].version não foi encontrada no pyproject.toml.")
    parse_version(current)
    return current


def read_project_text(pyproject_path: Path) -> str:
    try:
        with pyproject_path.open("r", encoding="utf-8", newline="") as pyproject_file:
            return pyproject_file.read()
    except OSError as exc:
        raise VersionError(f"Não foi possível ler {PYPROJECT_NAME} como texto: {exc}") from exc


def replace_version_line(text: str, current: str, new: str) -> str:
    old_line = f'version = "{current}"'
    new_line = f'version = "{new}"'
    lines = text.splitlines(keepends=True)
    matching_lines = [
        index for index, line in enumerate(lines) if line.rstrip("\r\n") == old_line
    ]
    if len(matching_lines) != 1:
        raise VersionError(
            f'Esperava exatamente uma linha `{old_line}` em {PYPROJECT_NAME}, '
            f"mas encontrei {len(matching_lines)}."
        )
    index = matching_lines[0]
    lines[index] = lines[index].replace(old_line, new_line, 1)
    return "".join(lines)


def write_project_text(pyproject_path: Path, text: str) -> None:
    try:
        with pyproject_path.open("w", encoding="utf-8", newline="") as pyproject_file:
            pyproject_file.write(text)
    except OSError as exc:
        raise VersionError(f"Não foi possível atualizar {PYPROJECT_NAME}: {exc}") from exc


def run_git(
    arguments: Sequence[str],
    *,
    cwd: Path,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(  # noqa: S603
            ["git", *arguments],  # noqa: S607
            cwd=cwd,
            check=check,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError as exc:
        raise VersionError("O executável do Git não foi encontrado.") from exc


def git_failure(action: str, error: subprocess.CalledProcessError) -> VersionError:
    detail = (error.stderr or error.stdout or "").strip()
    suffix = f" Detalhes do Git: {detail}" if detail else ""
    return VersionError(f"Falha ao {action}.{suffix}")


def ensure_clean_worktree(project_dir: Path) -> None:
    try:
        result = run_git(
            ["status", "--porcelain=v1", "--untracked-files=normal"], cwd=project_dir
        )
    except subprocess.CalledProcessError as exc:
        raise git_failure("verificar a árvore de trabalho", exc) from exc
    if result.stdout.strip():
        raise VersionError(
            "A árvore de trabalho não está limpa. Faça commit, stash ou remova "
            "as alterações antes de versionar."
        )


def ensure_tag_is_available(project_dir: Path, tag: str) -> None:
    result = run_git(
        ["rev-parse", "--verify", "--quiet", f"refs/tags/{tag}"],
        cwd=project_dir,
        check=False,
    )
    if result.returncode == 0:
        raise VersionError(f"A tag {tag} já existe.")
    if result.returncode != 1:
        raise VersionError(f"Não foi possível verificar se a tag {tag} já existe.")


def restore_after_git_failure(project_dir: Path, pyproject_path: Path, original: str) -> None:
    run_git(
        ["restore", "--staged", "--", PYPROJECT_NAME],
        cwd=project_dir,
        check=False,
    )
    write_project_text(pyproject_path, original)


def requested_version(current: str, args: argparse.Namespace) -> str:
    if args.version is not None:
        parse_version(args.version)
        return args.version
    release = next(name for name in ("major", "minor", "patch") if getattr(args, name))
    return bump_version(current, release)


def commit_version(project_dir: Path, pyproject_path: Path, original: str, new: str) -> None:
    try:
        run_git(["add", PYPROJECT_NAME], cwd=project_dir)
        run_git(
            ["commit", "-m", f"v{new}"],
            cwd=project_dir,
        )
    except subprocess.CalledProcessError as exc:
        restore_after_git_failure(project_dir, pyproject_path, original)
        raise git_failure("criar o commit de versão", exc) from exc

    try:
        run_git(["tag", f"v{new}"], cwd=project_dir)
    except subprocess.CalledProcessError as exc:
        error = git_failure("criar a tag de versão", exc)
        raise VersionError(
            f"{error} O commit da versão {new} foi criado, mas está sem a tag v{new}."
        ) from exc


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    project_dir = Path.cwd()
    pyproject_path = project_dir / PYPROJECT_NAME

    try:
        ensure_clean_worktree(project_dir)
        current = load_project_version(pyproject_path)
        new = requested_version(current, args)
        if new == current:
            raise VersionError(f"A versão solicitada já é {current}; nenhuma alteração foi feita.")

        tag = f"v{new}"
        ensure_tag_is_available(project_dir, tag)
        original = read_project_text(pyproject_path)
        updated = replace_version_line(original, current, new)
        write_project_text(pyproject_path, updated)
        commit_version(project_dir, pyproject_path, original, new)
    except VersionError as exc:
        print(f"Erro: {exc}", file=sys.stderr)
        return 1

    print(tag)
    return 0


if __name__ == "__main__":
    sys.exit(main())
