"""Allowlisted math expressions for plots, surfaces, and curves.

The grammar is the one in section 4. Anything outside it, including the
rejected examples, raises DocumentError with "This expression is not supported."
So does a source over 1000 characters or nested deeper than 64 levels,
which keeps parsing and emitting well inside the interpreter's stack. The
translator never evaluates the source.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass

from mathimator_worker.document_error import DocumentError

UNSUPPORTED = "This expression is not supported."
CALLS = frozenset({"sin", "cos", "tan", "exp", "log", "sqrt"})
MAX_EXPRESSION_LENGTH = 1000
MAX_EXPRESSION_DEPTH = 64


class _ParseError(Exception):
    pass


@dataclass(frozen=True)
class Number:
    value: float


@dataclass(frozen=True)
class Variable:
    name: str


@dataclass(frozen=True)
class Pi:
    pass


@dataclass(frozen=True)
class Call:
    name: str
    argument: Expression


@dataclass(frozen=True)
class UnaryMinus:
    operand: Expression


@dataclass(frozen=True)
class Group:
    inner: Expression


@dataclass(frozen=True)
class Binary:
    operator: str
    left: Expression
    right: Expression


Expression = Number | Variable | Pi | Call | UnaryMinus | Group | Binary


def expression_to_python(
    source: str,
    variables: frozenset[str],
    rename: dict[str, str] | None = None,
) -> str:
    """Return the lambda body for ``source``, or raise DocumentError.

    ``rename`` substitutes variable names after parsing. A surface expression
    is written in ``x`` and ``y`` and emitted as the ``u`` and ``v`` parameters
    of ``Surface``.
    """
    return _emit(_parse(source, variables, None), rename or {})


def assert_supported(source: str, variables: frozenset[str], field: str) -> None:
    """Refuse ``source`` with ``unsupported_expression`` naming ``field``."""
    _parse(source, variables, field)


def _parse(source: str, variables: frozenset[str], field: str | None) -> Expression:
    try:
        if len(source) > MAX_EXPRESSION_LENGTH:
            raise _ParseError
        return _Parser(source, variables).parse()
    except _ParseError as exc:
        raise DocumentError(UNSUPPORTED, code="unsupported_expression", field=field) from exc


class _Parser:
    def __init__(self, source: str, variables: frozenset[str]) -> None:
        self.source = source
        self.variables = variables
        self.index = 0
        self.length = len(source)
        self.depth = 0

    def parse(self) -> Expression:
        self._skip()
        if self.index >= self.length:
            raise _ParseError
        expression = self._parse_expr()
        self._skip()
        if self.index != self.length:
            raise _ParseError
        return expression

    def _skip(self) -> None:
        while self.index < self.length and self.source[self.index].isspace():
            self.index += 1

    def _peek(self) -> str:
        self._skip()
        if self.index >= self.length:
            return ""
        return self.source[self.index]

    def _parse_expr(self) -> Expression:
        left = self._parse_term()
        while self._peek() in {"+", "-"}:
            operator = self.source[self.index]
            self.index += 1
            right = self._parse_term()
            left = Binary(operator, left, right)
        return left

    def _parse_term(self) -> Expression:
        left = self._parse_unary()
        while self._peek() in {"*", "/"}:
            operator = self.source[self.index]
            self.index += 1
            right = self._parse_unary()
            left = Binary(operator, left, right)
        return left

    def _parse_unary(self) -> Expression:
        """A unary minus applies to the power after it, so ``-x^2`` is ``-(x^2)``."""
        if self._peek() == "-":
            self.index += 1
            return UnaryMinus(self._nested(self._parse_unary))
        return self._parse_power()

    def _parse_power(self) -> Expression:
        base = self._parse_primary()
        if self._peek() == "^":
            self.index += 1
            return Binary("**", base, self._parse_exponent())
        return base

    def _parse_exponent(self) -> Expression:
        """One primary after ``^``, which may carry minus signs: ``2^-x`` parses and ``x^2^2`` does not."""
        if self._peek() == "-":
            self.index += 1
            return UnaryMinus(self._nested(self._parse_exponent))
        return self._parse_primary()

    def _parse_primary(self) -> Expression:
        self._skip()
        if self.index >= self.length:
            raise _ParseError
        character = self.source[self.index]
        if character == "(":
            self.index += 1
            inner = self._nested(self._parse_expr)
            if self._peek() != ")":
                raise _ParseError
            self.index += 1
            return Group(inner)
        if character.isdigit() or character == ".":
            return self._parse_number()
        if character.isalpha() or character == "_":
            return self._parse_name()
        raise _ParseError

    def _nested(self, parse: Callable[[], Expression]) -> Expression:
        """Parse one level deeper: a group, a call argument or a unary minus operand."""
        if self.depth == MAX_EXPRESSION_DEPTH:
            raise _ParseError
        self.depth += 1
        expression = parse()
        self.depth -= 1
        return expression

    def _parse_number(self) -> Number:
        start = self.index
        saw_dot = False
        while self.index < self.length:
            character = self.source[self.index]
            if character.isdigit():
                self.index += 1
                continue
            if character == "." and not saw_dot:
                saw_dot = True
                self.index += 1
                continue
            break
        text = self.source[start : self.index]
        if text in {"", ".", "-"}:
            raise _ParseError
        try:
            value = float(text)
        except ValueError as exc:
            raise _ParseError from exc
        if not math.isfinite(value):
            raise _ParseError
        return Number(value)

    def _parse_name(self) -> Expression:
        start = self.index
        while self.index < self.length and (self.source[self.index].isalnum() or self.source[self.index] == "_"):
            self.index += 1
        name = self.source[start : self.index]
        if name == "pi" and self._peek() != "(":
            return Pi()
        if name in CALLS and self._peek() == "(":
            self.index += 1
            argument = self._nested(self._parse_expr)
            if self._peek() == ",":
                raise _ParseError
            if self._peek() != ")":
                raise _ParseError
            self.index += 1
            return Call(name, argument)
        if name in self.variables and self._peek() != "(":
            return Variable(name)
        raise _ParseError


# As in Python, a unary minus binds tighter than * and / and looser than **.
_PRECEDENCE = {"+": 1, "-": 1, "*": 2, "/": 2, "**": 4}
_UNARY_PRECEDENCE = 3


def _precedence(expression: Expression) -> int:
    if isinstance(expression, Binary):
        return _PRECEDENCE[expression.operator]
    if isinstance(expression, UnaryMinus):
        return _UNARY_PRECEDENCE
    return 5


def _emit(expression: Expression, rename: dict[str, str]) -> str:
    if isinstance(expression, Number):
        return _format_number(expression.value)
    if isinstance(expression, Variable):
        return rename.get(expression.name, expression.name)
    if isinstance(expression, Pi):
        return "np.pi"
    if isinstance(expression, Call):
        return f"np.{expression.name}({_emit(expression.argument, rename)})"
    if isinstance(expression, Group):
        return f"({_emit(expression.inner, rename)})"
    if isinstance(expression, UnaryMinus):
        operand = expression.operand
        if isinstance(operand, (Number, Variable, Pi, Call, Group)):
            return f"-{_emit(operand, rename)}"
        return f"-({_emit(operand, rename)})"
    if isinstance(expression, Binary):
        return _emit_chain(expression, rename)
    raise TypeError(f"unknown expression {type(expression).__name__}")


def _emit_chain(expression: Binary, rename: dict[str, str]) -> str:
    """Emit a left-nested run such as ``x + x + ... + x`` without recursing per operator.

    The parser builds ``a + b + c`` as ``(a + b) + c``, so a 1000-character sum
    is a chain hundreds of operators deep. Only right operands recurse, and the
    nesting limit bounds their depth.
    """
    chain: list[Binary] = []
    node: Expression = expression
    while isinstance(node, Binary):
        chain.append(node)
        node = node.left
    text = _emit(node, rename)
    for parent in reversed(chain):
        if _needs_parentheses(node, parent.operator, on_right=False):
            text = f"({text})"
        right = _parenthesize(parent.right, parent.operator, rename, on_right=True)
        text = f"{text} {parent.operator} {right}"
        node = parent
    return text


def _parenthesize(
    expression: Expression,
    operator: str,
    rename: dict[str, str],
    *,
    on_right: bool,
) -> str:
    rendered = _emit(expression, rename)
    if _needs_parentheses(expression, operator, on_right=on_right):
        return f"({rendered})"
    return rendered


def _needs_parentheses(expression: Expression, operator: str, *, on_right: bool) -> bool:
    parent = _PRECEDENCE[operator]
    child = _precedence(expression)
    if child < parent:
        return True
    return child == parent and on_right and operator in {"-", "/", "**"}


def _format_number(value: float) -> str:
    """A literal exactly as typed. A whole number prints as an integer, so ``2*x`` stays ``2 * x``."""
    if value.is_integer() and abs(value) < 2**53:
        return str(int(value))
    return repr(value)
