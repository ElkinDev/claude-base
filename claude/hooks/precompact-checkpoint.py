"""PreCompact hook: write a deterministic checkpoint to disk, then steer the summary.

Runs before every compaction, manual or automatic, and never blocks it. Two outputs:

1. A checkpoint file with what a summary tends to mangle and no model should be asked
   to remember: git truth for every repository under reach (branch, tip, uncommitted
   files, worktrees), the subagents of this session with their last words, and the
   last text of the dialogue. No model runs; this costs no tokens.
2. On stdout, the summarization instructions. Claude Code joins the stdout of every
   PreCompact hook and passes it to the summarizer as custom instructions, for manual
   and automatic compactions alike. That is the highest-leverage line of this file:
   it tells the summary to keep hashes, ids, paths and the next step verbatim, and to
   defer to the checkpoint file for git state instead of paraphrasing it.

Environment (all optional):
  CLAUDE_CHECKPOINT_DIR          folder for the checkpoints; default ~/.claude/checkpoints/<project>
  CLAUDE_CHECKPOINT_ROOTS        extra repositories to inspect, separated by ';'
  CLAUDE_CHECKPOINT_KEEP         files kept per folder, default 40
  CLAUDE_CHECKPOINT_QUIET=1      write the checkpoint but print no instructions
  CLAUDE_CHECKPOINT_SUBAGENTS=1  also checkpoint subagent compactions (skipped by default;
                                 their files carry an -agent-<id> tag, and no instructions
                                 are printed because the harness ignores them there)
  CLAUDE_CHECKPOINT_INSTRUCTIONS path to a text file replacing the built-in instructions;
                                 '{path}' inside it is replaced by the checkpoint path

stdin: the hook JSON (session_id, transcript_path, cwd, trigger, custom_instructions, and
agent_id plus agent_type when the compaction belongs to a subagent of the session; in that
case transcript_path still names the parent's transcript, verified on 2.1.248).
Exit code is always 0. A PreCompact hook that exits 2 cancels the compaction, and a
checkpoint must never do that.
"""
import glob
import json
import os
import subprocess
import sys
import time
from datetime import datetime

DEADLINE_SECONDS = 12      # the hook's own budget; the harness kills it later than this
GIT_TIMEOUT = 5            # per git call
MAX_ROOTS = 12
MAX_DIRTY_LINES = 40
MAX_SUBAGENTS = 12
MAX_FILE_BYTES = 64 * 1024
TAIL_BYTES = 4 * 1024 * 1024

INSTRUCTIONS = (
    "A checkpoint of the disk state was written to {path} just before this compaction; it is the "
    "source of truth for repositories, branches, tips and uncommitted files, so do not paraphrase "
    "git state, point at the file. Write the summary for continuation, not for reading: "
    "1. The open task and the exact next step, as one imperative sentence. "
    "2. Every decision taken since the previous compaction, with its reason, one line each. "
    "3. Every rule or constraint the user stated, verbatim when short. "
    "4. Every running or awaited item: background commands, agents, timers, watchers, locks, and "
    "which of them do not survive a process restart. "
    "5. At most five paths the next step must open, path only, no reason. "
    "6. Numbers, hashes, identifiers, paths, names and error strings verbatim; never round or "
    "paraphrase them. "
    "7. Questions still waiting for the user, in their exact wording. "
    "Leave out narration, tool chatter and anything the checkpoint file already records."
)

START = time.monotonic()


def time_left():
    return DEADLINE_SECONDS - (time.monotonic() - START)


def run_git(root, *args):
    """One git call with a hard timeout. Returns stdout or '' on any failure."""
    if time_left() <= 0:
        return ""
    try:
        proc = subprocess.run(
            ["git", "-C", root, *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=min(GIT_TIMEOUT, max(0.5, time_left())),
        )
        return proc.stdout if proc.returncode == 0 else ""
    except Exception:
        return ""


def is_repo(path):
    return os.path.isdir(path) and os.path.exists(os.path.join(path, ".git"))


def candidate_roots(cwd):
    """cwd if it is a repository; otherwise its immediate children that are; plus the
    roots named in CLAUDE_CHECKPOINT_ROOTS. Order preserved, duplicates removed."""
    roots = []
    if is_repo(cwd):
        roots.append(cwd)
    else:
        try:
            for name in sorted(os.listdir(cwd)):
                child = os.path.join(cwd, name)
                if is_repo(child):
                    roots.append(child)
        except Exception:
            pass
    for extra in (os.environ.get("CLAUDE_CHECKPOINT_ROOTS") or "").split(";"):
        extra = extra.strip().strip('"')
        if extra and is_repo(extra):
            roots.append(extra)
    seen, unique = set(), []
    for root in roots:
        key = os.path.normcase(os.path.abspath(root))
        if key not in seen:
            seen.add(key)
            unique.append(root)
    return unique[:MAX_ROOTS]


def repo_block(root):
    branch = run_git(root, "rev-parse", "--abbrev-ref", "HEAD").strip() or "?"
    tip = run_git(root, "log", "-1", "--format=%h %cd %s", "--date=format:%Y-%m-%d %H:%M").strip() or "?"
    status = run_git(root, "status", "--porcelain").splitlines()
    lines = [f"- {root}: branch {branch}, tip {tip}, {len(status)} uncommitted"]
    for entry in status[:MAX_DIRTY_LINES]:
        lines.append(f"    {entry.rstrip()}")
    if len(status) > MAX_DIRTY_LINES:
        lines.append(f"    ... {len(status) - MAX_DIRTY_LINES} more")
    return lines


def worktree_blocks(root, already):
    """Every other worktree of the repository at root, with its own status."""
    out = []
    listing = run_git(root, "worktree", "list", "--porcelain")
    entries, current = [], {}
    for line in listing.splitlines():
        if line.startswith("worktree "):
            if current:
                entries.append(current)
            current = {"path": line[9:].strip()}
        elif line.startswith("HEAD "):
            current["head"] = line[5:12]
        elif line.startswith("branch "):
            current["branch"] = line[7:].replace("refs/heads/", "")
        elif line.strip() == "detached":
            current["branch"] = "detached"
    if current:
        entries.append(current)
    for entry in entries:
        path = entry.get("path", "")
        key = os.path.normcase(os.path.abspath(path)) if path else ""
        if not path or key in already:
            continue
        already.add(key)
        if time_left() <= 1:
            out.append(f"- {path}: branch {entry.get('branch', '?')}, tip {entry.get('head', '?')} (status skipped, out of time)")
            continue
        status = run_git(path, "status", "--porcelain").splitlines()
        out.append(f"- {path}: worktree, branch {entry.get('branch', '?')}, tip {entry.get('head', '?')}, {len(status)} uncommitted")
        for line in status[:MAX_DIRTY_LINES]:
            out.append(f"    {line.rstrip()}")
        if len(status) > MAX_DIRTY_LINES:
            out.append(f"    ... {len(status) - MAX_DIRTY_LINES} more")
    return out


def disk_truth(cwd):
    roots = candidate_roots(cwd)
    if not roots:
        return [f"No git repository at {cwd} or directly below it."]
    lines, seen = [], set()
    for root in roots:
        seen.add(os.path.normcase(os.path.abspath(root)))
    for root in roots:
        lines.extend(repo_block(root))
        lines.extend(worktree_blocks(root, seen))
        if time_left() <= 0:
            lines.append("(remaining repositories skipped, out of time)")
            break
    return lines


def read_rows_tail(path, limit=TAIL_BYTES):
    """The last JSONL rows of a transcript, reading at most `limit` bytes from its end."""
    rows = []
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as handle:
            if size > limit:
                handle.seek(size - limit)
                handle.readline()  # drop the partial line
            for raw in handle:
                try:
                    rows.append(json.loads(raw.decode("utf-8", "replace")))
                except Exception:
                    continue
    except Exception:
        pass
    return rows


def text_blocks(row):
    message = row.get("message") or {}
    content = message.get("content")
    if isinstance(content, str):
        return [content]
    texts = []
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text" and block.get("text"):
                texts.append(block["text"])
    return texts


def clip(text, limit):
    text = " ".join(text.split())
    return text if len(text) <= limit else text[:limit] + " [...]"


def last_words(rows):
    """The last user messages and assistant text blocks, newest last."""
    assistant, user = [], []
    for row in reversed(rows):
        kind = row.get("type")
        if row.get("isMeta") or row.get("isCompactSummary"):
            continue
        if kind == "assistant" and len(assistant) < 6:
            for text in reversed(text_blocks(row)):
                if text.strip():
                    assistant.append(clip(text, 600))
                    break
        elif kind == "user" and len(user) < 2:
            for text in text_blocks(row):
                if text.strip() and not text.startswith("This session is being continued"):
                    user.append(clip(text, 400))
                    break
        if len(assistant) >= 6 and len(user) >= 2:
            break
    lines = []
    for text in reversed(user):
        lines.append(f"- user: {text}")
    for text in reversed(assistant):
        lines.append(f"- assistant: {text}")
    return lines or ["(no text found in the transcript tail)"]


def subagents(transcript_path, session_id):
    folder = os.path.join(os.path.dirname(transcript_path), session_id, "subagents")
    metas = glob.glob(os.path.join(folder, "agent-*.meta.json"))
    if not metas:
        return ["(no subagents in this session)"]
    items = []
    for meta_path in metas:
        transcript = meta_path[: -len(".meta.json")] + ".jsonl"
        mtime = os.path.getmtime(transcript) if os.path.isfile(transcript) else os.path.getmtime(meta_path)
        items.append((mtime, meta_path, transcript))
    items.sort(reverse=True)
    lines = []
    now = time.time()
    for mtime, meta_path, transcript in items[:MAX_SUBAGENTS]:
        try:
            with open(meta_path, encoding="utf-8", errors="replace") as handle:
                meta = json.load(handle)
        except Exception:
            meta = {}
        agent_id = os.path.basename(meta_path)[len("agent-"): -len(".meta.json")]
        stamp = datetime.fromtimestamp(mtime).strftime("%H:%M")
        recent = " (active in the last 10 min)" if now - mtime < 600 else ""
        head = f"- {agent_id} {meta.get('agentType') or '?'}: {clip(str(meta.get('description') or ''), 120)}; last write {stamp}{recent}"
        lines.append(head)
        rows = read_rows_tail(transcript, 512 * 1024) if os.path.isfile(transcript) else []
        for row in reversed(rows):
            if row.get("type") == "assistant":
                texts = [t for t in text_blocks(row) if t.strip()]
                if texts:
                    lines.append(f"    last words: {clip(texts[-1], 300)}")
                    break
    if len(items) > MAX_SUBAGENTS:
        lines.append(f"- ... {len(items) - MAX_SUBAGENTS} older subagents not listed")
    return lines


def checkpoint_dir(transcript_path, cwd):
    configured = os.environ.get("CLAUDE_CHECKPOINT_DIR")
    if configured:
        return configured
    if transcript_path:
        project = os.path.basename(os.path.dirname(transcript_path)) or "default"
    else:
        project = "".join(ch if ch.isalnum() else "-" for ch in cwd) or "default"
    return os.path.join(os.path.expanduser("~"), ".claude", "checkpoints", project)


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


def subagent_of(data):
    """(agent_id, is_subagent). A subagent's payload carries agent_id and agent_type while
    transcript_path still names the parent session's transcript."""
    agent_id = str(data.get("agent_id") or "")
    transcript = str(data.get("transcript_path") or "").replace("\\", "/")
    return agent_id, bool(agent_id or data.get("agent_type") or "/subagents/" in transcript)


def build(data):
    session_id = str(data.get("session_id") or "unknown")
    transcript_path = str(data.get("transcript_path") or "")
    cwd = os.path.normpath(str(data.get("cwd") or os.getcwd()))
    trigger = str(data.get("trigger") or "unknown")
    agent_id, _ = subagent_of(data)
    words_path = transcript_path
    if agent_id and transcript_path:
        own = os.path.join(os.path.dirname(transcript_path), session_id, "subagents", f"agent-{agent_id}.jsonl")
        if os.path.isfile(own):
            words_path = own
    now = datetime.now()
    header = [
        f"# Compaction checkpoint {now.strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        f"session: {session_id}",
    ]
    if agent_id:
        header.append(f"agent: {agent_id} ({data.get('agent_type') or '?'}), a subagent of the session")
    header += [
        f"trigger: {trigger}",
        f"cwd: {cwd}",
        f"transcript: {words_path or '?'}",
        "",
        "Written by the PreCompact hook without running a model. Git state below is the truth at",
        "the moment of compaction; the summary must point here instead of paraphrasing it.",
    ]
    sections = [
        ("## Disk truth", disk_truth(cwd)),
        ("## Subagents of this session", subagents(transcript_path, session_id) if transcript_path else ["(no transcript path in the payload)"]),
        ("## Last words before compaction", last_words(read_rows_tail(words_path)) if words_path and os.path.isfile(words_path) else ["(transcript not readable)"]),
    ]
    parts = header
    for title, lines in sections:
        parts.append("")
        parts.append(title)
        parts.extend(lines)
    text = "\n".join(parts) + "\n"
    if len(text.encode("utf-8")) > MAX_FILE_BYTES:
        text = text.encode("utf-8")[:MAX_FILE_BYTES].decode("utf-8", "ignore") + "\n[checkpoint capped]\n"
    return text, now, session_id, trigger, transcript_path, cwd


def instructions_for(path, data):
    template_path = os.environ.get("CLAUDE_CHECKPOINT_INSTRUCTIONS")
    template = INSTRUCTIONS
    if template_path and os.path.isfile(template_path):
        try:
            with open(template_path, encoding="utf-8", errors="replace") as handle:
                template = handle.read().strip() or INSTRUCTIONS
        except Exception:
            template = INSTRUCTIONS
    text = template.replace("{path}", path)
    user_text = str(data.get("custom_instructions") or "").strip()
    if user_text:
        text = f"User instructions for this compaction: {user_text} {text}"
    return text


def main():
    try:
        data = json.loads(sys.stdin.buffer.read().decode("utf-8", "replace") or "{}")
    except Exception:
        data = {}
    agent_id, is_subagent = subagent_of(data)
    if is_subagent and os.environ.get("CLAUDE_CHECKPOINT_SUBAGENTS") != "1":
        return 0
    path = ""
    try:
        text, now, session_id, trigger, transcript_path, cwd = build(data)
        folder = checkpoint_dir(transcript_path, cwd)
        os.makedirs(folder, exist_ok=True)
        tag = f"-agent-{agent_id[:8]}" if agent_id else ""
        name = f"{now.strftime('%Y%m%d-%H%M%S')}-{session_id[:8]}{tag}-{trigger}.md"
        path = os.path.join(folder, name)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(text)
        prune(folder)
    except Exception:
        path = ""
    if os.environ.get("CLAUDE_CHECKPOINT_QUIET") == "1" or is_subagent:
        return 0
    if path:
        sys.stdout.buffer.write(instructions_for(path, data).encode("utf-8"))
    else:
        sys.stdout.buffer.write(instructions_for("(checkpoint could not be written)", data).encode("utf-8"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
