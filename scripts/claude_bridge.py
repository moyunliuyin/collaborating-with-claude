"""
Claude Bridge — wraps `claude -p` as a sub-agent bridge with a JSON interface,
mirroring codex_bridge.py's {success, SESSION_ID, agent_messages} contract.

Why this exists: on the cc-switch + anyrouter relay, Claude Code's NATIVE
sub-agent request path gets 429'd for opus (haiku is fine); the native retry
budget (~195s) can't outlast it, so native opus sub-agents reliably die. An
independent `claude -p` process takes a different request path the relay does
NOT 429 for opus — verified by elimination (bridge opus survives with a fresh
random session, [1m] routing, and 66k requests) — and its retry window is ours
to widen past 195s. A relay-side path×model quirk: not a main-session warm-up
issue, and absent on official direct connections.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import List


def _get_windows_npm_paths() -> List[Path]:
    if os.name != "nt":
        return []
    paths: List[Path] = []
    env = os.environ
    if prefix := env.get("NPM_CONFIG_PREFIX") or env.get("npm_config_prefix"):
        paths.append(Path(prefix))
    if appdata := env.get("APPDATA"):
        paths.append(Path(appdata) / "npm")
    if localappdata := env.get("LOCALAPPDATA"):
        paths.append(Path(localappdata) / "npm")
    if programfiles := env.get("ProgramFiles"):
        paths.append(Path(programfiles) / "nodejs")
    return paths


def _claude_bin_dirs(env: dict) -> List[Path]:
    home = env.get("USERPROFILE") or env.get("HOME") or ""
    return [Path(home) / ".local" / "bin"] if home else []


def _augment_path_env(env: dict) -> None:
    if os.name != "nt":
        return
    path_key = next((k for k in env if k.upper() == "PATH"), "PATH")
    entries = [p for p in env.get(path_key, "").split(os.pathsep) if p]
    lower = {p.lower() for p in entries}
    for c in _get_windows_npm_paths() + _claude_bin_dirs(env):
        if c.is_dir() and str(c).lower() not in lower:
            entries.insert(0, str(c))
            lower.add(str(c).lower())
    env[path_key] = os.pathsep.join(entries)


def _resolve_executable(name: str, env: dict) -> str:
    if os.path.isabs(name) or os.sep in name or (os.altsep and os.altsep in name):
        return name
    path_key = next((k for k in env if k.upper() == "PATH"), "PATH")
    if resolved := shutil.which(name, path=env.get(path_key)):
        return resolved
    if os.name == "nt":
        for base in _get_windows_npm_paths() + _claude_bin_dirs(env):
            for ext in (".exe", ".cmd", ".bat", ".com"):
                cand = base / f"{name}{ext}"
                if cand.is_file():
                    return str(cand)
    return name


def configure_windows_stdio() -> None:
    if os.name != "nt":
        return
    for stream in (sys.stdout, sys.stderr):
        rc = getattr(stream, "reconfigure", None)
        if callable(rc):
            try:
                rc(encoding="utf-8")
            except (ValueError, OSError):
                pass


# Models that cold-start → 429 on the relay. Default from anyrouter (managed via
# cc-switch) field testing: sonnet is cold, opus/haiku are hot. YOUR relay may
# differ — override with --cold-models; empty string disables the fail-fast.
DEFAULT_COLD_MODELS = "sonnet"
RETRYABLE_STATUS = {429, 500, 502, 503, 529}


def build_command(args, session_id: str) -> List[str]:
    # PROMPT is fed via stdin (run_claude passes input=), NOT as an argv element:
    # Windows CreateProcess caps the command line at ~32k chars, so a long --PROMPT
    # used to crash with WinError 206. claude -p reads the prompt from stdin when no
    # positional prompt is given.
    cmd = ["claude", "-p",
           "--output-format", "json",
           "--permission-mode", "bypassPermissions",
           "--add-dir", args.cd,
           "--bare"]  # minimal mode always: no CLAUDE.md/skills/hooks/auto-memory — cheapest + anti-recursion
    if args.mcp:
        # MCP loads directly under --bare via --mcp-config (verified 2026-06-17 with a
        # real context7 tool call); --setting-sources is NOT needed for MCP. ToolSearch
        # is absent in lean -p sessions regardless of flags, but few MCP tools are listed
        # directly so it isn't needed.
        cmd += ["--mcp-config", args.mcp]
    if args.block_tool:
        # Anti-recursion belt: block the sub-agent from re-spawning your own bridge
        # wrapper, e.g. 'Bash(*your-wrapper*)'. Empty = no block (--bare already skips
        # skills/hooks, so recursion risk is low unless the bridge is invoked from a
        # tool the sub-agent could call back into).
        cmd += ["--disallowedTools", args.block_tool]
    if args.model:
        cmd += ["--model", args.model]
    if args.effort:
        cmd += ["--effort", args.effort]
    if args.fallback_model:
        cmd += ["--fallback-model", args.fallback_model]
    if args.schema:
        cmd += ["--json-schema", args.schema]
    if args.max_budget_usd:
        cmd += ["--max-budget-usd", str(args.max_budget_usd)]
    cmd += ["--resume", session_id] if session_id else ["--session-id", str(uuid.uuid4())]
    return cmd


def _is_retryable(data: dict) -> bool:
    if data.get("api_error_status") in RETRYABLE_STATUS:
        return True
    txt = (data.get("result") or "").lower()
    return any(s in txt for s in ("service unavailable", "overloaded", "(429)", "(503)", "(529)"))


def run_claude(args) -> dict:
    env = os.environ.copy()
    _augment_path_env(env)
    env.setdefault("ANTHROPIC_API_KEY", "PROXY_MANAGED")  # --bare needs a key placeholder; anyrouter/cc-switch injects the real token via ANTHROPIC_BASE_URL + ANTHROPIC_AUTH_TOKEN
    exe = _resolve_executable("claude", env)

    # Isolate session storage: `claude -p` persists each session under a project dir
    # derived from the SUBPROCESS CWD (empirically verified: not --add-dir). Run from
    # your home dir, every sub-agent session piles into the main project's /resume list
    # and is never cleaned up. Pin cwd to a dedicated stable dir so sessions land in an
    # isolated projects/<hash> folder no interactive /resume reads; the sub-agent still
    # reaches the target via --add-dir args.cd (cd was never the cwd, so prompts using
    # absolute paths are unaffected). Stable (not a tempfile) so --resume can find it.
    session_cwd = args.session_cwd or str(Path.home() / ".claude" / ".bridge-cwd")
    try:
        os.makedirs(session_cwd, exist_ok=True)
    except OSError:
        session_cwd = None  # fall back to inherited cwd if it can't be created

    cold_set = {m.strip() for m in (args.cold_models or "").split(",") if m.strip()}
    cold = args.model in cold_set
    last_err = ""
    last_session = args.SESSION_ID
    for attempt in range(args.retries + 1):  # retries=N → N+1 attempts, N backoff sleeps
        cmd = build_command(args, args.SESSION_ID)
        cmd[0] = exe
        try:
            proc = subprocess.run(
                cmd, env=env, capture_output=True, text=True,
                encoding="utf-8", errors="replace",
                timeout=args.timeout, input=args.PROMPT,  # PROMPT via stdin: avoids ~32k argv limit (WinError 206)
                cwd=session_cwd,  # isolate session storage out of the main project's /resume picker
            )
        except subprocess.TimeoutExpired:
            last_err = f"timeout after {args.timeout}s (attempt {attempt + 1})"
        else:
            out = (proc.stdout or "").strip()
            try:
                data = json.loads(out)
            except json.JSONDecodeError:
                last_err = (f"non-JSON stdout (attempt {attempt + 1}): "
                            f"{out[:200]!r} | stderr: {(proc.stderr or '')[:200]!r}")
            else:
                if data.get("session_id"):
                    last_session = data["session_id"]
                if not data.get("is_error"):
                    res = {"success": True, "SESSION_ID": last_session,
                           "agent_messages": data.get("result", "")}
                    if args.return_all_messages:
                        res["all_messages"] = data
                    return res
                if not _is_retryable(data):
                    return {"success": False, "error": data.get("result", "unknown error"),
                            "SESSION_ID": last_session}
                if cold:  # a cold model's 429 won't recover by retrying — fail fast
                    return {"success": False, "SESSION_ID": last_session,
                            "error": f"cold model '{args.model}' rejected (use opus/haiku): {(data.get('result') or '')[:150]}"}
                last_err = (f"retryable {data.get('api_error_status')} "
                            f"(attempt {attempt + 1}): {(data.get('result') or '')[:150]}")
        if attempt < args.retries:  # sleep between attempts, not after the last
            time.sleep(args.retry_base_delay * (2 ** attempt))  # 30,60,120,… outlasts anyrouter's ~195s cold-start warm-up (via cc-switch)
    return {"success": False, "error": last_err or "exhausted retries", "SESSION_ID": last_session}


def main():
    configure_windows_stdio()
    p = argparse.ArgumentParser(description="Claude Bridge")
    p.add_argument("--PROMPT", required=True)
    p.add_argument("--cd", required=True)
    p.add_argument("--SESSION_ID", default="", help="Resume an existing claude session; empty starts a new one.")
    p.add_argument("--model", default="", help="opus/haiku (hot). sonnet is cold→429. Empty=ccs default.")
    p.add_argument("--effort", default="", help="Thinking effort: low/medium/high/xhigh/max. Empty=CLI default.")
    p.add_argument("--mcp", default="", help="MCP config JSON string or file path. Empty=--bare (no MCP, cheapest).")
    p.add_argument("--fallback-model", dest="fallback_model", default="")
    p.add_argument("--schema", default="", help="JSON schema string for structured output (--json-schema).")
    p.add_argument("--max-budget-usd", dest="max_budget_usd", default="")
    p.add_argument("--cold-models", dest="cold_models", default=DEFAULT_COLD_MODELS,
                   help="Comma-list of models that cold-start→429 on your relay (fail-fast). Default 'sonnet' from anyrouter; empty disables.")
    p.add_argument("--block-tool", dest="block_tool", default="",
                   help="--disallowedTools pattern for anti-recursion, e.g. 'Bash(*your-wrapper*)'. Empty = no block.")
    p.add_argument("--session-cwd", dest="session_cwd", default="",
                   help="Stable dir used as the sub-agent's CWD so its session won't pollute your real projects' /resume picker. Empty=~/.claude/.bridge-cwd.")
    p.add_argument("--retries", type=int, default=3, help="Retries after the first attempt (N → N+1 total; default 3 → ~210s backoff > 195s).")
    p.add_argument("--retry-base-delay", dest="retry_base_delay", type=float, default=30.0)
    p.add_argument("--timeout", type=int, default=240, help="Per-attempt subprocess timeout (seconds); covers ~213s cold-start.")
    p.add_argument("--return-all-messages", action="store_true")
    args = p.parse_args()

    cold_set = {m.strip() for m in (args.cold_models or "").split(",") if m.strip()}
    if args.model in cold_set:
        sys.stderr.write(f"[warn] model '{args.model}' is configured cold (likely 429 on your relay); prefer a hot model (opus/haiku on anyrouter).\n")

    print(json.dumps(run_claude(args), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
