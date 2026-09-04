"""Progress for the long tools: the engine's phases, to the host, never able to fail the work."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from orchestrator.plugin.progress import FEATURE_PHASES, Reporter, phase_of


class _Ctx:
    def __init__(self, *, fail: bool = False) -> None:
        self.progress: list[tuple[float, float | None, str | None]] = []
        self.fail = fail

    async def report_progress(
        self, progress: float, total: float | None = None, message: str | None = None
    ) -> None:
        if self.fail:
            raise ValueError("Context is not available outside of a request")
        self.progress.append((progress, total, message))


def test_phase_of_reads_the_bracketed_prefix() -> None:
    assert phase_of("[run_tests #2] passed=False rc=1") == "run_tests"
    assert phase_of("  [spec] wrote spec") == "spec"
    assert phase_of("no prefix here") is None
    assert phase_of("[Not-A-Phase] x") is None


def test_the_phase_table_is_in_run_order_and_names_the_stages_the_runner_logs() -> None:
    """The runner's own log prefixes, in the order a run reaches them — a new stage in the
    runner is one line here. Sanity: the bookends are where they must be."""
    assert FEATURE_PHASES[0] == "backlog" and FEATURE_PHASES[-1] == "commit"
    assert (
        FEATURE_PHASES.index("implement")
        < FEATURE_PHASES.index("author_tests")
        < FEATURE_PHASES.index("run_tests")
    )
    assert FEATURE_PHASES.index("refine") < FEATURE_PHASES.index("pr")
    assert len(set(FEATURE_PHASES)) == len(FEATURE_PHASES)


async def test_a_reporter_without_a_context_is_a_silent_no_op() -> None:
    r = Reporter(None)
    await r.step(1, 3, "x")
    await r.log("y")
    await r.phase_line("[spec] z")
    assert r.high_water == FEATURE_PHASES.index("spec") + 1  # it still tracks the place


async def test_known_prefixes_advance_the_bar_monotonically_and_unknown_lines_ride_the_current_step() -> None:
    ctx = _Ctx()
    r = Reporter(ctx)
    for line in (
        "[spec] s",
        "[implement] i",
        "[run_tests #1] t",
        "[refine] r",
        "[run_tests #2] t",
        "[pr] p",
        "plain note",
    ):
        await r.phase_line(line)
    steps = [p for p, _t, _m in ctx.progress]
    assert steps == sorted(steps)  # never backwards: run_tests #2 after refine is skipped
    assert all(t == len(FEATURE_PHASES) for _p, t, _m in ctx.progress)
    assert ctx.progress[0][2] == "[spec] s"
    # The unknown line rides on the current step: same `done` as `[pr]`, its own message.
    assert ctx.progress[-1] == (FEATURE_PHASES.index("pr") + 1, len(FEATURE_PHASES), "plain note")
    assert r.high_water == FEATURE_PHASES.index("pr") + 1


async def test_a_context_outside_a_request_cannot_fail_the_tool() -> None:
    r = Reporter(_Ctx(fail=True))
    await r.step(1, 2, "x")  # raises inside; swallowed
    await r.phase_line("[spec] s")


async def test_as_log_feeds_a_sync_engine_callback_into_the_bar() -> None:
    ctx = _Ctx()
    r = Reporter(ctx)
    emit = r.as_log()
    emit("[design] d")
    emit("[implement] i")
    await asyncio.sleep(0)  # let the scheduled notifications run
    await asyncio.sleep(0)
    assert [m for _p, _t, m in ctx.progress] == ["[design] d", "[implement] i"]


def test_as_log_without_a_running_loop_drops_the_line_quietly() -> None:
    emit = Reporter(_Ctx()).as_log()
    emit("[spec] s")  # no loop: nothing to schedule on, nothing raised


@pytest.mark.parametrize("phases", [("a", "b"), ("one",)])
async def test_a_custom_phase_table_sets_the_total(phases: tuple[str, ...]) -> None:
    ctx = _Ctx()
    r = Reporter(ctx, phases=phases)
    await r.phase_line(f"[{phases[-1]}] end")
    assert ctx.progress == [(len(phases), len(phases), f"[{phases[-1]}] end")]


# ---- end to end: a host sees the runner's phases while sdlc_feature runs ---------------


@pytest.mark.skipif(__import__("importlib").util.find_spec("mcp") is None, reason="needs the 'mcp' extra")
async def test_a_host_receives_the_runners_phases_in_order(monkeypatch: pytest.MonkeyPatch) -> None:
    """The real server over in-memory streams, a real client with a progress callback, and
    the feature runner replaced by one that logs the stages a run logs. The wrapper from
    the scope guard must pass the injected Context through untouched."""
    import anyio
    from mcp import ClientSession
    from mcp.shared.memory import create_client_server_memory_streams

    from orchestrator.plugin.server import build_server

    class _Result:
        passed, intent_id, issue_key, branch, files, iterations, grounding_chars, live, pr_url = (
            True,
            "I-1",
            "K-1",
            "feat/x",
            ["a.py"],
            1,
            0,
            False,
            None,
        )

    async def fake_run_feature(source: str, **kw: Any) -> _Result:
        log = kw["log"]
        for line in (
            "[spec] wrote spec",
            "[implement] 1 file",
            "[run_tests #1] passed=True",
            "[pr] skipped (safe)",
        ):
            log(line)
        await asyncio.sleep(0)  # let the scheduled notifications go out before we return
        await asyncio.sleep(0)
        return _Result()

    monkeypatch.setattr("orchestrator.sdlc.feature_runner.run_feature", fake_run_feature)
    server = build_server()
    seen: list[tuple[float, float | None, str | None]] = []

    async def on_progress(progress: float, total: float | None, message: str | None) -> None:
        seen.append((progress, total, message))

    async with create_client_server_memory_streams() as ((cr, cw), (sr, sw)), anyio.create_task_group() as tg:
        low = server._lowlevel_server
        tg.start_soon(low.run, sr, sw, low.create_initialization_options())
        async with ClientSession(cr, cw) as client:
            await client.initialize()
            tool = next(t for t in (await client.list_tools()).tools if t.name == "sdlc_feature")
            assert "ctx" not in tool.input_schema["properties"]  # the host never sees the context
            result = await client.call_tool(
                "sdlc_feature", {"source": "file://./spec.md"}, progress_callback=on_progress
            )
        tg.cancel_scope.cancel()

    assert not result.is_error and (result.structured_content or {}).get("passed") is True
    messages = [m for _p, _t, m in seen]
    assert messages == [
        "[spec] wrote spec",
        "[implement] 1 file",
        "[run_tests #1] passed=True",
        "[pr] skipped (safe)",
    ]
    steps = [p for p, _t, _m in seen]
    assert steps == sorted(steps) and all(t == len(FEATURE_PHASES) for _p, t, _m in seen)
    assert steps[0] == FEATURE_PHASES.index("spec") + 1 and steps[-1] == FEATURE_PHASES.index("pr") + 1


@pytest.mark.skipif(__import__("importlib").util.find_spec("mcp") is None, reason="needs the 'mcp' extra")
async def test_the_progress_tools_advertise_the_same_schema_and_hints_as_before() -> None:
    from orchestrator.plugin.server import _TOOLS, build_server, tool_annotations

    with_ctx = {"sdlc_feature", "understand_repo", "sdlc_remediate", "sdlc_address_review", "audit_repo"}
    tools = {t.name: t for t in await build_server().list_tools()}
    assert set(tools) == {fn.__name__ for fn in _TOOLS}
    for name in with_ctx:
        assert "ctx" not in tools[name].input_schema["properties"], name
        assert tools[name].annotations is not None
        assert tools[name].annotations.model_dump(exclude_none=True, exclude={"title"}) == tool_annotations(
            name
        )
    assert set(tools["understand_repo"].input_schema["properties"]) == {
        "repo_path",
        "check",
        "refresh",
        "out",
    }
