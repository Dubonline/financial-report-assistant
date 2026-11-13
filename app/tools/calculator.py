"""受限财务算术。

dubonline：模型组织公式，执行交给白名单解释器；限制语法比执行任意代码更可控。
Decimal 用于十进制运算，但数字字面量仍经 Python AST 解析，不能视为任意精度输入。
"""

import ast
import operator
import re
from decimal import Decimal

from langchain_core.tools import tool


_BINARY_OPERATORS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
}

def _eval_ast(node: ast.AST) -> Decimal:
    """递归执行已白名单的 AST 节点；其他任意 Python 语法一律拒绝。"""

    if isinstance(node, ast.Expression):
        return _eval_ast(node.body)

    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return Decimal(str(node.value))

    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        return -_eval_ast(node.operand)
    if isinstance(node, ast.BinOp) and type(node.op) in _BINARY_OPERATORS:
        left, right = _eval_ast(node.left), _eval_ast(node.right)
        if isinstance(node.op, ast.Div) and right == 0:
            raise ValueError("Division by zero")
        return _BINARY_OPERATORS[type(node.op)](left, right)
    raise ValueError("Only numeric literals and +, -, *, / are allowed")


@tool(parse_docstring=True)
def safe_calculate(expression: str) -> str:
    """用受限算术表达式计算财务数值，返回可直接复核的十进制结果。

    Args:
        expression: 长度不超过 200 个字符的算术表达式，例如
            ``(225 - 180) / 180 * 100``。支持数字、括号、加减乘除、
            逗号千分位、美元符号和紧跟数字的百分号；须先把财务指标名称
            替换为报告中的原始数值，不支持变量、函数或 Python 代码。

    Returns:
        Decimal 计算结果的字符串形式，例如 ``"25"``，不附加单位或百分号。

    Raises:
        ValueError: 表达式含有非法字符、不支持的语法，或发生除零。
        SyntaxError: 字符合法但表达式不完整，无法解析为语法树。

    Notes:
        使用字符白名单和 AST 节点白名单限制输入，不调用 ``eval``。
    """
    if len(expression) > 200 or not re.fullmatch(r"[\d\s.,()+\-*/%$]+", expression):
        raise ValueError("Expression contains unsupported characters")
    normalized = expression.replace(",", "").replace("$", "")
    if "%" in normalized:
        normalized = re.sub(r"(\d+(?:\.\d+)?)%", r"(\1/100)", normalized)
    tree = ast.parse(normalized, mode="eval")
    result = _eval_ast(tree)
    return format(result.normalize(), "f")