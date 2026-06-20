"""
Claude Orchestrator — native-style sub-agent primitives (agent / parallel /
pipeline) over claude_bridge, with a concurrency cap mirroring Claude Code's
Workflow (min(16, cpu-2)). Import as a library, or run for a self-test.

Mapping to native Workflow:
  agent(prompt, cd, **opts)        ~ agent(prompt, opts)
  parallel(thunks, cap)            ~ parallel(thunks)  (barrier; raises -> None)
  pipeline(items, *stages, cap)    ~ pipeline(items, ...stages)  (no inter-stage barrier)
"""
from __future__ import annotations

import concurrent.futures
import os
import sys
from typing import Any, Callable, List

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from claude_bridge import run_claude, DEFAULT_COLD_MODELS  # noqa: E402

DEFAULT_CAP = min(16, max(1, (os.cpu_count() or 4) - 2))


class _Args:
    __slots__ = ("PROMPT", "cd", "model", "mcp", "schema", "SESSION_ID",
                 "fallback_model", "max_budget_usd", "retries",
                 "retry_base_delay", "timeout", "return_all_messages",
                 "cold_models", "block_tool", "effort", "session_cwd")

    def __init__(self, prompt, cd, *, model="", mcp="", schema="", session_id="",
                 fallback_model="", max_budget_usd="", retries=3,
                 retry_base_delay=30.0, timeout=240, return_all_messages=False,
                 cold_models=DEFAULT_COLD_MODELS, block_tool="", effort="", session_cwd=""):
        self.PROMPT = prompt
        self.cd = cd
        self.model = model
        self.mcp = mcp
        self.schema = schema
        self.SESSION_ID = session_id
        self.fallback_model = fallback_model
        self.max_budget_usd = max_budget_usd
        self.retries = retries
        self.retry_base_delay = retry_base_delay
        self.timeout = timeout
        self.return_all_messages = return_all_messages
        self.cold_models = cold_models
        self.block_tool = block_tool
        self.effort = effort
        self.session_cwd = session_cwd


def agent(prompt: str, cd: str, **opts) -> dict:
    """One claude sub-agent. Returns {success, SESSION_ID, agent_messages} or {success:False, error}."""
    return run_claude(_Args(prompt, cd, **opts))


def parallel(tasks: List[Callable[[], Any]], cap: int = DEFAULT_CAP) -> List[Any]:
    """Barrier fan-out over thunks () -> result. A thunk that raises -> None (mirrors native .filter(Boolean))."""
    results: List[Any] = [None] * len(tasks)
    if not tasks:
        return results
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, cap)) as ex:
        futs = {ex.submit(t): i for i, t in enumerate(tasks)}
        for f in concurrent.futures.as_completed(futs):
            try:
                results[futs[f]] = f.result()
            except Exception:
                results[futs[f]] = None
    return results


def pipeline(items: List[Any], *stages: Callable[[Any, Any, int], Any], cap: int = DEFAULT_CAP) -> List[Any]:
    """Each item flows through all stages independently — no barrier between stages.
    Stage signature: (prev_result, original_item, index) -> next_result.
    A stage that raises (or returns None) drops that item to None and skips the rest."""
    def chain(item: Any, idx: int) -> Any:
        cur: Any = item
        for st in stages:
            cur = st(cur, item, idx)
            if cur is None:
                return None
        return cur
    return parallel([(lambda it=it, i=i: chain(it, i)) for i, it in enumerate(items)], cap=cap)


def _selftest():
    cd = os.environ.get("CB_TEST_CD", os.getcwd())
    res = parallel([
        lambda: agent("Reply with exactly: alpha", cd),
        lambda: agent("Reply with exactly: beta", cd),
    ])
    for i, r in enumerate(res):
        ok = bool(r and r.get("success"))
        print(f"agent[{i}] success={ok} msg={((r or {}).get('agent_messages') or (r or {}).get('error'))!r}")
    print(f"cap={DEFAULT_CAP} all_ok={all(bool(r and r.get('success')) for r in res)}")


if __name__ == "__main__":
    _selftest()
