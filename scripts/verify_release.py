from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import tarfile
import venv
import zipfile
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath


class ReleaseGateError(RuntimeError):
    """Raised when a release artifact violates the publishing contract."""


@dataclass(frozen=True)
class ReleaseArtifacts:
    wheel: Path
    sdist: Path


FORBIDDEN_PARTS = {"tests", "test", "docs", "eval", "__pycache__"}
FORBIDDEN_NAMES = {".env", "api-key.txt", "api_key.txt", "apikey.txt"}
NATIVE_BUILD_MARKERS = (
    "building wheel for tokenizers",
    "running cargo",
    "cargo rustc",
    "rustc ",
)


def select_release_artifacts(dist_dir: Path) -> ReleaseArtifacts:
    wheels = sorted(dist_dir.glob("*.whl"))
    sdists = sorted(dist_dir.glob("*.tar.gz"))
    if len(wheels) != 1:
        raise ReleaseGateError(f"expected exactly one wheel, found {len(wheels)}")
    if len(sdists) != 1:
        raise ReleaseGateError(f"expected exactly one sdist, found {len(sdists)}")
    return ReleaseArtifacts(wheel=wheels[0], sdist=sdists[0])


def assert_clean_archive(members: Sequence[str]) -> None:
    violations: list[str] = []
    for member in members:
        path = PurePosixPath(member.replace("\\", "/"))
        lowered_parts = {part.lower() for part in path.parts}
        name = path.name.lower()
        if (
            lowered_parts & FORBIDDEN_PARTS
            or name in FORBIDDEN_NAMES
            or name.endswith((".pyc", ".pyo"))
            or "secret" in name
            or "token" in name and name.endswith((".txt", ".json", ".yaml", ".yml"))
        ):
            violations.append(member)
    if violations:
        preview = ", ".join(violations[:10])
        raise ReleaseGateError(f"forbidden archive members: {preview}")


def validate_wheel_metadata(metadata: str) -> None:
    python_match = re.search(r"^Requires-Python:\s*(.+)$", metadata, re.MULTILINE)
    requirements = re.findall(r"^Requires-Dist:\s*(.+)$", metadata, re.MULTILINE)
    normalized = {requirement.strip().replace(" ", "").lower() for requirement in requirements}
    if python_match is None or python_match.group(1).strip() != ">=3.10":
        raise ReleaseGateError("wheel metadata has unexpected Requires-Python")
    if "litellm<1.92,>=1.91.4" not in normalized:
        raise ReleaseGateError("wheel metadata has unverified LiteLLM range")


def assert_no_native_tokenizers_build(install_log: str) -> None:
    lowered = install_log.lower()
    if any(marker in lowered for marker in NATIVE_BUILD_MARKERS):
        raise ReleaseGateError("tokenizers native build detected in installation log")


def _archive_members(path: Path) -> list[str]:
    if path.suffix == ".whl":
        with zipfile.ZipFile(path) as archive:
            return archive.namelist()
    with tarfile.open(path, "r:gz") as archive:
        return archive.getnames()


def _wheel_metadata(path: Path) -> str:
    with zipfile.ZipFile(path) as archive:
        metadata_files = [
            name for name in archive.namelist() if name.endswith(".dist-info/METADATA")
        ]
        if len(metadata_files) != 1:
            raise ReleaseGateError(f"expected one wheel METADATA file, found {len(metadata_files)}")
        return archive.read(metadata_files[0]).decode("utf-8")


def _run(command: Sequence[str], *, cwd: Path, log_path: Path | None = None) -> str:
    display = " ".join(command)
    print(f"[release-gate] {display}")
    result = subprocess.run(
        command,
        cwd=cwd,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        env={**os.environ, "PYTHONUTF8": "1", "PIP_DISABLE_PIP_VERSION_CHECK": "1"},
    )
    if log_path is not None:
        log_path.write_text(result.stdout, encoding="utf-8")
    print(result.stdout, end="")
    if result.returncode:
        raise ReleaseGateError(f"command failed with exit code {result.returncode}: {display}")
    return result.stdout


def _venv_python(environment: Path) -> Path:
    if os.name == "nt":
        return environment / "Scripts" / "python.exe"
    return environment / "bin" / "python"


def installed_cli(environment: Path) -> Path:
    if os.name == "nt":
        return environment / "Scripts" / "repopilot.exe"
    return environment / "bin" / "repopilot"


def ensure_safe_work_dir(project_root: Path, work_dir: Path) -> Path:
    test_root = (project_root / "test").resolve()
    resolved = work_dir.resolve()
    if resolved == test_root or test_root not in resolved.parents:
        raise ReleaseGateError("work directory must be inside the project test directory")
    return resolved


def verify_release(project_root: Path, work_dir: Path, index_url: str) -> None:
    work_dir = ensure_safe_work_dir(project_root, work_dir)
    if work_dir.exists():
        shutil.rmtree(work_dir)
    dist_dir = work_dir / "dist"
    environment = work_dir / "venv"
    dist_dir.mkdir(parents=True)

    _run(
        [sys.executable, "-m", "build", "--outdir", str(dist_dir)],
        cwd=project_root,
    )
    artifacts = select_release_artifacts(dist_dir)
    for artifact in (artifacts.wheel, artifacts.sdist):
        assert_clean_archive(_archive_members(artifact))
    validate_wheel_metadata(_wheel_metadata(artifacts.wheel))
    _run(
        [sys.executable, "-m", "twine", "check", str(artifacts.wheel), str(artifacts.sdist)],
        cwd=project_root,
    )

    venv.EnvBuilder(with_pip=True, clear=True).create(environment)
    python = _venv_python(environment)
    _run(
        [str(python), "-m", "pip", "install", "--upgrade", "pip"],
        cwd=project_root,
    )
    install_log = _run(
        [
            str(python),
            "-m",
            "pip",
            "install",
            "--index-url",
            index_url,
            "--report",
            str(work_dir / "pip-report.json"),
            str(artifacts.wheel),
        ],
        cwd=project_root,
        log_path=work_dir / "pip-install.log",
    )
    assert_no_native_tokenizers_build(install_log)
    cli = installed_cli(environment)
    _run([str(cli), "--version"], cwd=work_dir)
    _run([str(cli), "--help"], cwd=work_dir)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build and verify RepoPilot release artifacts.")
    parser.add_argument(
        "--index-url",
        default="https://pypi.org/simple",
        help="Package index used to install wheel dependencies.",
    )
    parser.add_argument(
        "--work-dir",
        type=Path,
        default=Path("test/release_gate"),
        help="Temporary directory under the project root.",
    )
    parser.add_argument("--keep-work-dir", action="store_true")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    project_root = Path(__file__).resolve().parents[1]
    work_dir = args.work_dir
    if not work_dir.is_absolute():
        work_dir = project_root / work_dir
    work_dir = ensure_safe_work_dir(project_root, work_dir)
    try:
        verify_release(project_root, work_dir.resolve(), args.index_url)
    except (OSError, ReleaseGateError) as exc:
        print(f"[release-gate] FAILED: {exc}", file=sys.stderr)
        return 1
    else:
        print("[release-gate] PASSED")
        return 0
    finally:
        if not args.keep_work_dir and work_dir.exists():
            shutil.rmtree(work_dir)


if __name__ == "__main__":
    raise SystemExit(main())
