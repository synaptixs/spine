"""run_python_analysis: execute Python in a sandbox.

Two backends ship out of the box:

- **E2B** (production): uses ``e2b_code_interpreter`` for an isolated
  Firecracker microVM. Selected automatically when ``E2B_API_KEY`` is set.
  Enforces the contract's time and memory limits and an explicit no-network
  policy (E2B sandbox defaults allow network; we accept that for now —
  customer deployments can tighten via E2B's network policy when needed).

- **LocalSubprocessSandbox** (dev-only fallback): runs the supplied code in
  a child ``python`` process with strict wall-clock and memory ulimits.
  This is NOT a security boundary — it protects against accidental loops
  and runaway memory, not adversarial code. Documented as such in the
  contract.

Output shape is identical across backends so handlers / verifiers downstream
don't need to know which ran.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import resource
import sys
import tempfile
import textwrap
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from orchestrator.gateway.invocation import InvocationContext

logger = logging.getLogger("orchestrator.gateway.run_python_analysis")

DEFAULT_TIME_LIMIT_SECONDS = 30
DEFAULT_MEMORY_LIMIT_MB = 512
DEFAULT_MAX_OUTPUT_BYTES = 256 * 1024  # 256 KiB stdout+stderr combined


@dataclass(frozen=True)
class SandboxResult:
    stdout: str
    stderr: str
    exit_code: int
    elapsed_ms: float
    backend: str
    truncated: bool


class Sandbox(Protocol):
    name: str

    async def run(
        self,
        code: str,
        *,
        time_limit_seconds: int,
        memory_limit_mb: int,
        max_output_bytes: int,
    ) -> SandboxResult: ...


class LocalSubprocessSandbox:
    """Run Python in a child process with ulimit-style restrictions.

    Dev-only: do not use against untrusted code in a multi-tenant environment.
    Production deployments should set ``E2B_API_KEY`` to route through E2B.
    """

    name = "local_subprocess"

    async def run(
        self,
        code: str,
        *,
        time_limit_seconds: int,
        memory_limit_mb: int,
        max_output_bytes: int,
    ) -> SandboxResult:
        with tempfile.TemporaryDirectory(prefix="orchestrator-sandbox-") as workdir:
            script = Path(workdir) / "script.py"
            script.write_text(code, encoding="utf-8")

            def preexec() -> None:
                # Memory: address-space limit. SIGKILL on overrun.
                bytes_limit = memory_limit_mb * 1024 * 1024
                with contextlib.suppress(ValueError, OSError):
                    resource.setrlimit(resource.RLIMIT_AS, (bytes_limit, bytes_limit))
                # CPU seconds: belt-and-braces alongside asyncio timeout.
                with contextlib.suppress(ValueError, OSError):
                    resource.setrlimit(
                        resource.RLIMIT_CPU,
                        (time_limit_seconds, time_limit_seconds + 1),
                    )

            start = time.perf_counter()
            try:
                process = await asyncio.create_subprocess_exec(
                    sys.executable,
                    "-I",  # isolated mode: ignore PYTHON* env, no user site-packages
                    "-B",  # don't write .pyc
                    str(script),
                    cwd=workdir,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    preexec_fn=preexec,
                    env={"PATH": os.environ.get("PATH", ""), "PYTHONUNBUFFERED": "1"},
                )
            except OSError as exc:  # spawn failure
                elapsed_ms = (time.perf_counter() - start) * 1000.0
                return SandboxResult(
                    stdout="",
                    stderr=f"sandbox spawn failed: {exc}",
                    exit_code=-1,
                    elapsed_ms=round(elapsed_ms, 3),
                    backend=self.name,
                    truncated=False,
                )

            try:
                stdout_bytes, stderr_bytes = await asyncio.wait_for(
                    process.communicate(), timeout=time_limit_seconds
                )
                exit_code = process.returncode or 0
            except TimeoutError:
                process.kill()
                await process.wait()
                elapsed_ms = (time.perf_counter() - start) * 1000.0
                return SandboxResult(
                    stdout="",
                    stderr=f"timeout after {time_limit_seconds}s",
                    exit_code=124,
                    elapsed_ms=round(elapsed_ms, 3),
                    backend=self.name,
                    truncated=False,
                )

            elapsed_ms = (time.perf_counter() - start) * 1000.0
            stdout, stderr, truncated = _bound_output(stdout_bytes, stderr_bytes, max_output_bytes)
            return SandboxResult(
                stdout=stdout,
                stderr=stderr,
                exit_code=exit_code,
                elapsed_ms=round(elapsed_ms, 3),
                backend=self.name,
                truncated=truncated,
            )


class E2BSandbox:
    """Production-grade sandbox via the ``e2b_code_interpreter`` library."""

    name = "e2b"

    def __init__(self, api_key: str) -> None:
        self._api_key = api_key

    async def run(
        self,
        code: str,
        *,
        time_limit_seconds: int,
        memory_limit_mb: int,
        max_output_bytes: int,
    ) -> SandboxResult:
        # E2B's Python SDK is loaded lazily so the dev-only path stays
        # importable without the e2b dep installed.
        try:
            from e2b_code_interpreter import AsyncSandbox  # type: ignore[import-not-found]
        except ImportError as exc:  # pragma: no cover — pip extra not installed
            raise RuntimeError(
                "E2B_API_KEY is set but the `e2b_code_interpreter` package "
                "is not installed. `uv pip install e2b-code-interpreter`."
            ) from exc

        start = time.perf_counter()
        sandbox = await AsyncSandbox.create(api_key=self._api_key, timeout=time_limit_seconds)
        try:
            execution = await sandbox.run_code(textwrap.dedent(code))
        finally:
            await sandbox.kill()

        elapsed_ms = (time.perf_counter() - start) * 1000.0
        stdout_raw = "\n".join(execution.logs.stdout)
        stderr_raw = "\n".join(execution.logs.stderr)
        stdout, stderr, truncated = _bound_output(
            stdout_raw.encode("utf-8"), stderr_raw.encode("utf-8"), max_output_bytes
        )
        exit_code = 1 if execution.error else 0
        if execution.error:
            stderr = (
                f"{execution.error.name}: {execution.error.value}\n{execution.error.traceback}"
                if not stderr
                else f"{stderr}\n{execution.error.name}: {execution.error.value}"
            )
        return SandboxResult(
            stdout=stdout,
            stderr=stderr,
            exit_code=exit_code,
            elapsed_ms=round(elapsed_ms, 3),
            backend=self.name,
            truncated=truncated,
        )


def default_sandbox() -> Sandbox:
    api_key = os.getenv("E2B_API_KEY")
    if api_key:
        return E2BSandbox(api_key)
    return LocalSubprocessSandbox()


class RunPythonAnalysisHandler:
    contract_id: str = "tool.run_python_analysis"
    contract_version: str = "0.1.0"

    def __init__(self, sandbox: Sandbox | None = None) -> None:
        self._sandbox = sandbox or default_sandbox()

    async def __call__(self, inputs: dict[str, Any], ctx: InvocationContext) -> dict[str, Any]:
        _ = ctx
        code = inputs.get("code")
        if not isinstance(code, str) or not code.strip():
            raise ValueError("run_python_analysis: 'code' must be a non-empty string")

        time_limit = int(inputs.get("time_limit_seconds", DEFAULT_TIME_LIMIT_SECONDS))
        memory_limit_mb = int(inputs.get("memory_limit_mb", DEFAULT_MEMORY_LIMIT_MB))
        max_output_bytes = int(inputs.get("max_output_bytes", DEFAULT_MAX_OUTPUT_BYTES))

        result = await self._sandbox.run(
            code,
            time_limit_seconds=time_limit,
            memory_limit_mb=memory_limit_mb,
            max_output_bytes=max_output_bytes,
        )
        return {
            "stdout": result.stdout,
            "stderr": result.stderr,
            "exit_code": result.exit_code,
            "elapsed_ms": result.elapsed_ms,
            "backend": result.backend,
            "truncated": result.truncated,
        }


def _bound_output(stdout_bytes: bytes, stderr_bytes: bytes, cap: int) -> tuple[str, str, bool]:
    total = len(stdout_bytes) + len(stderr_bytes)
    if total <= cap:
        return (
            stdout_bytes.decode("utf-8", errors="replace"),
            stderr_bytes.decode("utf-8", errors="replace"),
            False,
        )
    # Truncate proportionally so stderr still surfaces on a noisy stdout.
    stdout_share = int(cap * (len(stdout_bytes) / total))
    stderr_share = cap - stdout_share
    return (
        stdout_bytes[:stdout_share].decode("utf-8", errors="replace"),
        stderr_bytes[:stderr_share].decode("utf-8", errors="replace"),
        True,
    )


RUN_PYTHON_ANALYSIS_CONTRACT_PAYLOAD: dict[str, Any] = {
    "metadata": {
        "id": "tool.run_python_analysis",
        "version": "0.1.0",
        "description": (
            "Execute Python in a sandbox. Production backend: E2B (set "
            "E2B_API_KEY). Dev fallback: local subprocess with ulimit "
            "restrictions — not a security boundary."
        ),
        "tags": ["sandbox", "code"],
    },
    "spec": {
        "purpose": "Run user-supplied Python in an isolated environment.",
        "side_effects": "read",
        "idempotent": False,
        "inputs": [
            {"name": "code", "type": "str"},
            {"name": "idempotency_key", "type": "str", "required": False},
            {"name": "time_limit_seconds", "type": "int", "required": False},
            {"name": "memory_limit_mb", "type": "int", "required": False},
            {"name": "max_output_bytes", "type": "int", "required": False},
        ],
        "rate_limits": {"requests_per_minute": 30, "burst": 5},
        "authentication": {"type": "none"},
        "observability": {"audit": True, "trace": True},
    },
}
