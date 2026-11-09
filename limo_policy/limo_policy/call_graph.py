"""
Call-graph utilities shared by limo_llm (hierarchical generation) and the validator.

Moved unchanged from limo_llm/function_parser.py. The Code as Policies FunctionParser
port itself stays in limo_llm.
"""

import ast
from typing import Dict, List, Optional, Set


def called_names(node_or_code) -> Set[str]:
    """Names of all functions called (``name(...)``) inside a node or code string."""
    node = ast.parse(node_or_code) if isinstance(node_or_code, str) else node_or_code
    return {n.func.id for n in ast.walk(node)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}


def build_call_graph(helper_sources: Dict[str, str]) -> Dict[str, Set[str]]:
    """Return ``{helper: {helpers it calls}}`` restricted to the given helpers."""
    names = set(helper_sources)
    return {name: called_names(src) & names for name, src in helper_sources.items()}


def find_cycle(graph: Dict[str, Set[str]]) -> Optional[List[str]]:
    """Return one cycle as ``[a, b, ..., a]`` (self-recursion is ``[a, a]``) or None."""
    white, grey, black = 0, 1, 2
    color = {n: white for n in graph}
    stack: List[str] = []

    def dfs(n):
        color[n] = grey
        stack.append(n)
        for m in sorted(graph.get(n, ())):
            if color.get(m, black) == grey:
                return stack[stack.index(m):] + [m]
            if color.get(m) == white:
                cyc = dfs(m)
                if cyc:
                    return cyc
        stack.pop()
        color[n] = black
        return None

    for n in sorted(graph):
        if color[n] == white:
            cyc = dfs(n)
            if cyc:
                return cyc
    return None


def topological_order(graph: Dict[str, Set[str]]) -> List[str]:
    """Dependencies first (callee before caller). Graph must be acyclic."""
    order, seen = [], set()

    def visit(n):
        if n in seen:
            return
        seen.add(n)
        for m in sorted(graph.get(n, ())):
            visit(m)
        order.append(n)

    for n in sorted(graph):
        visit(n)
    return order
