"""One-command local launch: ``orchestrator up``.

Brings up the *whole* local stack a non-technical user needs to reach the
delegation inbox — Docker infra (Postgres + Temporal) → DB migrations → the
web/API server **and** the Temporal worker — with sensible env defaults, then
prints the URL and login key and streams logs until Ctrl-C, tearing the child
processes down cleanly on exit.

Why the full stack and not just the API: the inbox's *delegate a feature* action
(``POST /v1/runs/start`` → ``run_control.start_run``) starts ``SDLCWorkflow`` on
the Temporal queue and 503s if the worker is down. So an API-only launch would
let you browse but not delegate — the worker + Temporal are required.

The heavy lifting is subprocess orchestration; the command builders and the
env/readiness helpers are factored as pure functions so they can be unit-tested
without spawning anything.
"""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

# Docker services from ``docker-compose.dev.yml`` that the delegate-a-feature
# path actually needs. MinIO / Jaeger / temporal-ui are intentionally omitted
# (the worker runs with an in-memory artifact store — see ``build_child_env``).
REQUIRED_SERVICES: tuple[str, ...] = ("postgres", "temporal", "temporal-postgres")

# Self-contained infra compose, written out when the repo's
# ``docker-compose.dev.yml`` isn't next to the caller (e.g. a pip-only install).
# Ports/creds mirror the dev file so the default DB URL + Temporal host line up.
EMBEDDED_COMPOSE = """\
services:
  postgres:
    image: postgres:16
    environment:
      POSTGRES_USER: orchestrator
      POSTGRES_PASSWORD: orchestrator
      POSTGRES_DB: orchestrator
    ports:
      - "5433:5432"
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U orchestrator -d orchestrator"]
      interval: 5s
      timeout: 5s
      retries: 10
  temporal-postgres:
    image: postgres:16
    environment:
      POSTGRES_USER: temporal
      POSTGRES_PASSWORD: temporal
      POSTGRES_DB: temporal
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U temporal -d temporal"]
      interval: 5s
      timeout: 5s
      retries: 10
  temporal:
    image: temporalio/auto-setup:1.25
    environment:
      DB: postgres12
      DB_PORT: 5432
      POSTGRES_USER: temporal
      POSTGRES_PWD: temporal
      POSTGRES_SEEDS: temporal-postgres
    depends_on:
      temporal-postgres:
        condition: service_healthy
    ports:
      - "7233:7233"
"""

Echo = Callable[[str], None]


@dataclass
class LaunchConfig:
    """Resolved options for ``orchestrator up``."""

    host: str = "127.0.0.1"
    port: int = 8000
    use_docker: bool = True
    start_worker: bool = True
    compose_file: Path | None = None
    api_key: str = "dev-key"
    session_secret: str = "dev-session-secret"
    # Ports we poll to know infra is reachable (host-published compose ports).
    postgres_port: int = 5433
    temporal_port: int = 7233
    env: dict[str, str] = field(default_factory=lambda: dict(os.environ))

    @property
    def app_url(self) -> str:
        # 127.0.0.1 is the bind address; users open it as localhost.
        shown = "localhost" if self.host in {"127.0.0.1", "0.0.0.0"} else self.host
        return f"http://{shown}:{self.port}/app"

    @property
    def healthz_url(self) -> str:
        return f"http://{self.host}:{self.port}/healthz"

    @property
    def readyz_url(self) -> str:
        return f"http://{self.host}:{self.port}/readyz"


# --------------------------------------------------------------------------- #
# Pure helpers (unit-testable without spawning anything)
# --------------------------------------------------------------------------- #
def find_project_file(name: str, start: Path | None = None) -> Path | None:
    """Search ``start`` and its parents for ``name`` (repo-checkout support)."""
    here = (start or Path.cwd()).resolve()
    for directory in (here, *here.parents):
        candidate = directory / name
        if candidate.is_file():
            return candidate
    return None


def resolve_compose_base() -> list[str] | None:
    """The docker compose invocation to use, or None if Docker is unavailable.

    Prefers Compose v2 (``docker compose``); falls back to the legacy
    ``docker-compose`` binary.
    """
    if shutil.which("docker"):
        try:
            subprocess.run(
                ["docker", "compose", "version"],
                check=True,
                capture_output=True,
            )
            return ["docker", "compose"]
        except (subprocess.CalledProcessError, OSError):
            pass
    if shutil.which("docker-compose"):
        return ["docker-compose"]
    return None


def build_child_env(config: LaunchConfig) -> dict[str, str]:
    """Env for the API + worker children: caller's env plus safe defaults.

    Only fills a variable when it isn't already set, so a real exported value
    (or one from ``.env``) always wins.
    """
    child = dict(config.env)
    child.setdefault("ORCHESTRATOR_API_KEY", config.api_key)
    child.setdefault("ORCHESTRATOR_SESSION_SECRET", config.session_secret)
    # No MinIO in the minimal stack — keep artifacts in-memory so the worker
    # doesn't block on an object store the user didn't start.
    child.setdefault("ORCHESTRATOR_ARTIFACT_STORE", "memory")
    # Real codegen by default (the SDLC worker falls back to a stub otherwise),
    # so a delegated feature actually produces code. Needs an LLM key — run_up
    # warns if none is set.
    child.setdefault("SDLC_CODEGEN", "llm")
    return child


def api_command(config: LaunchConfig) -> list[str]:
    """The uvicorn command that serves the API + web UI."""
    return [
        sys.executable,
        "-m",
        "uvicorn",
        "orchestrator.registry.api.app:create_app",
        "--factory",
        "--host",
        config.host,
        "--port",
        str(config.port),
    ]


def worker_command() -> list[str]:
    """The SDLC Temporal worker command.

    This is ``orchestrator.sdlc.worker`` — the one that registers ``SDLCWorkflow``
    on the ``sdlc-tasks`` queue, which is exactly what the inbox's *delegate a
    feature* action (``run_control.start_run``) kicks off. (The separate
    ``orchestrator.temporal.worker`` hosts a different workflow on another queue.)
    """
    return [sys.executable, "-m", "orchestrator.sdlc.worker"]


def alembic_command(ini_path: Path) -> list[str]:
    """``alembic upgrade head`` against the given ini (prefer the console script)."""
    alembic = shutil.which("alembic")
    base = [alembic] if alembic else [sys.executable, "-m", "alembic"]
    return [*base, "-c", str(ini_path), "upgrade", "head"]


# --------------------------------------------------------------------------- #
# Readiness polls
# --------------------------------------------------------------------------- #
def wait_for_tcp(host: str, port: int, timeout: float = 60.0, interval: float = 1.0) -> bool:
    """Block until a TCP connect to ``host:port`` succeeds or ``timeout`` elapses."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=2.0):
                return True
        except OSError:
            time.sleep(interval)
    return False


def wait_for_http(url: str, timeout: float = 60.0, interval: float = 0.5) -> bool:
    """Block until ``url`` returns a 2xx, or ``timeout`` elapses."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=3.0) as resp:  # noqa: S310 (localhost only)
                if 200 <= resp.status < 300:
                    return True
        except (urllib.error.URLError, OSError):
            pass
        time.sleep(interval)
    return False


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #
def _run(
    cmd: list[str],
    echo: Echo,
    *,
    env: dict[str, str] | None = None,
    capture_output: bool = False,
) -> subprocess.CompletedProcess[bytes]:
    echo(f"$ {' '.join(cmd)}")
    return subprocess.run(cmd, env=env, capture_output=capture_output)


def _resolve_compose_file(config: LaunchConfig, echo: Echo) -> Path:
    if config.compose_file is not None:
        return config.compose_file
    found = find_project_file("docker-compose.dev.yml")
    if found is not None:
        return found
    # Pip-only install: materialize the embedded minimal stack next to the CWD.
    target = Path.cwd() / ".orchestrator-compose.yml"
    if not target.exists():
        target.write_text(EMBEDDED_COMPOSE, encoding="utf-8")
        echo(f"Wrote a minimal infra compose file to {target}")
    return target


def _bring_up_docker(config: LaunchConfig, echo: Echo) -> None:
    compose_base = resolve_compose_base()
    if compose_base is None:
        raise LaunchError(
            "Docker isn't available. Install Docker Desktop and retry, or pass "
            "--no-docker if Postgres + Temporal are already running."
        )
    compose_file = _resolve_compose_file(config, echo)
    echo("Starting infra (Postgres + Temporal)…")
    result = _run(
        [*compose_base, "-f", str(compose_file), "up", "-d", *REQUIRED_SERVICES],
        echo=echo,
    )
    if result.returncode != 0:
        raise LaunchError("docker compose failed to start the infra services.")


def _run_migrations(config: LaunchConfig, echo: Echo) -> None:
    ini = find_project_file("alembic.ini")
    if ini is None:
        echo(
            "! No alembic.ini found — skipping migrations. If the API's /readyz "
            "stays unhealthy, run `alembic upgrade head` against the database."
        )
        return
    echo("Applying database migrations…")
    # Postgres may still be starting; retry a few times so migrations double as
    # the DB-ready gate.
    last: subprocess.CompletedProcess[bytes] | None = None
    for attempt in range(1, 11):
        last = _run(alembic_command(ini), echo=echo, env=build_child_env(config), capture_output=True)
        if last.returncode == 0:
            return
        echo(f"  …database not ready yet (attempt {attempt}/10)")
        time.sleep(3.0)
    detail = (last.stderr.decode(errors="replace") if last and last.stderr else "").strip()
    raise LaunchError(f"Migrations failed after retries. Last error:\n{detail}")


def _has_llm_key(env: dict[str, str]) -> bool:
    return bool(env.get("ANTHROPIC_API_KEY") or env.get("OPENAI_API_KEY"))


class LaunchError(RuntimeError):
    """A fatal, user-facing launch failure (message is printed as-is)."""


def run_up(config: LaunchConfig, echo: Echo = print) -> int:
    """Bring up the full local stack and block until interrupted.

    Returns a process exit code. Raises nothing for the normal Ctrl-C path — it
    tears the children down and returns 0.
    """
    child_env = build_child_env(config)
    children: list[tuple[str, subprocess.Popen[bytes]]] = []

    try:
        if config.use_docker:
            _bring_up_docker(config, echo)
            echo("Waiting for Postgres…")
            if not wait_for_tcp(config.host, config.postgres_port, timeout=90):
                raise LaunchError(f"Postgres didn't come up on port {config.postgres_port}.")
            echo("Waiting for Temporal…")
            if not wait_for_tcp(config.host, config.temporal_port, timeout=120):
                raise LaunchError(f"Temporal didn't come up on port {config.temporal_port}.")
        else:
            echo("--no-docker: assuming Postgres + Temporal are already reachable.")

        _run_migrations(config, echo)

        if config.start_worker and not _has_llm_key(child_env):
            echo(
                "! No LLM key (ANTHROPIC_API_KEY / OPENAI_API_KEY) detected — you can browse "
                "the UI, but delegating a feature will fail until one is set in .env."
            )

        echo("Starting the API + web UI…")
        api_proc = subprocess.Popen(api_command(config), env=child_env)
        children.append(("api", api_proc))

        if config.start_worker:
            echo("Starting the Temporal worker…")
            worker_proc = subprocess.Popen(worker_command(), env=child_env)
            children.append(("worker", worker_proc))

        echo("Waiting for the API to become healthy…")
        if not wait_for_http(config.healthz_url, timeout=60):
            raise LaunchError("The API didn't become healthy — check the log output above.")
        if not wait_for_http(config.readyz_url, timeout=30):
            echo("! API is up but /readyz is failing (database not migrated/reachable?).")

        _print_ready_banner(config, echo)
        _block_until_interrupted(children, echo)
        return 0
    finally:
        _teardown(children, echo)


def _print_ready_banner(config: LaunchConfig, echo: Echo) -> None:
    echo("")
    echo("──────────────────────────────────────────────")
    echo("  Spine is up.")
    echo(f"  Open:    {config.app_url}")
    echo(f"  Log in with the API key:  {config.api_key}")
    echo("  Press Ctrl-C to stop.")
    echo("──────────────────────────────────────────────")
    echo("")


def _block_until_interrupted(children: list[tuple[str, subprocess.Popen[bytes]]], echo: Echo) -> None:
    """Wait until Ctrl-C, or until a child dies unexpectedly."""
    try:
        while True:
            for name, proc in children:
                code = proc.poll()
                if code is not None:
                    echo(f"! The {name} process exited (code {code}). Shutting down.")
                    return
            time.sleep(1.0)
    except KeyboardInterrupt:
        echo("\nStopping…")


def _teardown(children: list[tuple[str, subprocess.Popen[bytes]]], echo: Echo) -> None:
    for name, proc in reversed(children):
        if proc.poll() is not None:
            continue
        echo(f"Stopping {name}…")
        proc.terminate()
    for _name, proc in reversed(children):
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
    # Infra containers are left running on purpose (fast restarts); tell the user
    # how to stop them.
    if children:
        echo("Stopped app processes. Infra containers are still running — stop them with:")
        echo("  docker compose -f docker-compose.dev.yml down")
