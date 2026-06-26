"""Block A.4 unit tests: diff parsing + the three code-aware verifiers."""

from __future__ import annotations

from orchestrator.codereview.diff_utils import iter_added_lines
from orchestrator.codereview.github_client import ChangedFile, PRDiff
from orchestrator.codereview.verifiers import (
    SecretsVerifier,
    SecurityVerifier,
    Severity,
    StyleVerifier,
    default_code_verifiers,
    run_verifiers,
    worst_severity,
)


def _diff(filename: str, patch: str, *, status: str = "modified") -> PRDiff:
    return PRDiff(
        repo="a/b",
        pr_number=1,
        head_sha="sha",
        files=(ChangedFile(filename=filename, status=status, additions=1, deletions=0, patch=patch),),
    )


# ---- diff parser ----------------------------------------------------------


def test_iter_added_lines_tracks_new_file_line_numbers() -> None:
    patch = "@@ -1,2 +1,3 @@\n context\n+added one\n+added two\n more context"
    assert list(iter_added_lines(patch)) == [(2, "added one"), (3, "added two")]


def test_iter_added_lines_removed_lines_do_not_advance_counter() -> None:
    patch = "@@ -1,3 +1,2 @@\n keep\n-gone\n+replacement"
    # new-file: line 1 = keep, line 2 = replacement (removed line doesn't count)
    assert list(iter_added_lines(patch)) == [(2, "replacement")]


def test_iter_added_lines_handles_multiple_hunks() -> None:
    patch = "@@ -1 +1 @@\n+first\n@@ -10,0 +20,1 @@\n+second"
    assert list(iter_added_lines(patch)) == [(1, "first"), (20, "second")]


# ---- SecretsVerifier ------------------------------------------------------


def test_secrets_flags_aws_key_as_blocker() -> None:
    patch = '@@ -0,0 +1 @@\n+AWS_KEY = "AKIAIOSFODNN7EXAMPLE"'
    findings = SecretsVerifier().scan(_diff("config.py", patch))
    assert any(f.rule == "aws_access_key" and f.severity is Severity.BLOCKER for f in findings)
    assert findings[0].line == 1
    assert findings[0].path == "config.py"


def test_secrets_flags_generic_assignment_and_private_key() -> None:
    patch = '@@ -0,0 +2 @@\n+password = "hunter2hunter2"\n+-----BEGIN RSA PRIVATE KEY-----'
    rules = {f.rule for f in SecretsVerifier().scan(_diff("secrets.py", patch))}
    assert "generic_secret" in rules
    assert "private_key" in rules


def test_secrets_scans_non_code_files_too() -> None:
    # creds in a .env / yaml must still be caught
    patch = '@@ -0,0 +1 @@\n+api_key: "sk-livesecret-1234567890"'
    findings = SecretsVerifier().scan(_diff("deploy.yaml", patch))
    assert any(f.rule == "generic_secret" for f in findings)


def test_secrets_ignores_removed_files() -> None:
    patch = '@@ -1 +0,0 @@\n-token = "ghp_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"'
    assert SecretsVerifier().scan(_diff("old.py", patch, status="removed")) == []


def test_secrets_suppresses_placeholder_values() -> None:
    placeholders = [
        'password = "dummy-password-123"',
        'api_key = "your-api-key-here"',
        'secret = "<insert-secret>"',
        'token = "${VAULT_TOKEN}"',
        'passwd = "changeme-now!"',
        'api_key = "test-fixture-key"',
        'secret = "testsecret123"',
    ]
    patch = "@@ -0,0 +7 @@\n" + "\n".join(f"+{line}" for line in placeholders)
    assert SecretsVerifier().scan(_diff("config.py", patch)) == []


def test_secrets_placeholder_test_needs_boundary() -> None:
    # "latest" contains "test" mid-word — must NOT be treated as a placeholder
    patch = '@@ -0,0 +1 @@\n+api_key = "sk-latest-9f8e7d6c5b"'
    findings = SecretsVerifier().scan(_diff("config.py", patch))
    assert any(f.rule == "generic_secret" and f.severity is Severity.BLOCKER for f in findings)


def test_secrets_generic_downgrades_to_warning_in_test_files() -> None:
    patch = '@@ -0,0 +1 @@\n+password = "hunter2hunter2"'
    findings = SecretsVerifier().scan(_diff("tests/conftest.py", patch))
    assert [f.severity for f in findings if f.rule == "generic_secret"] == [Severity.WARNING]


def test_secrets_structured_tokens_still_block_in_test_files() -> None:
    # A real token format in a test file is still a live credential
    patch = '@@ -0,0 +1 @@\n+token = "ghp_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"'
    findings = SecretsVerifier().scan(_diff("tests/test_auth.py", patch))
    assert any(f.rule == "github_token" and f.severity is Severity.BLOCKER for f in findings)


# ---- SecurityVerifier -----------------------------------------------------


def test_security_flags_eval_and_shell_true_as_blockers() -> None:
    patch = "@@ -0,0 +2 @@\n+result = eval(user_input)\n+subprocess.run(cmd, shell=True)"
    findings = SecurityVerifier().scan(_diff("run.py", patch))
    rules = {f.rule: f.severity for f in findings}
    assert rules["eval_exec"] is Severity.BLOCKER
    assert rules["shell_true"] is Severity.BLOCKER


def test_security_flags_heuristics_as_warnings() -> None:
    patch = (
        "@@ -0,0 +3 @@\n"
        "+data = pickle.loads(blob)\n"
        "+requests.get(url, verify=False)\n"
        '+cursor.execute(f"select * from t where id={x}")'
    )
    findings = SecurityVerifier().scan(_diff("svc.py", patch))
    rules = {f.rule: f.severity for f in findings}
    assert rules["pickle_loads"] is Severity.WARNING
    assert rules["tls_verify_off"] is Severity.WARNING
    assert rules["sql_fstring"] is Severity.WARNING


def test_security_skips_non_code_files() -> None:
    patch = "@@ -0,0 +1 @@\n+eval(something)"
    assert SecurityVerifier().scan(_diff("notes.md", patch)) == []


def test_security_yaml_safe_load_not_flagged() -> None:
    patch = "@@ -0,0 +2 @@\n+cfg = yaml.safe_load(text)\n+cfg2 = yaml.load(text, Loader=yaml.SafeLoader)"
    rules = {f.rule for f in SecurityVerifier().scan(_diff("cfg.py", patch))}
    assert "yaml_load_unsafe" not in rules


# ---- StyleVerifier --------------------------------------------------------


def test_style_flags_nits_only() -> None:
    long_line = "x = " + "a" * 130
    patch = f"@@ -0,0 +3 @@\n+{long_line}\n+y = 1  # TODO clean this up\n+print('debug')  "
    findings = StyleVerifier().scan(_diff("app.py", patch))
    rules = {f.rule for f in findings}
    assert "line_too_long" in rules
    assert "leftover_todo" in rules
    assert "stray_print" in rules
    assert "trailing_whitespace" in rules
    assert all(f.severity is Severity.NIT for f in findings)


def test_style_allows_print_in_test_files() -> None:
    patch = "@@ -0,0 +1 @@\n+print('ok')"
    rules = {f.rule for f in StyleVerifier().scan(_diff("tests/test_app.py", patch))}
    assert "stray_print" not in rules


# ---- aggregation ----------------------------------------------------------


def test_run_verifiers_concatenates_and_worst_severity() -> None:
    patch = '@@ -0,0 +2 @@\n+secret = "supersecretvalue"\n+y = 1  # TODO'
    findings = run_verifiers(_diff("app.py", patch))
    assert worst_severity(findings) is Severity.BLOCKER  # secret dominates the nit
    assert {v.verifier_id for v in default_code_verifiers()} == {"secrets", "security", "style"}


def test_worst_severity_none_when_clean() -> None:
    patch = "@@ -0,0 +1 @@\n+x = 1"
    assert worst_severity(run_verifiers(_diff("clean.py", patch))) is None
