"""
Reference interpreter for the policy grammar (tests / mock executor only).

Walks the AST of a validated PolicyBundle and evaluates only the grammar subset in
config/robot_api.yaml. It never calls ``exec``/``eval``/``compile``. Robot API calls go
to the injected callables. Step, loop-iteration and call-depth budgets are enforced, and
the cancel flag is checked before every statement.

This shows limo_executor one possible implementation of the contract; it is not the
production executor and has not been reviewed as a security boundary.
"""

import ast


class PolicyRuntimeError(Exception):
    """Policy execution stopped. ``code`` matches the ExecutePolicy result codes."""

    def __init__(self, code, message):
        self.code = code
        super().__init__(f'[{code}] {message}')


class PolicyCanceled(PolicyRuntimeError):

    def __init__(self, message='canceled'):
        super().__init__('CANCELED', message)


class _Break(Exception):
    pass


class _Return(Exception):

    def __init__(self, value):
        super().__init__()
        self.value = value


_CMP = {
    'Eq': lambda a, b: a == b, 'NotEq': lambda a, b: a != b,
    'Lt': lambda a, b: a < b, 'LtE': lambda a, b: a <= b,
    'Gt': lambda a, b: a > b, 'GtE': lambda a, b: a >= b,
    'In': lambda a, b: a in b, 'NotIn': lambda a, b: a not in b,
    'Is': lambda a, b: a is b, 'IsNot': lambda a, b: a is not b,
}
_SAFE_VALUE_TYPES = (str, int, float, bool, type(None), list, tuple)


class PolicyInterpreter:

    def __init__(self, spec, api, cancel_check=lambda: False):
        missing = set(spec.functions) - set(api)
        if missing:
            raise ValueError(f'robot API implementation lacks {sorted(missing)}')
        self.spec = spec
        self.api = {name: api[name] for name in spec.functions}
        self.builtins = {name: {'len': len}[name] for name in spec.allowed_builtins}
        self.cancel_check = cancel_check
        lim = spec.limits
        self.max_steps = lim.get('max_steps', 2000)
        self.max_loop_iterations = lim.get('max_loop_iterations', 100)
        self.max_call_depth = lim.get('max_call_depth', 8)
        self.steps = 0
        self.loop_iterations = 0
        self.depth = 0
        self.functions = {}

    # ------------------------------------------------------------------ entry

    def run(self, helper_sources, policy_code):
        for name, src in helper_sources.items():
            fn = ast.parse(src).body[0]
            if not isinstance(fn, ast.FunctionDef) or fn.name != name:
                raise PolicyRuntimeError('VALIDATION_ERROR', f'bad helper {name!r}')
            self.functions[name] = fn
        tree = ast.parse(policy_code)
        for node in tree.body:
            if isinstance(node, ast.FunctionDef):
                self.functions[node.name] = node
        global_scope = {}
        try:
            self._block([n for n in tree.body if not isinstance(n, ast.FunctionDef)],
                        global_scope, None)
        except _Break:
            raise PolicyRuntimeError('RUNTIME_ERROR', 'break outside loop')
        except _Return:
            raise PolicyRuntimeError('RUNTIME_ERROR', 'return outside function')
        return global_scope

    # ------------------------------------------------------------------ statements

    def _block(self, stmts, g, local):
        for stmt in stmts:
            self._stmt(stmt, g, local)

    def _tick(self):
        if self.cancel_check():
            raise PolicyCanceled()
        self.steps += 1
        if self.steps > self.max_steps:
            raise PolicyRuntimeError('LIMIT_EXCEEDED', f'more than {self.max_steps} steps')

    def _stmt(self, node, g, local):
        self._tick()
        scope = local if local is not None else g
        kind = type(node).__name__
        if kind == 'Assign':
            scope[node.targets[0].id] = self._eval(node.value, g, local)
        elif kind == 'Expr':
            self._eval(node.value, g, local)
        elif kind == 'If':
            branch = node.body if self._eval(node.test, g, local) else node.orelse
            self._block(branch, g, local)
        elif kind == 'For':
            items = self._eval(node.iter, g, local)
            if not isinstance(items, (list, tuple)):
                raise PolicyRuntimeError('RUNTIME_ERROR', 'for-loop over a non-list value')
            for item in list(items):
                self.loop_iterations += 1
                if self.loop_iterations > self.max_loop_iterations:
                    raise PolicyRuntimeError('LIMIT_EXCEEDED', 'loop iteration budget exceeded')
                scope[node.target.id] = item
                try:
                    self._block(node.body, g, local)
                except _Break:
                    break
        elif kind == 'Break':
            raise _Break()
        elif kind == 'Return':
            raise _Return(None if node.value is None else self._eval(node.value, g, local))
        elif kind == 'Pass':
            pass
        else:
            raise PolicyRuntimeError('VALIDATION_ERROR', f'unsupported statement {kind}')

    # ------------------------------------------------------------------ expressions

    def _lookup(self, name, g, local):
        if local is not None and name in local:
            return local[name]
        if name in g:
            return g[name]
        raise PolicyRuntimeError('RUNTIME_ERROR', f'name {name!r} is not defined')

    def _eval(self, node, g, local):
        kind = type(node).__name__
        if kind == 'Constant':
            return node.value
        if kind == 'Name':
            return self._lookup(node.id, g, local)
        if kind == 'List':
            return [self._eval(e, g, local) for e in node.elts]
        if kind == 'Tuple':
            return tuple(self._eval(e, g, local) for e in node.elts)
        if kind == 'BoolOp':
            is_and = isinstance(node.op, ast.And)
            value = None
            for v in node.values:   # short-circuit like Python
                value = self._eval(v, g, local)
                if (not value) if is_and else value:
                    return value
            return value
        if kind == 'UnaryOp':
            operand = self._eval(node.operand, g, local)
            if isinstance(node.op, ast.Not):
                return not operand
            if isinstance(node.op, ast.USub) and isinstance(operand, (int, float)):
                return -operand
            raise PolicyRuntimeError('RUNTIME_ERROR', 'bad unary operation')
        if kind == 'BinOp' and isinstance(node.op, ast.Add):
            left, right = self._eval(node.left, g, local), self._eval(node.right, g, local)
            if type(left) is not type(right) and not (
                    isinstance(left, (int, float)) and isinstance(right, (int, float))):
                raise PolicyRuntimeError('RUNTIME_ERROR', 'cannot add different types')
            return left + right
        if kind == 'Compare':
            left = self._eval(node.left, g, local)
            for op, comp in zip(node.ops, node.comparators):
                right = self._eval(comp, g, local)
                if not _CMP[type(op).__name__](left, right):
                    return False
                left = right
            return True
        if kind == 'Subscript':
            value = self._eval(node.value, g, local)
            try:
                return value[node.slice.value]
            except (IndexError, TypeError, KeyError) as e:
                raise PolicyRuntimeError('RUNTIME_ERROR', f'bad index: {e}') from e
        if kind == 'JoinedStr':
            parts = []
            for v in node.values:
                if isinstance(v, ast.Constant):
                    parts.append(v.value)
                else:
                    parts.append(str(self._eval(v.value, g, local)))
            return ''.join(parts)
        if kind == 'Call':
            return self._call(node, g, local)
        raise PolicyRuntimeError('VALIDATION_ERROR', f'unsupported expression {kind}')

    def _call(self, node, g, local):
        name = node.func.id
        args = [self._eval(a, g, local) for a in node.args]
        kwargs = {k.arg: self._eval(k.value, g, local) for k in node.keywords}
        for v in list(args) + list(kwargs.values()):
            if not isinstance(v, _SAFE_VALUE_TYPES):
                raise PolicyRuntimeError('RUNTIME_ERROR', f'unsupported argument type {type(v)}')
        if name in self.api:
            if self.cancel_check():
                raise PolicyCanceled()
            return self.api[name](*args, **kwargs)
        if name in self.builtins:
            return self.builtins[name](*args, **kwargs)
        if name in self.functions:
            return self._call_helper(self.functions[name], args, kwargs, g)
        raise PolicyRuntimeError('RUNTIME_ERROR', f'call to undefined function {name!r}')

    def _call_helper(self, fn, args, kwargs, g):
        params = [a.arg for a in fn.args.args]
        if len(args) + len(kwargs) != len(params):
            raise PolicyRuntimeError('RUNTIME_ERROR', f'{fn.name}() argument count mismatch')
        local = dict(zip(params, args))
        for k, v in kwargs.items():
            if k not in params or k in local:
                raise PolicyRuntimeError('RUNTIME_ERROR', f'{fn.name}() bad keyword {k!r}')
            local[k] = v
        self.depth += 1
        if self.depth > self.max_call_depth:
            raise PolicyRuntimeError('LIMIT_EXCEEDED', 'helper call depth exceeded')
        try:
            self._block(fn.body, g, local)
        except _Return as r:
            return r.value
        except _Break:
            raise PolicyRuntimeError('RUNTIME_ERROR', 'break outside loop')
        finally:
            self.depth -= 1
        return None
