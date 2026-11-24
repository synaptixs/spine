"""Brownfield TypeScript test facts: which runner a repository uses and where its tests live (B177).

A field run on a React Native repository that runs Jest wrote Vitest tests beside the source; the
repository keeps its tests in `__tests__/`. Every TypeScript repository was told the greenfield story.
These tests pin what is read from the repository instead, and that a project Spine scaffolds itself is
unchanged. Fixture repositories are built in `tmp_path`, never on disk under the checkout.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from orchestrator.sdlc.layout import TargetLayout, detect_typescript_test_facts, resolve_layout

NPM_DEFAULT_TEST = 'echo "Error: no test specified" && exit 1'


def _package(root: Path, **fields: object) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "package.json").write_text(json.dumps({"name": "app", **fields}), encoding="utf-8")


def _touch(root: Path, *paths: str) -> None:
    for rel in paths:
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("// test\n", encoding="utf-8")


class TestRunner:
    def test_jest_from_the_test_script(self, tmp_path: Path) -> None:
        _package(tmp_path, scripts={"test": "jest --ci"})
        facts = detect_typescript_test_facts(tmp_path)
        assert facts.runner == "jest"

    def test_vitest_from_the_test_script(self, tmp_path: Path) -> None:
        _package(tmp_path, scripts={"test": "vitest run"}, devDependencies={"jest": "29"})
        assert detect_typescript_test_facts(tmp_path).runner == "vitest"  # the script wins

    def test_react_scripts_test_is_jest(self, tmp_path: Path) -> None:
        _package(tmp_path, scripts={"test": "react-scripts test"})
        assert detect_typescript_test_facts(tmp_path).runner == "jest"

    def test_indirect_script_falls_through_to_the_config_file(self, tmp_path: Path) -> None:
        _package(tmp_path, scripts={"test": "npm run unit"})
        _touch(tmp_path, "jest.config.js")
        assert detect_typescript_test_facts(tmp_path).runner == "jest"

    def test_jest_key_in_package_json(self, tmp_path: Path) -> None:
        _package(tmp_path, jest={"preset": "react-native"})
        assert detect_typescript_test_facts(tmp_path).runner == "jest"

    def test_vitest_config_file(self, tmp_path: Path) -> None:
        _package(tmp_path)
        _touch(tmp_path, "vitest.config.ts")
        assert detect_typescript_test_facts(tmp_path).runner == "vitest"

    def test_both_configs_fall_through_to_dev_dependencies(self, tmp_path: Path) -> None:
        _package(tmp_path, devDependencies={"vitest": "2"})
        _touch(tmp_path, "jest.config.js", "vitest.config.ts")
        assert detect_typescript_test_facts(tmp_path).runner == "vitest"

    def test_dev_dependency_alone(self, tmp_path: Path) -> None:
        _package(tmp_path, devDependencies={"ts-jest": "29", "typescript": "5"})
        assert detect_typescript_test_facts(tmp_path).runner == "jest"

    def test_an_unrecognised_runner_keeps_the_script(self, tmp_path: Path) -> None:
        script = "mocha -r ts-node/register 'test/**/*.ts'"
        _package(tmp_path, scripts={"test": script}, devDependencies={"mocha": "10"})
        facts = detect_typescript_test_facts(tmp_path)
        assert facts.runner == "other"
        assert facts.test_command == script

    def test_npm_default_script_is_no_runner(self, tmp_path: Path) -> None:
        _package(tmp_path, scripts={"test": NPM_DEFAULT_TEST})
        facts = detect_typescript_test_facts(tmp_path)
        assert facts.runner == ""
        assert facts.test_command == ""

    def test_unreadable_package_json_is_no_runner(self, tmp_path: Path) -> None:
        (tmp_path / "package.json").write_text("{not json", encoding="utf-8")
        assert detect_typescript_test_facts(tmp_path).runner == ""


class TestPlacement:
    def test_tests_folder_majority(self, tmp_path: Path) -> None:
        _package(tmp_path)
        _touch(
            tmp_path,
            "src/services/__tests__/authService.test.ts",
            "src/__tests__/App.test.ts",
            "src/util.test.ts",
        )
        facts = detect_typescript_test_facts(tmp_path)
        assert facts.placement == "__tests__"
        assert facts.test_suffix == ".test.ts"
        assert facts.example == "src/__tests__/App.test.ts"  # sorted first in the winning group

    def test_colocated_majority(self, tmp_path: Path) -> None:
        _package(tmp_path)
        _touch(tmp_path, "src/a.test.ts", "src/b/c.test.ts", "src/__tests__/d.test.ts")
        facts = detect_typescript_test_facts(tmp_path)
        assert facts.placement == "colocated"
        assert facts.example == "src/a.test.ts"

    def test_a_tie_goes_to_colocated(self, tmp_path: Path) -> None:
        _package(tmp_path)
        _touch(tmp_path, "src/a.test.ts", "src/__tests__/b.test.ts")
        assert detect_typescript_test_facts(tmp_path).placement == "colocated"

    def test_top_level_test_directory(self, tmp_path: Path) -> None:
        _package(tmp_path)
        _touch(tmp_path, "test/a.test.ts", "test/unit/b.test.ts", "src/c.test.ts")
        facts = detect_typescript_test_facts(tmp_path)
        assert facts.placement == "directory"
        assert facts.test_dir == "test"
        assert facts.example == "test/a.test.ts"

    def test_spec_suffix_majority(self, tmp_path: Path) -> None:
        _package(tmp_path)
        _touch(tmp_path, "src/a.spec.ts", "src/b.spec.ts", "src/c.test.ts")
        assert detect_typescript_test_facts(tmp_path).test_suffix == ".spec.ts"

    def test_tsx_suffix(self, tmp_path: Path) -> None:
        _package(tmp_path)
        _touch(tmp_path, "src/__tests__/App.test.tsx", "src/__tests__/Nav.test.tsx")
        assert detect_typescript_test_facts(tmp_path).test_suffix == ".test.tsx"

    def test_plain_file_inside_tests_folder(self, tmp_path: Path) -> None:
        _package(tmp_path)
        _touch(tmp_path, "src/__tests__/App.ts", "src/__tests__/Nav.ts")
        facts = detect_typescript_test_facts(tmp_path)
        assert facts.placement == "__tests__"
        assert facts.test_suffix == ".ts"

    def test_dependencies_and_dot_directories_are_not_the_repository(self, tmp_path: Path) -> None:
        _package(tmp_path)
        _touch(
            tmp_path,
            *(f"node_modules/lib{i}/__tests__/x.test.ts" for i in range(5)),
            *(f".cache/__tests__/y{i}.test.ts" for i in range(5)),
            "src/a.test.ts",
        )
        facts = detect_typescript_test_facts(tmp_path)
        assert facts.placement == "colocated"
        assert facts.example == "src/a.test.ts"

    def test_non_js_tests_are_ignored(self, tmp_path: Path) -> None:
        _package(tmp_path)
        _touch(tmp_path, "android/app/src/test/FooTest.java", "ios/Tests/BarTests.swift")
        facts = detect_typescript_test_facts(tmp_path)
        assert facts.placement == ""
        assert facts.example == ""

    def test_tooling_tests_in_another_language_do_not_outvote(self, tmp_path: Path) -> None:
        _package(tmp_path)
        _touch(tmp_path, "tests/test_release.py", "tests/test_lint.py", "src/a.test.ts")
        facts = detect_typescript_test_facts(tmp_path)
        assert facts.placement == "colocated"
        assert facts.example == "src/a.test.ts"

    def test_no_tests_at_all(self, tmp_path: Path) -> None:
        _package(tmp_path)
        facts = detect_typescript_test_facts(tmp_path)
        assert (facts.placement, facts.test_suffix, facts.example) == ("", "", "")


class TestModuleResolution:
    @pytest.mark.parametrize(
        ("compiler_options", "expected"),
        [
            ('"moduleResolution": "NodeNext"', "nodenext"),
            ('"module": "Node16"', "node16"),
            ('"module": "NodeNext", "moduleResolution": "Bundler"', "bundler"),
            ('"moduleResolution": "node"', "node"),
            ('"strict": true', ""),
        ],
    )
    def test_read_from_tsconfig(self, tmp_path: Path, compiler_options: str, expected: str) -> None:
        _package(tmp_path)
        # tsconfig is JSONC: comments and trailing commas are legal there.
        (tmp_path / "tsconfig.json").write_text(
            '{\n  // project settings\n  "compilerOptions": { ' + compiler_options + ", /* inline */ },\n}\n",
            encoding="utf-8",
        )
        assert detect_typescript_test_facts(tmp_path).module_resolution == expected

    def test_no_tsconfig(self, tmp_path: Path) -> None:
        _package(tmp_path)
        assert detect_typescript_test_facts(tmp_path).module_resolution == ""

    def test_a_url_in_a_string_is_not_a_comment(self, tmp_path: Path) -> None:
        _package(tmp_path)
        (tmp_path / "tsconfig.json").write_text(
            '{"$schema": "https://json.schemastore.org/tsconfig", '
            '"compilerOptions": {"moduleResolution": "bundler"}}',
            encoding="utf-8",
        )
        assert detect_typescript_test_facts(tmp_path).module_resolution == "bundler"


class TestLayoutCarriesTheFacts:
    """Through `resolve_layout`, which reaches `_resolve_typescript_layout` by its string name in
    `toolchains.py` — the path the graph cannot trace, so this is the test that covers it."""

    def test_existing_jest_repository(self, tmp_path: Path) -> None:
        _package(tmp_path, scripts={"test": "jest"})
        (tmp_path / "tsconfig.json").write_text('{"compilerOptions": {"moduleResolution": "node"}}')
        _touch(tmp_path, "src/services/__tests__/authService.test.ts", "src/services/authService.ts")
        layout = resolve_layout(tmp_path, mode="auto", language="typescript")
        assert layout.mode == "existing"
        assert layout.test_library == "jest"
        assert layout.test_placement == "__tests__"
        assert layout.test_suffix == ".test.ts"
        assert layout.test_example == "src/services/__tests__/authService.test.ts"
        assert layout.module_resolution == "node"

    def test_other_runner_keeps_its_command(self, tmp_path: Path) -> None:
        _package(tmp_path, scripts={"test": "node --test"})
        layout = resolve_layout(tmp_path, mode="existing", language="typescript")
        assert layout.test_library == "other"
        assert layout.test_command == "node --test"

    def test_nested_project_paths_are_repository_relative(self, tmp_path: Path) -> None:
        app = tmp_path / "mobile"
        _package(app, scripts={"test": "jest"})
        _touch(app, "src/__tests__/App.test.tsx")
        layout = resolve_layout(tmp_path, mode="existing", language="typescript")
        assert layout.project_dir == "mobile"
        assert layout.test_example == "mobile/src/__tests__/App.test.tsx"
        assert layout.test_dir == ""

    def test_nested_top_level_test_directory(self, tmp_path: Path) -> None:
        app = tmp_path / "svc"
        _package(app, scripts={"test": "jest"})
        _touch(app, "test/a.test.ts")
        layout = resolve_layout(tmp_path, mode="existing", language="typescript")
        assert layout.test_placement == "directory"
        assert layout.test_dir == "svc/test"

    def test_a_new_project_is_unchanged(self, tmp_path: Path) -> None:
        layout = resolve_layout(tmp_path, mode="new", language="typescript", repo="https://x/widgets")
        assert layout == TargetLayout(
            "widgets", "src", "src", True, "new", language="typescript", build_tool="npm"
        )

    def test_new_mode_ignores_an_existing_package(self, tmp_path: Path) -> None:
        _package(tmp_path, scripts={"test": "jest"})
        _touch(tmp_path, "src/__tests__/a.test.ts")
        layout = resolve_layout(tmp_path, mode="new", language="typescript", repo="w")
        assert (layout.test_library, layout.test_placement, layout.test_example) == ("", "", "")
