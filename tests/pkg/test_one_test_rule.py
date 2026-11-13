"""The one test rule, as a cross-language table (P3, D12; B138, B139).

Every row is a path and the answer the rule must give, with the convention it stands for. A
future language front-end adds its rows here. The first block is the twelve-path sample the
plan measured nine rules on (§1.1): eleven of the twelve split them.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from orchestrator.pkg import test_rule
from orchestrator.pkg.test_rule import is_test_path

# The P0 sample: one path per convention, on which the nine old rules disagreed 11 times in 12.
_SAMPLE = [
    ("src/__tests__/cart.ts", True),  # jest / vitest folder
    ("src/cart.spec.ts", True),
    ("src/cart.test.js", True),
    ("pkg/cart/cart_test.go", True),  # Go keeps tests beside the code
    ("app/src/test/kotlin/CartTest.kt", True),  # Gradle `src/test/`
    ("Shop.Tests/CartTests.cs", True),  # a .NET test project
    ("src/latest.py", False),  # B138: not `Test.<ext>` in any case but PascalCase
    ("t/basic.t", True),  # Perl
    ("src/specs/loader.py", False),  # B139: `specs` is not a test folder
    ("src/main/java/CartTest.java", True),  # PascalCase, outside a test folder
    ("lib/test_client.py", True),  # a *file* named test_*; the function `test_client` is not (D12)
    ("tests/conftest.py", True),
]

_LANGUAGES = [
    # Python
    ("tests/test_cart.py", True),
    ("src/shop/cart_test.py", True),
    ("conftest.py", True),
    ("shop/tests.py", True),  # Django's per-app tests module
    ("src/shop/cart.py", False),
    ("src/flask/testing.py", False),  # flask's public test helpers are product code
    # TypeScript / JavaScript
    ("packages/runtime-core/__tests__/hydration.spec.ts", True),
    ("src/cart.test.tsx", True),
    ("packages/runtime-core/src/renderer.ts", False),
    ("scripts/setup-vitest.ts", False),  # B138: `vitest.` is not `Test.`
    # Go
    ("gin_test.go", True),
    ("gin.go", False),
    # Kotlin / Java
    ("app/src/androidTest/java/CartUiTest.kt", True),
    ("src/test/java/shop/Cart.java", True),
    ("src/test/kotlin/CartSpec.kt", True),  # Kotest
    ("src/main/kotlin/shop/CartSpec.kt", False),  # B144: `Spec` names product classes too (`V1PodSpec`)
    ("src/main/java/io/k8s/models/V1PodSpec.java", False),  # B144
    ("src/test/groovy/shop/CartSpec.groovy", True),  # Spock, by its folder
    ("src/main/kotlin/shop/Cart.kt", False),
    ("src/main/java/shop/Contest.java", False),
    # C#
    ("Shop.UnitTests/Cart.cs", True),  # a PascalCase test project names every file in it
    ("tests/IntegrationTests/CheckoutFlow.cs", True),
    ("src/Shop/Cart.cs", False),
    ("src/Shop/Protests.cs", False),  # lower-case `t` before `ests`: an ordinary word
    # PHP
    ("tests/Unit/CartTest.php", True),
    ("src/CartTest.php", True),
    ("src/Cart.php", False),
    # Perl
    ("t/lib/Helper.pm", False),  # `t/` is a Perl test folder by convention, but not by name
    ("lib/Shop/Cart.pm", False),
    # Ruby
    ("spec/models/cart_spec.rb", True),  # RSpec's own pattern
    ("test/cart_test.rb", True),  # minitest
    ("spec/spec_helper.rb", False),  # B139: `spec/` is not a test folder outside the file pattern
    ("lib/shop/cart.rb", False),
    # Rust: the path half; `#[test]` / `#[cfg(test)] mod` are the front-end's (D16)
    ("tests/api.rs", True),  # a Cargo integration-test target
    ("src/cart_test.rs", True),
    ("src/lib.rs", False),
    # C / C++
    ("test/test_fs.c", True),
    ("src/fs.c", False),
    ("docs/code/proc-streams/test.c", False),  # a docs example program, not `tests.c` or `test_*`
]

_NOT_TESTS = [
    # B138: the old coverage rule matched `test.<ext>` case-insensitively.
    ("src/contest.py", False),
    ("scripts/swebench_localization/selftest.py", False),
    # B139: a `specs` segment and a `*_tests` file name are product modules.
    ("src/orchestrator/intake/specs.py", False),
    ("src/orchestrator/sdlc/own_tests.py", False),
    # Doubtful names are read as product: calling product code a test hides it.
    ("src/orchestrator/sdlc/AgentSpec.py", False),  # `Spec` is a test suffix only on the JVM
    ("src/testing/utils.py", False),
    ("src/test_data/cart.py", False),  # a *folder* called test_data is not a test folder
    ("testimonials/views.py", False),
    ("app/contests/views.py", False),  # a folder named like a PascalCase test project, lower-case
    ("src/latest/cart.py", False),  # B138 again, as a folder
    ("src/api_spec.py", False),  # an OpenAPI spec module: `*_spec` is a test only in Ruby
    ("src/orchestrator/sdlc/testenv.py", False),
    ("", False),
]

_SPELLINGS = [
    ("tests\\test_a.py", True),  # a Windows separator
    ("./tests/x.py", True),
    ("TESTS/x.py", True),  # folder names are compared in any case
    ("src/Tests/x.py", True),
]


@pytest.mark.parametrize(("path", "expected"), _SAMPLE + _LANGUAGES + _NOT_TESTS + _SPELLINGS)
def test_the_one_test_rule(path: str, expected: bool) -> None:
    assert is_test_path(path) is expected


def test_the_rule_imports_nothing_from_spine() -> None:
    """A leaf: `sdlc/`, `knowledge/` and `codereview/` all import it, so it can import none of them."""
    tree = ast.parse(Path(test_rule.__file__).read_text(encoding="utf-8"))
    imported = [
        alias.name if isinstance(node, ast.Import) else (node.module or "")
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    ]
    assert not [name for name in imported if name.startswith("orchestrator")]
