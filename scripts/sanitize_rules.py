"""The rule sets behind the sanitization guard.

Three of them. The committed generic rules describe the shape of a leak without naming anything.
The private denylist, untracked, holds the real names; nothing matched by it is ever echoed. The
allowlist clears a sanctioned placeholder, and it clears it by full-matching the name a rule
captured, never a substring of the line, so a placeholder cannot cover a longer real value that
merely starts with it.

`scripts/sanitize-check.py` is the command; `scripts/sanitize_guard.py` does the scanning.
"""
import os
import re
from collections import namedtuple
from pathlib import Path

# id: the rule id printed in a finding. names: the rule is also applied to path components.
Rule = namedtuple("Rule", "id regex private names")
Finding = namedtuple("Finding", "rule label line fragment private")
Waived = namedtuple("Waived", "rule label line")

PRIVATE_DIR = ".sanitize"
PRIVATE_PATH = os.path.join(PRIVATE_DIR, "private-denylist.txt")

MAX_BYTES = 2 * 1024 * 1024
SNIFF_BYTES = 8192
FRAGMENT = 60
NAMELESS_LIMIT = 64 * 1024

REDACTED = "redacted"
REDACTED_PATH = "<redacted path>"
FIXTURE_SEGMENT = "fixtures"
ALL = "all"
WAIVER = re.compile(r"sanitize-ok:[ \t]*([A-Za-z0-9_.-]+)")

# A literal denylist term is bounded by anything that is not a letter or a digit, and also by the
# hump of a CamelCase word and by a digit: for a term `acme`, AcmeViewModel, ACME_API_ID, acme_app,
# XAcmeClient, v2AcmeClient and ACMEapp are hits while acmeish and ACMEISH are not. The known
# limitation is an all-caps term glued to more capitals, ACMEAPP or MYACME, which cannot be told
# apart from an ordinary word (CACHED carries ACHE) without false positives; a project that uses
# such a constant covers it with an explicit `regex:` line on its denylist.
TERM_START = (r"(?:(?<![A-Za-z0-9])|(?<=[a-z])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])|(?<=[0-9]))")

TEXT_EXTENSIONS = frozenset("""
.md .txt .py .ps1 .sh .json .yml .yaml .toml .ini .cfg .xml .html .css .js .ts .kt .java .cs
.sql .csv .gitignore .gitattributes
""".split())


class GuardError(Exception):
    """Something the run cannot recover from: a bad rule set, an unreadable file, git failing."""


def read_lines(path, required=True):
    try:
        return Path(path).read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as error:
        if required:
            raise GuardError("cannot read %s: %s" % (path, error))
        return []


def word_edge(character):
    return character.isalnum() or character == "_"


def term_pattern(term):
    """A literal denylist term as a regex. Case-insensitive, but never inside a longer word."""
    body = re.escape(term)
    caps = re.escape(term.upper())
    head = TERM_START if word_edge(term[0]) else ""
    if not word_edge(term[-1]):
        return head + "(?i:" + body + ")"
    # The all-caps branch stops only at a capital that is not starting a new CamelCase word, so
    # a term glued to a capitalised word is a hit while one glued to more capitals cannot be told
    # apart from an ordinary word and stays a documented miss.
    tail = r"(?![A-Z](?![a-z]))"
    return head + "(?:" + caps + tail + r"|(?!" + caps + ")(?i:" + body + r")(?![a-z]))"


QUANTIFIERS = "+*{"


def nested_quantifier(pattern):
    """True when a quantified group or class is itself quantified: the shape that never returns.

    Python's re has no timeout, so a denylist line like `(a+)+$` would spin on a long line and
    take the whole push with it. Escaped characters are dropped first, then every closer is
    matched to its opener: a group that carries a quantifier and is quantified again is refused.
    """
    plain = re.sub(r"\\.", "", pattern, flags=re.S)
    stack = []
    for index, character in enumerate(plain):
        if character in "([":
            stack.append(index)
        elif character in ")]" and stack:
            body = plain[stack.pop() + 1:index]
            after = plain[index + 1:index + 2]
            if after in QUANTIFIERS and after and any(item in body for item in QUANTIFIERS):
                return True
    return False


def compile_rule(rule_id, pattern, private=False, names=False, flags=0):
    try:
        return Rule(rule_id, re.compile(pattern, flags), private, names)
    except re.error as error:
        raise GuardError("rule %s does not compile: %s" % (rule_id, error))


def load_rules(path):
    """The committed generic rules: `id<TAB>regex`, with an optional third column of flags."""
    rules = []
    for line in read_lines(path):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if "\t" not in line:
            raise GuardError("rule line without a tab in %s: %s" % (path, line.strip()))
        fields = [field.strip() for field in line.split("\t")]
        rule_id, pattern = fields[0], fields[1]
        if not rule_id or not pattern:
            # An empty pattern matches every line and an empty id prints as nothing, so both are
            # the same hard failure as a line with no tab at all.
            raise GuardError("rule line without an id or a pattern in %s: %s"
                             % (path, line.strip()))
        flags = fields[2].split(",") if len(fields) > 2 and fields[2] else []
        rules.append(compile_rule(rule_id, pattern, names="name" in flags))
    return rules


def load_private(path):
    """The untracked denylist as rules private-1..n. Absent is the normal case (CI never has it).

    A `regex:` line is taken as written and matched case-insensitively; anything else is a literal
    term and gets the CamelCase boundaries above.
    """
    rules = []
    for line in read_lines(path, required=False):
        entry = line.strip()
        if not entry or entry.startswith("#"):
            continue
        rule_id = "private-%d" % (len(rules) + 1)
        pattern = entry[6:].strip() if entry.startswith("regex:") else ""
        if pattern and nested_quantifier(pattern):
            # Refused rather than compiled, and the pattern is never echoed: the terms are the
            # thing this file exists to keep out of the output.
            raise GuardError("rule %s rejected: nested quantifier, which can hang the scan on a "
                             "long line; rewrite it as an alternation or a literal term"
                             % rule_id)
        try:
            if pattern:
                rules.append(compile_rule(rule_id, pattern, private=True, names=True,
                                          flags=re.IGNORECASE))
            else:
                rules.append(compile_rule(rule_id, term_pattern(entry), private=True, names=True))
        except GuardError:
            # re's own message quotes the pattern (a bad group name comes back verbatim), and
            # nothing can redact it here because the redaction rules are what failed to load.
            raise GuardError("rule %s does not compile" % rule_id)
    return rules


def load_allow(path):
    """Allow entries, compiled to be full-matched against a captured name."""
    allows = []
    for line in read_lines(path):
        entry = line.strip()
        if not entry or entry.startswith("#"):
            continue
        pattern = entry[6:].strip() if entry.startswith("regex:") else re.escape(entry)
        try:
            allows.append(re.compile(pattern, re.IGNORECASE))
        except re.error as error:
            raise GuardError("allow entry does not compile: %s (%s)" % (entry, error))
    return allows


def allowed(rule, match, allows):
    """True when the name this rule captured is a sanctioned placeholder or known-good value.

    A rule that captures no `name` group can never be allowlisted, which is what keeps credential
    shapes out of reach of the allowlist.
    """
    if "name" not in rule.regex.groupindex:
        return False
    name = match.group("name")
    if not name:
        return False
    for entry in allows:
        if entry.fullmatch(name):
            return True
    return False


def redact(text, private_rules):
    """Any private term inside a message we are about to print, replaced by its rule id."""
    for rule in private_rules:
        text = rule.regex.sub("<%s>" % rule.id, text)
    return text


def trim(text):
    text = " ".join(text.split())
    return text if len(text) <= FRAGMENT else text[:FRAGMENT - 3] + "..."


def as_posix(label):
    return label.replace("\\", "/")


def is_fixture(label):
    """True when a `fixtures` directory is one of the path segments above the file."""
    return FIXTURE_SEGMENT in as_posix(label).split("/")[:-1]


def is_text_name(label, size):
    """Whether a file that carries a NUL byte was still meant to be text."""
    name = as_posix(label).rsplit("/", 1)[-1]
    extension = os.path.splitext(name)[1].lower()
    if not extension and name.startswith("."):
        extension = name.lower()
    if extension in TEXT_EXTENSIONS:
        return True
    return not extension and size <= NAMELESS_LIMIT


def under_private_dir(label):
    return PRIVATE_DIR in as_posix(label).split("/")
