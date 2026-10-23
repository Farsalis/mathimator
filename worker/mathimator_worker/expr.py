"""Allowlisted math expressions for plots, surfaces, and curves.

The grammar is the one in section 4. Anything outside it, including the
rejected examples, raises DocumentError with "This expression is not supported."
The translator never evaluates the source.
"""

from __future__ import annotations

from dataclasses import dataclass

from mathimator_worker.document_error import DocumentError

UNSUPPORTED = "This expression is not supported."
CALLS = frozenset({"sin", "cos", "tan", "exp", "log", "sqrt"})


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
    try:
        parsed = _Parser(source, variables).parse()
    except _ParseError as exc:
        raise DocumentError(UNSUPPORTED) from exc
    return _emit(parsed, rename or {})


def assert_supported(source: str, variables: frozenset[str]) -> None:
    expression_to_python(source, variables)


class _Parser:
    def __init__(self, source: str, variables: frozenset[str]) -> None:
        self.source = source
        self.variables = variables
        self.index = 0
        self.length = len(source)

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
        left = self._parse_factor()
        while self._peek() in {"*", "/"}:
            operator = self.source[self.index]
            self.index += 1
            right = self._parse_factor()
            left = Binary(operator, left, right)
        return left

    def _parse_factor(self) -> Expression:
        left = self._parse_primary()
        if self._peek() == "^":
            self.index += 1
            right = self._parse_primary()
            return Binary("**", left, right)
        return left

    def _parse_primary(self) -> Expression:
        self._skip()
        if self.index >= self.length:
            raise _ParseError
        character = self.source[self.index]
        if character == "-":
            self.index += 1
            return UnaryMinus(self._parse_primary())
        if character == "(":
            self.index += 1
            inner = self._parse_expr()
            if self._peek() != ")":
                raise _ParseError
            self.index += 1
            return Group(inner)
        if character.isdigit() or character == ".":
            return self._parse_number()
        if character.isalpha() or character == "_":
            return self._parse_name()
        raise _ParseError

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
            argument = self._parse_expr()
            if self._peek() == ",":
                raise _ParseError
            if self._peek() != ")":
                raise _ParseError
            self.index += 1
            return Call(name, argument)
        if name in self.variables and self._peek() != "(":
            return Variable(name)
        raise _ParseError


_PRECEDENCE = {"+": 1, "-": 1, "*": 2, "/": 2, "**": 3}


def _precedence(expression: Expression) -> int:
    if isinstance(expression, Binary):
        return _PRECEDENCE[expression.operator]
    if isinstance(expression, UnaryMinus):
        return 4
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
        left = _parenthesize(expression.left, expression.operator, rename, on_right=False)
        right = _parenthesize(expression.right, expression.operator, rename, on_right=True)
        return f"{left} {expression.operator} {right}"
    raise TypeError(f"unknown expression {type(expression).__name__}")


def _parenthesize(
    expression: Expression,
    operator: str,
    rename: dict[str, str],
    *,
    on_right: bool,
) -> str:
    parent = _PRECEDENCE[operator]
    child = _precedence(expression)
    needs = child < parent
    if not needs and child == parent and on_right and operator in {"-", "/", "**"}:
        needs = True
    if not needs and operator == "**" and isinstance(expression, UnaryMinus):
        needs = True
    rendered = _emit(expression, rename)
    if needs:
        return f"({rendered})"
    return rendered


def _format_number(value: float) -> str:
    rounded = round(value, 3)
    if abs(rounded - round(rounded)) < 1e-9:
        return str(int(round(rounded)))
    return f"{rounded:.3f}".rstrip("0").rstrip(".")
