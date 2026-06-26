"""End-to-end SDLC orchestration.

A parent ``SDLCWorkflow`` marches a Confluence page A→Z to merged, CI-green
feature PRs, with two human approval gates (intent + merge) and an audit row
at every stage. Each issue fans out to a child
``FeatureImplementationWorkflow`` that owns the per-issue git worktree and
the codegen → test → refine → review loop.

Every external seam is an adapter Protocol (codegen / tests / review / PR /
CI); real implementations are wired by env in ``worker.build_deps`` and safe
stubs remain the defaults. Deployment is out of scope — the pipeline ends at
the merge and hands off to existing CD.
"""

from __future__ import annotations
