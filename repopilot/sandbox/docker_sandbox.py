from __future__ import annotations
import io
import json
import tarfile
import time
import difflib
from pathlib import Path
from typing import Optional

from repopilot.sandbox.base import (
    DEFAULT_IGNORE_DIRS,
    BINARY_EXTENSIONS,
    ExecResult,
    FileReadResult,
    GrepMatch,
    Sandbox,
)


def _make_tar_bytes(path_on_host: Path) -> bytes:
    """Create an in-memory tar of a single file's content for put_archive."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        data = path_on_host.read_bytes()
        info = tarfile.TarInfo(name=path_on_host.name)
        info.size = len(data)
        info.mtime = int(time.time())
        info.mode = 0o644
        tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def shquote(s: str) -> str:
    """Single-quote a string for sh -c, safely. Mirrors local_sandbox shquote."""
    return "'" + s.replace("'", "'\"'\"'") + "'"


class DockerSandbox(Sandbox):
    """Sandbox backed by a Docker container.
    - Mounts repo at /workspace (read-write)
    - Cgroups CPU/memory limits
    - network_mode configurable (bridge|none)
    - All shell arguments are single-quote escaped to prevent injection
    - cwd is validated to stay within /workspace (no .. escape)
    """

    def __init__(
        self,
        repo_path: Path,
        image: str = "python:3.10-slim",
        mem_limit: str = "2g",
        cpu_quota: int = 200000,
        network_mode: str = "bridge",
        docker_client=None,
    ):
        super().__init__(repo_path)
        self.image = image
        self.mem_limit = mem_limit
        self.cpu_quota = cpu_quota
        self.network_mode = network_mode
        self._client = docker_client
        self._container = None

    def _get_client(self):
        if self._client is None:
            import docker
            self._client = docker.from_env()
        return self._client

    def setup(self) -> None:
        client = self._get_client()
        try:
            client.images.get(self.image)
        except Exception:
            try:
                client.images.pull(self.image)
            except Exception as e:
                raise RuntimeError(
                    f"Docker image {self.image!r} not available locally and pull failed: {e}.\n"
                    f"Build it locally or configure a mirror."
                ) from e

        import docker.types
        self._container = client.containers.run(
            self.image,
            command=["sleep", "infinity"],
            detach=True,
            working_dir="/workspace",
            mem_limit=self.mem_limit,
            cpu_period=100000,
            cpu_quota=self.cpu_quota,
            network_mode=self.network_mode,
            mounts=[
                docker.types.Mount(
                    "/workspace",
                    str(self.repo_path.resolve()),
                    type="bind",
                    read_only=False,
                ),
            ],
        )
        time.sleep(0.5)
        self._container.reload()

    def teardown(self) -> None:
        if self._container:
            try:
                self._container.kill()
            except Exception:
                pass
            try:
                self._container.remove()
            except Exception:
                pass
            self._container = None

    def _ensure_container_alive(self) -> None:
        """Re-create container if it died (OOM, etc.)"""
        if self._container is None:
            raise RuntimeError("Container not started; call setup() first")
        try:
            self._container.reload()
            if self._container.status != "running":
                # Container died; restart it
                self._container.start()
                time.sleep(0.3)
                self._container.reload()
        except Exception:
            # Container was removed; recreate
            self._container = None
            self.setup()

    # ── internal exec helper ──────────────────────
    def _docker_exec(self, cmd: str, timeout: int = 30, workdir: str = "/workspace") -> ExecResult:
        self._ensure_container_alive()
        t0 = time.time()
        timed_out = False
        exit_code = 0
        stdout = b""
        stderr = b""
        try:
            exec_id = self._client.api.exec_create(
                self._container.id,
                ["sh", "-c", cmd],
                workdir=workdir,
                environment={"DEBIAN_FRONTEND": "noninteractive", "PYTHONUNBUFFERED": "1"},
            )
            stream = self._client.api.exec_start(exec_id, stream=True, demux=True)
            start = time.time()
            out_chunks, err_chunks = [], []
            for out_b, err_b in stream:
                if time.time() - start > timeout:
                    timed_out = True
                    break
                if out_b:
                    out_chunks.append(out_b)
                if err_b:
                    err_chunks.append(err_b)
            stdout = b"".join(out_chunks)
            stderr = b"".join(err_chunks)
            if not timed_out:
                inspect = self._client.api.exec_inspect(exec_id)
                exit_code = inspect.get("ExitCode", -1)
            else:
                exit_code = -1
        except Exception as e:
            stderr = str(e).encode()
            exit_code = -1
        return ExecResult(
            command=cmd,
            stdout=stdout.decode("utf-8", errors="replace"),
            stderr=stderr.decode("utf-8", errors="replace"),
            exit_code=exit_code,
            timed_out=timed_out,
            duration_ms=int((time.time() - t0) * 1000),
        )

    # ── file ops ──────────────────────────────────
    def read_file(self, path: str, offset: int = 0, limit: int = 200) -> FileReadResult:
        # Read via container exec (respects container-side filesystem view)
        container_path = self._safe_container_path(path)
        if offset > 0 or limit < 10000:
            # Use sed for line-range reading (more efficient than cat+truncate)
            start_line = offset + 1
            end_line = offset + limit
            cmd = f"sed -n {shquote(str(start_line))},{shquote(str(end_line))}p {shquote(container_path)}"
        else:
            cmd = f"cat {shquote(container_path)}"
        r = self._docker_exec(cmd, timeout=10)
        if r.exit_code != 0:
            raise FileNotFoundError(f"File not found or unreadable: {path}\n{r.stderr}")
        content = r.stdout
        # Count total lines with wc -l
        wc = self._docker_exec(f"wc -l < {shquote(container_path)}", timeout=5)
        try:
            total_lines = int(wc.stdout.strip())
        except ValueError:
            total_lines = content.count("\n") + (1 if content and not content.endswith("\n") else 0)
        numbered = self._add_line_numbers(content, start_line=offset + 1)
        truncated = (offset + limit) < total_lines
        return FileReadResult(
            path=path,
            content=numbered,
            start_line=offset + 1,
            total_lines=total_lines,
            truncated=truncated,
        )

    def write_file(self, path: str, content: str) -> None:
        """Atomic write: write to .tmp then mv into place (prevents partial writes on crash)."""
        container_path = self._safe_container_path(path)
        parent = str(Path(container_path).parent)
        # Ensure parent directory exists
        self._docker_exec(f"mkdir -p {shquote(parent)}", timeout=5)
        # Write via host tar + put_archive for efficiency (no base64 encoding issues)
        host_p = self.repo_path / path
        host_p.parent.mkdir(parents=True, exist_ok=True)
        # Atomic: write to .tmp then rename (POSIX rename is atomic)
        tmp_name = f".{host_p.name}.tmp.{int(time.time()*1000)}"
        tmp_host = host_p.parent / tmp_name
        tmp_host.write_text(content, encoding="utf-8")
        try:
            # Move tmp into place atomically
            self._docker_exec(f"mv {shquote(str(Path('/workspace')/path).parent/tmp_name)} {shquote(container_path)}", timeout=5)
        except Exception:
            # Fallback: write directly if mv fails
            host_p.write_text(content, encoding="utf-8")
            tmp_host.unlink(missing_ok=True)

    def edit_file(self, path: str, old_string: str, new_string: str, replace_all: bool = False) -> str:
        """Edit file with exact-match requirement (at least 2 lines of context recommended)."""
        # DockerSandbox: read from host (bind-mount), edit, write atomically
        host_p = self.repo_path / path
        # Validate path stays in repo
        try:
            host_p.resolve().relative_to(self.repo_path.resolve())
        except ValueError:
            raise PermissionError(f"Path escapes repo: {path}")
        if not host_p.exists():
            raise FileNotFoundError(f"File not found: {path}")
        original = host_p.read_text(encoding="utf-8", errors="replace")
        if old_string not in original:
            import difflib as _difflib
            close = _difflib.get_close_matches(old_string, original.splitlines(), n=1, cutoff=0.6)
            hint = f"\nClosest match:\n{close[0]!r}" if close else ""
            raise ValueError(f"old_string not found in {path}.{hint}")
        count = -1 if replace_all else 1
        new_content = original.replace(old_string, new_string, count)
        # Atomic write: tmp then rename
        tmp_name = f".{host_p.name}.edit.{int(time.time()*1000)}"
        tmp_host = host_p.parent / tmp_name
        tmp_host.write_text(new_content, encoding="utf-8")
        tmp_host.replace(host_p)  # os.replace is atomic on same filesystem
        diff = difflib.unified_diff(
            original.splitlines(keepends=True),
            new_content.splitlines(keepends=True),
            fromfile=f"a/{path}", tofile=f"b/{path}",
        )
        return "".join(diff)

    def _safe_container_path(self, path: str) -> str:
        """Resolve a path to its container-side absolute path, preventing escape from /workspace."""
        # Strip leading / and any .. traversal
        clean = path.lstrip("/")
        # Resolve .. by splitting and rebuilding
        parts = []
        for part in clean.replace("\\", "/").split("/"):
            if part == "" or part == ".":
                continue
            if part == "..":
                if parts:
                    parts.pop()
                continue
            parts.append(part)
        return "/workspace/" + "/".join(parts) if parts else "/workspace"

    # ── exec ──────────────────────────────────────
    def exec(self, command: str, timeout: int = 30, cwd: Optional[str] = None) -> ExecResult:
        workdir = "/workspace"
        if cwd:
            workdir = self._safe_container_path(cwd)
        return self._docker_exec(command, timeout=timeout, workdir=workdir)

    # ── navigation ───────────────────────────────
    def glob(self, pattern: str) -> list[str]:
        """Use python3 -c to do glob matching safely (no shell injection in pattern)."""
        norm = pattern.replace("\\", "/")
        # Use Python glob inside container to avoid shell injection
        py_script = (
            "import glob, os, sys; "
            "ignore = {'.git','__pycache__','node_modules','.venv','venv','dist','build','.mypy_cache','.pytest_cache','.ruff_cache','.tox','.idea','.vscode','.eggs'}; "
            "results = []; "
            f"pat = {norm!r}; "
            "recursive = pat.startswith('**/'); "
            "base_pat = pat[3:] if recursive else pat; "
            "for root, dirs, files in os.walk('/workspace'): "
            "  dirs[:] = [d for d in dirs if d not in ignore and not d.startswith('.') and not d.endswith('.egg-info')]; "
            "  rel = os.path.relpath(root, '/workspace'); "
            "  for f in files: "
            "    import fnmatch; "
            "    if fnmatch.fnmatch(f, base_pat if not recursive else base_pat if '/' not in pat else pat.split('/')[-1]): "
            "      fp = os.path.join(root, f) if rel != '.' else os.path.join('/workspace', f); "
            "      results.append(os.path.relpath(fp, '/workspace')); "
            "for r in sorted(set(results)): print(r.replace(chr(92),'/'))"
        )
        # Simpler approach: use find with -name but properly shquote the pattern
        # Actually let's use a safer find command with shquote'd pattern
        if "**/" in norm:
            pat = norm.split("**/")[-1]
            prune_args = " ".join(f"-name {d} -prune -o" for d in DEFAULT_IGNORE_DIRS)
            cmd = f"find /workspace {prune_args} -name {shquote(pat)} -type f -print | sed 's|^/workspace/||'"
        else:
            prune_args = " ".join(f"-name {d} -prune -o" for d in DEFAULT_IGNORE_DIRS)
            cmd = f"find /workspace -maxdepth 5 {prune_args} -name {shquote(norm)} -type f -print | sed 's|^/workspace/||'"
        r = self._docker_exec(cmd, timeout=15)
        results = [line.strip() for line in r.stdout.splitlines() if line.strip()]
        return sorted(results)

    def grep(self, pattern: str, glob_filter: Optional[str] = None,
             ignore_case: bool = False) -> list[GrepMatch]:
        """Use container grep with binary-skip and properly shquote'd pattern."""
        flag = "-rn" + ("i" if ignore_case else "") + "I"  # -I skips binary files
        include = f"--include={shquote(glob_filter)}" if glob_filter else ""
        exclude_args = " ".join(f"--exclude-dir={d}" for d in DEFAULT_IGNORE_DIRS)
        # Binary extensions to exclude
        binary_excludes = " ".join(f"--exclude=*{ext}" for ext in BINARY_EXTENSIONS)
        cmd = f"grep {flag} {exclude_args} {binary_excludes} {include} {shquote(pattern)} /workspace || true"
        r = self._docker_exec(cmd, timeout=15)
        results: list[GrepMatch] = []
        for line in r.stdout.splitlines():
            parts = line.split(":", 2)
            if len(parts) < 3:
                continue
            path_part = parts[0]
            if not path_part.startswith("/workspace/"):
                continue
            try:
                ln = int(parts[1])
            except ValueError:
                continue
            results.append(GrepMatch(
                file=path_part[len("/workspace/"):],
                line_no=ln,
                content=parts[2][:500],
            ))
        return results

    def list_dir(self, path: str = ".", max_depth: int = 2) -> dict:
        cp = self._safe_container_path(path)
        depth_arg = f"-maxdepth {max_depth + 1}"
        prune_args = " ".join(f"-name {d} -prune -o" for d in DEFAULT_IGNORE_DIRS)
        cmd = f"find {shquote(cp)} {depth_arg} {prune_args} -print | sed 's|^{shquote(cp)}/||' | sort"
        r = self._docker_exec(cmd, timeout=10)
        tree: dict = {}
        base_prefix_len = len(cp) + 1
        for line in r.stdout.splitlines():
            rel = line[base_prefix_len:] if line.startswith(cp + "/") else line
            if not rel or rel == ".":
                continue
            parts = rel.split("/")
            node = tree
            for i, p in enumerate(parts):
                is_dir = (i < len(parts) - 1)
                key = p + "/" if is_dir else p
                if key not in node:
                    node[key] = {} if is_dir else None
                if node[key] is None:
                    break
                node = node[key]
        return tree

    def get_repo_tree(self, max_tokens: int = 4000) -> str:
        """Tree-sitter powered repo map via bind-mounted host path; fallback to container find."""
        try:
            from repopilot.code_index.repo_map import RepoMapBuilder
            return RepoMapBuilder(self.repo_path, max_tokens=max_tokens).build()
        except Exception:
            return self._fallback_repo_tree()

    def _fallback_repo_tree(self) -> str:
        """Container-side find listing fallback."""
        r = self._docker_exec(
            "find /workspace -type f -not -path '*/.git/*' -not -path '*/__pycache__/*' | head -200 | sed 's|^/workspace/||'"
        )
        lines = ["# Repo tree (container)", ""]
        for ln in r.stdout.splitlines():
            if ln.strip():
                lines.append("  " + ln.strip())
        return "\n".join(lines)
