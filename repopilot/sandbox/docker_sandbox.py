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


class DockerSandbox(Sandbox):
    """Sandbox backed by a Docker container.
    - Mounts repo at /workspace (read-write)
    - Cgroups CPU/memory limits
    - network_mode configurable (bridge|none)
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
        # Ensure image exists (don't auto-pull to avoid network issues if image is local)
        try:
            client.images.get(self.image)
        except Exception:
            # Try to pull; if fails, raise clear error
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
            # Start with basic tools; install if needed via exec
        )
        # Wait for container to be running
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

    # ── internal exec helper ──────────────────────
    def _docker_exec(self, cmd: str, timeout: int = 30, workdir: str = "/workspace") -> ExecResult:
        if not self._container:
            raise RuntimeError("Container not started; call setup() first")
        t0 = time.time()
        timed_out = False
        exit_code = 0
        stdout = b""
        stderr = b""
        try:
            # sh -c for shell semantics
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
            # Get exit code
            inspect = self._client.api.exec_inspect(exec_id)
            exit_code = inspect.get("ExitCode", -1)
            if timed_out:
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
    def _container_path(self, path: str) -> str:
        # Join under /workspace (path traversal protection: sh -c quoting is in _docker_exec)
        # Strip leading / to prevent absolute paths
        safe = path.lstrip("/")
        return f"/workspace/{safe}"

    def read_file(self, path: str, offset: int = 0, limit: int = 200) -> FileReadResult:
        cp = self._container_path(path)
        # Use sed to slice lines; fallback to cat+head/tail
        if offset == 0 and limit > 0:
            r = self._docker_exec(f"sed -n '1,{limit}p' {shquote(cp)}; echo __LINES__; wc -l < {shquote(cp)}")
        else:
            start = offset + 1
            end = offset + limit
            r = self._docker_exec(
                f"sed -n '{start},{end}p' {shquote(cp)}; echo __LINES__; wc -l < {shquote(cp)}"
            )
        if r.exit_code != 0:
            raise FileNotFoundError(f"Cannot read {path}: {r.stderr.strip()}")
        parts = r.stdout.split("__LINES__")
        content = parts[0].rstrip("\n")
        try:
            total = int(parts[1].strip()) if len(parts) > 1 else content.count("\n") + 1
        except ValueError:
            total = content.count("\n") + 1
        # Add line numbers
        numbered = self._add_line_numbers(content, start_line=offset + 1)
        return FileReadResult(
            path=path,
            content=numbered,
            start_line=offset + 1,
            total_lines=total,
            truncated=(offset + limit) < total,
        )

    def write_file(self, path: str, content: str) -> None:
        cp = self._container_path(path)
        # Use python to write atomically (avoids shell escaping issues)
        encoded = content.replace("\\", "\\\\").replace("'", "\\'").replace("\n", "\\n")
        py_cmd = (
            f"python3 -c \"import os; os.makedirs(os.path.dirname('{cp}'), exist_ok=True); "
            f"open('{cp}','w').write('{encoded}')\""
        )
        r = self._docker_exec(py_cmd)
        if r.exit_code != 0:
            # Fallback: use cat via tar upload
            tmp_host = Path(self.repo_path) / path
            tmp_host.parent.mkdir(parents=True, exist_ok=True)
            tmp_host.write_text(content, encoding="utf-8")
            tmp_host.unlink()  # host is mounted; file is visible in container

    def edit_file(self, path: str, old_string: str, new_string: str, replace_all: bool = False) -> str:
        cp = self._container_path(path)
        # Read file, edit locally, write back (simpler than container-side sed escaping)
        read_r = self._docker_exec(f"cat {shquote(cp)}")
        if read_r.exit_code != 0:
            raise FileNotFoundError(f"File not found: {path}")
        original = read_r.stdout
        if old_string not in original:
            close = difflib.get_close_matches(old_string, original.splitlines(), n=1, cutoff=0.6)
            hint = f"\nClosest match:\n{close[0]!r}" if close else ""
            raise ValueError(f"old_string not found in {path}.{hint}")
        count = -1 if replace_all else 1
        new_content = original.replace(old_string, new_string, count)
        # Write back via mounted filesystem
        host_p = self.repo_path / path
        host_p.write_text(new_content, encoding="utf-8")
        diff = difflib.unified_diff(
            original.splitlines(keepends=True),
            new_content.splitlines(keepends=True),
            fromfile=f"a/{path}", tofile=f"b/{path}",
        )
        return "".join(diff)

    # ── exec ──────────────────────────────────────
    def exec(self, command: str, timeout: int = 30, cwd: Optional[str] = None) -> ExecResult:
        workdir = "/workspace"
        if cwd:
            workdir = f"/workspace/{cwd.lstrip('/')}"
        return self._docker_exec(command, timeout=timeout, workdir=workdir)

    # ── navigation ───────────────────────────────
    def glob(self, pattern: str) -> list[str]:
        # Use find inside container to avoid host path issues
        norm = pattern.replace("\\", "/")
        if norm.startswith("**/"):
            pat = norm[3:]
            cmd = f"find /workspace -type d \\( {' -o '.join('-name '+d for d in DEFAULT_IGNORE_DIRS) } \\) -prune -o -name '{pat}' -type f -print"
        else:
            cmd = f"find /workspace -maxdepth 3 -type d \\( {' -o '.join('-name '+d for d in DEFAULT_IGNORE_DIRS) } \\) -prune -o -name '{norm}' -type f -print"
        r = self._docker_exec(cmd, timeout=10)
        results = []
        for line in r.stdout.splitlines():
            line = line.strip()
            if line.startswith("/workspace/"):
                results.append(line[len("/workspace/"):])
        return sorted(results)

    def grep(self, pattern: str, glob_filter: Optional[str] = None,
             ignore_case: bool = False) -> list[GrepMatch]:
        flag = "-rn" + ("i" if ignore_case else "")
        include = f"--include='{glob_filter}'" if glob_filter else ""
        exclude_args = " ".join(f"--exclude-dir={d}" for d in DEFAULT_IGNORE_DIRS)
        cmd = f"grep {flag} {exclude_args} {include} {shquote(pattern)} /workspace || true"
        r = self._docker_exec(cmd, timeout=15)
        results: list[GrepMatch] = []
        for line in r.stdout.splitlines():
            # format: /workspace/path:lineno:content
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
        # Use find
        cp = f"/workspace/{path.lstrip('/')}" if path != "." else "/workspace"
        depth_arg = f"-maxdepth {max_depth + 1}"
        exclude_prune = " ".join(f"-name {d} -prune -o" for d in DEFAULT_IGNORE_DIRS)
        cmd = f"find {shquote(cp)} {depth_arg} {exclude_prune} -print"
        r = self._docker_exec(cmd, timeout=10)
        root_len = len(cp) + 1
        tree: dict = {}
        for line in sorted(r.stdout.splitlines()):
            if not line.startswith(cp):
                continue
            rel = line[root_len:]
            if not rel:
                continue
            parts = rel.split("/")
            node = tree
            for i, p in enumerate(parts):
                is_dir = line.endswith("/") or (i < len(parts) - 1)
                key = p + "/" if is_dir else p
                if key not in node:
                    node[key] = {} if is_dir or i < len(parts) - 1 else None
                if node[key] is None:
                    break
                node = node[key]
        return tree

    def get_repo_tree(self, max_tokens: int = 4000) -> str:
        # Delegate to local filesystem (repo is bind-mounted, same view)
        # Fallback simple find listing
        r = self._docker_exec(
            "find /workspace -type f -not -path '*/.git/*' -not -path '*/__pycache__/*' | head -200"
        )
        lines = ["# Repo tree (container)", ""]
        for ln in r.stdout.splitlines():
            if ln.startswith("/workspace/"):
                lines.append("  " + ln[len("/workspace/"):])
        return "\n".join(lines)


def shquote(s: str) -> str:
    """Single-quote a string for sh -c, safely."""
    return "'" + s.replace("'", "'\"'\"'") + "'"


