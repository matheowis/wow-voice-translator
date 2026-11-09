"""Safe reader for Lua data files (QuestieDB tables): a tokenizer and a parser for a small
expression subset. Nothing is executed.

Supported: table constructors, strings (all escapes, long brackets), numbers, nil/true/false,
dotted names resolved by a caller-supplied function (``questKeys.name`` → 1), unary minus,
``+ - * /`` and ``..``. Anything else (calls, function bodies, loops) raises :class:`LuaError`;
:meth:`Parser.entities` skips such an entry and keeps going, so one odd correction never
loses the whole file.

Files are read as latin-1 so every byte maps to one character; strings are turned back into
bytes and decoded as UTF-8, which handles both raw UTF-8 and ``\\195\\169`` escapes.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Callable

__all__ = ["LuaError", "Unresolved", "Parser", "tokenize", "read_lua", "find_tokens", "to_list"]


class LuaError(ValueError):
    pass


class Unresolved(LuaError):
    """A name the resolver does not know (a call or a constant we do not load)."""


_TOKEN = re.compile(
    r"""
    (?P<ws>\s+)
  | (?P<comment>--(?:\[(?P<ceq>=*)\[.*?\](?P=ceq)\]|[^\n]*))
  | (?P<long>\[(?P<leq>=*)\[.*?\](?P=leq)\])
  | (?P<str>"(?:[^"\\\n]|\\.|\\\n)*"|'(?:[^'\\\n]|\\.|\\\n)*')
  | (?P<num>0[xX][0-9a-fA-F]+|(?:\d+\.\d*|\.\d+|\d+)(?:[eE][+-]?\d+)?)
  | (?P<name>[A-Za-z_][A-Za-z0-9_]*)
  | (?P<op>\.\.\.|\.\.|==|~=|<=|>=|[{}\[\]=,;.+\-*/%^\#<>():])
    """,
    re.S | re.X,
)

# kinds
NAME, NUM, STR, OP = "name", "num", "str", "op"

_ESC = {"n": "\n", "t": "\t", "r": "\r", "a": "\a", "b": "\b", "f": "\f", "v": "\v",
        "\\": "\\", '"': '"', "'": "'", "\n": "\n"}
_BLOCK_OPEN = {"function", "if", "do"}


def _bytes_to_text(s: str) -> str:
    return s.encode("latin-1", "replace").decode("utf-8", "replace")


def _unescape(body: str) -> str:
    if "\\" not in body:
        return _bytes_to_text(body)
    out: list[str] = []
    i, n = 0, len(body)
    while i < n:
        ch = body[i]
        if ch != "\\":
            out.append(ch)
            i += 1
            continue
        i += 1
        if i >= n:
            break
        e = body[i]
        if e in _ESC:
            out.append(_ESC[e])
            i += 1
        elif e.isdigit():
            j = i
            while j < n and j - i < 3 and body[j].isdigit():
                j += 1
            out.append(chr(int(body[i:j]) & 0xFF))
            i = j
        elif e == "x":
            out.append(chr(int(body[i + 1:i + 3], 16)))
            i += 3
        elif e == "z":
            i += 1
            while i < n and body[i].isspace():
                i += 1
        else:
            out.append(e)
            i += 1
    return _bytes_to_text("".join(out))


def _long_body(raw: str) -> str:
    eq = raw.index("[", 1) + 1          # "[==[" -> body starts after the second "["
    body = raw[eq:len(raw) - eq]
    if body.startswith("\r\n"):
        body = body[2:]
    elif body.startswith("\n"):
        body = body[1:]
    return body


def tokenize(src: str) -> list[tuple[str, Any, int]]:
    """Tokens as (kind, value, offset). Strings are already unescaped and decoded."""
    out: list[tuple[str, Any, int]] = []
    pos, n = 0, len(src)
    match = _TOKEN.match
    append = out.append
    while pos < n:
        m = match(src, pos)
        if m is None:
            raise LuaError(f"unexpected character {src[pos]!r} at {pos}")
        kind = m.lastgroup
        if kind in ("ceq", "leq"):  # an inner group can be lastgroup; recover the outer one
            kind = "comment" if m.group("comment") is not None else "long"
        text = m.group(0)
        if kind == "name":
            append((NAME, text, pos))
        elif kind == "num":
            if text[:2] in ("0x", "0X"):
                append((NUM, int(text, 16), pos))
            elif any(c in text for c in ".eE"):
                v = float(text)
                append((NUM, int(v) if v.is_integer() and "e" not in text.lower() and "." not in text else v, pos))
            else:
                append((NUM, int(text), pos))
        elif kind == "str":
            append((STR, _unescape(text[1:-1]), pos))
        elif kind == "long":
            append((STR, _bytes_to_text(_long_body(text)), pos))
        elif kind == "op":
            append((OP, text, pos))
        pos = m.end()
    return out


def read_lua(path: Path) -> str:
    return Path(path).read_bytes().decode("latin-1")


def find_tokens(tokens: list, pattern: list[tuple[str, Any]], start: int = 0) -> int:
    """Index of the first place where ``pattern`` (kind, value-or-None) matches, or -1."""
    k = len(pattern)
    for i in range(start, len(tokens) - k + 1):
        for j, (kind, value) in enumerate(pattern):
            tk = tokens[i + j]
            if tk[0] != kind or (value is not None and tk[1] != value):
                break
        else:
            return i
    return -1


def to_list(t: Any) -> list:
    """A Lua array-like table (dict with 1..n keys, nil holes allowed) as a Python list."""
    if t is None:
        return []
    if isinstance(t, list):
        return t
    if not isinstance(t, dict):
        return [t]
    ints = [k for k in t if isinstance(k, int) and k >= 1]
    if not ints:
        return []
    return [t.get(i) for i in range(1, max(ints) + 1)]


Resolver = Callable[[list[str]], Any]


def _no_names(parts: list[str]) -> Any:
    raise Unresolved(".".join(parts))


class Parser:
    def __init__(self, tokens: list, resolve: Resolver | None = None, start: int = 0):
        self.t = tokens
        self.i = start
        self.resolve = resolve or _no_names
        self.skipped = 0
        self.skipped_examples: list[str] = []

    # -- helpers --
    def peek(self, k: int = 0):
        j = self.i + k
        return self.t[j] if j < len(self.t) else ("eof", None, -1)

    def _is(self, kind: str, value: Any = None, k: int = 0) -> bool:
        tk = self.peek(k)
        return tk[0] == kind and (value is None or tk[1] == value)

    def expect(self, kind: str, value: Any = None):
        tk = self.peek()
        if tk[0] != kind or (value is not None and tk[1] != value):
            raise LuaError(f"expected {value or kind}, got {tk[1]!r} at {tk[2]}")
        self.i += 1
        return tk

    # -- expressions --
    def expr(self) -> Any:
        v = self.term()
        while True:
            tk = self.peek()
            if tk[0] != OP or tk[1] not in ("+", "-", ".."):
                return v
            self.i += 1
            w = self.term()
            try:
                if tk[1] == "+":
                    v = v + w
                elif tk[1] == "-":
                    v = v - w
                else:
                    v = f"{v}{w}"
            except TypeError as e:
                raise LuaError(f"bad operands for {tk[1]} at {tk[2]}") from e

    def term(self) -> Any:
        v = self.factor()
        while self._is(OP, "*") or self._is(OP, "/"):
            op = self.peek()[1]
            self.i += 1
            w = self.factor()
            try:
                v = v * w if op == "*" else v / w
            except (TypeError, ZeroDivisionError) as e:
                raise LuaError(f"bad operands for {op}") from e
        return v

    def factor(self) -> Any:
        kind, value, pos = self.peek()
        if kind == OP and value == "-":
            self.i += 1
            v = self.factor()
            if not isinstance(v, (int, float)):
                raise LuaError(f"unary minus on {v!r} at {pos}")
            return -v
        if kind in (NUM, STR):
            self.i += 1
            return value
        if kind == OP and value == "{":
            self.i += 1
            return self.table()
        if kind == OP and value == "(":
            self.i += 1
            v = self.expr()
            self.expect(OP, ")")
            return v
        if kind == NAME:
            if value == "nil":
                self.i += 1
                return None
            if value == "true":
                self.i += 1
                return True
            if value == "false":
                self.i += 1
                return False
            if value in ("function", "not", "and", "or", "local", "return", "end"):
                raise LuaError(f"unsupported {value!r} at {pos}")
            parts = [value]
            self.i += 1
            while self._is(OP, ".") and self._is(NAME, None, 1):
                parts.append(self.peek(1)[1])
                self.i += 2
            if self._is(OP, "(") or self._is(OP, ":") or self._is(OP, "["):
                raise Unresolved(".".join(parts) + "(...)")
            return self.resolve(parts)
        raise LuaError(f"unexpected {value!r} at {pos}")

    def table(self) -> dict:
        """After '{'. Positional items get keys 1..n; nil values are not stored."""
        d: dict = {}
        n = 0
        while True:
            kind, value, pos = self.peek()
            if kind == OP and value == "}":
                self.i += 1
                return d
            if kind == "eof":
                raise LuaError("unterminated table")
            if kind == OP and value == "[":
                self.i += 1
                k = self.expr()
                self.expect(OP, "]")
                self.expect(OP, "=")
                v = self.expr()
                if k is not None and v is not None:
                    d[k] = v
            elif kind == NAME and self._is(OP, "=", 1):
                self.i += 2
                v = self.expr()
                if v is not None:
                    d[value] = v
            else:
                n += 1
                v = self.expr()
                if v is not None:
                    d[n] = v
            kind, value, pos = self.peek()
            if kind == OP and value in (",", ";"):
                self.i += 1
            elif not (kind == OP and value == "}"):
                raise LuaError(f"expected , or }} got {value!r} at {pos}")

    # -- robust entity tables --
    def skip_field(self) -> None:
        """Skip one table field (up to a top-level ',' ';' or the closing '}')."""
        depth = 0
        while True:
            kind, value, _ = self.peek()
            if kind == "eof":
                return
            if kind == OP and value in ("{", "(", "["):
                depth += 1
            elif kind == OP and value in ("}", ")", "]"):
                if depth == 0:
                    return
                depth -= 1
            elif kind == NAME and value in _BLOCK_OPEN:
                depth += 1
            elif kind == NAME and value == "end":
                depth -= 1
            elif kind == OP and value in (",", ";") and depth == 0:
                self.i += 1
                return
            self.i += 1

    def record(self) -> dict:
        """Like :meth:`table` (after '{'), but a field that fails is skipped instead of the whole record."""
        d: dict = {}
        n = 0
        while True:
            kind, value, pos = self.peek()
            if kind == "eof":
                raise LuaError("unterminated record")
            if kind == OP and value == "}":
                self.i += 1
                return d
            if kind == OP and value in (",", ";"):
                self.i += 1
                continue
            start = self.i
            try:
                if kind == OP and value == "[":
                    self.i += 1
                    k = self.expr()
                    self.expect(OP, "]")
                    self.expect(OP, "=")
                    v = self.expr()
                    if k is not None and v is not None:
                        d[k] = v
                elif kind == NAME and self._is(OP, "=", 1):
                    self.i += 2
                    v = self.expr()
                    if v is not None:
                        d[value] = v
                else:
                    n += 1
                    v = self.expr()
                    if v is not None:
                        d[n] = v
                kind2, value2, _ = self.peek()
                if kind2 == OP and value2 in (",", ";"):
                    self.i += 1
                elif not (kind2 == OP and value2 == "}"):
                    raise LuaError(f"expected , or }} got {value2!r}")
            except LuaError as e:
                self.i = start
                self.skip_field()
                self.skipped += 1
                if len(self.skipped_examples) < 8:
                    self.skipped_examples.append(str(e)[:120])

    def entities(self, records: bool = False) -> dict:
        """Parse ``{ [id] = {...}, ... }`` (after its '{'), skipping entries that fail.

        ``records=True`` parses each entry with :meth:`record`, so one bad field only loses that field.
        """
        out: dict = {}
        while True:
            kind, value, pos = self.peek()
            if kind == "eof":
                return out
            if kind == OP and value == "}":
                self.i += 1
                return out
            if kind == OP and value in (",", ";"):
                self.i += 1
                continue
            start = self.i
            try:
                if kind == OP and value == "[":
                    self.i += 1
                    key = self.expr()
                    self.expect(OP, "]")
                    self.expect(OP, "=")
                    if records and self._is(OP, "{"):
                        self.i += 1
                        val = self.record()
                    else:
                        val = self.expr()
                    if key is not None and val is not None:
                        out[key] = val
                elif kind == NAME and self._is(OP, "=", 1):
                    self.i += 2
                    out[value] = self.expr()
                else:
                    raise LuaError(f"unexpected {value!r} at {pos}")
                kind2, value2, _ = self.peek()
                if kind2 == OP and value2 in (",", ";"):
                    self.i += 1
                elif not (kind2 == OP and value2 == "}"):
                    raise LuaError(f"expected , or }} got {value2!r}")
            except LuaError as e:
                self.i = start
                self.skip_field()
                self.skipped += 1
                if len(self.skipped_examples) < 8:
                    self.skipped_examples.append(str(e)[:120])
