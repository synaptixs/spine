"""Infrastructure detection — deterministic, from manifests / build / container configs."""

from __future__ import annotations

from pathlib import Path

from orchestrator.knowledge.infrastructure import detect_infrastructure


def _write(p: Path, text: str) -> None:
    p.write_text(text, encoding="utf-8")


def test_detects_datastores_web_workers_and_compose(tmp_path: Path) -> None:
    _write(
        tmp_path / "pyproject.toml",
        '[project]\nname="x"\ndependencies=["fastapi","uvicorn","psycopg","sqlalchemy",'
        '"temporalio","aioboto3","pyjwt","opentelemetry-sdk"]\n',
    )
    _write(
        tmp_path / "docker-compose.yml",
        "services:\n  db:\n    image: postgres:16\n  cache:\n    image: redis:7\n"
        "  store:\n    image: minio/minio:latest\n",
    )
    _write(tmp_path / ".env.example", "ANTHROPIC_API_KEY=\nJIRA_BASE_URL=\nAWS_REGION=\n")

    infra = detect_infrastructure(tmp_path)
    cats = infra.categories
    assert "FastAPI (HTTP API)" in cats["Web / API"]
    assert "PostgreSQL" in cats["Datastores"]
    assert "Temporal (workflow engine)" in cats["Messaging / workers"]
    assert "AWS (boto3)" in cats["Cloud / storage"]
    assert "JWT auth" in cats["Auth / crypto"]
    assert "OpenTelemetry (tracing)" in cats["Observability"]
    # docker-compose images resolve to friendly service names
    compose = next(x for x in cats["Containers & deploy"] if x.startswith("docker-compose"))
    assert "PostgreSQL" in compose and "Redis" in compose and "MinIO" in compose
    # env prefixes → external services
    assert "Anthropic API" in cats["External services (env)"]
    assert "Jira" in cats["External services (env)"]
    # summary = the backing services you must stand up
    assert "PostgreSQL" in infra.summary and "Temporal (workflow engine)" in infra.summary


def test_detects_c_build_and_meson_deps(tmp_path: Path) -> None:
    _write(
        tmp_path / "meson.build",
        "project('demo','c')\ngnutls = dependency('gnutls')\nmongoc = dependency('libmongoc-1.0')\n",
    )
    infra = detect_infrastructure(tmp_path)
    assert "Meson" in infra.categories["Build & CI"]
    assert "TLS (GnuTLS)" in infra.categories["Auth / crypto"]
    assert "MongoDB" in infra.categories["Datastores"]


def test_empty_repo_has_no_infrastructure(tmp_path: Path) -> None:
    (tmp_path / "main.py").write_text("print('hi')\n")
    assert detect_infrastructure(tmp_path).is_empty()
