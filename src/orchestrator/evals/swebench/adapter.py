"""Run Spine's pipeline on one benchmark instance and file the outcome.

One instance is one subprocess (``sdlc autorun``) with its own process group, wall-clock limit and
spend cap, so a hang or a runaway cannot take the sweep with it. Around it:

1. pull the instance image by digest and check it is built on the instance's base commit;
2. build the generation repository from that commit, exported by a checkout (not ``git archive``) into
   one fresh commit and compared tree-for-tree with the image, so there is no history, remote or future
   object to find and nothing was lost on the way (plan D29);
3. write the issue text — ``problem_statement`` and nothing else — as the pipeline's source (D30);
4. launch the pipeline with only an allow-listed environment and ``SDLC_TEST_COMMAND`` pointing at the
   overlay wrapper, so the project's tests run in the instance image (D28);
5. read the run record, cut the patch (minus bookkeeping and the files the grader overwrites) and file
   the outcome in exactly one bucket (D31, D32);
6. remove the image (D33).

Nothing here raises for an instance-level failure: a pull that fails or an image that is not the base
commit is a ``generator-error`` row, and the sweep goes on.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import io
import json
import os
import shutil
import signal
import subprocess
import sys
import tarfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from orchestrator.evals.swebench.exec import kind_for
from orchestrator.evals.swebench.instances import GenerationInput, Instance
from orchestrator.evals.swebench.ledger import Ledger, LedgerRow
from orchestrator.evals.swebench.outcome import (
    Patch,
    budget_parked,
    classify,
    crashed,
    extract_patch,
    find_record,
    harness_failure,
)

#: Environment the pipeline inherits: the shell basics and the model provider's own settings, nothing
#: else. In particular no ``SDLC_*``, ``ORCHESTRATOR_*`` or ``SPINE_*`` setting from the harness's
#: environment: those change what the pipeline *does* (a repository URL, a model per stage, agentic
#: codegen), and a benchmark must measure the pipeline as configured by the harness, not by whatever
#: the operator's shell happens to export. The harness sets the few it needs, explicitly.
_ENV_EXACT = frozenset(
    {
        "PATH",
        "HOME",
        "USER",
        "LOGNAME",
        "LANG",
        "TERM",
        "TMPDIR",
        "SHELL",
        "VIRTUAL_ENV",
        "DOCKER_HOST",
        "DOCKER_CONFIG",
    }
)
_ENV_PREFIXES = ("LC_", "ANTHROPIC_", "OPENAI_", "LITELLM_")


@dataclass(frozen=True)
class Config:
    workdir: Path
    max_cost_usd: float
    wall_clock_s: int
    model: str = "claude-opus-5"
    spine_sha: str = ""
    test_timeout_s: int = 600
    python: str = sys.executable
    #: Keep the cloned generation repository and workspaces after the run (they can be large).
    keep_workspaces: bool = False
    #: A ``.env`` to take the model key from — and only that. See :func:`read_model_keys`.
    env_file: Path | None = None
    #: Extra variable *names* to pass through from the harness's environment, for a provider that needs
    #: more than the two key names (cloud credentials, say). Named one by one, never by prefix.
    pass_env: tuple[str, ...] = ()


#: Bash that writes a tar of one commit's tracked files to stdout. It is a checkout, not ``git archive``:
#: ``archive`` expands ``export-subst`` (matplotlib's ``_version.py`` came out with the commit hash baked
#: in) and drops ``export-ignore`` paths, so what it returned was not the commit. ``SPINE_TESTBED`` is
#: overridden only by tests, so the script can be run against a local repository.
EXPORT_SCRIPT = r"""
set -eu
cd "${SPINE_TESTBED:-/testbed}"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
export GIT_INDEX_FILE="$tmp/index"
git read-tree "$1"
git checkout-index -a -f --prefix="$tmp/tree/"
tar -c -C "$tmp/tree" .
"""


class Docker(Protocol):
    def pull(self, image_ref: str) -> None: ...
    def image_base(self, image_ref: str) -> str: ...
    def image_tree(self, image_ref: str, commit: str) -> str: ...
    def archive(self, image_ref: str, commit: str) -> bytes: ...
    def remove(self, image_ref: str) -> None: ...


class Launcher(Protocol):
    async def __call__(
        self, argv: list[str], env: dict[str, str], cwd: Path, log: Path, timeout_s: float
    ) -> tuple[int | None, bool]: ...


class CliDocker:
    """The real thing: the ``docker`` CLI, one linux/amd64 image at a time."""

    _PLATFORM = ("--platform", "linux/amd64")

    def _run(self, *argv: str, timeout: float = 300) -> subprocess.CompletedProcess[bytes]:
        try:
            return subprocess.run(["docker", *argv], capture_output=True, timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(f"docker {argv[0]} did not finish within {int(timeout)}s") from exc

    def pull(self, image_ref: str) -> None:
        done = self._run("pull", *self._PLATFORM, image_ref, timeout=1800)
        if done.returncode:
            raise RuntimeError(f"docker pull failed: {done.stderr.decode('utf-8', 'replace').strip()[-300:]}")

    def _in_image(self, image_ref: str, script: str, *args: str) -> subprocess.CompletedProcess[bytes]:
        return self._run(
            "run",
            "--rm",
            "--network",
            "none",
            *self._PLATFORM,
            image_ref,
            "bash",
            "-c",
            script,
            "spine",
            *args,
        )

    def image_base(self, image_ref: str) -> str:
        """``HEAD^`` — the images are the base commit plus one SWE-bench setup commit."""
        done = self._in_image(image_ref, "cd /testbed && git rev-parse HEAD^")
        base = done.stdout.decode().strip()
        if done.returncode or not base:
            raise RuntimeError(
                f"cannot read the image's git state: {done.stderr.decode('utf-8', 'replace').strip()[-200:]}"
            )
        return base

    def image_tree(self, image_ref: str, commit: str) -> str:
        """``<commit>^{tree}`` — what a faithful copy of that commit's tracked files must hash to."""
        done = self._in_image(image_ref, 'cd /testbed && git rev-parse "$1^{tree}"', commit)
        tree = done.stdout.decode().strip()
        if done.returncode or not tree:
            raise RuntimeError(
                f"cannot read the image's tree: {done.stderr.decode('utf-8', 'replace').strip()[-200:]}"
            )
        return tree

    def archive(self, image_ref: str, commit: str) -> bytes:
        done = self._in_image(image_ref, EXPORT_SCRIPT, commit)
        if done.returncode:
            raise RuntimeError(f"export failed: {done.stderr.decode('utf-8', 'replace').strip()[-200:]}")
        return done.stdout

    def remove(self, image_ref: str) -> None:
        self._run("rmi", "-f", image_ref)


async def subprocess_launcher(
    argv: list[str], env: dict[str, str], cwd: Path, log: Path, timeout_s: float
) -> tuple[int | None, bool]:
    """Run ``argv`` in its own process group; on the clock, kill the whole group (it spawns children).

    SIGTERM first, then SIGKILL to the group regardless of whether the leader has exited: a child that
    ignores TERM outlives its parent, and the group is still addressable.
    """
    with log.open("wb") as sink:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            cwd=str(cwd),
            env=env,
            stdout=sink,
            stderr=asyncio.subprocess.STDOUT,
            start_new_session=True,
        )
        try:
            await asyncio.wait_for(proc.wait(), timeout=timeout_s)
        except TimeoutError:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(proc.pid, signal.SIGTERM)
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(proc.wait(), timeout=5)
            with contextlib.suppress(ProcessLookupError):
                os.killpg(proc.pid, signal.SIGKILL)
            await proc.wait()
            return None, True
    return proc.returncode, False


#: The only names read from an env file. A checkout's ``.env`` also carries tracker, wiki, chat and
#: GitHub App credentials and a repository URL; a benchmark run must have none of them.
_KEY_NAMES = ("ANTHROPIC_API_KEY", "OPENAI_API_KEY")


def read_model_keys(env_file: Path | None) -> dict[str, str]:
    """The model keys in ``env_file``, and nothing else from it. Missing file or ``None`` → ``{}``."""
    if env_file is None or not env_file.is_file():
        return {}
    found: dict[str, str] = {}
    for raw in env_file.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, _, value = line.partition("=")
        name = name.strip().removeprefix("export ").strip()
        if name in _KEY_NAMES and name not in found:  # the first occurrence wins, as in Spine's own loader
            found[name] = value.strip().strip('"').strip("'")
    return found


def issue_markdown(gen: GenerationInput) -> str:
    """The pipeline's source document: the issue text exactly as written, and nothing else."""
    return gen.problem_statement.rstrip("\n") + "\n"


def child_env(cfg: Config, instance_dir: Path, test_command: list[str]) -> dict[str, str]:
    env = {
        k: v
        for k, v in os.environ.items()
        if k in _ENV_EXACT or k.startswith(_ENV_PREFIXES) or k in cfg.pass_env
    }
    for name, value in read_model_keys(cfg.env_file).items():
        env.setdefault(name, value)  # as Spine's own loader: a real environment variable wins
    env.update(
        {
            # One model for every stage, recorded in the ledger because it is what ran.
            "ORCHESTRATOR_MODEL": cfg.model,
            "SDLC_TEST_COMMAND": json.dumps(test_command),
            "SDLC_WORKSPACE_ROOT": str(instance_dir / "workspaces"),
            "SPINE_RUN_STATE": str(instance_dir / "state"),
            "ORCHESTRATOR_INTAKE_CACHE_DIR": str(instance_dir / "intake-cache"),
            "ORCHESTRATOR_BACKLOG_PATH": str(instance_dir / "BACKLOG.md"),
            "SDLC_RUN_BUDGET_USD": str(cfg.max_cost_usd),
            "PYTHONDONTWRITEBYTECODE": "1",
        }
    )
    return env


def test_command(gen: GenerationInput, base_sha: str, cfg: Config) -> list[str]:
    """The ``SDLC_TEST_COMMAND`` argv: the overlay wrapper for this instance's image and base."""
    return [
        cfg.python,
        "-m",
        "orchestrator.evals.swebench.exec",
        "--image",
        gen.image_ref,
        "--base",
        base_sha,
        "--kind",
        kind_for(gen.repo),
        "--timeout",
        str(cfg.test_timeout_s),
        "--",
        "{files}",
    ]


def autorun_argv(cfg: Config, issue: Path, repo: Path, out: Path) -> list[str]:
    return [
        cfg.python,
        "-c",
        "from orchestrator.cli import app; app()",
        "sdlc",
        "autorun",
        "--source",
        f"file://{issue}",
        "--repo",
        str(repo),
        "--path",
        str(repo),
        "--issue-type",
        "Bug",
        "--language",
        "python",
        "--no-plan-gate",
        "--max-cost",
        str(cfg.max_cost_usd),
        "--out",
        str(out),
    ]


def build_generation_repo(gen: GenerationInput, dest: Path, docker: Docker) -> str:
    """The instance's base commit as one fresh commit; returns that commit's sha.

    The images are the base commit plus one SWE-bench *setup* commit that is not empty (measured:
    file-mode changes, a ``pyproject.toml`` pin, a ``tox.ini`` flag), so the image's ``HEAD`` is not the
    commit the issue was filed against and the gold patches are not relative to it. The generator starts
    from the base commit itself, whose identity is checked against the image's ``HEAD^``.

    Then the copy is checked, not trusted: its tree must equal the image's ``<base>^{tree}``. Extracting
    and re-adding files can lose or alter tracked ones (``.gitignore``, ``export-ignore``,
    ``export-subst``, line-ending attributes), and the generator would then work on a repository that is
    not the one graded.
    """
    base = docker.image_base(gen.image_ref)
    if base != gen.base_commit:
        raise RuntimeError(
            f"the image is not at the base commit (HEAD^ {base[:12]}, expected {gen.base_commit[:12]})"
        )
    dest.mkdir(parents=True, exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(docker.archive(gen.image_ref, base))) as tar:
        tar.extractall(dest, filter="data")

    def git(*args: str) -> str:
        done = subprocess.run(
            ["git", "-c", "user.email=bench@spine.invalid", "-c", "user.name=bench", *args],
            cwd=dest,
            capture_output=True,
            text=True,
            check=True,
        )
        return done.stdout.strip()

    git("init", "-q", "-b", "main")
    git("config", "user.email", "bench@spine.invalid")
    git("config", "user.name", "bench")
    # -f: the tree is exactly the files the base commit tracks, so a .gitignore match is not a reason to
    # leave one out (a tracked ``.env`` fixture in flask was).
    git("add", "-A", "-f")
    git("commit", "-q", "-m", f"{gen.instance_id} at {gen.base_commit[:12]}")
    expected = docker.image_tree(gen.image_ref, base)
    actual = git("rev-parse", "HEAD^{tree}")
    if actual != expected:
        raise RuntimeError(
            f"the generation tree {actual[:12]} differs from the image's {expected[:12]}: "
            "files were lost or added between the image and the repository the generator would see"
        )
    return git("rev-parse", "HEAD")


def _inside(path: Path, parent: Path) -> bool:
    try:
        return path.resolve().is_relative_to(parent.resolve())
    except OSError:
        return False


def _row(inst: Instance, cfg: Config, **kw: object) -> LedgerRow:
    gen = inst.generation
    base: dict[str, object] = {
        "instance_id": gen.instance_id,
        "repo": gen.repo,
        "image_digest": gen.image_digest,
        "spine_sha": cfg.spine_sha,
        "model": cfg.model,
    }
    base.update(kw)
    return LedgerRow(**base)  # type: ignore[arg-type]


async def run_instance(
    inst: Instance, cfg: Config, ledger: Ledger, docker: Docker, launcher: Launcher
) -> LedgerRow:
    """Run one instance end to end and append its row. Never raises for an instance-level failure.

    Anything unexpected — a bad archive, an unreadable record, a bug of mine — becomes a
    ``generator-error`` row, so one instance cannot stop a sweep and leave the rest unrun. (Only
    ``BaseException`` — an interrupt — gets through.) The row's spend is cumulative over the
    instance's earlier attempts, and each attempt starts from a clean per-run directory, so a retry
    can neither read the previous attempt's record nor erase its cost.
    """
    gen = inst.generation
    started = time.monotonic()
    workdir = cfg.workdir.resolve()
    inst_dir = workdir / gen.instance_id
    history = ledger.history(gen.instance_id)
    carried = history[-1].spent_usd if history else 0.0
    attempts = len(history) + 1
    spent_so_far = carried

    def failed(reason: str, **kw: object) -> LedgerRow:
        spend = float(kw.pop("spent_usd", 0.0)) + carried  # type: ignore[arg-type]
        return _row(
            inst, cfg, bucket="generator-error", reason=reason, seconds=time.monotonic() - started,
            spent_usd=spend, attempts=attempts, **kw,
        )  # fmt: skip

    row: LedgerRow
    try:
        for stale in ("state", "out", "workspaces", "repo", "intake-cache"):
            shutil.rmtree(inst_dir / stale, ignore_errors=True)
        inst_dir.mkdir(parents=True, exist_ok=True)
        try:
            docker.pull(gen.image_ref)
            repo = inst_dir / "repo"
            base_sha = build_generation_repo(gen, repo, docker)
        except (RuntimeError, OSError, subprocess.CalledProcessError, tarfile.TarError) as exc:
            row = failed(f"setup failed: {exc}")
            ledger.append(row)
            return row

        issue = inst_dir / "issue.md"
        issue.write_text(issue_markdown(gen), encoding="utf-8")
        env = child_env(cfg, inst_dir, test_command(gen, base_sha, cfg))
        log = inst_dir / "autorun.log"
        _, timed_out = await launcher(
            autorun_argv(cfg, issue, repo, inst_dir / "out"), env, inst_dir, log, float(cfg.wall_clock_s)
        )

        state = inst_dir / "state"
        try:
            record = find_record(state)
        except ValueError as exc:
            row = failed(f"could not read the run's result: {exc}")
            ledger.append(row)
            return row
        spent_so_far = carried + (float(record.get("spent_usd") or 0.0) if record else 0.0)
        raw_worktree = str(record.get("worktree") or "") if record else ""
        patch = Patch(data=b"", files=(), dropped=())
        try:
            # An empty path is "no worktree" — as a Path it would be the current directory, which is
            # whatever repository the harness was started in.
            if raw_worktree and not timed_out:
                worktree = Path(raw_worktree)
                if not _inside(worktree, inst_dir):
                    raise RuntimeError(
                        f"the run's worktree {worktree} is outside the instance directory {inst_dir}"
                    )
                patch = extract_patch(worktree, base_sha, drop=inst.test_patch_files)
        except (RuntimeError, OSError, subprocess.CalledProcessError) as exc:
            row = failed(
                f"could not read the run's result: {exc}",
                spent_usd=float(record.get("spent_usd") or 0.0) if record else 0.0,
                run_id=str(record.get("run_id") or "") if record else "",
            )
            ledger.append(row)
            return row
        log_text = log.read_text(encoding="utf-8", errors="replace") if log.is_file() else ""
        outcome = classify(
            record,
            timed_out=timed_out,
            patch_text=patch.data,
            budget_parked=budget_parked(state, record) if record else False,
            crashed=crashed(log_text),
            harness_failure=harness_failure(log_text),
        )
        sha = ""
        if outcome.bucket == "submitted":
            patches = workdir / "patches"
            patches.mkdir(parents=True, exist_ok=True)
            (patches / f"{gen.instance_id}.diff").write_bytes(patch.data)
            sha = hashlib.sha256(patch.data).hexdigest()
        row = _row(
            inst,
            cfg,
            bucket=outcome.bucket,
            reason=outcome.reason,
            verdict=outcome.verdict,
            spent_usd=carried + outcome.spent_usd,
            seconds=time.monotonic() - started,
            run_id=str(record.get("run_id") or "") if record else "",
            patch_sha256=sha,
            patch_files=list(patch.files),
            dropped=list(patch.dropped),
            attempts=attempts,
        )
        ledger.append(row)
        return row
    except Exception as exc:  # noqa: BLE001 — the contract above: an instance never takes the sweep down
        row = failed(f"unexpected {type(exc).__name__}: {exc}", spent_usd=spent_so_far - carried)
        ledger.append(row)
        return row
    finally:
        with contextlib.suppress(Exception):
            docker.remove(gen.image_ref)
        if not cfg.keep_workspaces:
            for name in ("repo", "workspaces"):
                shutil.rmtree(inst_dir / name, ignore_errors=True)
