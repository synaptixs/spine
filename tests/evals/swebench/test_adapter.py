"""The adapter: one instance in, one ledger row out, and nothing from the answer in between.

Docker and the pipeline subprocess are faked here; what is real is git (the generation repository
and the patch), the filesystem layout, the environment handed to the child, and the process-group
kill. The end-to-end run against a real image and a real model is a separate step.
"""

from __future__ import annotations

import asyncio
import io
import json
import os
import subprocess
import sys
import tarfile
import time
from pathlib import Path
from typing import Any

import pytest

from orchestrator.evals.swebench import adapter as ad
from orchestrator.evals.swebench.instances import GenerationInput, Instance
from orchestrator.evals.swebench.ledger import Ledger, LedgerRow
from orchestrator.sdlc.testrunner import parse_test_command

BASE = "b9cf764be62e77b4777b3a75ec256f6209a57671"
DIGEST = "sha256:" + "d" * 64
GEN = GenerationInput(
    instance_id="django__django-10097",
    repo="django/django",
    version="2.2",
    base_commit=BASE,
    problem_statement="URLValidator accepts invalid characters.\n\nIt should not.",
    image="swebench/sweb.eval.x86_64.django_1776_django-10097:latest",
    image_digest=DIGEST,
)
INST = Instance(
    generation=GEN, test_patch_files=("tests/validators/tests.py", "tests/validators/valid_urls.txt")
)


def _tar(files: dict[str, str]) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        for name, text in files.items():
            data = text.encode()
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


class FakeDocker:
    def __init__(self, *, base: str = BASE, clean: bool = True, pull_error: str = "", tree: str = "") -> None:
        self.base, self.clean, self.pull_error, self.tree = base, clean, pull_error, tree
        self.events: list[str] = []

    def pull(self, image_ref: str) -> None:
        self.events.append(f"pull {image_ref}")
        if self.pull_error:
            raise RuntimeError(self.pull_error)

    def image_base(self, image_ref: str) -> tuple[str, bool]:
        return self.base, self.clean

    def image_tree(self, image_ref: str) -> str:
        """The tree hash the image would report: the real one for this archive, unless a test overrides it."""
        return self.tree or _tree_of(self.archive(image_ref))

    def archive(self, image_ref: str) -> bytes:
        return _tar({"django/core/validators.py": "SCHEMES = 1\n", "tests/validators/tests.py": "T = 1\n"})

    def remove(self, image_ref: str) -> None:
        self.events.append(f"remove {image_ref}")


def _tree_of(archive: bytes) -> str:
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
            tar.extractall(root, filter="data")
        subprocess.run(["git", "init", "-q"], cwd=root, check=True, capture_output=True)
        subprocess.run(["git", "add", "-A"], cwd=root, check=True, capture_output=True)
        return subprocess.run(
            ["git", "write-tree"], cwd=root, check=True, capture_output=True, text=True
        ).stdout.strip()


def _git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", "user.email=t@e", "-c", "user.name=t", *args],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout


class FakeLauncher:
    """Plays the pipeline: clones the generation repo, commits a change, writes a run record."""

    def __init__(
        self,
        *,
        status: str = "done",
        phase: str = "done",
        change: bool = True,
        record: bool = True,
        timed_out: bool = False,
        approval_title: str = "",
        verdict: str = "",
    ) -> None:
        self.status, self.phase, self.change, self.write_record = status, phase, change, record
        self.timed_out, self.approval_title, self.verdict = timed_out, approval_title, verdict
        self.calls: list[dict[str, Any]] = []

    async def __call__(
        self, argv: list[str], env: dict[str, str], cwd: Path, log: Path, timeout_s: float
    ) -> tuple[int | None, bool]:
        self.calls.append({"argv": argv, "env": env, "cwd": cwd, "timeout_s": timeout_s})
        log.write_text("[autorun] fake\n", encoding="utf-8")
        if self.timed_out:
            return None, True
        repo = Path(argv[argv.index("--repo") + 1])
        worktree = Path(env["SDLC_WORKSPACE_ROOT"]) / "wt"
        worktree.parent.mkdir(parents=True, exist_ok=True)
        _git(repo, "clone", "-q", str(repo), str(worktree))
        if self.change:
            (worktree / "django" / "core" / "validators.py").write_text("SCHEMES = 2\n", encoding="utf-8")
            (worktree / "tests" / "validators" / "tests.py").write_text(
                "T = 2\n", encoding="utf-8"
            )  # overwritten by the grader
            (worktree / "tests" / "validators" / "test_new.py").write_text(
                "def test_n():\n    pass\n", encoding="utf-8"
            )
            (worktree / ".spine").mkdir(exist_ok=True)
            (worktree / ".spine" / "plan.md").write_text("x", encoding="utf-8")
            _git(worktree, "add", "-A")
            _git(worktree, "commit", "-q", "-m", "the build")
        if self.write_record:
            state = Path(env["SPINE_RUN_STATE"])
            (state / "_state").mkdir(parents=True, exist_ok=True)
            rec = {
                "run_id": "r1",
                "status": self.status,
                "phase": self.phase,
                "verdict": self.verdict,
                "spent_usd": 0.42,
                "branch": "feat/x",
                "worktree": str(worktree),
                "parked_reason": "because",
            }
            if self.approval_title:
                (state / "_approvals").mkdir(exist_ok=True)
                (state / "_approvals" / "ap1.json").write_text(
                    json.dumps({"title": self.approval_title}), encoding="utf-8"
                )
                rec["approval_id"] = "ap1"
            (state / "_state" / "r1.json").write_text(json.dumps(rec), encoding="utf-8")
        return 0, False


def _cfg(tmp_path: Path, **kw: Any) -> ad.Config:
    return ad.Config(
        workdir=tmp_path / "work",
        max_cost_usd=10.0,
        wall_clock_s=1800,
        model="claude-opus-5",
        spine_sha="56335503",
        **kw,
    )


def _run(
    tmp_path: Path, docker: FakeDocker, launcher: FakeLauncher, **kw: Any
) -> tuple[Any, Ledger, ad.Config]:
    cfg = _cfg(tmp_path, **kw)
    book = Ledger(cfg.workdir / "ledger.jsonl")
    row = asyncio.run(ad.run_instance(INST, cfg, book, docker, launcher))
    return row, book, cfg


# --- the happy path -----------------------------------------------------------------------


def test_a_passing_run_is_submitted_with_its_patch_filed_and_the_image_removed(tmp_path: Path) -> None:
    docker, launcher = FakeDocker(), FakeLauncher()

    row, book, cfg = _run(tmp_path, docker, launcher)

    assert row.bucket == "submitted" and row.spent_usd == 0.42 and row.model == "claude-opus-5"
    assert row.image_digest == DIGEST and row.spine_sha == "56335503"
    patch = (cfg.workdir / "patches" / "django__django-10097.diff").read_text()
    assert "SCHEMES = 2" in patch and "test_new.py" in patch
    assert "tests/validators/tests.py" not in patch  # the grader overwrites it, so it is dropped
    assert ".spine" not in patch
    assert row.patch_files == ["django/core/validators.py", "tests/validators/test_new.py"]
    assert row.dropped == ["tests/validators/tests.py"]
    assert docker.events[-1] == f"remove {GEN.image_ref}"
    assert [r.bucket for r in book.rows()] == ["submitted"]


def test_the_pipeline_is_launched_with_the_decided_options(tmp_path: Path) -> None:
    launcher = FakeLauncher()
    _, _, cfg = _run(tmp_path, FakeDocker(), launcher)

    call = launcher.calls[0]
    argv = call["argv"]
    assert argv[0] == sys.executable and argv[1] == "-c" and "orchestrator.cli" in argv[2]
    assert argv[3:5] == ["sdlc", "autorun"]
    issue = cfg.workdir / "django__django-10097" / "issue.md"
    assert argv[argv.index("--source") + 1] == f"file://{issue}"
    assert argv[argv.index("--issue-type") + 1] == "Bug"
    assert argv[argv.index("--max-cost") + 1] == "10.0"
    assert argv[argv.index("--language") + 1] == "python"
    assert "--no-plan-gate" in argv
    assert "--live" not in argv  # safe mode is the default: no external write anywhere
    assert call["timeout_s"] == 1800
    assert issue.read_text() == GEN.problem_statement + "\n"


def test_the_test_command_points_at_the_overlay_wrapper_with_the_pinned_image(tmp_path: Path) -> None:
    launcher = FakeLauncher()
    _run(tmp_path, FakeDocker(), launcher)

    command = parse_test_command(launcher.calls[0]["env"]["SDLC_TEST_COMMAND"])

    assert command[:3] == [sys.executable, "-m", "orchestrator.evals.swebench.exec"]
    assert command[command.index("--image") + 1] == GEN.image_ref
    assert command[command.index("--kind") + 1] == "django"
    assert len(command[command.index("--base") + 1]) == 40
    assert command[-1] == "{files}" and command[-2] == "--"


# --- the leak audit: written so that it can fail ------------------------------------------


def test_nothing_from_the_grading_side_reaches_generation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GRADING_SENTINEL", "SENTINEL-GRADING")
    monkeypatch.setenv("HF_TOKEN", "hf_SENTINEL")
    monkeypatch.setenv("SWEBENCH_INSTANCES", "/data/instances.jsonl")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-key")
    launcher = FakeLauncher()

    _, _, cfg = _run(tmp_path, FakeDocker(), launcher)

    call = launcher.calls[0]
    env = call["env"]
    assert env["ANTHROPIC_API_KEY"] == "sk-test-key"  # the model must stay reachable
    assert "GRADING_SENTINEL" not in env and "HF_TOKEN" not in env and "SWEBENCH_INSTANCES" not in env
    surface = json.dumps({"argv": call["argv"], "env": env})
    for forbidden in (*INST.test_patch_files, "SENTINEL", "instances.jsonl"):
        assert forbidden not in surface, forbidden
    issue = (cfg.workdir / GEN.instance_id / "issue.md").read_text()
    assert not any(name in issue for name in INST.test_patch_files)


def test_the_generation_repository_is_one_commit_with_no_remote_or_other_refs(tmp_path: Path) -> None:
    launcher = FakeLauncher()
    _run(tmp_path, FakeDocker(), launcher, keep_workspaces=True)

    repo = Path(launcher.calls[0]["argv"][launcher.calls[0]["argv"].index("--repo") + 1])
    assert _git(repo, "rev-list", "--all", "--count").strip() == "1"
    assert _git(repo, "remote").strip() == ""
    assert _git(repo, "tag").strip() == ""
    assert len(_git(repo, "branch", "--list").strip().splitlines()) == 1


# --- how runs end -------------------------------------------------------------------------


def test_a_wall_clock_kill_is_a_generator_error_and_still_removes_the_image(tmp_path: Path) -> None:
    docker = FakeDocker()
    row, _, _ = _run(tmp_path, docker, FakeLauncher(timed_out=True))
    assert row.bucket == "generator-error" and "wall-clock" in row.reason
    assert docker.events[-1].startswith("remove ")


def test_a_validity_park_is_parked_with_its_verdict_and_files_no_patch(tmp_path: Path) -> None:
    row, _, cfg = _run(
        tmp_path,
        FakeDocker(),
        FakeLauncher(status="parked", phase="validity", verdict="UNLOCALIZED", change=False),
    )
    assert (row.bucket, row.verdict) == ("parked", "UNLOCALIZED")
    assert not (cfg.workdir / "patches" / "django__django-10097.diff").exists()


def test_a_spend_cap_park_is_a_budget_stop(tmp_path: Path) -> None:
    row, _, _ = _run(
        tmp_path,
        FakeDocker(),
        FakeLauncher(
            status="parked",
            phase="implement",
            approval_title="budget exhausted — raise the cap or drop the run?",
            change=False,
        ),
    )
    assert row.bucket == "budget-stop"


def test_a_run_that_wrote_no_record_is_a_generator_error(tmp_path: Path) -> None:
    row, _, _ = _run(tmp_path, FakeDocker(), FakeLauncher(record=False, change=False))
    assert row.bucket == "generator-error" and "no run record" in row.reason


def test_a_finished_run_that_changed_nothing_is_unresolved(tmp_path: Path) -> None:
    row, _, _ = _run(tmp_path, FakeDocker(), FakeLauncher(change=False))
    assert row.bucket == "unresolved"


def test_a_pull_failure_is_a_generator_error_and_never_launches(tmp_path: Path) -> None:
    launcher = FakeLauncher()
    row, _, _ = _run(tmp_path, FakeDocker(pull_error="denied"), launcher)
    assert row.bucket == "generator-error" and "denied" in row.reason
    assert launcher.calls == []


@pytest.mark.parametrize(("base", "clean"), [("0" * 40, True), (BASE, False)])
def test_an_image_that_is_not_the_base_commit_is_refused(tmp_path: Path, base: str, clean: bool) -> None:
    launcher = FakeLauncher()
    row, _, _ = _run(tmp_path, FakeDocker(base=base, clean=clean), launcher)
    assert row.bucket == "generator-error" and "base commit" in row.reason
    assert launcher.calls == []


# --- the real subprocess launcher ---------------------------------------------------------


def test_the_launcher_returns_the_childs_code_and_captures_its_output(tmp_path: Path) -> None:
    log = tmp_path / "run.log"
    code, timed_out = asyncio.run(
        ad.subprocess_launcher(
            [sys.executable, "-c", "print('hello'); raise SystemExit(3)"], dict(os.environ), tmp_path, log, 30
        )
    )
    assert (code, timed_out) == (3, False) and "hello" in log.read_text()


def test_the_launcher_kills_the_whole_process_group_on_timeout(tmp_path: Path) -> None:
    marker = tmp_path / "grandchild.pid"
    child = (
        "import subprocess, sys, time\n"
        f"p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
        f"open({str(marker)!r}, 'w').write(str(p.pid))\n"
        "time.sleep(60)\n"
    )
    started = time.monotonic()
    code, timed_out = asyncio.run(
        ad.subprocess_launcher(
            [sys.executable, "-c", child], dict(os.environ), tmp_path, tmp_path / "run.log", 1.5
        )
    )

    assert timed_out is True and time.monotonic() - started < 20
    grandchild = int(marker.read_text())
    time.sleep(0.5)
    with pytest.raises(ProcessLookupError):
        os.kill(grandchild, 0)


# --- the model key: the only thing taken from a .env file ---------------------------------


def test_only_the_model_keys_are_read_from_an_env_file(tmp_path: Path) -> None:
    """A `.env` here also holds Jira, Confluence, Slack and GitHub App credentials and a repo URL.
    None of that may reach a benchmark run; only the keys the model needs are read."""
    env = tmp_path / ".env"
    env.write_text(
        "# comment\n"
        "ANTHROPIC_API_KEY='sk-ant-from-file'\n"
        "OPENAI_API_KEY=sk-oai-from-file\n"
        "JIRA_API_TOKEN=SENTINEL-JIRA\n"
        "SDLC_REPO_URL=https://github.com/field/SENTINEL-REPO\n"
        "SLACK_WEBHOOK_URL=SENTINEL-SLACK\n"
        "GITHUB_APP_PRIVATE_KEY_PATH=/secrets/SENTINEL.pem\n",
        encoding="utf-8",
    )

    assert ad.read_model_keys(env) == {
        "ANTHROPIC_API_KEY": "sk-ant-from-file",
        "OPENAI_API_KEY": "sk-oai-from-file",
    }
    assert ad.read_model_keys(tmp_path / "missing.env") == {}
    assert ad.read_model_keys(None) == {}


def test_the_key_reaches_the_child_and_nothing_else_in_the_file_does(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for name in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text(
        "ANTHROPIC_API_KEY=sk-ant-from-file\nJIRA_API_TOKEN=SENTINEL-JIRA\nSDLC_REPO_URL=https://x/SENTINEL-REPO\n",
        encoding="utf-8",
    )
    launcher = FakeLauncher()

    _run(tmp_path, FakeDocker(), launcher, env_file=env_file)

    env = launcher.calls[0]["env"]
    assert env["ANTHROPIC_API_KEY"] == "sk-ant-from-file"
    assert "JIRA_API_TOKEN" not in env and "SDLC_REPO_URL" not in env
    assert "SENTINEL" not in json.dumps(env)


def test_a_key_already_in_the_environment_wins_over_the_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-from-environment")
    env_file = tmp_path / ".env"
    env_file.write_text("ANTHROPIC_API_KEY=sk-ant-from-file\n", encoding="utf-8")
    launcher = FakeLauncher()

    _run(tmp_path, FakeDocker(), launcher, env_file=env_file)

    assert launcher.calls[0]["env"]["ANTHROPIC_API_KEY"] == "sk-ant-from-environment"


# --- a run that never made a worktree, and a worktree that is not the instance's ----------


class _NoWorktree(FakeLauncher):
    """The pipeline died at intake: the record exists, `worktree` is the empty string."""

    async def __call__(
        self, argv: list[str], env: dict[str, str], cwd: Path, log: Path, timeout_s: float
    ) -> tuple[int | None, bool]:
        self.calls.append({"argv": argv, "env": env, "cwd": cwd, "timeout_s": timeout_s})
        log.write_text(
            "[autorun] LLMError: AuthenticationError: Missing Anthropic API Key\n", encoding="utf-8"
        )
        state = Path(env["SPINE_RUN_STATE"]) / "_state"
        state.mkdir(parents=True, exist_ok=True)
        rec = {
            "run_id": "r1",
            "status": "failed",
            "phase": "intake",
            "verdict": "",
            "spent_usd": 0.0,
            "worktree": "",
            "branch": "",
        }
        (state / "r1.json").write_text(json.dumps(rec), encoding="utf-8")
        return 1, False


def test_an_empty_worktree_is_no_worktree_and_git_never_runs_in_the_current_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`Path("")` is the current directory — here, the Spine checkout. It was once diffed against the
    instance's base commit (a read, but a harness must never be able to look at the wrong repository)."""

    def forbidden(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("extract_patch must not run without a worktree")

    monkeypatch.setattr(ad, "extract_patch", forbidden)

    row, _, _ = _run(tmp_path, FakeDocker(), _NoWorktree())

    assert row.bucket == "generator-error" and "unexpected exception" in row.reason  # the LLMError in the log
    assert row.patch_sha256 == ""


def test_a_worktree_outside_the_instances_directory_is_refused(tmp_path: Path) -> None:
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    _git(elsewhere, "init", "-q")

    class _Wrong(FakeLauncher):
        async def __call__(
            self, argv: list[str], env: dict[str, str], cwd: Path, log: Path, timeout_s: float
        ) -> tuple[int | None, bool]:
            result = await super().__call__(argv, env, cwd, log, timeout_s)
            record = next((Path(env["SPINE_RUN_STATE"]) / "_state").glob("*.json"))
            data = json.loads(record.read_text())
            data["worktree"] = str(elsewhere)
            record.write_text(json.dumps(data), encoding="utf-8")
            return result

    row, _, _ = _run(tmp_path, FakeDocker(), _Wrong())

    assert row.bucket == "generator-error" and "outside" in row.reason


def test_a_failure_while_reading_the_result_is_a_row_not_an_exception(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("git diff --name-only -z failed: fatal: bad object")

    monkeypatch.setattr(ad, "extract_patch", broken)

    row, book, _ = _run(tmp_path, FakeDocker(), FakeLauncher())

    assert row.bucket == "generator-error" and "bad object" in row.reason
    assert [r.bucket for r in book.rows()] == ["generator-error"]


# --- review round: one bad instance must not stop the sweep, and the books must tell the truth ----


class _Recording(FakeLauncher):
    """Like FakeLauncher, but also lets a test choose the record's spend and add file bytes."""

    def __init__(self, *, spent: float = 0.42, content: bytes | None = None, **kw: Any) -> None:
        super().__init__(**kw)
        self.spent, self.content = spent, content

    async def __call__(
        self, argv: list[str], env: dict[str, str], cwd: Path, log: Path, timeout_s: float
    ) -> tuple[int | None, bool]:
        result = await super().__call__(argv, env, cwd, log, timeout_s)
        state = Path(env["SPINE_RUN_STATE"]) / "_state"
        for record in state.glob("*.json") if state.is_dir() else []:
            data = json.loads(record.read_text())
            data["spent_usd"] = self.spent
            record.write_text(json.dumps(data), encoding="utf-8")
        if self.content is not None:
            worktree = Path(env["SDLC_WORKSPACE_ROOT"]) / "wt"
            (worktree / "django" / "core" / "validators.py").write_bytes(self.content)
            _git(worktree, "add", "-A")
            _git(worktree, "commit", "-q", "-m", "latin-1")
        return result


def test_a_patch_with_bytes_that_are_not_utf8_is_kept_byte_for_byte(tmp_path: Path) -> None:
    content = b"# caf\xe9\nSCHEMES = 3\n"  # a latin-1 e-acute: not valid UTF-8

    row, _, cfg = _run(tmp_path, FakeDocker(), _Recording(content=content))

    assert row.bucket == "submitted"
    stored = (cfg.workdir / "patches" / "django__django-10097.diff").read_bytes()
    assert b"caf\xe9" in stored
    import hashlib

    assert row.patch_sha256 == hashlib.sha256(stored).hexdigest()


def test_an_archive_with_an_absolute_symlink_is_a_setup_failure_not_a_crash(tmp_path: Path) -> None:
    class _Linky(FakeDocker):
        def archive(self, image_ref: str) -> bytes:
            buf = io.BytesIO()
            with tarfile.open(fileobj=buf, mode="w") as tar:
                link = tarfile.TarInfo("evil")
                link.type = tarfile.SYMTYPE
                link.linkname = "/etc/passwd"
                tar.addfile(link)
            return buf.getvalue()

    row, _, _ = _run(tmp_path, _Linky(), FakeLauncher())

    assert row.bucket == "generator-error" and "setup failed" in row.reason


def test_a_truncated_run_record_is_a_generator_error_not_a_crash(tmp_path: Path) -> None:
    class _Truncated(FakeLauncher):
        async def __call__(
            self, argv: list[str], env: dict[str, str], cwd: Path, log: Path, timeout_s: float
        ) -> tuple[int | None, bool]:
            state = Path(env["SPINE_RUN_STATE"]) / "_state"
            state.mkdir(parents=True, exist_ok=True)
            (state / "r1.json").write_text('{"run_id": "r1", "status": "do', encoding="utf-8")
            log.write_text("", encoding="utf-8")
            return None, False

    row, _, _ = _run(tmp_path, FakeDocker(), _Truncated())

    assert row.bucket == "generator-error" and "run record" in row.reason


def test_any_unexpected_exception_while_running_is_a_row_and_the_image_still_goes(tmp_path: Path) -> None:
    class _Boom(FakeLauncher):
        async def __call__(
            self, argv: list[str], env: dict[str, str], cwd: Path, log: Path, timeout_s: float
        ) -> tuple[int | None, bool]:
            raise ValueError("the launcher itself broke")

    docker = FakeDocker()
    row, book, _ = _run(tmp_path, docker, _Boom())

    assert row.bucket == "generator-error" and "the launcher itself broke" in row.reason
    assert docker.events[-1].startswith("remove ") and [r.bucket for r in book.rows()] == ["generator-error"]


def test_a_killed_run_books_what_it_had_spent(tmp_path: Path) -> None:
    """A wall-clock kill with $4.90 recorded was booked at $0.00 — and, being a generator-error, retried."""
    row, _, _ = _run(
        tmp_path, FakeDocker(), _Recording(spent=4.9, timed_out=False, status="running", phase="implement")
    )
    assert row.bucket == "generator-error" and row.spent_usd == pytest.approx(4.9)

    class _Killed(_Recording):
        async def __call__(
            self, argv: list[str], env: dict[str, str], cwd: Path, log: Path, timeout_s: float
        ) -> tuple[int | None, bool]:
            await FakeLauncher.__call__(self, argv, env, cwd, log, timeout_s)
            state = Path(env["SPINE_RUN_STATE"]) / "_state"
            rec = next(state.glob("*.json"))
            data = json.loads(rec.read_text())
            data["spent_usd"] = 4.9
            rec.write_text(json.dumps(data), encoding="utf-8")
            return None, True

    row2, _, _ = _run(tmp_path / "second", FakeDocker(), _Killed(status="running"))
    assert row2.bucket == "generator-error" and "wall-clock" in row2.reason
    assert row2.spent_usd == pytest.approx(4.9)


def test_a_retry_carries_the_spend_of_the_attempts_before_it(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    book = Ledger(cfg.workdir / "ledger.jsonl")
    book.append(
        LedgerRow("django__django-10097", "django/django", "generator-error", "docker", spent_usd=3.0)
    )

    row = asyncio.run(ad.run_instance(INST, cfg, book, FakeDocker(), _Recording(spent=0.5)))

    assert row.spent_usd == pytest.approx(3.5) and row.attempts == 2
    assert [r.spent_usd for r in book.rows()] == [pytest.approx(3.5)]


def test_a_retry_does_not_read_the_earlier_attempts_run_record(tmp_path: Path) -> None:
    """The instance directory is reused; the newest record in it used to be taken as this attempt's."""
    cfg = _cfg(tmp_path)
    book = Ledger(cfg.workdir / "ledger.jsonl")
    first = asyncio.run(ad.run_instance(INST, cfg, book, FakeDocker(), _Recording(spent=7.0)))
    assert first.run_id == "r1"

    second = asyncio.run(
        ad.run_instance(INST, cfg, book, FakeDocker(), FakeLauncher(record=False, change=False))
    )

    assert second.bucket == "generator-error" and "no run record" in second.reason and second.run_id == ""
    assert second.spent_usd == pytest.approx(7.0)  # the first attempt's spend is carried, this one added none


# --- review round 2: the environment, the tree, and the small things ---------------------------


def test_settings_in_the_harnesss_environment_cannot_change_what_the_pipeline_does(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`SDLC_REPO_URL`, a per-stage model, agentic codegen: each changes what the pipeline *does*, and a
    benchmark must measure it as the harness configures it. (This is also why the first version of the
    key test failed in a full suite: another test had loaded a `.env` into the process.)"""
    for name, value in {
        "SDLC_REPO_URL": "https://github.com/field/SENTINEL",
        "SDLC_AGENTIC_CODEGEN": "1",
        "SDLC_CODEGEN_MODEL": "some-other-model",
        "SDLC_JUDGE_MODEL": "some-other-model",
        "ORCHESTRATOR_INTAKE_MODEL": "some-other-model",
        "SPINE_ONTOMESH_URL": "http://x",
        "SDLC_TEST_ISOLATION": "venv",
        "AWS_SECRET_ACCESS_KEY": "SENTINEL-AWS",
        "ANTHROPIC_API_KEY": "sk-test",
    }.items():
        monkeypatch.setenv(name, value)
    launcher = FakeLauncher()

    _run(tmp_path, FakeDocker(), launcher)

    env = launcher.calls[0]["env"]
    gone_names = (
        "SDLC_REPO_URL",
        "SDLC_AGENTIC_CODEGEN",
        "SDLC_CODEGEN_MODEL",
        "SDLC_JUDGE_MODEL",
        "ORCHESTRATOR_INTAKE_MODEL",
        "SPINE_ONTOMESH_URL",
        "SDLC_TEST_ISOLATION",
        "AWS_SECRET_ACCESS_KEY",
    )
    for gone in gone_names:
        assert gone not in env, gone
    assert env["ANTHROPIC_API_KEY"] == "sk-test"
    assert (
        env["ORCHESTRATOR_MODEL"] == "claude-opus-5"
    )  # one model for every stage — the one the ledger records


def test_a_variable_named_in_pass_env_is_passed_and_only_that_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "for-bedrock")
    monkeypatch.setenv("AWS_SESSION_TOKEN", "not-named")
    launcher = FakeLauncher()

    _run(tmp_path, FakeDocker(), launcher, pass_env=("AWS_SECRET_ACCESS_KEY",))

    env = launcher.calls[0]["env"]
    assert env["AWS_SECRET_ACCESS_KEY"] == "for-bedrock" and "AWS_SESSION_TOKEN" not in env


def test_export_lines_are_understood_and_the_first_duplicate_wins(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text("export ANTHROPIC_API_KEY=first\nANTHROPIC_API_KEY=second\n", encoding="utf-8")
    assert ad.read_model_keys(env) == {"ANTHROPIC_API_KEY": "first"}


def test_a_generation_tree_that_differs_from_the_images_is_refused(tmp_path: Path) -> None:
    """`git archive` honours `export-ignore` and `export-subst`, and `git add -A` honours `.gitignore`:
    either can make the tree the model sees differ from the tree the tests run against."""
    launcher = FakeLauncher()
    row, _, _ = _run(tmp_path, FakeDocker(tree="0" * 40), launcher)

    assert row.bucket == "generator-error" and "differs from the image" in row.reason
    assert launcher.calls == []


@pytest.mark.parametrize(
    "bad",
    ["../../escaped__x-1", "a__b-1/../../x", "/abs__x-1", ".hidden__x-1", "no-dunder", "a__b-x", "a__b-1 "],
)
def test_an_instance_id_is_not_trusted_as_a_path(tmp_path: Path, bad: str) -> None:
    from orchestrator.evals.swebench.instances import load_instances

    row = {
        "instance_id": bad,
        "repo": "psf/requests",
        "version": "1.1",
        "base_commit": "b" * 40,
        "problem_statement": "p",
        "image": "swebench/x:latest",
        "image_digest": "sha256:" + "a" * 64,
        "test_patch_files": [],
    }
    path = tmp_path / "i.jsonl"
    path.write_text(json.dumps(row) + "\n", encoding="utf-8")

    with pytest.raises(ValueError, match="instance_id"):
        load_instances(path)


def test_a_child_that_ignores_terminate_does_not_outlive_its_group(tmp_path: Path) -> None:
    marker = tmp_path / "stubborn.pid"
    child = (
        "import os, signal, subprocess, sys, time\n"
        "stubborn = ('import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(60)')\n"
        "p = subprocess.Popen([sys.executable, '-c', stubborn])\n"
        f"open({str(marker)!r}, 'w').write(str(p.pid))\n"
        "signal.signal(signal.SIGTERM, lambda *a: os._exit(0))\n"  # the leader exits on TERM
        "time.sleep(60)\n"
    )
    code, timed_out = asyncio.run(
        ad.subprocess_launcher(
            [sys.executable, "-c", child], dict(os.environ), tmp_path, tmp_path / "run.log", 2.0
        )
    )
    assert timed_out is True
    pid = int(marker.read_text())
    time.sleep(0.5)
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


def test_a_docker_command_that_never_returns_is_an_error_not_a_hang(monkeypatch: pytest.MonkeyPatch) -> None:
    def hang(argv: list[str], **kw: Any) -> Any:
        raise subprocess.TimeoutExpired(argv, kw["timeout"])

    monkeypatch.setattr(subprocess, "run", hang)

    with pytest.raises(RuntimeError, match="did not finish"):
        ad.CliDocker().pull("img@sha256:" + "a" * 64)


def test_the_real_docker_wrapper_reads_the_images_base_state(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []

    def fake(argv: list[str], **kw: Any) -> Any:
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, stdout=b"abc123\nCLEAN\n", stderr=b"")

    monkeypatch.setattr(subprocess, "run", fake)

    assert ad.CliDocker().image_base("img@sha256:" + "a" * 64) == ("abc123", True)
    assert "--network" in calls[0] and calls[0][calls[0].index("--network") + 1] == "none"
    assert calls[0][calls[0].index("--platform") + 1] == "linux/amd64"
