"""Deterministic infrastructure + runtime detection for the Current State report.

Answers "what do you need to run and deploy this?" by reading the repo's own
dependency manifests, build files, container/orchestration configs, and env
template — no LLM, no network, same input → same answer. It reports what the code
*declares* (a Postgres driver, a `docker-compose` service, a `dependency('gnutls')`),
not what's running; absence of a signal means "not declared here", not "not used".
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from orchestrator.pkg.extractor import DEFAULT_IGNORE_DIRS

# (substring in the lowercased manifest/build text) → (category, friendly label).
# First match per label wins; labels are de-duplicated per category.
_SIGNALS: tuple[tuple[str, str, str], ...] = (
    # Web / API
    ("fastapi", "Web / API", "FastAPI (HTTP API)"),
    ("flask", "Web / API", "Flask (HTTP API)"),
    ("django", "Web / API", "Django"),
    ("uvicorn", "Web / API", "uvicorn (ASGI server)"),
    ("gunicorn", "Web / API", "gunicorn (WSGI server)"),
    ("starlette", "Web / API", "Starlette / ASGI"),
    ("express", "Web / API", "Express (Node)"),
    ("aspnetcore", "Web / API", "ASP.NET Core"),
    ("microsoft.net.sdk.web", "Web / API", "ASP.NET Core"),
    ("springframework", "Web / API", "Spring"),
    ("nghttp2", "Web / API", "HTTP/2 (nghttp2)"),
    ("libmicrohttpd", "Web / API", "HTTP server (libmicrohttpd)"),
    # Datastores
    ("psycopg", "Datastores", "PostgreSQL"),
    ("asyncpg", "Datastores", "PostgreSQL"),
    ("postgres", "Datastores", "PostgreSQL"),
    ("sqlalchemy", "Datastores", "SQL via SQLAlchemy (ORM)"),
    ("sqlmodel", "Datastores", "SQL via SQLModel"),
    ("aiosqlite", "Datastores", "SQLite"),
    ("sqlite", "Datastores", "SQLite"),
    ("pymongo", "Datastores", "MongoDB"),
    ("motor", "Datastores", "MongoDB"),
    ("mongoc", "Datastores", "MongoDB"),
    ("mysqlclient", "Datastores", "MySQL"),
    ("mariadb", "Datastores", "MariaDB / MySQL"),
    ("redis", "Datastores", "Redis"),
    ("elasticsearch", "Datastores", "Elasticsearch"),
    # Messaging / workers
    ("temporalio", "Messaging / workers", "Temporal (workflow engine)"),
    ("kafka", "Messaging / workers", "Kafka"),
    ("pika", "Messaging / workers", "RabbitMQ"),
    ("amqp", "Messaging / workers", "RabbitMQ / AMQP"),
    ("celery", "Messaging / workers", "Celery workers"),
    ("nats", "Messaging / workers", "NATS"),
    ("langgraph", "Messaging / workers", "LangGraph (agent orchestration)"),
    # Cloud / storage
    ("aioboto3", "Cloud / storage", "AWS (boto3)"),
    ("boto3", "Cloud / storage", "AWS (boto3)"),
    ("azure-", "Cloud / storage", "Azure"),
    ("google-cloud", "Cloud / storage", "Google Cloud"),
    ("minio", "Cloud / storage", "S3 / MinIO object storage"),
    # Observability
    ("opentelemetry", "Observability", "OpenTelemetry (tracing)"),
    ("prometheus", "Observability", "Prometheus (metrics)"),
    ("jaeger", "Observability", "Jaeger (tracing)"),
    ("grafana", "Observability", "Grafana"),
    ("sentry", "Observability", "Sentry"),
    # Auth / crypto
    ("pyjwt", "Auth / crypto", "JWT auth"),
    ('"jsonwebtoken"', "Auth / crypto", "JWT auth"),
    ("oauthlib", "Auth / crypto", "OAuth"),
    ("auth0", "Auth / crypto", "Auth0"),
    ("gnutls", "Auth / crypto", "TLS (GnuTLS)"),
    ("openssl", "Auth / crypto", "TLS (OpenSSL)"),
    ("gcrypt", "Auth / crypto", "libgcrypt"),
    ("libsctp", "Auth / crypto", "SCTP transport"),
    # Domain / semantic
    ("rdflib", "Domain / semantic", "RDF graph (rdflib)"),
    ("pyshacl", "Domain / semantic", "SHACL validation"),
)

# docker-compose image name fragment → friendly service name.
_IMAGE_LABELS: tuple[tuple[str, str], ...] = (
    ("postgres", "PostgreSQL"),
    ("mysql", "MySQL"),
    ("mariadb", "MariaDB"),
    ("mongo", "MongoDB"),
    ("redis", "Redis"),
    ("minio/mc", "MinIO client"),
    ("minio", "MinIO (S3 object storage)"),
    ("temporalio/ui", "Temporal UI"),
    ("temporal", "Temporal (workflow engine)"),
    ("jaeger", "Jaeger (tracing)"),
    ("rabbitmq", "RabbitMQ"),
    ("kafka", "Kafka"),
    ("elasticsearch", "Elasticsearch"),
    ("nginx", "nginx"),
    ("prom/prometheus", "Prometheus"),
    ("grafana", "Grafana"),
)

# .env var prefix → external service it configures.
_ENV_PREFIXES: tuple[tuple[str, str], ...] = (
    ("ANTHROPIC", "Anthropic API"),
    ("OPENAI", "OpenAI API"),
    ("JIRA", "Jira"),
    ("CONFLUENCE", "Confluence"),
    ("GITHUB", "GitHub"),
    ("SLACK", "Slack"),
    ("AWS", "AWS"),
    ("OTEL", "OpenTelemetry collector"),
    ("TEMPORAL", "Temporal"),
    ("MINIO", "MinIO / S3"),
    ("OBJECT_STORE", "object storage"),
    ("DATABASE", "database"),
    ("REDIS", "Redis"),
)

# Build system marker file → label.
_BUILD_FILES: tuple[tuple[str, str], ...] = (
    ("CMakeLists.txt", "CMake"),
    ("meson.build", "Meson"),
    ("pom.xml", "Maven"),
    ("build.gradle", "Gradle"),
    ("Makefile", "Make"),
    ("Cargo.toml", "Cargo"),
)

_CATEGORY_ORDER = (
    "Web / API",
    "Datastores",
    "Messaging / workers",
    "Cloud / storage",
    "Observability",
    "Auth / crypto",
    "Domain / semantic",
    "Containers & deploy",
    "Build & CI",
    "External services (env)",
)


@dataclass
class Infrastructure:
    """What the repo declares it needs to run and deploy. ``categories`` is an
    ordered map of category → de-duplicated labels; ``summary`` is the headline
    backing services."""

    categories: dict[str, list[str]] = field(default_factory=dict)

    @property
    def summary(self) -> list[str]:
        """The backing services a reader most needs to stand up (datastores, queues,
        cloud, plus declared compose services)."""
        seen: set[str] = set()
        out: list[str] = []
        for cat in ("Datastores", "Messaging / workers", "Cloud / storage"):
            for label in self.categories.get(cat, []):
                if label not in seen:
                    seen.add(label)
                    out.append(label)
        return out

    def is_empty(self) -> bool:
        return not any(self.categories.values())


def detect_infrastructure(root: Path | str) -> Infrastructure:
    """Scan the repo's manifests / build files / container + env configs (bounded,
    deterministic) for infrastructure signals."""
    root_path = Path(root)
    text = _manifest_text(root_path)
    cats: dict[str, list[str]] = {c: [] for c in _CATEGORY_ORDER}

    def add(cat: str, label: str) -> None:
        if label not in cats[cat]:
            cats[cat].append(label)

    for needle, cat, label in _SIGNALS:
        if needle in text:
            add(cat, label)

    # Containers & deploy: Docker, docker-compose services, Kubernetes, Helm.
    if (root_path / "Dockerfile").is_file() or _glob_any(root_path, "Dockerfile*"):
        add("Containers & deploy", "Docker")
    compose_services = _compose_services(root_path)
    if compose_services:
        add("Containers & deploy", "docker-compose: " + ", ".join(compose_services))
    if _has_k8s(root_path):
        add("Containers & deploy", "Kubernetes")
    if (root_path / "Chart.yaml").is_file() or _glob_any(root_path, "**/Chart.yaml"):
        add("Containers & deploy", "Helm")

    # Build & CI.
    for fname, label in _BUILD_FILES:
        if (root_path / fname).is_file() or _glob_any(root_path, f"**/{fname}"):
            add("Build & CI", label)
    if (root_path / ".github" / "workflows").is_dir():
        add("Build & CI", "GitHub Actions")
    if "alembic" in text or (root_path / "alembic.ini").is_file():
        add("Datastores", "DB migrations (Alembic)")

    # External services configured via env.
    for svc in _env_services(root_path):
        add("External services (env)", svc)

    return Infrastructure(categories={c: v for c, v in cats.items() if v})


# --- helpers ---------------------------------------------------------------


def _manifest_text(root: Path) -> str:
    """Lowercased concat of the dependency / build / container manifests."""
    blobs: list[str] = []
    for rel in (
        "pyproject.toml",
        "package.json",
        "pom.xml",
        "build.gradle",
        "go.mod",
        "Cargo.toml",
        "meson.build",
        "CMakeLists.txt",
        "Dockerfile",
    ):
        p = root / rel
        if p.is_file():
            blobs.append(_safe_read(p))
    for pat in ("requirements*.txt", "docker-compose*.y*ml", "compose*.y*ml"):
        for p in root.glob(pat):
            blobs.append(_safe_read(p))
    blobs.extend(_bounded_reads(root, (".csproj",), limit=40))
    return "\n".join(blobs).lower()


def _compose_services(root: Path) -> list[str]:
    """Friendly names of the services a ``docker-compose`` file declares (by image)."""
    images: list[str] = []
    for pat in ("docker-compose*.y*ml", "compose*.y*ml"):
        for p in root.glob(pat):
            for m in re.finditer(r"(?mi)^\s*image:\s*['\"]?([\w./-]+)", _safe_read(p)):
                images.append(m.group(1).lower())
    labels: list[str] = []
    for img in images:
        label = next((lab for frag, lab in _IMAGE_LABELS if frag in img), None)
        if label and label not in labels:
            labels.append(label)
    return labels


def _has_k8s(root: Path) -> bool:
    for p in list(root.glob("**/*.yaml"))[:200] + list(root.glob("**/*.yml"))[:200]:
        if any(part in DEFAULT_IGNORE_DIRS for part in p.parts):
            continue
        if re.search(r"(?mi)^kind:\s*(Deployment|StatefulSet|DaemonSet)", _safe_read(p)):
            return True
    return False


def _env_services(root: Path) -> list[str]:
    env = root / ".env.example"
    if not env.is_file():
        return []
    keys = {m.group(0) for m in re.finditer(r"(?m)^[A-Z][A-Z0-9_]+", _safe_read(env))}
    out: list[str] = []
    for prefix, svc in _ENV_PREFIXES:
        if any(k.startswith(prefix) for k in keys) and svc not in out:
            out.append(svc)
    return out


def _glob_any(root: Path, pattern: str) -> bool:
    return any(not any(part in DEFAULT_IGNORE_DIRS for part in p.parts) for p in root.glob(pattern))


def _bounded_reads(root: Path, suffixes: tuple[str, ...], *, limit: int) -> list[str]:
    blobs: list[str] = []
    for dirpath, dirnames, files in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in DEFAULT_IGNORE_DIRS and not d.startswith(".")]
        for fn in files:
            if fn.endswith(suffixes):
                blobs.append(_safe_read(Path(dirpath) / fn))
                if len(blobs) >= limit:
                    return blobs
    return blobs


def _safe_read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""


__all__ = ["Infrastructure", "detect_infrastructure"]
