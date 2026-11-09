"""
Static validation of generated policy code against the shared grammar subset.

This is an early filter in limo_llm (reject / report before anything is sent to the
robot). It is NOT a sandbox: limo_executor must re-validate the bundle and run it with
its own restricted interpreter, which is the final safety authority.

The allowed AST node types, operators, built-ins and limits come from
``config/robot_api.yaml`` so the executor can enforce the very same specification.
"""

import ast
import builtins
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional

from limo_llm.function_parser import build_call_graph, find_cycle

_CONSTANT_TYPES = (str, int, float, bool, type(None))
_LIST_RETURNING_APIS = ('get_search_locations', 'get_known_objects', 'get_visible_obj_names')
# Every Python built-in name except the literals; the spec's allowed_builtins are removed
# from this set by the validator.
_PY_BUILTINS = frozenset(n for n in dir(builtins) if not n.startswith('_')) - {
    'True', 'False', 'None'}


@dataclass(frozen=True)
class ValidationIssue:
    code: str
    message: str
    where: str = 'policy'
    lineno: Optional[int] = None

    def __str__(self):
        loc = f'{self.where}:{self.lineno}' if self.lineno else self.where
        return f'[{self.code}] {loc}: {self.message}'


class PolicyValidationError(Exception):
    """Raised with the list of issues when a policy or helper is rejected."""

    def __init__(self, issues: List[ValidationIssue]):
        self.issues = list(issues)
        super().__init__('; '.join(str(i) for i in self.issues))


def _const_values(node, binds):
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return {node.value}
    if isinstance(node, ast.Name) and binds.get(node.id):
        return set(binds[node.id])
    return None


def _scope_calls(stmts):
    """Yield the calls in a scope, not descending into nested function definitions."""
    todo = list(stmts)
    while todo:
        n = todo.pop()
        if isinstance(n, ast.FunctionDef):
            continue
        if isinstance(n, ast.Call):
            yield n
        todo.extend(ast.iter_child_nodes(n))


def helper_param_bindings(tree):
    """
    Constant string values flowing into helper parameters (module-level defs).

    ``{helper: {param: {values}}}``; a parameter that ever receives a non-constant is
    ``None``. Iterates to a fixpoint so constants flow through helper -> helper calls.
    """
    defs = {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}
    pb = {name: {a.arg: set() for a in fn.args.args} for name, fn in defs.items()}
    module = [n for n in tree.body if not isinstance(n, ast.FunctionDef)]
    for _ in range(len(defs) + 1):
        changed = False
        scopes = [(module, constant_bindings(module))]
        scopes += [(fn.body, constant_bindings(fn.body, seed=pb[name]))
                   for name, fn in defs.items()]
        for body, binds in scopes:
            for call in _scope_calls(body):
                if not (isinstance(call.func, ast.Name) and call.func.id in defs):
                    continue
                params = [a.arg for a in defs[call.func.id].args.args]
                pairs = list(zip(params, call.args))
                pairs += [(k.arg, k.value) for k in call.keywords if k.arg in params]
                for param, arg in pairs:
                    cur = pb[call.func.id][param]
                    if cur is None:
                        continue
                    vals = _const_values(arg, binds)
                    new = None if vals is None else cur | vals
                    if new != cur:
                        pb[call.func.id][param] = new
                        changed = True
        if not changed:
            break
    return pb


def constant_bindings(stmts, unknown=(), seed=None):
    """
    Flow-insensitive constant propagation for string variables in one scope.

    ``{name: {values}}`` when every binding of ``name`` in the scope is a string literal
    (``x = 'cup'`` or ``for x in ['cup', 'book']``); ``None`` otherwise. Nested function
    bodies are separate scopes and are skipped. ``seed`` gives known parameter values.
    """
    out = {name: None for name in unknown}
    for name, values in (seed or {}).items():
        out[name] = set(values) if values else None

    def bind(name, values):
        if name in out and out[name] is None:
            return
        out[name] = None if values is None else (out.get(name) or set()) | values

    def strs(nodes):
        if all(isinstance(e, ast.Constant) and isinstance(e.value, str) for e in nodes):
            return {e.value for e in nodes}
        return None

    def walk(nodes):
        for n in nodes:
            if isinstance(n, ast.FunctionDef):
                continue
            if isinstance(n, ast.Assign) and len(n.targets) == 1 \
                    and isinstance(n.targets[0], ast.Name):
                bind(n.targets[0].id, strs([n.value]))
            elif isinstance(n, ast.For) and isinstance(n.target, ast.Name):
                it = n.iter
                bind(n.target.id, strs(it.elts) if isinstance(it, (ast.List, ast.Tuple))
                     else None)
            walk(getattr(n, 'body', []) or [])
            walk(getattr(n, 'orelse', []) or [])

    walk(stmts)
    return out


class _Checker(ast.NodeVisitor):

    def __init__(self, validator, where, callable_names, pending, issues):
        self.v = validator
        self.where = where
        self.callable_names = callable_names
        self.pending = pending
        self.issues = issues
        self.function_depth = 0
        self.bindings = {}   # name -> set of string constants (None: not constant)
        self.param_bindings = {}   # helper -> {param: set | None}
        self.targets = []    # object names statically passed to object parameters

    # ------------------------------------------------------------------ helpers

    def err(self, node, code, msg):
        self.issues.append(ValidationIssue(code, msg, self.where, getattr(node, 'lineno', None)))

    def check_name(self, node, name, purpose):
        if '__' in name:
            self.err(node, 'DUNDER', f'name {name!r} contains "__"')
        elif name in self.v.forbidden_builtins:
            self.err(node, 'FORBIDDEN_BUILTIN', f'{purpose} {name!r} is not an allowed built-in')

    def check_binding(self, node, name):
        self.check_name(node, name, 'name')
        if name in self.v.spec.reserved_names or name in self.callable_names:
            self.err(node, 'SHADOWING', f'cannot assign/define reserved or helper name {name!r}')

    # ------------------------------------------------------------------ generic

    def visit(self, node):
        kind = type(node).__name__
        if kind not in self.v.allowed_nodes:
            self.err(node, 'UNSUPPORTED_SYNTAX', f'{kind} is not part of the policy grammar')
            return None  # do not descend into unsupported constructs
        return super().visit(node)

    # ------------------------------------------------------------------ statements

    def visit_Assign(self, node):  # noqa: N802
        if len(node.targets) != 1 or not isinstance(node.targets[0], ast.Name):
            self.err(node, 'UNSUPPORTED_SYNTAX', 'only `name = expression` assignment is allowed')
            return
        self.check_binding(node, node.targets[0].id)
        self.visit(node.value)

    def visit_Expr(self, node):  # noqa: N802
        if isinstance(node.value, ast.Call):
            self.visit(node.value)
        elif (self.function_depth and isinstance(node.value, ast.Constant)
              and isinstance(node.value.value, str)):
            pass  # docstring / string statement inside a helper is harmless
        else:
            self.err(node, 'UNSUPPORTED_SYNTAX', 'expression statements must be function calls')

    def visit_For(self, node):  # noqa: N802
        if node.orelse:
            self.err(node, 'UNSUPPORTED_SYNTAX', 'for ... else is not allowed')
        if not isinstance(node.target, ast.Name):
            self.err(node, 'UNSUPPORTED_SYNTAX', 'for-loop target must be a single name')
        else:
            self.check_binding(node, node.target.id)
        it = node.iter
        ok = (isinstance(it, (ast.Name, ast.List, ast.Tuple))
              or (isinstance(it, ast.Call) and isinstance(it.func, ast.Name)
                  and it.func.id in _LIST_RETURNING_APIS))
        if not ok:
            self.err(node, 'UNSUPPORTED_SYNTAX',
                     'for-loop must iterate over a name, a list/tuple literal or '
                     + '/'.join(_LIST_RETURNING_APIS) + '()')
        self.visit(it)
        for stmt in node.body:
            self.visit(stmt)

    def visit_FunctionDef(self, node):  # noqa: N802
        if self.function_depth:
            self.err(node, 'UNSUPPORTED_SYNTAX', 'nested function definitions are not allowed')
            return
        if node.decorator_list:
            self.err(node, 'UNSUPPORTED_SYNTAX', 'decorators are not allowed')
        a = node.args
        if a.posonlyargs or a.vararg or a.kwonlyargs or a.kwarg or a.defaults or a.kw_defaults:
            self.err(node, 'UNSUPPORTED_SYNTAX', 'only plain positional parameters are allowed')
        if node.returns is not None or any(p.annotation is not None for p in a.args):
            self.err(node, 'UNSUPPORTED_SYNTAX', 'type annotations are not allowed')
        self.check_name(node, node.name, 'function')
        if node.name in self.v.spec.reserved_names:
            self.err(node, 'NAME_COLLISION',
                     f'helper {node.name!r} collides with a robot API / built-in name')
        for p in a.args:
            self.check_binding(p, p.arg)
        saved = self.bindings
        seed = {p.arg: None for p in a.args}
        seed.update(self.param_bindings.get(node.name) or {})
        self.bindings = constant_bindings(node.body, seed=seed)
        self.function_depth += 1
        for stmt in node.body:
            self.visit(stmt)
        self.function_depth -= 1
        self.bindings = saved

    def visit_Return(self, node):  # noqa: N802
        if not self.function_depth:
            self.err(node, 'UNSUPPORTED_SYNTAX', 'return is only allowed inside a helper')
        if node.value is not None:
            self.visit(node.value)

    # ------------------------------------------------------------------ expressions

    def visit_Constant(self, node):  # noqa: N802
        if not isinstance(node.value, _CONSTANT_TYPES):
            self.err(node, 'UNSUPPORTED_SYNTAX', f'constant of type {type(node.value).__name__}')

    def visit_Name(self, node):  # noqa: N802
        self.check_name(node, node.id, 'name')

    def visit_Call(self, node):  # noqa: N802
        if not isinstance(node.func, ast.Name):
            self.err(node, 'ATTRIBUTE_OR_DYNAMIC_CALL',
                     f'only direct calls by name are allowed: {ast.unparse(node.func)!r}')
            return
        name = node.func.id
        self.check_name(node, name, 'function')
        if any(isinstance(a, ast.Starred) for a in node.args) or \
                any(k.arg is None for k in node.keywords):
            self.err(node, 'UNSUPPORTED_SYNTAX', 'starred / ** arguments are not allowed')
        if name in self.v.spec.functions:
            self.check_api_call(node, self.v.spec.functions[name])
        elif name in self.v.spec.allowed_builtins:
            if len(node.args) != 1 or node.keywords:
                self.err(node, 'BAD_ARGUMENTS', f'{name}() takes exactly one argument')
        elif name not in self.callable_names and name not in self.pending \
                and name not in self.v.forbidden_builtins and '__' not in name:
            self.err(node, 'UNDEFINED_FUNCTION', f'call to undefined function {name!r}')
        for a in node.args:
            self.visit(a)
        for k in node.keywords:
            self.visit(k.value)

    def check_api_call(self, node, fn):
        params = {p.name: p for p in fn.params}
        n_given = len(node.args) + len(node.keywords)
        bad_kw = [k.arg for k in node.keywords if k.arg not in params]
        if n_given != len(fn.params) or bad_kw or len(node.args) > len(fn.params):
            self.err(node, 'BAD_ARGUMENTS', f'{fn.signature} called as {ast.unparse(node)!r}')
            return
        bound = list(zip(fn.params, node.args))
        bound += [(params[k.arg], k.value) for k in node.keywords]
        for param, value in bound:
            if isinstance(value, ast.Constant):
                values = [value.value]
            elif isinstance(value, ast.Name) and self.bindings.get(value.id):
                values = sorted(self.bindings[value.id])   # e.g. target = 'umbrella'
            else:
                continue  # unknown statically: the executor checks at run time
            for v in values:
                self.check_value(node, fn, param, v)

    def check_value(self, node, fn, param, v):
        if param.kind == 'object':
            if v not in self.v.object_names:
                self.err(node, 'UNKNOWN_OBJECT',
                         f'{v!r} is not a known object {sorted(self.v.object_names)}')
            elif v not in self.targets:
                self.targets.append(v)
        elif param.kind == 'location' and v not in self.v.location_names:
            self.err(node, 'UNKNOWN_LOCATION',
                     f'{v!r} is not a registered location {sorted(self.v.location_names)}')
        elif param.type == 'str' and not isinstance(v, str):
            self.err(node, 'BAD_ARGUMENTS', f'{fn.name}: {param.name} must be a string')

    def visit_Subscript(self, node):  # noqa: N802
        if not (isinstance(node.slice, ast.Constant) and type(node.slice.value) is int):
            self.err(node, 'UNSUPPORTED_SYNTAX', 'only constant integer indexing is allowed')
        if not isinstance(node.ctx, ast.Load):
            self.err(node, 'UNSUPPORTED_SYNTAX', 'item assignment is not allowed')
        self.visit(node.value)

    def visit_FormattedValue(self, node):  # noqa: N802
        if node.conversion != -1 or node.format_spec is not None:
            self.err(node, 'UNSUPPORTED_SYNTAX', 'f-string conversions/format specs not allowed')
        self.visit(node.value)

    def visit_List(self, node):  # noqa: N802
        self._load_only(node)

    def visit_Tuple(self, node):  # noqa: N802
        self._load_only(node)

    def _load_only(self, node):
        if not isinstance(node.ctx, ast.Load):
            self.err(node, 'UNSUPPORTED_SYNTAX', 'unpacking assignment is not allowed')
        for e in node.elts:
            self.visit(e)

    def visit_BinOp(self, node):  # noqa: N802
        self._op(node, node.op, 'binary')
        self.visit(node.left)
        self.visit(node.right)

    def visit_UnaryOp(self, node):  # noqa: N802
        self._op(node, node.op, 'unary')
        self.visit(node.operand)

    def visit_BoolOp(self, node):  # noqa: N802
        self._op(node, node.op, 'boolean')
        for v in node.values:
            self.visit(v)

    def visit_Compare(self, node):  # noqa: N802
        for op in node.ops:
            self._op(node, op, 'compare')
        self.visit(node.left)
        for c in node.comparators:
            self.visit(c)

    def _op(self, node, op, group):
        if type(op).__name__ not in self.v.spec.operators.get(group, ()):
            self.err(node, 'UNSUPPORTED_SYNTAX', f'operator {type(op).__name__} not allowed')


class PolicyValidator:
    """Checks policy / helper source against the grammar in ``RobotApiSpec``."""

    def __init__(self, spec, object_names: Iterable[str], location_names: Iterable[str]):
        self.spec = spec
        self.object_names = frozenset(object_names)
        self.location_names = frozenset(location_names)
        self.forbidden_builtins = _PY_BUILTINS - frozenset(spec.allowed_builtins)
        ops = frozenset().union(*spec.operators.values()) if spec.operators else frozenset()
        self.allowed_nodes = (spec.statements | spec.expressions | spec.contexts | ops
                              | {'Module'})

    # ------------------------------------------------------------------ public

    def check_code(self, code, *, helper_names=(), pending=(), where='policy'):
        """Return issues for a code string (policy body, may contain module-level defs)."""
        issues: List[ValidationIssue] = []
        limit = self.spec.limits.get('max_policy_chars')
        if limit and len(code) > limit:
            issues.append(ValidationIssue('TOO_LONG', f'{len(code)} chars > {limit}', where))
        try:
            tree = ast.parse(code)
        except SyntaxError as e:
            return issues + [ValidationIssue('SYNTAX_ERROR', str(e.msg), where, e.lineno)]
        self._run_checker(tree, where, helper_names, pending, issues)
        return issues

    def object_targets(self, code, helper_names=()):
        """Object names statically passed to object parameters (literal or constant var)."""
        return self._run_checker(ast.parse(code), 'policy', helper_names, (), []).targets

    def _run_checker(self, tree, where, helper_names, pending, issues):
        def_names = [n.name for n in tree.body if isinstance(n, ast.FunctionDef)]
        for name in {n for n in def_names if def_names.count(n) > 1}:
            issues.append(ValidationIssue('NAME_COLLISION', f'{name!r} is defined twice', where))
        checker = _Checker(self, where, frozenset(helper_names) | set(def_names),
                           frozenset(pending), issues)
        checker.bindings = constant_bindings(tree.body)
        checker.param_bindings = helper_param_bindings(tree)
        checker.visit(tree)
        return checker

    def check_helper(self, name, source, *, helper_names=(), pending=()):
        """Check that a helper is exactly one module-level ``def name(...)`` in the grammar."""
        issues = self._check_helper_shape(name, source)
        if issues:
            return issues
        return self.check_code(source, helper_names=helper_names, pending=pending, where=name)

    @staticmethod
    def _check_helper_shape(name, source):
        try:
            tree = ast.parse(source)
        except SyntaxError as e:
            return [ValidationIssue('SYNTAX_ERROR', str(e.msg), name, e.lineno)]
        if len(tree.body) != 1 or not isinstance(tree.body[0], ast.FunctionDef) \
                or tree.body[0].name != name:
            return [ValidationIssue('BAD_HELPER', f'expected exactly one `def {name}(...)`', name)]
        return []

    def check_bundle(self, policy_code, helpers: Dict[str, str]):
        """Full check: every call resolves, arities match, no recursion, limits."""
        issues: List[ValidationIssue] = []
        names = set(helpers)
        max_h = self.spec.limits.get('max_helper_functions')
        if max_h is not None and len(helpers) > max_h:
            issues.append(ValidationIssue('TOO_MANY_FUNCTIONS', f'{len(helpers)} > {max_h}'))
        for name, src in helpers.items():
            issues += self._check_helper_shape(name, src)
        try:
            policy_tree = ast.parse(policy_code)
        except SyntaxError as e:
            issues.append(ValidationIssue('SYNTAX_ERROR', str(e.msg), 'policy', e.lineno))
            return issues
        if any(isinstance(n, ast.FunctionDef) and n.name in names for n in policy_tree.body):
            issues.append(ValidationIssue('NAME_COLLISION',
                                          'policy redefines a helper of the bundle'))
        if issues:
            return issues
        # Semantic check on helpers + policy as one module, so constant object/location
        # names are followed through helper parameters.
        parts = [(name, src.strip('\n')) for name, src in helpers.items()]
        parts.append(('policy', policy_code.strip('\n')))
        full, starts, line = [], [], 1
        for name, src in parts:
            starts.append((line, name))
            full.append(src)
            line += src.count('\n') + 2
        for issue in self.check_code('\n\n'.join(full), where='bundle'):
            where, lineno = issue.where, issue.lineno
            if lineno:
                start, where = max(s for s in starts if s[0] <= lineno)
                lineno = lineno - start + 1
            issues.append(ValidationIssue(issue.code, issue.message, where, lineno))
        if issues:
            return issues
        cycle = find_cycle(build_call_graph(helpers))
        if cycle:
            issues.append(ValidationIssue('RECURSION', 'recursive helper calls: '
                                          + ' -> '.join(cycle)))
        issues += self._check_helper_arity(policy_code, helpers)
        return issues

    def validate_bundle(self, policy_code, helpers):
        issues = self.check_bundle(policy_code, helpers)
        if issues:
            raise PolicyValidationError(issues)

    # ------------------------------------------------------------------ internal

    @staticmethod
    def _check_helper_arity(policy_code, helpers):
        params = {}
        for name, src in helpers.items():
            fn = ast.parse(src).body[0]
            params[name] = [a.arg for a in fn.args.args]
        issues = []
        for where, src in [('policy', policy_code)] + list(helpers.items()):
            for node in ast.walk(ast.parse(src)):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) \
                        and node.func.id in params:
                    p = params[node.func.id]
                    kws = [k.arg for k in node.keywords]
                    if len(node.args) + len(kws) != len(p) or len(node.args) > len(p) \
                            or any(k not in p[len(node.args):] for k in kws):
                        issues.append(ValidationIssue(
                            'BAD_ARGUMENTS',
                            f'{ast.unparse(node)!r} does not match def {node.func.id}'
                            f'({", ".join(p)})', where, node.lineno))
        return issues
