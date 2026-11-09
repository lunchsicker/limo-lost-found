"""
Function discovery for hierarchical code generation.

``FunctionParser`` is the Code as Policies reference class
(google-research/code_as_policies/Interactive_Demo.ipynb) with ``astunparse.unparse``
replaced by the standard library ``ast.unparse`` (Python >= 3.9). The other helpers
replace the original ``var_exists`` (which used ``eval``) with set membership. Call-graph
and cycle analysis (not in the original) live in ``limo_policy.call_graph``.
"""

import ast
from typing import Iterable


class FunctionParser(ast.NodeTransformer):
    """
    Collect every called function name and its call signature (original logic).

    ``fs[name]`` is the unparsed call (``f(x, y=1)``); ``f_assigns[name]`` is the full
    assignment when the call result is assigned (``ret = f(x)``), which the original
    prefers as the signature given to the function-generating LMP.
    """

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
    """Return ``{f_name: f_sig}`` like the start of ``LMPFGen.create_new_fs_from_code``."""
    fs, f_assigns = {}, {}
    FunctionParser(fs, f_assigns).visit(ast.parse(code_str))
    for f_name, f_assign in f_assigns.items():
        if f_name in fs:
            fs[f_name] = f_assign
    return fs


def defined_functions(code_str):
    """Names of functions defined with ``def`` at module level of ``code_str``."""
    tree = ast.parse(code_str)
    return [n.name for n in tree.body if isinstance(n, ast.FunctionDef)]


def find_undefined_functions(code_str, known_names: Iterable[str]):
    """
    Return called functions that are neither known (API, built-ins, helpers) nor defined here.

    Replaces ``var_exists(f_name, all_vars)`` from the original, which evaluated the name
    with ``eval``.
    """
    known = set(known_names) | set(defined_functions(code_str))
    return {name: sig for name, sig in parse_called_functions(code_str).items()
            if name not in known}


def call_arity(sig_or_call):
    """
    ``(n_positional, keyword_names)`` of the call in a signature string.

    Accepts ``f(a, b=1)`` or ``ret = f(a, b=1)``.
    """
    node = ast.parse(sig_or_call).body[0]
    call = node.value
    if not isinstance(call, ast.Call):
        raise ValueError(f'not a call signature: {sig_or_call!r}')
    return len(call.args), [k.arg for k in call.keywords]
