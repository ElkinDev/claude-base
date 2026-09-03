#!/usr/bin/env python3
"""What a shell command reads, and how much of what it reads reaches the window.

`guard-read.py` beside this file applies the two rules; this module answers the question they
need answered first: which files would this command line print, and would it print them whole.
The two travel together, since the installer copies the whole hooks directory.

The line is split on the shell separators, each piece is parsed for read targets and for the
slice it already asks for, and the command inside a `$( )` or a backtick span is parsed the same
way, since what it prints is pasted into the line that runs it.

A piece is left alone only when its bytes do not reach the window: its output is written to a
file, or a later stage of its pipeline narrows the stream to a match, a count or a bounded
slice. A pipe by itself is not enough, since the last stage of `cat big.txt | cat` prints every
byte the first stage read. An input redirect is not a redirect away from the window at all:
`cat < big.txt` reads that file and prints it, so the word after `<` is a read target like any
other, while `<<` and `<<<` name a heredoc delimiter or a literal string.

Anything that is not one of the reading commands passes untouched. This parses the shapes a file
usually arrives in; it is not a sandbox. A command it does not know, `sort` or a script of your
own, is not judged, and that same tolerance is what lets a `grep` at the end of a pipeline pass.
"""
import os
import re

# The two numbers the guard denies on. They live here because the parser needs them to tell a
# bounded slice from a whole file, and guard-read.py imports them, so there is one definition.
# 48 KB is about 12k tokens, the ceiling for one whole tool result, chosen on the token flow
# measurement of 2026-09-03, where text results of 60 to 150 KB were the expensive class. The
# separate measurement, that whole results stop being re-attached above about 12 KB, is in
# docs/CONTEXT-ECONOMICS.md lines 150 to 152 and is not the reason for this limit. It is a
# ceiling on unbounded reads only, never on ranged ones: a read that already asks for
# ALLOWED_LIMIT_LINES lines or fewer passes at any file size, and images and PDFs are exempt
# from the byte rule entirely (guard-read.py, SIZE_EXEMPT_EXTENSIONS).
SIZE_LIMIT_BYTES = 48 * 1024
ALLOWED_LIMIT_LINES = 400

COUNT_FLAGS = ("-totalcount", "-head", "-first", "-tail", "-last")
VALUE_FLAGS = COUNT_FLAGS + ("-path", "-literalpath", "-encoding", "-readcount", "-delimiter",
                             "-filter", "-include", "-exclude", "-stream")
PATH_FLAGS = ("-path", "-literalpath")
SED_RANGE = re.compile(r"^(\d+)(?:,(\d+))?p$")
DASH_NUMBER = re.compile(r"^-(\d+)$")
DRIVE_PATH = re.compile(r"^/([a-zA-Z])/")
SHORT_FLAGS = re.compile(r"^-[a-zA-Z]+$")
REDIRECT_STOP = " \t;\n|&<>"
# Pipeline stages that let through only what was asked for. A large file read into one of these
# is the shape the guard already allows when the same command opens the file itself, so the pipe
# form is allowed too. Every other stage is read as passing its input on to the window.
NARROWING_COMMANDS = ("grep", "egrep", "fgrep", "rg", "findstr", "select-string", "sls",
                      "wc", "measure-object")


def redirect_word(command, index):
    """(word, next index) for the word a redirect points at, quotes removed."""
    word, quote = [], None
    while index < len(command) and command[index] in " \t":
        index += 1
    while index < len(command):
        char = command[index]
        if quote:
            if char == quote:
                quote = None
            else:
                word.append(char)
        elif char in "'\"":
            quote = char
        elif char in REDIRECT_STOP:
            break
        else:
            word.append(char)
        index += 1
    return "".join(word), index


def substitutions(text, backticks=True):
    """The command inside every `$( )` span, and inside every backtick pair in a POSIX shell.
    A single-quoted span is literal in both shells and is skipped. In PowerShell a backtick is
    the escape character, not a substitution, so that form is read only when asked for."""
    found, index = [], 0
    while index < len(text):
        char = text[index]
        if char == "'" or (char == "`" and backticks):
            end = text.find(char, index + 1)
            if end < 0:
                break
            if char == "`":
                found.append(text[index + 1:end])
            index = end + 1
            continue
        if text.startswith("$(", index):
            depth, index = 1, index + 2
            start = index
            while index < len(text) and depth:
                depth += (text[index] == "(") - (text[index] == ")")
                index += 1
            if depth:
                break
            found.append(text[start:index - 1])
            continue
        index += 1
    return found


def bounds_stream(tokens):
    """True when a pipeline stage lets through only part of what it reads: a bounded slice, a
    match, a count. Everything else is taken to pass its input on, which is what `cat`,
    `base64` and `Out-String` do."""
    if not tokens:
        return False
    name = os.path.basename(tokens[0]).lower()
    if name.endswith(".exe"):
        name = name[:-4]
    if name in NARROWING_COMMANDS:
        return True
    plan = read_plan(tokens)
    if not plan:
        return False
    limit = plan[1]
    return limit is not None and limit <= ALLOWED_LIMIT_LINES


def ends_elsewhere(pieces, position):
    """True when what a piece prints never reaches the window: it is written to a file, or a
    later stage of its pipeline narrows the stream before the window sees it."""
    _, piped, to_file = pieces[position]
    if to_file:
        return True
    while piped and position + 1 < len(pieces):
        position += 1
        text, piped, to_file = pieces[position]
        if to_file or bounds_stream(tokenize(text)):
            return True
    return False


def split_segments(command, backticks=True):
    """[(text, elsewhere)] for every command in a shell line, including the ones inside a
    substitution. `elsewhere` is True when the bytes that piece prints do not reach the
    window."""
    pieces, buf, quote, to_file, index = [], [], None, False, 0
    while index < len(command):
        char = command[index]
        if quote:
            buf.append(char)
            if char == quote:
                quote = None
            index += 1
            continue
        if char in "'\"":
            quote = char
            buf.append(char)
            index += 1
            continue
        if command[index:index + 2] in ("&&", "||"):
            pieces.append(("".join(buf), False, to_file))
            buf, to_file, index = [], False, index + 2
            continue
        if char in ";\n&":
            pieces.append(("".join(buf), False, to_file))
            buf, to_file, index = [], False, index + 1
            continue
        if char == "|":
            pieces.append(("".join(buf), True, to_file))
            buf, to_file, index = [], False, index + 1
            continue
        if char == ">":
            # an output redirect: those bytes go to a file, not to the window
            to_file = True
            while index < len(command) and command[index] not in ";\n|&":
                index += 1
            continue
        if char == "<":
            if command[index:index + 2] == "<<":
                # a heredoc or a here-string: the word names a delimiter, not a file
                index += 3 if command[index:index + 3] == "<<<" else 2
                _, index = redirect_word(command, index)
                continue
            word, index = redirect_word(command, index + 1)
            if word:
                buf.append(" %s " % word)  # read into this command, and printed by it
            continue
        buf.append(char)
        index += 1
    pieces.append(("".join(buf), False, to_file))
    segments = []
    for position in range(len(pieces)):
        text = pieces[position][0].strip()
        if not text:
            continue
        elsewhere = ends_elsewhere(pieces, position)
        segments.append((text, elsewhere))
        for inner in substitutions(text, backticks):
            # what a substitution prints is pasted into the line that runs it, so it reaches
            # the window wherever that line's own output goes
            for nested, nested_elsewhere in split_segments(inner, backticks):
                segments.append((nested, elsewhere or nested_elsewhere))
    return segments


def tokenize(segment):
    """Words of one command, quotes honoured and removed, backslashes left alone so a
    Windows path survives."""
    tokens, current, quote, quoted = [], [], None, False
    for char in segment:
        if quote:
            if char == quote:
                quote = None
            else:
                current.append(char)
            continue
        if char in "'\"":
            quote, quoted = char, True
            continue
        if char.isspace():
            if current or quoted:
                tokens.append("".join(current))
                current, quoted = [], False
            continue
        current.append(char)
    if current or quoted:
        tokens.append("".join(current))
    return tokens


def as_count(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def sed_lines(scripts):
    """How many lines `sed -n` prints for these scripts, or None when that cannot be told.
    Every -e adds its own slice, so the total is the sum over all of them."""
    total = 0
    for script in scripts:
        for part in script.split(";"):
            match = SED_RANGE.match(part.strip())
            if not match:
                return None
            first, last = int(match.group(1)), int(match.group(2) or match.group(1))
            total += max(0, last - first + 1)
    return total


def read_plan(tokens):
    """(paths, max_lines) for a command that prints a file, else None. max_lines is None
    when the whole file would be printed and 0 when the slice is bounded by bytes."""
    if not tokens:
        return None
    name = os.path.basename(tokens[0]).lower()
    if name.endswith(".exe"):
        name = name[:-4]
    args, paths, count = tokens[1:], [], None
    if name in ("cat", "type"):
        return [a for a in args if not a.startswith("-")], None
    if name in ("head", "tail"):
        index = 0
        while index < len(args):
            arg = args[index]
            if arg in ("-n", "-c", "--lines", "--bytes"):
                value = args[index + 1] if index + 1 < len(args) else ""
                count = as_count(value)
                if name == "tail" and value.startswith("+"):
                    # an offset, not a count: `tail -n +100` prints line 100 to the end
                    count = None
                elif arg in ("-c", "--bytes") and count is not None:
                    count = 0 if count <= SIZE_LIMIT_BYTES else None
                index += 2
                continue
            if DASH_NUMBER.match(arg):
                count = int(arg[1:])
            elif not arg.startswith("-"):
                paths.append(arg)
            index += 1
        return paths, 10 if count is None and not any(a in ("-n", "-c", "--lines", "--bytes") for a in args) else count
    if name == "sed":
        return sed_plan(args, paths)
    if name in ("get-content", "gc"):
        index = 0
        while index < len(args):
            arg = args[index]
            lowered = arg.lower()
            if lowered in VALUE_FLAGS:
                value = args[index + 1] if index + 1 < len(args) else ""
                if lowered in COUNT_FLAGS:
                    count = as_count(value)
                elif lowered in PATH_FLAGS:
                    paths.append(value)
                index += 2
                continue
            if arg.startswith("-"):
                index += 1
                continue
            paths.append(arg)
            index += 1
        return paths, count
    return None


def sed_plan(args, paths):
    """(paths, max_lines) for a sed command. Every -e carries a script of its own and short
    flags come bundled (-ne), so the scripts are collected as they are read instead of taking
    the first bare word for the whole program."""
    if any(a == "-i" or a.startswith("--in-place") or (a.startswith("-i") and len(a) > 2) for a in args):
        return None
    quiet, scripts, unknown, index = False, [], False, 0
    while index < len(args):
        arg = args[index]
        if arg == "--expression" or arg.startswith("--expression="):
            if "=" in arg:
                scripts.append(arg.split("=", 1)[1])
                index += 1
            else:
                scripts.append(args[index + 1] if index + 1 < len(args) else "")
                index += 2
            continue
        if arg in ("-f", "--file") or arg.startswith("--file="):
            unknown = True  # the script is in a file, so what it prints cannot be told
            index += 2 if arg in ("-f", "--file") else 1
            continue
        if arg in ("--quiet", "--silent"):
            quiet = True
            index += 1
            continue
        if SHORT_FLAGS.match(arg):
            letters = arg[1:]
            if "n" in letters:
                quiet = True
            if letters.endswith("e"):  # -e, or a bundle ending in it, takes the next word
                scripts.append(args[index + 1] if index + 1 < len(args) else "")
                index += 2
                continue
            if "e" in letters:
                unknown = True
            index += 1
            continue
        if arg.startswith("-"):
            index += 1
            continue
        if not scripts and not unknown:
            scripts.append(arg)
        else:
            paths.append(arg)
        index += 1
    return paths, (sed_lines(scripts) if quiet and scripts and not unknown else None)


def resolve(raw, cwd):
    path = os.path.expanduser(raw)
    if not os.path.isabs(path) and cwd:
        path = os.path.join(cwd, path)
    if os.path.isfile(path):
        return path
    match = DRIVE_PATH.match(raw)
    if match:  # a Git Bash path, /c/Repo/... for C:/Repo/...
        translated = "%s:/%s" % (match.group(1).upper(), raw[3:])
        if os.path.isfile(translated):
            return translated
    return path


def reads(command, cwd, backticks=True):
    """(path, max_lines, elsewhere) for every existing file this command line would print."""
    for segment, elsewhere in split_segments(command, backticks):
        plan = read_plan(tokenize(segment))
        if not plan:
            continue
        targets, limit = plan
        for target in targets:
            path = resolve(target, cwd)
            if os.path.isfile(path):
                yield path, limit, elsewhere
