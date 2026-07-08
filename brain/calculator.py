"""Exact arithmetic for Claude, instead of relying on mental math.

Confirmed necessary: asked "53 times 72 minus 23 minus 15, squared", Claude
(Haiku) confidently answered 14,272,484 -- the correct answer is 14,273,284.
Small/fast models are well known to be unreliable at multi-step arithmetic;
giving them a real calculator tool sidesteps that entirely instead of hoping
prompting fixes math.

Deliberately not Python's `eval`/`exec` (dynamic code execution on
LLM-produced input is exactly the kind of thing to avoid) -- this walks a
parsed expression tree and only permits numeric literals and arithmetic
operators, so it's structurally incapable of doing anything beyond math,
regardless of what string it's given.
"""
from __future__ import annotations

import ast
import operator

_OPERATORS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
    ast.USub: operator.neg,
    ast.UAdd: operator.pos,
}


def _eval_node(node: ast.AST) -> float:
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value
    if isinstance(node, ast.BinOp) and type(node.op) in _OPERATORS:
        return _OPERATORS[type(node.op)](_eval_node(node.left), _eval_node(node.right))
    if isinstance(node, ast.UnaryOp) and type(node.op) in _OPERATORS:
        return _OPERATORS[type(node.op)](_eval_node(node.operand))
    raise ValueError("only numbers and + - * / // % ** are allowed")


def calculate(expression: str) -> str:
    try:
        tree = ast.parse(expression, mode="eval")
        result = _eval_node(tree.body)
    except (SyntaxError, ValueError, ZeroDivisionError, TypeError) as exc:
        return f"Could not evaluate '{expression}': {exc}. Tell the user you couldn't compute that."
    # Whole numbers print as e.g. "4" not "4.0" -- more natural to say aloud.
    if isinstance(result, float) and result.is_integer():
        result = int(result)
    return str(result)
