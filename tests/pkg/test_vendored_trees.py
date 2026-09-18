"""CB-686: a React Native checkout's `ios/Pods` and the symlinks CocoaPods leaves in it.

Two defects, one fixture. `ios/Pods` was walked, so boost, glog and every React header became
the repository's own symbols. And `Pods/Headers/Public/*` are symlinks into `node_modules/` —
an ignored directory — which `path.resolve()` followed and relabelled, so the ignored tree came
back into the graph one header at a time.
"""

from __future__ import annotations

import os
from pathlib import Path

from orchestrator.pkg.extractor import DEFAULT_IGNORE_DIRS, RepoCodeExtractor


def _rn_checkout(root: Path) -> None:
    """The CB-686 shape: app sources, a vendored `ios/Pods`, a header symlinked from it into
    `node_modules`, and a stray build script under Pods."""
    (root / "src" / "screens").mkdir(parents=True)
    (root / "src" / "screens" / "deleteAccount.tsx").write_text(
        "export function DeleteAccountScreen(): number { return 1; }\n", encoding="utf-8"
    )
    pods = root / "ios" / "Pods"
    (pods / "glog" / "src").mkdir(parents=True)
    (pods / "glog" / "src" / "logging.cc").write_text(
        "namespace google { int Reason(int r) { return r; } }\n"
    )
    (pods / "hermes-engine" / "utils").mkdir(parents=True)
    (pods / "hermes-engine" / "utils" / "build.py").write_text("def build():\n    return 1\n")
    header = root / "node_modules" / "react-native" / "React" / "FBReactNativeSpec.h"
    header.parent.mkdir(parents=True)
    header.write_text("struct EXTaskLaunchReason { int reason; };\n")
    public = pods / "Headers" / "Public" / "React-Core"
    public.mkdir(parents=True)
    os.symlink(os.path.relpath(header, public), public / "FBReactNativeSpec.h")


def _files(root: Path) -> set[str]:
    return {n.provenance.file for n in RepoCodeExtractor().extract(root).nodes if n.provenance}


def test_pods_is_ignored_and_the_symlinked_header_does_not_bring_node_modules_back(tmp_path: Path) -> None:
    _rn_checkout(tmp_path)
    files = _files(tmp_path)
    assert "src/screens/deleteAccount.tsx" in files
    assert not [f for f in files if f.startswith(("ios/Pods/", "node_modules/"))], sorted(files)
    assert "Pods" in DEFAULT_IGNORE_DIRS


def test_an_in_tree_symlink_keeps_its_own_path(tmp_path: Path) -> None:
    """A link inside the tree is a real path a reader can open; it is admitted under its own
    name, never relabelled as its target."""
    (tmp_path / "lib").mkdir()
    (tmp_path / "lib" / "real.py").write_text("def f() -> int:\n    return 1\n", encoding="utf-8")
    (tmp_path / "app").mkdir()
    os.symlink("../lib/real.py", tmp_path / "app" / "linked.py")
    files = _files(tmp_path)
    assert {"lib/real.py", "app/linked.py"} <= files


def test_a_symlink_that_leaves_the_root_or_points_into_an_ignored_directory_is_skipped(
    tmp_path: Path,
) -> None:
    outside = tmp_path.parent / f"{tmp_path.name}-outside"
    outside.mkdir(exist_ok=True)
    (outside / "secret.py").write_text("SECRET = 1\n", encoding="utf-8")
    (tmp_path / "src").mkdir()
    os.symlink(outside / "secret.py", tmp_path / "src" / "escape.py")
    (tmp_path / "node_modules" / "pkg").mkdir(parents=True)
    (tmp_path / "node_modules" / "pkg" / "index.ts").write_text("export const x = 1;\n", encoding="utf-8")
    os.symlink("../node_modules/pkg/index.ts", tmp_path / "src" / "vendored.ts")
    os.symlink("nowhere.py", tmp_path / "src" / "dangling.py")
    files = _files(tmp_path)
    assert not [f for f in files if f in {"src/escape.py", "src/vendored.ts", "src/dangling.py"}], sorted(
        files
    )
    assert not [f for f in files if f.startswith("node_modules/")]
