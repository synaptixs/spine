"""Per-language scope walking — what binds a name, and every reason *not* to say it does.

The risk is asymmetric in the same way as the Python detector's, and worse here: this module
exists to accuse the front-ends of fabricating edges, so a walker that over-binds manufactures
findings that are themselves fiction. Two such bugs were found by hand-checking the first
measurement run — a TypeScript destructuring default read as a binding (3 false findings on
vue/core) and Go's short-variable-declaration scope taken to cover its own right-hand side
(5 on grpc-go). Both have a test below, and each was verified against the language spec, not
against what the walker happened to do.
"""

from __future__ import annotations

import pytest

from orchestrator.pkg.scope import NOT_APPLICABLE, WALKERS, scopes_for_source

_SUFFIX = {"typescript": ".ts", "go": ".go", "csharp": ".cs", "cpp": ".cpp", "c": ".c"}
_EXTRA = {
    "typescript": "tree_sitter_typescript",
    "go": "tree_sitter_go",
    "csharp": "tree_sitter_c_sharp",
    "cpp": "tree_sitter_cpp",
    "c": "tree_sitter_c",
}


def _shadowed(language: str, source: str, line: int) -> frozenset[str]:
    pytest.importorskip(_EXTRA[language], reason=f"install the '{language}' extra")
    fs = scopes_for_source(source.encode(), language, _SUFFIX[language])
    return fs.shadowed(line)


def _bare(language: str, source: str, line: int) -> frozenset[str]:
    pytest.importorskip(_EXTRA[language], reason=f"install the '{language}' extra")
    fs = scopes_for_source(source.encode(), language, _SUFFIX[language])
    return fs.bare_call_names(line)


# ---- the shape the whole programme is about --------------------------------


def test_typescript_parameter_shadows_a_module_function() -> None:
    src = "export function send(x: string): void {}\nexport function outer(send: F): void { send('hi'); }\n"
    assert "send" in _shadowed("typescript", src, 2)


def test_go_parameter_shadows_a_package_function() -> None:
    src = 'package main\n\nfunc Send(x string) {}\nfunc Outer(Send func(string)) { Send("hi") }\n'
    assert "Send" in _shadowed("go", src, 4)


def test_csharp_parameter_shadows_a_sibling_method() -> None:
    src = 'class A {\n  void Send(string x) {}\n  void Outer(Action<string> Send) { Send("hi"); }\n}\n'
    assert "Send" in _shadowed("csharp", src, 3)


def test_cpp_function_pointer_parameter_shadows_a_free_function() -> None:
    src = 'void send(const char *x) {}\nvoid outer(void (*send)(const char *)) { send("hi"); }\n'
    assert "send" in _shadowed("cpp", src, 2)


# ---- the file scope is not a shadow (AC: honest calls stay honest) ---------


@pytest.mark.parametrize(
    ("language", "src", "line", "name"),
    [
        ("typescript", "export function send(): void {}\nfunction honest() { send(); }\n", 2, "send"),
        ("go", "package main\nfunc Send() {}\nfunc Honest() { Send() }\n", 3, "Send"),
        ("csharp", "class A {\n  void Send() {}\n  void Honest() { Send(); }\n}\n", 3, "Send"),
        ("cpp", "void send() {}\nvoid honest() { send(); }\n", 2, "send"),
        ("c", "void send(void) {}\nvoid honest(void) { send(); }\n", 2, "send"),
    ],
)
def test_a_file_level_definition_never_shadows_itself(language: str, src: str, line: int, name: str) -> None:
    """The declaration the front-end resolved to. Counting it would flag every correct call."""
    assert name not in _shadowed(language, src, line)


# ---- binding forms, per language ------------------------------------------


def test_typescript_binding_forms() -> None:
    src = (
        "function f(xs: any, {a, b}: O, ...rest: string[]) {\n"
        "  const local = () => {};\n"
        "  let v = 1;\n"
        "  for (const item of rest) {}\n"
        "  try {} catch (err) {}\n"
        "  target();\n"
        "}\n"
    )
    assert {"xs", "a", "b", "rest", "local", "v", "item", "err"} <= _shadowed("typescript", src, 6)


def test_go_binding_forms() -> None:
    src = (
        "package main\n"
        "func f(p func(), a, b int) {\n"
        "\tlocal := func() {}\n"
        "\tvar v, w = 1, 2\n"
        "\tconst k = 3\n"
        "\tfor _, item := range xs {\n"
        "\t}\n"
        "\ttarget()\n"
        "}\n"
    )
    assert {"p", "a", "b", "local", "v", "w", "k", "item"} <= _shadowed("go", src, 8)


def test_csharp_binding_forms() -> None:
    src = (
        "class A {\n"
        "  void f(Action p, int n) {\n"
        "    Action local = () => {};\n"
        "    var v = 1;\n"
        "    foreach (var item in xs) {}\n"
        "    try {} catch (Exception err) {}\n"
        "    target();\n"
        "  }\n"
        "}\n"
    )
    assert {"p", "n", "local", "v", "item", "err"} <= _shadowed("csharp", src, 7)


def test_cpp_binding_forms() -> None:
    src = (
        "void f(void (*p)(), int n) {\n"
        "  auto lam = [](int q){ return q; };\n"
        "  int a = 1, b = 2;\n"
        "  for (auto &item : xs) {}\n"
        "  target();\n"
        "}\n"
    )
    assert {"p", "n", "lam", "a", "b", "item"} <= _shadowed("cpp", src, 5)


def test_c_binding_forms() -> None:
    src = "void f(void (*p)(void), int n) {\n  void (*local)(void) = 0;\n  target();\n}\n"
    assert {"p", "n", "local"} <= _shadowed("c", src, 3)


# ---- reasons NOT to flag ---------------------------------------------------


def test_typescript_destructuring_default_is_not_a_binding() -> None:
    """`{ arg: slotName = makeDefault() }` binds `slotName`. It does not bind `makeDefault`.

    Reading the default expression as a pattern made every later call to that import look
    shadowed — 3 of the 4 findings on vue/core, all fiction.
    """
    src = (
        "function f(xs: any) {\n  const { arg: slotName = makeDefault('x') } = xs;\n  makeDefault('y');\n}\n"
    )
    shadowed = _shadowed("typescript", src, 3)
    assert "slotName" in shadowed
    assert "makeDefault" not in shadowed


def test_typescript_array_destructuring_default_is_not_a_binding() -> None:
    src = "function f(xs: any) {\n  const [a, b = fallback()] = xs;\n  fallback();\n}\n"
    shadowed = _shadowed("typescript", src, 3)
    assert {"a", "b"} <= shadowed
    assert "fallback" not in shadowed


def test_typescript_a_function_types_own_parameters_bind_nothing() -> None:
    """`cb: (nested: string) => void` declares `nested` inside a *type*. Nothing binds it."""
    src = "function f(cb: (nested: string) => void) {\n  nested('boom');\n}\n"
    assert "nested" not in _shadowed("typescript", src, 2)


def test_go_short_declaration_is_not_in_scope_on_its_own_line() -> None:
    """Go §Declarations: the scope of a `:=` identifier begins at the end of the statement.

    `cmd := cmd(path, logger, args)` therefore calls the package-level `cmd` — idiomatic, and
    reported as invention until the rule was applied (5 false findings on grpc-go).
    """
    src = "package main\nfunc f() {\n\tcmd := cmd(path)\n\t_ = cmd\n}\n"
    assert "cmd" not in _shadowed("go", src, 3)


def test_go_short_declaration_shadows_every_line_below_it() -> None:
    src = "package main\nfunc f() {\n\tcmd := cmd(path)\n\tcmd()\n}\n"
    assert "cmd" in _shadowed("go", src, 4)


def test_cpp_declaration_is_not_in_scope_on_its_own_line() -> None:
    src = "void f() {\n  auto helper = helper();\n  helper();\n}\n"
    assert "helper" not in _shadowed("cpp", src, 2)
    assert "helper" in _shadowed("cpp", src, 3)


def test_cpp_function_pointer_parameters_own_names_bind_nothing() -> None:
    """In `void (*send)(const char *x)`, `x` names a parameter of the pointed-to function."""
    src = "void f(void (*send)(const char *x)) {\n  x();\n}\n"
    shadowed = _shadowed("cpp", src, 2)
    assert "send" in shadowed
    assert "x" not in shadowed


# ---- bare calls: the only shape a shadow can reach -------------------------


def test_a_member_call_is_not_a_bare_call() -> None:
    src = "class A {\n  m() { this.send(); other.send(); send(); }\n}\n"
    assert _bare("typescript", src, 2) == frozenset({"send"})


def test_bare_calls_are_recorded_with_their_line() -> None:
    src = "package main\nfunc f() {\n\talpha()\n\tbeta()\n}\n"
    assert _bare("go", src, 3) == frozenset({"alpha"})
    assert _bare("go", src, 4) == frozenset({"beta"})


# ---- the roster itself -----------------------------------------------------


def test_java_and_sql_are_excluded_with_a_reason_not_a_walker() -> None:
    """Silence is the failure mode this project keeps having — so absence carries a why."""
    for language in ("java", "sql"):
        assert language not in WALKERS
        assert NOT_APPLICABLE[language]


def test_every_walker_declares_both_halves_of_the_contract() -> None:
    for language, walker in WALKERS.items():
        assert walker.scope_nodes, language
        assert walker.call_nodes, language
