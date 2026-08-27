"""PreCompact hook. Copies the human and assistant text of the transcript tail into
<cwd>/NOTES.autosave.md before the compaction rewrites the window, so what was said in
the last stretch is never unrecoverable. Tool results, tool calls and thinking are skipped.

stdin: the hook JSON (session_id, transcript_path, trigger, cwd, custom_instructions).
Keeps the last 40 text messages, each cut at 1,500 characters, and trims the file to its
last 200 KB. Never blocks the compaction: every failure exits 0 silently.
"""
import json
import os
import sys
from datetime import datetime

KEEP = 40
PER_MSG = 1500
FILE_CAP = 200_000


def texts_of(entry):
    msg = entry.get("message") or {}
    role = msg.get("role") or entry.get("type")
    content = msg.get("content")
    parts = []
    if isinstance(content, str):
        parts.append(content)
    elif isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text" and block.get("text"):
                parts.append(block["text"])
    text = "\n".join(p.strip() for p in parts if p and p.strip())
    if not text or text.startswith("<system-reminder>") or text.startswith("<local-command"):
        return None
    return role, text


def main():
    try:
        data = json.loads(sys.stdin.buffer.read().decode("utf-8", "replace") or "{}")
        path = data.get("transcript_path")
        cwd = data.get("cwd") or os.getcwd()
        if not path or not os.path.isfile(path):
            return 0
        rows = []
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                try:
                    entry = json.loads(line)
                except Exception:
                    continue
                if entry.get("type") not in ("user", "assistant"):
                    continue
                got = texts_of(entry)
                if got:
                    rows.append(got)
        rows = rows[-KEEP:]
        if not rows:
            return 0
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
        out = [f"\n\n## Autosave before compaction, {stamp} (trigger: {data.get('trigger', '?')}, session {str(data.get('session_id', ''))[:8]})\n"]
        for role, text in rows:
            if len(text) > PER_MSG:
                text = text[:PER_MSG] + " [cut]"
            out.append(f"\n**{role}:** {text}\n")
        target = os.path.join(cwd, "NOTES.autosave.md")
        prev = ""
        if os.path.isfile(target):
            prev = open(target, encoding="utf-8", errors="replace").read()
        else:
            prev = "# NOTES.autosave.md\n\nWritten by the PreCompact hook: the last text messages before each compaction. Not for commit.\n"
        content = prev + "".join(out)
        if len(content) > FILE_CAP:
            content = content[-FILE_CAP:]
        with open(target, "w", encoding="utf-8", newline="\n") as f:
            f.write(content)
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
