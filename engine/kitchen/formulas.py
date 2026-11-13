"""Безопасный вычислитель формул construction.yaml.

Разбирает строки вроде ``"H - 4"`` и ``"(внутр_ширина - 75) x (500 - 10)"`` в дерево
и считает над именованными переменными. Никакого ``eval``: только целые числа,
имена переменных, скобки, ``+ - * /`` и разделитель пары размеров ``x``/``х``.
Деление — только нацело. Все ошибки — по-русски: ``FormulaError`` превращается
в ошибку загрузки мастерской (см. `compile_formula` и `kitchen.models`).
"""

import re

__all__ = ["Formula", "FormulaError", "compile_formula", "evaluate", "parse", "variables"]

_TOKEN_RE = re.compile(r"[0-9]+(?:\.[0-9]+)?|[^\W\d][\w]*|[()+\-*/]", re.UNICODE)


class FormulaError(ValueError):
    """Формула не разобралась или не посчиталась. Сообщение по-русски."""


def _tokenize(text: str) -> list[str]:
    tokens: list[str] = []
    pos = 0
    for m in _TOKEN_RE.finditer(text):
        if text[pos : m.start()].strip():
            raise FormulaError(f"неизвестный символ в формуле «{text}»")
        tokens.append(m.group())
        pos = m.end()
    if text[pos:].strip():
        raise FormulaError(f"неизвестный символ в формуле «{text}»")
    return tokens


# Дерево формулы: ("num", n) | ("var", name) | ("+"| "-" | "*" | "/", a, b) | ("pair", a, b)
Node = tuple


class _Parser:
    """Рекурсивный спуск: expr → term → factor. Сверху — необязательная пара через «x»."""

    def __init__(self, text: str):
        self.text = text
        self.tokens = _tokenize(text)
        self.pos = 0

    def error(self, message: str) -> FormulaError:
        return FormulaError(f"{message} в формуле «{self.text}»")

    def peek(self) -> str | None:
        return self.tokens[self.pos] if self.pos < len(self.tokens) else None

    def parse(self) -> Node:
        node = self.expr()
        if self.peek() in ("x", "х"):  # разделитель пары размеров
            self.pos += 1
            node = ("pair", node, self.expr())
        if self.peek() is not None:
            raise self.error(f"неожиданный элемент «{self.peek()}»")
        return node

    def expr(self) -> Node:
        node = self.term()
        while self.peek() in ("+", "-"):
            op = self.tokens[self.pos]
            self.pos += 1
            node = (op, node, self.term())
        return node

    def term(self) -> Node:
        node = self.factor()
        while self.peek() in ("*", "/"):
            op = self.tokens[self.pos]
            self.pos += 1
            node = (op, node, self.factor())
        return node

    def factor(self) -> Node:
        token = self.peek()
        if token is None:
            raise self.error("формула оборвалась, ожидалось число, переменная или «(»")
        self.pos += 1
        if token == "(":
            node = self.expr()
            if self.peek() != ")":
                raise self.error("не хватает закрывающей скобки")
            self.pos += 1
            return node
        if token.isdigit() or (token[0].isdigit() and "." in token):
            return ("num", float(token) if "." in token else int(token))
        if len(token) == 1 and token in "+-*/)":
            raise self.error(f"неожиданный элемент «{token}»")
        return ("var", token)


def parse(text: str) -> Node:
    """Разбирает текст формулы в дерево. Ошибка синтаксиса — FormulaError по-русски."""
    if not isinstance(text, str) or not text.strip():
        raise FormulaError(f"пустая формула: {text!r}")
    return _Parser(text).parse()


def _walk(node: Node):
    yield node
    for child in node[1:]:
        if isinstance(child, tuple):
            yield from _walk(child)


def variables(text: str) -> set[str]:
    """Имена переменных, которые использует формула."""
    return {node[1] for node in _walk(parse(text)) if node[0] == "var"}


def evaluate(formula: str | Node, **var_values) -> int | tuple[int, int]:
    """Считает формулу над переменными. Результат — целое число или пара (через «x»).

    Переменные — целые миллиметры: дробное значение — русская ошибка.
    """
    node = parse(formula) if isinstance(formula, str) else formula
    text = formula if isinstance(formula, str) else "<формула>"
    allowed: dict[str, int] = {}
    for name, value in var_values.items():
        if isinstance(value, float) and not value.is_integer():
            raise FormulaError(f"формула «{text}»: переменная «{name}» = {value}, а ожидалось целое число")
        allowed[name] = int(value)
    return _eval(node, allowed, text)


def _eval(node: Node, var_values: dict[str, int], text: str) -> int | tuple[int, int]:
    kind = node[0]
    if kind == "num":
        value = node[1]
        if isinstance(value, float) and not value.is_integer():
            raise FormulaError(f"формула «{text}»: {value} — не целое число, дробные не поддерживаются")
        return int(value)
    if kind == "var":
        name = node[1]
        if name not in var_values:
            raise FormulaError(f"формула «{text}»: неизвестная переменная «{name}»")
        return var_values[name]
    if kind == "pair":
        return (_eval(node[1], var_values, text), _eval(node[2], var_values, text))
    a = _eval(node[1], var_values, text)
    b = _eval(node[2], var_values, text)
    if kind == "+":
        return a + b
    if kind == "-":
        return a - b
    if kind == "*":
        return a * b
    if b == 0:
        raise FormulaError(f"формула «{text}»: деление на ноль")
    if a % b:
        raise FormulaError(f"формула «{text}»: {a} не делится нацело на {b}")
    return a // b


class Formula(str):
    """Строка-формула из construction.yaml: синтаксис проверен при загрузке."""


def compile_formula(value: object) -> Formula:
    """Разбирает формулу заранее (при загрузке мастерской); ошибка — ValueError по-русски."""
    if isinstance(value, Formula):
        return value
    try:
        parse(value)  # type: ignore[arg-type]
    except FormulaError as e:
        raise ValueError(str(e)) from e
    return Formula(value)
