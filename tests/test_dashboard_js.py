"""
The dashboard is published as a static page, so nothing type-checks it and no
Python test exercises it. `amer()` shipped as a call with no definition and the
whole deck went blank for six scheduled runs: render() threw, the fetch chain's
catch replaced <main>, and the page blamed data.json for a fault in index.html.

These tests are the cheap half of that lesson — they read the inline script and
assert (a) every function it calls is actually defined, and (b) every element it
reaches for by id exists in the markup. Both are the failure this class of bug
takes: a name that is only wrong at render time.
"""

from __future__ import annotations

import re
from pathlib import Path

INDEX = Path(__file__).resolve().parent.parent / "docs" / "index.html"

# Globals the script is entitled to call without defining them.
BUILTINS = {
    "alert", "confirm", "prompt", "fetch", "parseFloat", "parseInt", "isFinite",
    "isNaN", "String", "Number", "Boolean", "Array", "Object", "Date", "Math",
    "JSON", "Promise", "Error", "Blob", "URL", "Set", "Map", "RegExp",
    "encodeURIComponent", "decodeURIComponent", "setTimeout", "clearTimeout",
    "setInterval", "clearInterval", "requestAnimationFrame", "console",
    "document", "window", "localStorage", "crypto", "if", "for", "while",
    "switch", "catch", "return", "function", "typeof", "new", "do", "else",
    "of", "in", "instanceof", "await", "delete", "void", "try", "yield",
}


def _script() -> str:
    html = INDEX.read_text(encoding="utf-8")
    blocks = re.findall(r"<script[^>]*>(.*?)</script>", html, re.S)
    assert blocks, "no inline <script> found in docs/index.html"
    return "\n".join(blocks)


def _strip_literals(src: str) -> str:
    """
    Blank out comments, strings and regex literals, but KEEP the code inside
    `${...}` interpolations — that is where most of the rendering lives, and
    where the undefined call actually was.
    """
    out, i, n = [], 0, len(src)
    tmpl_depth = []          # brace depth per open template literal

    def prev_code_char() -> str:
        for c in reversed(out):
            if not c.isspace():
                return c
        return ""

    while i < n:
        c = src[i]
        nxt = src[i + 1] if i + 1 < n else ""

        if c == "/" and nxt == "/":
            while i < n and src[i] != "\n":
                i += 1
            continue
        if c == "/" and nxt == "*":
            i = src.find("*/", i)
            i = n if i < 0 else i + 2
            continue
        # A '/' is a regex only where a value may start, never after an operand.
        if c == "/" and prev_code_char() in "(,=:[!&|?{};+-*%~^<>" + "":
            i += 1
            while i < n and src[i] != "/":
                i += 2 if src[i] == "\\" else 1
            i += 1
            while i < n and src[i].isalpha():   # flags
                i += 1
            out.append(" ")
            continue
        if c in "'\"":
            quote, i = c, i + 1
            while i < n and src[i] != quote:
                i += 2 if src[i] == "\\" else 1
            i += 1
            out.append(" ")
            continue
        if c == "`":
            tmpl_depth.append(None)
            i += 1
            while i < n:
                if src[i] == "\\":
                    i += 2
                    continue
                if src[i] == "`":
                    i += 1
                    break
                if src[i] == "$" and i + 1 < n and src[i + 1] == "{":
                    depth, i, expr = 1, i + 2, []
                    while i < n and depth:
                        if src[i] == "{":
                            depth += 1
                        elif src[i] == "}":
                            depth -= 1
                            if not depth:
                                break
                        expr.append(src[i])
                        i += 1
                    i += 1
                    # `;` not ` `: `${team} @ ${(+p)...}` would otherwise leave
                    # `team (` behind and read as a call that was never made.
                    out.append(";" + _strip_literals("".join(expr)) + ";")
                    continue
                i += 1
            tmpl_depth.pop()
            out.append(" ")
            continue

        out.append(c)
        i += 1
    return "".join(out)


def test_every_called_helper_is_defined():
    code = _strip_literals(_script())

    defined = set(re.findall(r"\bfunction\s+([A-Za-z_$][\w$]*)", code))
    defined |= set(re.findall(r"\b(?:const|let|var)\s+([A-Za-z_$][\w$]*)", code))
    # destructured bindings: const {n,fee} = ...
    for group in re.findall(r"\b(?:const|let|var)\s*\{([^}]*)\}", code):
        defined |= {p.split(":")[-1].strip() for p in group.split(",") if p.strip()}
    # parameters — a callback passed in and then invoked is defined, not missing
    for group in re.findall(r"\bfunction\s*[\w$]*\s*\(([^)]*)\)", code):
        defined |= {p.split("=")[0].strip(" {}[]") for p in group.split(",") if p.strip()}
    for group in re.findall(r"\(([^()]*)\)\s*=>", code):
        defined |= {p.split("=")[0].strip(" {}[]") for p in group.split(",") if p.strip()}

    called = {m.group(1) for m in
              re.finditer(r"(?<![.\w$])([A-Za-z_$][\w$]*)\s*\(", code)}

    missing = sorted(called - defined - BUILTINS)
    assert not missing, f"called but never defined in docs/index.html: {missing}"


def test_every_referenced_element_id_exists():
    html = INDEX.read_text(encoding="utf-8")
    ids = set(re.findall(r'id="([^"]+)"', html))
    refs = set(re.findall(r"""\$\(['"]#([^'"]+)['"]\)""", html))
    refs |= set(re.findall(r"""getElementById\(['"]([^'"]+)['"]\)""", html))
    missing = sorted(refs - ids)
    assert not missing, f"script reaches for ids that are not in the markup: {missing}"


def test_render_reads_the_shape_the_exporter_writes():
    """The panels the exporter fills must be the panels the page reads."""
    code = _script()
    for key in ("pure_arbs", "value", "manufactured", "structure",
                "generated_at", "source"):
        assert key in code, f"index.html never reads payload key {key!r}"
