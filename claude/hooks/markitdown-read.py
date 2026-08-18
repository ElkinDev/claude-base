#!/usr/bin/env python3
"""PreToolUse hook: convert a document to Markdown before Claude reads it.

When Claude is about to Read a PDF or Office document, convert it to Markdown
with MarkItDown first and redirect the Read to the generated .md file.

If the document has no usable text layer (for example a scanned PDF) or the
conversion fails, emit nothing so the native visual Read proceeds on the
original file. That keeps scanned documents readable instead of feeding an
empty .md back to the model.
"""
import sys
import json
import os

DOC_EXTS = {".pdf", ".docx", ".pptx", ".xlsx", ".xls", ".doc", ".ppt", ".epub"}
MIN_TEXT = 16


def emit(md_path, tool_input):
    new_input = dict(tool_input)
    new_input["file_path"] = md_path
    new_input.pop("pages", None)
    src = os.path.basename(tool_input.get("file_path", ""))
    out = {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "allow",
            "permissionDecisionReason": "Auto-converted to Markdown via markitdown",
            "updatedInput": new_input,
            "additionalContext": (
                "Read redirected by the markitdown hook: "
                "%s was converted to Markdown; reading %s instead."
                % (src, os.path.basename(md_path))
            ),
        }
    }
    sys.stdout.write(json.dumps(out))


def main():
    try:
        raw = sys.stdin.buffer.read().decode("utf-8-sig")
        data = json.loads(raw)
    except Exception:
        return
    if data.get("tool_name") != "Read":
        return
    tool_input = data.get("tool_input") or {}
    fp = tool_input.get("file_path") or ""
    ext = os.path.splitext(fp)[1].lower()
    if ext not in DOC_EXTS or not os.path.isfile(fp):
        return

    md_path = fp + ".md"

    # Reuse a fresh cached conversion.
    if os.path.isfile(md_path) and os.path.getmtime(md_path) >= os.path.getmtime(fp):
        emit(md_path, tool_input)
        return

    try:
        from markitdown import MarkItDown
        result = MarkItDown(enable_plugins=False).convert(fp)
        text = getattr(result, "markdown", None) or getattr(result, "text_content", "") or ""
    except Exception:
        return  # conversion unavailable or failed; let native Read handle it

    if len(text.strip()) < MIN_TEXT:
        return  # no usable text layer (scanned?); fall back to visual Read

    try:
        with open(md_path, "w", encoding="utf-8") as f:
            f.write(text)
    except Exception:
        return

    emit(md_path, tool_input)


if __name__ == "__main__":
    main()
