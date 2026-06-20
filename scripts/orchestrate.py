"""
Orchestrate — one-shot fan-out entry over claude_orchestrator.

Feed a JSON spec (--spec FILE / --inline JSON / stdin); it runs
agent|parallel|pipeline via the `claude -p` bridge and prints aggregated JSON
to stdout. Purpose: the main session never hand-writes a driver and never fires
the native Workflow tool (which 429-dies on the cc-switch proxy cold-start).

Spec:
{
  "mode": "agent" | "parallel" | "pipeline",   # default "parallel"
  "cd":   "E:/code/proj",        # default cwd (per-agent/stage may override)
  "model":"opus"|"haiku",        # default model (sonnet is cold -> 429)
  "mcp":  "", "cap": 16,         # optional
  "retries": 3, "timeout": 240,  # optional default agent opts

  # mode agent|parallel:
  "agents": [ {"prompt":"...", "label":"...", "cd":"...", "model":"...",
               "mcp":"...", "schema":"..."} ],

  # mode pipeline (each item flows through all stages, no inter-stage barrier):
  "items":  ["...", {...}],
  "stages": [ {"prompt":"... {input} ...", "model":"...", ...} ]   # {input}=prev stage text
}
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import claude_orchestrator as co  # noqa: E402

# keys agent()/_Args accept; per-node overrides spec-level defaults
AGENT_OPT_KEYS = ("model", "mcp", "schema", "session_id", "fallback_model",
                  "max_budget_usd", "retries", "retry_base_delay", "timeout",
                  "cold_models", "block_tool", "effort", "session_cwd")


def _merge_opts(spec: dict, node: dict) -> dict:
    opts = {}
    for k in AGENT_OPT_KEYS:
        if node.get(k, "") != "":
            opts[k] = node[k]
        elif spec.get(k, "") != "":
            opts[k] = spec[k]
    return opts


def _cd_of(spec: dict, node: dict) -> str:
    cd = node.get("cd") or spec.get("cd")
    if not cd:
        raise SystemExit("spec error: 'cd' required (top-level or per-node)")
    return cd


def _text(x) -> str:
    if isinstance(x, dict):
        if "agent_messages" in x or "error" in x:   # a bridge result dict
            return x.get("agent_messages") or x.get("error") or ""
        return json.dumps(x, ensure_ascii=False)     # a raw structured pipeline item
    if isinstance(x, list):
        return json.dumps(x, ensure_ascii=False)
    return str(x)


def _norm(r, label: str) -> dict:
    r = r or {"success": False, "error": "no result (None / dropped)"}
    return {"label": label,
            "success": bool(r.get("success")),
            "agent_messages": r.get("agent_messages", ""),
            "error": r.get("error"),
            "SESSION_ID": r.get("SESSION_ID", "")}


def run(spec: dict) -> dict:
    mode = spec.get("mode", "parallel")
    cap = int(spec.get("cap") or co.DEFAULT_CAP)

    if mode in ("agent", "parallel"):
        agents = spec.get("agents") or []
        if not agents:
            raise SystemExit(f"spec error: 'agents' required for mode={mode}")
        for i, a in enumerate(agents):
            if not a.get("prompt"):
                raise SystemExit(f"spec error: agents[{i}] missing 'prompt'")
        labels = [a.get("label") or f"agent[{i}]" for i, a in enumerate(agents)]
        thunks = [(lambda a=a: co.agent(a["prompt"], _cd_of(spec, a), **_merge_opts(spec, a)))
                  for a in agents]
        if mode == "agent":
            results, labels = [thunks[0]()], labels[:1]
        else:
            results = co.parallel(thunks, cap=cap)
        norm = [_norm(r, labels[i]) for i, r in enumerate(results)]

    elif mode == "pipeline":
        items, stages = spec.get("items") or [], spec.get("stages") or []
        if not items or not stages:
            raise SystemExit("spec error: 'items' and 'stages' required for mode=pipeline")
        for i, st in enumerate(stages):
            if not st.get("prompt"):
                raise SystemExit(f"spec error: stages[{i}] missing 'prompt'")

        def make_stage(st):
            def fn(prev, _original, _idx):
                prompt = st["prompt"].replace("{input}", _text(prev))
                r = co.agent(prompt, _cd_of(spec, st), **_merge_opts(spec, st))
                return r if (r and r.get("success")) else None
            return fn

        results = co.pipeline(items, *[make_stage(st) for st in stages], cap=cap)
        norm = [_norm(r, f"item[{i}]") for i, r in enumerate(results)]

    else:
        raise SystemExit(f"spec error: unknown mode {mode!r}")

    ok = sum(1 for n in norm if n["success"])
    return {"mode": mode, "cap": cap, "ok_count": ok, "total": len(norm), "results": norm}


def load_spec(args) -> dict:
    try:
        if args.spec:
            with open(args.spec, encoding="utf-8") as f:
                return json.load(f)
        if args.inline:
            return json.loads(args.inline)
        data = sys.stdin.read()
        if not data.strip():
            raise SystemExit("no spec: use --spec FILE, --inline JSON, or pipe JSON to stdin")
        return json.loads(data)
    except json.JSONDecodeError as e:
        raise SystemExit(f"spec error: invalid JSON ({e})")
    except OSError as e:
        raise SystemExit(f"spec error: cannot read --spec file ({e})")


def main():
    for s in (sys.stdout, sys.stderr):
        rc = getattr(s, "reconfigure", None)
        if callable(rc):
            try:
                rc(encoding="utf-8")
            except (ValueError, OSError):
                pass
    p = argparse.ArgumentParser(description="One-shot orchestration over the claude -p bridge")
    p.add_argument("--spec", help="Path to a JSON spec file")
    p.add_argument("--inline", help="Inline JSON spec string")
    args = p.parse_args()

    out = run(load_spec(args))
    sys.stderr.write(f"[orchestrate] mode={out['mode']} ok={out['ok_count']}/{out['total']} cap={out['cap']}\n")
    print(json.dumps(out, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
