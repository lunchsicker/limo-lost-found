"""Collect every called function name and its call signature."""

import ast
from typing import Iterable


class FunctionParser(ast.NodeTransformer):

    def __init__(self, fs, f_assigns):
        super().__init__()
        self._fs = fs
        self._f_assigns = f_assigns

    def visit_Call(self, node):  # noqa: N802 (ast API name)
        self.generic_visit(node)
        if isinstance(node.func, ast.Name):
            f_sig = ast.unparse(node).strip()
            f_name = ast.unparse(node.func).strip()
            self._fs[f_name] = f_sig
        return node

    def visit_Assign(self, node):  # noqa: N802 (ast API name)
        self.generic_visit(node)
        if isinstance(node.value, ast.Call):
            assign_str = ast.unparse(node).strip()
            f_name = ast.unparse(node.value.func).strip()
            self._f_assigns[f_name] = assign_str
        return node


def parse_called_functions(code_str):
    fs, f_assigns = {}, {}
    FunctionParser(fs, f_assigns).visit(ast.parse(code_str))
    for f_name, f_assign in f_assigns.items():
        if f_name in fs:
            fs[f_name] = f_assign
    return fs


def defined_functions(code_str):
    tree = ast.parse(code_str)
    return [n.name for n in tree.body if isinstance(n, ast.FunctionDef)]


def find_undefined_functions(code_str, known_names: Iterable[str]):
    known = set(known_names) | set(defined_functions(code_str))
    return {name: sig for name, sig in parse_called_functions(code_str).items()
            if name not in known}


def call_arity(sig_or_call):
    node = ast.parse(sig_or_call).body[0]
    call = node.value
    if not isinstance(call, ast.Call):
        raise ValueError(f'not a call signature: {sig_or_call!r}')
    return len(call.args), [k.arg for k in call.keywords]
