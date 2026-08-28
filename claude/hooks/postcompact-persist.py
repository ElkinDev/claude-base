"""PostCompact hook: keep the summary the harness just produced, next to the checkpoint.

The summary is already in the new context, so this hook prints nothing (its stdout is
only a display message, never context). It exists so that the summary of every
compaction survives on disk: the next session can diff it against the checkpoint, and
`scripts/compaction-report.py` can measure summary sizes against the floor they leave.

Environment: CLAUDE_CHECKPOINT_DIR, CLAUDE_CHECKPOINT_KEEP and CLAUDE_CHECKPOINT_SUBAGENTS,
with the same meaning as in precompact-checkpoint.py.

stdin: the hook JSON (session_id, transcript_path, cwd, trigger, compact_summary, and
agent_id plus agent_type for a subagent's compaction, whose transcript_path is still the
parent's). Exit code is always 0.
"""
import glob
import json
import os
import sys
from datetime import datetime


def checkpoint_dir(transcript_path, cwd):
    configured = os.environ.get("CLAUDE_CHECKPOINT_DIR")
    if configured:
        return configured
    if transcript_path:
        project = os.path.basename(os.path.dirname(transcript_path)) or "default"
    else:
        project = "".join(ch if ch.isalnum() else "-" for ch in cwd) or "default"
    return os.path.join(os.path.expanduser("~"), ".claude", "checkpoints", project)


def newest_checkpoint(folder, session_id, tag=""):
    """The newest checkpoint of this session (or of this subagent when tag is set)."""
    files = [
        path
        for path in sorted(glob.glob(os.path.join(folder, f"????????-??????-{session_id[:8]}{tag}-*.md")))
        if not path.endswith("-summary.md") and (tag or "-agent-" not in os.path.basename(path))
    ]
    return files[-1] if files else ""


def prune(folder):
    try:
        keep = int(os.environ.get("CLAUDE_CHECKPOINT_KEEP") or 40)
    except ValueError:
        keep = 40
    files = sorted(glob.glob(os.path.join(folder, "????????-??????-*.md")))
    for path in files[:-keep] if keep > 0 else []:
        try:
            os.remove(path)
        except Exception:
            pass


def main():
    try:
        data = json.loads(sys.stdin.buffer.read().decode("utf-8", "replace") or "{}")
    except Exception:
        data = {}
    transcript_path = str(data.get("transcript_path") or "")
    agent_id = str(data.get("agent_id") or "")
    is_subagent = bool(agent_id or data.get("agent_type") or "/subagents/" in transcript_path.replace("\\", "/"))
    if is_subagent and os.environ.get("CLAUDE_CHECKPOINT_SUBAGENTS") != "1":
        return 0
    summary = str(data.get("compact_summary") or "")
    if not summary.strip():
        return 0
    try:
        session_id = str(data.get("session_id") or "unknown")
        cwd = str(data.get("cwd") or os.getcwd())
        folder = checkpoint_dir(transcript_path, cwd)
        os.makedirs(folder, exist_ok=True)
        now = datetime.now()
        tag = f"-agent-{agent_id[:8]}" if agent_id else ""
        path = os.path.join(folder, f"{now.strftime('%Y%m%d-%H%M%S')}-{session_id[:8]}{tag}-summary.md")
        checkpoint = newest_checkpoint(folder, session_id, tag)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(f"# Compaction summary {now.strftime('%Y-%m-%d %H:%M:%S')}\n\n")
            handle.write(f"session: {session_id}\n")
            if agent_id:
                handle.write(f"agent: {agent_id} ({data.get('agent_type') or '?'}), a subagent of the session\n")
            handle.write(f"trigger: {data.get('trigger') or 'unknown'}\n")
            handle.write(f"checkpoint: {checkpoint or '(none found)'}\n")
            handle.write(f"summary characters: {len(summary)}\n\n")
            handle.write(summary.rstrip() + "\n")
        prune(folder)
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
