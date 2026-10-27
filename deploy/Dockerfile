# Spine's shared MCP server: one container a company runs inside its network, which people
# reach from Claude Code, Codex, VS Code, Gemini CLI or Cursor with their company login.
# deploy/README.md is the guide. Built from this checkout (D36) so CI can build and smoke-test
# the image on every change; the release job pushes it to ghcr.io/synaptixs/spine-mcp.
#
#   docker build -f deploy/Dockerfile -t spine-mcp .
#
# Every base is pinned by digest, the dependencies are exactly uv.lock's, the server runs as
# uid 10001, and nothing secret is baked in: auth and the repository list come at run time.

FROM ghcr.io/astral-sh/uv:0.8.0@sha256:5778d479c0fd7995fedd44614570f38a9d849256851f2786c451c220d7bd8ccd AS uv

FROM python:3.12-slim@sha256:12316debbfb86890f6b2d825820612741ba527ad5a44e3902759141d572bfe8f AS build
COPY --from=uv /uv /bin/uv
WORKDIR /src
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
# The wheel for this commit, and the locked dependencies for the language front-ends that ship
# wheels (D23) — Rust's grammar builds from source and is left out.
RUN uv build --wheel --out-dir /dist \
 && uv export --frozen --no-dev --no-emit-project --format requirements-txt \
      --extra mcp --extra docs --extra c --extra cpp --extra csharp --extra go --extra java \
      --extra kotlin --extra perl --extra php --extra sql --extra typescript \
      --output-file /dist/requirements.txt

FROM python:3.12-slim@sha256:12316debbfb86890f6b2d825820612741ba527ad5a44e3902759141d572bfe8f
# git: the PKG keys its cache on the checkout's commit.
RUN apt-get update \
 && apt-get install -y --no-install-recommends git \
 && rm -rf /var/lib/apt/lists/* \
 && git config --system --add safe.directory '*'
COPY --from=uv /uv /bin/uv
COPY --from=build /dist /dist
RUN uv pip install --system --no-cache -r /dist/requirements.txt \
 && uv pip install --system --no-cache --no-deps /dist/*.whl \
 && rm -rf /dist /bin/uv \
 && useradd --system --uid 10001 --home-dir /home/spine --create-home spine \
 && mkdir -p /data /repos /srv/spine/.spine \
 && chown -R 10001:10001 /data /srv/spine

# Every cache on the /data volume; the admin's repository list at the path the tools' own
# default (`.spine/repos.yaml` from the working directory) already names.
ENV ORCHESTRATOR_PKG_CACHE_DIR=/data/pkg \
    ORCHESTRATOR_INTAKE_CACHE_DIR=/data/intake \
    ORCHESTRATOR_DOCS_CACHE_DIR=/data/docs \
    SDLC_WORKSPACE_ROOT=/data/workspaces \
    ORCHESTRATOR_MCP_REPOS=/srv/spine/.spine/repos.yaml \
    PYTHONUNBUFFERED=1

WORKDIR /srv/spine
USER 10001:10001
VOLUME ["/data"]
EXPOSE 8080

# Read + plan (D4). A public bind refuses to start without auth and without the repository
# list; neither escape hatch is passed here, on purpose.
ENTRYPOINT ["orchestrator-mcp", "--http", "--host", "0.0.0.0", "--port", "8080", "--level", "plan"]
