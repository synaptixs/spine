"""The overlay wrapper: this run's changes, laid over a pristine instance image, then the tests.

Measured on a scikit-learn instance: a clean worktree mounted over ``/testbed`` fails at collection,
because the compiled extensions are untracked build output that only the image has. So the image's
own tree is kept and only what the run changed is streamed in. These tests use a real temporary git
repository for what changed and a fake ``docker`` for the container.
"""

from __future__ import annotations

import io
import subprocess
import tarfile
from pathlib import Path

import pytest

from orchestrator.evals.swebench import exec as sx


def _git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", "user.email=t@e", "-c", "user.name=t", *args],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout


def _repo(tmp_path: Path) -> tuple[Path, str]:
    _git(tmp_path, "init", "-q")
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "mod.py").write_text("X = 1\n", encoding="utf-8")
    (tmp_path / "pkg" / "gone.py").write_text("Y = 1\n", encoding="utf-8")
    script = tmp_path / "run.sh"
    script.write_text("#!/bin/sh\n", encoding="utf-8")
    script.chmod(0o755)
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "base")
    return tmp_path, _git(tmp_path, "rev-parse", "HEAD").strip()


def _members(data: bytes) -> dict[str, bytes]:
    found: dict[str, bytes] = {}
    with tarfile.open(fileobj=io.BytesIO(data)) as tar:
        for member in tar:
            handle = tar.extractfile(member)
            found[member.name] = handle.read() if handle is not None else b""
    return found


# --- what changed -------------------------------------------------------------------------


def test_the_overlay_carries_modified_and_new_files_and_names_the_deleted(tmp_path: Path) -> None:
    root, base = _repo(tmp_path)
    (root / "pkg" / "mod.py").write_text("X = 2\n", encoding="utf-8")  # modified
    (root / "pkg" / "new.py").write_text("Z = 1\n", encoding="utf-8")  # untracked
    (root / "pkg" / "gone.py").unlink()  # deleted

    members = _members(sx.overlay(root, base))

    assert members["pkg/mod.py"] == b"X = 2\n"
    assert members["pkg/new.py"] == b"Z = 1\n"
    assert "pkg/gone.py" not in members
    assert members[sx.DELETED_MANIFEST].split(b"\0") == [b"pkg/gone.py", b""]


def test_what_was_committed_after_the_base_is_still_carried(tmp_path: Path) -> None:
    """`autorun` commits the build; the overlay is 'since the base', not 'since HEAD'."""
    root, base = _repo(tmp_path)
    (root / "pkg" / "mod.py").write_text("X = 3\n", encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "the build")

    assert _members(sx.overlay(root, base))["pkg/mod.py"] == b"X = 3\n"


def test_spines_own_bookkeeping_is_not_overlaid(tmp_path: Path) -> None:
    root, base = _repo(tmp_path)
    (root / ".spine").mkdir()
    (root / ".spine" / "plan.md").write_text("x", encoding="utf-8")
    (root / "BACKLOG.md").write_text("x", encoding="utf-8")
    (root / "pkg" / "__pycache__").mkdir()
    (root / "pkg" / "__pycache__" / "mod.cpython-36.pyc").write_bytes(b"\0")

    assert set(_members(sx.overlay(root, base))) == {sx.DELETED_MANIFEST}


def test_no_change_is_an_overlay_that_changes_nothing(tmp_path: Path) -> None:
    root, base = _repo(tmp_path)
    assert set(_members(sx.overlay(root, base))) == {sx.DELETED_MANIFEST}


def test_the_executable_bit_and_odd_names_survive(tmp_path: Path) -> None:
    root, base = _repo(tmp_path)
    (root / "run.sh").write_text("#!/bin/sh\necho hi\n", encoding="utf-8")
    odd = root / "tests" / "test a;b $(x) -q.py"
    odd.parent.mkdir()
    odd.write_text("", encoding="utf-8")

    with tarfile.open(fileobj=io.BytesIO(sx.overlay(root, base))) as tar:
        assert tar.getmember("run.sh").mode & 0o111
        assert "tests/test a;b $(x) -q.py" in tar.getnames()


def test_a_base_that_does_not_exist_is_an_error(tmp_path: Path) -> None:
    root, _ = _repo(tmp_path)
    with pytest.raises(RuntimeError, match="changed files"):
        sx.overlay(root, "deadbeef" * 5)


# --- what runs ----------------------------------------------------------------------------


def test_the_kind_follows_the_repository() -> None:
    assert sx.kind_for("django/django") == "django"
    assert sx.kind_for("sympy/sympy") == "sympy"
    assert sx.kind_for("scikit-learn/scikit-learn") == "pytest"
    assert sx.kind_for("psf/requests") == "pytest"


def test_django_takes_labels_not_paths() -> None:
    assert sx.args_for("django", ["tests/validators/tests.py", "tests/admin_views/test_forms.py"]) == [
        "validators.tests",
        "admin_views.test_forms",
    ]
    assert sx.args_for("pytest", ["tests/test_a.py"]) == ["tests/test_a.py"]
    assert sx.args_for("sympy", ["sympy/core/tests/test_basic.py"]) == ["sympy/core/tests/test_basic.py"]


def test_the_container_command_never_contains_a_file_name() -> None:
    """File names are argv, not text pasted into a shell script."""
    hostile = ["tests/test_$(touch pwned).py", "tests/test a;b.py", "-x_test.py"]
    argv = sx.docker_argv("img@sha256:" + "a" * 64, "pytest", hostile, timeout_s=300)

    script = argv[argv.index("bash") + 2]
    assert not any(name in script for name in hostile)
    assert argv[-len(hostile) :] == hostile
    assert "--" in script  # an argument starting with "-" is a file, not an option


def test_the_container_is_offline_pinned_and_interactive() -> None:
    argv = sx.docker_argv("img@sha256:" + "a" * 64, "django", ["validators.tests"], timeout_s=300)

    assert argv[:2] == ["docker", "run"]
    assert "-i" in argv and "--rm" in argv
    assert argv[argv.index("--network") + 1] == "none"
    assert argv[argv.index("--platform") + 1] == "linux/amd64"
    assert "img@sha256:" + "a" * 64 in argv
    assert "LC_ALL=C.UTF-8" in argv


def test_the_time_limit_is_enforced_inside_the_container() -> None:
    """Killing the docker client does not stop the process in the container (found in review)."""
    script = sx.SCRIPT
    assert "timeout -k" in script


class _Fake:
    def __init__(self, rc: int = 0, out: str = "ok") -> None:
        self.rc, self.out = rc, out
        self.calls: list[tuple[list[str], bytes, float]] = []

    def __call__(self, argv: list[str], data: bytes, timeout: float) -> tuple[int, str]:
        self.calls.append((argv, data, timeout))
        return self.rc, self.out


def test_run_streams_the_overlay_in_and_returns_the_containers_code(tmp_path: Path) -> None:
    root, base = _repo(tmp_path)
    (root / "pkg" / "mod.py").write_text("X = 9\n", encoding="utf-8")
    fake = _Fake(rc=3, out="1 failed")

    rc, out = sx.run("img@sha256:" + "a" * 64, base, "pytest", ["tests/test_a.py"], cwd=root, runner=fake)

    assert (rc, out) == (3, "1 failed")
    argv, data, timeout = fake.calls[0]
    assert _members(data)["pkg/mod.py"] == b"X = 9\n"
    assert argv[-1] == "tests/test_a.py"
    assert timeout > 600  # the outer clock is looser than the one inside the container


def test_a_hung_container_is_reported_as_a_timeout(tmp_path: Path) -> None:
    root, base = _repo(tmp_path)

    def hung(argv: list[str], data: bytes, timeout: float) -> tuple[int, str]:
        raise subprocess.TimeoutExpired(argv, timeout)

    rc, out = sx.run("img@sha256:" + "a" * 64, base, "pytest", ["t.py"], cwd=root, runner=hung)

    assert rc == 124 and "timed out" in out


def test_main_runs_in_the_current_directory_and_exits_with_the_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    root, base = _repo(tmp_path)
    monkeypatch.chdir(root)
    seen: dict[str, object] = {}

    def fake_run(image: str, b: str, kind: str, files: list[str], **kw: object) -> tuple[int, str]:
        seen.update(image=image, base=b, kind=kind, files=files, cwd=kw["cwd"])
        return 7, "output text\n"

    monkeypatch.setattr(sx, "run", fake_run)
    code = sx.main(
        [
            "--image",
            "img@sha256:" + "a" * 64,
            "--base",
            base,
            "--kind",
            "django",
            "--",
            "tests/validators/tests.py",
        ]
    )

    assert code == 7
    assert seen["files"] == ["tests/validators/tests.py"] and seen["kind"] == "django"
    assert Path(str(seen["cwd"])).resolve() == root.resolve()
    assert "output text" in capsys.readouterr().out


# --- the in-container script, run for real under bash with a fake image -------------------


def _image(tmp_path: Path) -> dict[str, str]:
    """A stand-in for the instance image: /testbed, a conda `activate`, `timeout`, and `python`.

    The fake `activate` fails if it is handed any argument — which is what real conda does, and
    what `source` does by default: it passes the caller's positional parameters through. That bug
    shipped in the first version of the script and was found only by running it on a real image.
    """
    testbed = tmp_path / "testbed"
    (testbed / "tests").mkdir(parents=True)
    (testbed / "pkg").mkdir()
    (testbed / "pkg" / "old.py").write_text("old\n", encoding="utf-8")
    runtests = testbed / "tests" / "runtests.py"
    runtests.write_text('#!/bin/bash\necho "RUNTESTS $*"\n', encoding="utf-8")
    runtests.chmod(0o755)
    (testbed / "bin").mkdir()
    (testbed / "bin" / "test").write_text("", encoding="utf-8")
    activate = tmp_path / "activate"
    activate.write_text(
        'if [ "$#" -ne 0 ]; then echo "activate was handed arguments: $*" >&2; return 1; fi\n'
        "conda() { return 0; }\n",
        encoding="utf-8",
    )
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, body in {
        "timeout": 'shift 2; shift; exec "$@"\n',  # drop "-k 5 <limit>"
        "python": 'echo "PYTHON $*"\n',
    }.items():
        (bin_dir / name).write_text("#!/bin/bash\n" + body, encoding="utf-8")
        (bin_dir / name).chmod(0o755)
    import os

    return {
        **os.environ,
        "SPINE_ACTIVATE": str(activate),
        "SPINE_TESTBED": str(testbed),
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
    }


def _overlay_tar(files: dict[str, str], deleted: list[str]) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        for name, text in files.items():
            data = text.encode()
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
        manifest = b"".join(d.encode() + b"\0" for d in deleted)
        info = tarfile.TarInfo(sx.DELETED_MANIFEST)
        info.size = len(manifest)
        tar.addfile(info, io.BytesIO(manifest))
    return buf.getvalue()


def _run_script(env: dict[str, str], stdin: bytes, *args: str) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        ["bash", "-c", sx.SCRIPT, "spine-exec", *args], input=stdin, capture_output=True, env=env, timeout=60
    )


def test_the_script_does_not_hand_its_arguments_to_conda(tmp_path: Path) -> None:
    done = _run_script(
        _image(tmp_path), _overlay_tar({}, []), "300", "django", "str.tests", "validators.tests"
    )

    assert done.returncode == 0, done.stderr.decode()
    assert b"RUNTESTS --settings=test_sqlite --parallel 1 str.tests validators.tests" in done.stdout


def test_the_script_lays_the_overlay_over_the_tree_and_applies_deletions(tmp_path: Path) -> None:
    env = _image(tmp_path)
    testbed = Path(env["SPINE_TESTBED"])
    tar = _overlay_tar({"pkg/new.py": "new\n"}, ["pkg/old.py"])

    done = _run_script(env, tar, "300", "django", "x.tests")

    assert done.returncode == 0, done.stderr.decode()
    assert (testbed / "pkg" / "new.py").read_text() == "new\n"
    assert not (testbed / "pkg" / "old.py").exists()
    assert not (testbed / sx.DELETED_MANIFEST).exists()


def test_the_other_runners_get_their_files_after_a_double_dash(tmp_path: Path) -> None:
    env = _image(tmp_path)

    pytest_run = _run_script(env, _overlay_tar({}, []), "300", "pytest", "-x_test.py", "tests/test_a.py")
    sympy_run = _run_script(env, _overlay_tar({}, []), "300", "sympy", "sympy/core/tests/test_basic.py")

    assert b"PYTHON -m pytest -q -rfE -p no:cacheprovider -- -x_test.py tests/test_a.py" in pytest_run.stdout
    assert b"PYTHON bin/test -C --verbose -- sympy/core/tests/test_basic.py" in sympy_run.stdout


def test_a_script_with_no_files_still_runs(tmp_path: Path) -> None:
    done = _run_script(_image(tmp_path), _overlay_tar({}, []), "300", "pytest")
    assert done.returncode == 0 and b"PYTHON -m pytest" in done.stdout


def test_an_environment_that_will_not_activate_is_exit_97_and_a_bad_overlay_is_98(tmp_path: Path) -> None:
    env = _image(tmp_path)
    bad_activate = tmp_path / "bad"
    bad_activate.write_text("return 1\n", encoding="utf-8")

    assert _run_script({**env, "SPINE_ACTIVATE": str(bad_activate)}, b"", "300", "pytest").returncode == 97
    assert _run_script(env, b"this is not a tar", "300", "pytest").returncode == 98


def test_a_renamed_file_removes_its_old_path_in_the_container(tmp_path: Path) -> None:
    """After `git mv` the old file stayed in the container beside the new one."""
    root, base = _repo(tmp_path)
    _git(root, "mv", "pkg/mod.py", "pkg/moved.py")

    members = _members(sx.overlay(root, base))

    assert "pkg/moved.py" in members
    assert members[sx.DELETED_MANIFEST].split(b"\0") == [b"pkg/mod.py", b""]
