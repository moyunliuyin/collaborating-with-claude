---
name: collaborating-with-claude
description: One-shot multi-agent orchestration over the `claude -p` bridge. FALLBACK since 2026-07-02 — native Workflow/Agent on the default (main-session-inherited) model verified alive again, prefer native. Use this bridge only for sub-agents on a NON-main-session model (native path 429s those; bridge hot-matrix is 2026-06-17 data — probe one agent before fanning out).
---

# collaborating-with-claude

**Status 2026-07-02: FALLBACK, not default.** The relay's per-model availability drifts over
time. Current measurements: native `Workflow`/`Agent` on the default model (inherited from
the main session, currently fable) are alive again — **prefer native**. Native haiku/sonnet
429-die even after warm-up retries (~250s each); opus untested natively. This skill routes
orchestration through independent `claude -p` processes for models the native path 429s —
but its own hot-matrix (opus/haiku alive) is 2026-06-17 data, so probe a single agent
before fanning out.

## When to use (fallback — prefer native Workflow/Agent when alive)

Only when the sub-agents must run on a model the native path currently 429s:

- Ultracode is on and the task wants multi-agent decomposition.
- You need genuine parallelism (fan-out N sub-agents at once).
- A multi-stage pipeline (each item flows stage→stage independently).
- Any single isolated sub-agent that would otherwise be a native `Agent`/`Task` call.

If the task is trivial or sequential, just do it in the main session — don't orchestrate.

## One-click entry

Write a JSON spec, then run **once** (background, no timeout — bridge cold-start can take ~210s):

```bash
python ~/.claude/skills/collaborating-with-claude/scripts/orchestrate.py --spec /tmp/spec.json
# or:  ... --inline '<json>'   |   echo '<json>' | python orchestrate.py
```

stdout is aggregated JSON: `{mode, cap, ok_count, total, results:[{label,success,agent_messages,error,SESSION_ID}]}`.

## Spec format

```json
{
  "mode": "agent" | "parallel" | "pipeline",
  "cd":   "E:/code/proj",          // default cwd; per-agent/stage may override
  "model":"opus" | "haiku",        // hot on anyrouter. sonnet is COLD → 429. omit = relay default
  "mcp":  "", "cap": 16,           // optional (mcp config string/path; concurrency cap)
  "cold_models":"sonnet", "block_tool":"", "effort":"",  // optional: cold-set(fail-fast); anti-recursion pattern; thinking effort low→max
  "retries": 3, "timeout": 240,    // optional default agent opts

  // mode agent|parallel:
  "agents": [
    {"prompt": "...", "label": "scout-a", "cd": "...", "model": "...", "mcp": "...", "schema": "..."}
  ],

  // mode pipeline ({input} = previous stage's text; stage 1 gets the raw item):
  "items":  ["fileA.py", "fileB.py"],
  "stages": [
    {"prompt": "Review {input} for bugs, list findings."},
    {"prompt": "Adversarially verify these findings:\n{input}"}
  ]
}
```

## Rules

- **opus / haiku only** for `model` (verified hot on **anyrouter** via cc-switch). `sonnet` is cold → fail-fast 429. Your relay may differ — override the cold set with `cold_models` (empty disables the fail-fast). Omit `model` to use the relay default.
- **Background + no timeout** — a cold bridge attempt can run ~210s before the retry backoff clears the proxy warm-up.
- `mode:"agent"` runs `agents[0]` only; `parallel` is a barrier (failed agent → `success:false`, never crashes the batch); `pipeline` has no inter-stage barrier (a failed stage drops that item).
- Costs real tokens per sub-agent. Haiku ~$0.013/agent, opus ~$0.078/agent (incl. ~10–12k `--bare` cache creation); scale the fan-out to the task.
- Underlying primitives live in `scripts/claude_orchestrator.py` (`agent`/`parallel`/`pipeline`); for control flow a JSON spec can't express (loops, dedup, conditional fan-out), import that module in a hand-written driver instead.

## Self-test

```bash
python scripts/orchestrate.py --inline '{"mode":"parallel","cd":".","model":"haiku","agents":[{"prompt":"Reply one word: alpha"},{"prompt":"Reply one word: beta"}]}'
# expect ok_count=2
```
