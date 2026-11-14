"""Safe calculator: parses with `ast` and allows only numbers and arithmetic operators. No eval."""
from __future__ import annotations

import ast
import operator
import re

_BIN = {
    ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
    ast.Div: operator.truediv, ast.FloorDiv: operator.floordiv, ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}
_UNARY = {ast.UAdd: operator.pos, ast.USub: operator.neg}
MAX_EXPONENT = 100
MAX_LEN = 200


class CalculatorError(ValueError):
    pass


def _eval(node):
    if isinstance(node, ast.Expression):
        return _eval(node.body)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
        return node.value
    if isinstance(node, ast.BinOp) and type(node.op) in _BIN:
        left, right = _eval(node.left), _eval(node.right)
        if isinstance(node.op, ast.Pow) and abs(right) > MAX_EXPONENT:
            raise CalculatorError("Exponent too large.")
        return _BIN[type(node.op)](left, right)
    if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY:
        return _UNARY[type(node.op)](_eval(node.operand))
    raise CalculatorError(f"Unsupported expression element: {type(node).__name__}")


def safe_calculate(expression: str) -> float:
    """Evaluate an arithmetic expression such as '(8400 - 6000) / 6000 * 100'."""
    if not isinstance(expression, str) or not expression.strip():
        raise CalculatorError("Empty expression.")
    expr = expression.replace(",", "").replace("₹", "").strip()
    # "20%" (percent) -> "20/100"; "10 % 3" (modulo) is left alone
    expr = re.sub(r"(\d(?:\.\d+)?)\s*%(?!\s*[\d(.])", r"(\1/100)", expr)
    if len(expr) > MAX_LEN:
        raise CalculatorError("Expression too long.")
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError as exc:
        raise CalculatorError(f"Invalid expression: {expression}") from exc
    try:
        result = _eval(tree)
    except ZeroDivisionError as exc:
        raise CalculatorError("Division by zero.") from exc
    return round(float(result), 4)
