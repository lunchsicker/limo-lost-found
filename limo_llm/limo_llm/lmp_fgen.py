"""
Function-generating LMP (hierarchical code generation), port of ``LMPFGen``.

Original behaviour kept (Interactive_Demo.ipynb):
  * the query is ``# define function: <f_sig>.`` appended to the fgen few-shot prompt
  * every called-but-undefined function in the generated code is created from its call
    signature, and the body of each new function is searched again (recursion)

Changes (documented in docs/limo_llm_design.md):
  * generated functions are NOT executed: their source is collected for the PolicyBundle
  * ``var_exists`` (eval) -> name-set membership
  * duplicate generation prevention, recursion (cycle) detection, depth / count limits
  * name collisions with robot API / built-ins rejected
  * each function must be a single ``def`` in the shared policy grammar
  * the retired ``code-davinci-edit-001`` ``fix_bugs`` pass is not ported
"""

import ast
from typing import Dict, Iterable, List, Optional

from limo_llm.function_parser import call_arity, parse_called_functions
from limo_llm.llm_client import clean_completion
from limo_policy.call_graph import called_names


class FGenError(Exception):
    """Hierarchical generation failed (code is a short machine-readable reason)."""

    def __init__(self, code, message):
        self.code = code
        super().__init__(f'[{code}] {message}')


def extract_function(src, f_name):
    """
    Return the source of the module-level ``def f_name`` in ``src``.

    Returns ``(source, dropped)`` where ``dropped`` counts other top-level statements.
    """
    try:
        tree = ast.parse(src)
    except SyntaxError as e:
        raise FGenError('SYNTAX_ERROR', f'generated function {f_name!r} does not parse: '
                                        f'{e.msg} (line {e.lineno})') from e
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == f_name:
            return ast.get_source_segment(src, node), len(tree.body) - 1
    raise FGenError('BAD_FUNCTION', f'completion does not define {f_name!r}')


class LMPFGen:

    def __init__(self, cfg, prompt_text, llm, validator, log=None):
        self._cfg = cfg
        self._base_prompt = prompt_text
        self._stop_tokens = list(cfg['stop'])
        self._llm = llm
        self._validator = validator
        self._reserved = validator.spec.reserved_names
        self._forbidden = validator.forbidden_builtins
        self._log = log or (lambda header, body='': None)
        self.warnings: List[str] = []
        self.signatures: Dict[str, str] = {}
        self.llm_calls = 0

    def reset(self):
        self.warnings = []
        self.signatures = {}
        self.llm_calls = 0

    # ------------------------------------------------------------------ original API

    def create_f_from_sig(self, f_name, f_sig):
        """Ask the LLM for ``def f_name`` given its call signature. Returns source."""
        self._log(f'Creating function: {f_sig}')
        use_query = f'{self._cfg["query_prefix"]}{f_sig}{self._cfg["query_suffix"]}'
        prompt = f'{self._base_prompt}\n{use_query}'
        self.llm_calls += 1
        raw = self._llm.complete(prompt, stop=self._stop_tokens,
                                 temperature=self._cfg['temperature'],
                                 max_tokens=self._cfg['max_tokens'])
        src, dropped = extract_function(clean_completion(raw, self._stop_tokens), f_name)
        if dropped:
            self.warnings.append(f'fgen {f_name}: dropped {dropped} extra top-level '
                                 'statement(s) from the completion')
        self._check_signature(f_name, f_sig, src)
        self._log('LMP FGEN created:', f'{use_query}\n{src}')
        return src

    def create_new_fs_from_code(self, code_str, other_names: Iterable[str] = (),
                                _depth=0, _stack=(), _new_fs: Optional[Dict] = None):
        """
        Create every undefined function called in ``code_str``, recursively.

        Returns ``{name: source}`` in dependency order (callees before callers).
        """
        new_fs = {} if _new_fs is None else _new_fs
        try:
            tree = ast.parse(code_str)
        except SyntaxError as e:
            raise FGenError('SYNTAX_ERROR', f'code does not parse: {e.msg}') from e
        defined_here = {n.name for n in tree.body if isinstance(n, ast.FunctionDef)}
        known = set(self._reserved) | set(other_names) | defined_here

        for f_name, f_sig in parse_called_functions(code_str).items():
            if f_name in known or f_name in new_fs:
                continue  # exists already (original: var_exists) / duplicate prevention
            if f_name in self._forbidden or '__' in f_name:
                continue  # never generate shadows of Python built-ins; validator rejects
            if f_name in _stack:
                raise FGenError('RECURSION', 'recursive helper calls: '
                                + ' -> '.join(list(_stack[_stack.index(f_name):]) + [f_name]))
            max_depth = self._cfg['max_depth']
            if _depth >= max_depth:
                raise FGenError('DEPTH_LIMIT', f'{f_name!r} would exceed fgen depth {max_depth}')
            if len(new_fs) >= self._cfg['max_functions']:
                raise FGenError('TOO_MANY_FUNCTIONS',
                                f'more than {self._cfg["max_functions"]} helper functions')

            f_src = self.create_f_from_sig(f_name, f_sig)
            issues = self._validator.check_helper(
                f_name, f_src, helper_names=set(new_fs) | set(other_names) | {f_name},
                pending=called_names(f_src))
            if issues:
                raise FGenError('INVALID_FUNCTION', '; '.join(str(i) for i in issues))
            if f_name in called_names(f_src):
                raise FGenError('RECURSION', f'{f_name} calls itself')
            self.signatures[f_name] = f_sig

            # Recursively define child functions used in the new function. (The original
            # re-parsed the unparsed body; parsing the whole def finds the same calls.)
            self.create_new_fs_from_code(f_src, other_names, _depth + 1,
                                         _stack + (f_name,), new_fs)
            new_fs[f_name] = f_src
        return new_fs

    # ------------------------------------------------------------------ helpers

    def _check_signature(self, f_name, f_sig, src):
        n_pos, kws = call_arity(f_sig)
        params = [a.arg for a in ast.parse(src).body[0].args.args]
        if n_pos + len(kws) != len(params) or any(k not in params[n_pos:] for k in kws):
            raise FGenError('SIGNATURE_MISMATCH',
                            f'def {f_name}({", ".join(params)}) does not match call {f_sig!r}')
