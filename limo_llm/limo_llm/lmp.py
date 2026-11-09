"""
Language Model Program (LMP), port of the Code as Policies ``LMP`` class.

Original behaviour kept (Interactive_Demo.ipynb):
  * prompt = base prompt [+ session history] [+ context] + ``# <query>.``
  * completion with temperature 0 and stop tokens
  * undefined functions in the completion are created by ``LMPFGen``
  * with ``include_context`` the context lines are prepended to the code
  * with ``maintain_session`` previous code is kept in the prompt

Changes (documented in docs/limo_llm_design.md):
  * the code is NOT executed (``exec_safe`` removed); it is validated against the shared
    grammar and returned for the PolicyBundle
  * the ``{variable_vars_imports}`` placeholder is replaced by the PromptBuilder API header
  * no ``has_return`` / ``return_val_name``: nested runtime LMPs are not part of the MVP
  * the query suffix is not added when the query already ends with . ? or !
"""

from limo_llm.function_parser import find_undefined_functions
from limo_llm.llm_client import clean_completion
from limo_policy.validator import PolicyValidationError, ValidationIssue


class LMP:

    def __init__(self, name, cfg, prompt_text, lmp_fgen, llm, validator, log=None):
        self._name = name
        self._cfg = cfg
        self._base_prompt = prompt_text
        self._stop_tokens = list(cfg['stop'])
        self._lmp_fgen = lmp_fgen
        self._llm = llm
        self._validator = validator
        self._log = log or (lambda header, body='': None)
        self.exec_hist = ''   # name kept from the original; holds generated (not run) code

    def clear_exec_hist(self):
        self.exec_hist = ''

    def build_prompt(self, query, context='', extra=''):
        prompt = self._base_prompt
        if self._cfg['maintain_session'] and self.exec_hist:
            prompt += f'\n{self.exec_hist}'
        if context != '':
            prompt += f'\n{context}'
        if extra:
            prompt += f'\n{extra}'   # retry feedback (not in the original)
        query = query.strip()
        suffix = '' if query.endswith(('.', '?', '!')) else self._cfg['query_suffix']
        use_query = f'{self._cfg["query_prefix"]}{query}{suffix}'
        prompt += f'\n{use_query}'
        return prompt, use_query

    def __call__(self, query, context='', extra=''):
        """Generate policy code + helpers. Returns ``(to_exec, new_fs, code_str)``."""
        prompt, use_query = self.build_prompt(query, context=context, extra=extra)
        raw = self._llm.complete(prompt, stop=self._stop_tokens,
                                 temperature=self._cfg['temperature'],
                                 max_tokens=self._cfg['max_tokens'])
        code_str = clean_completion(raw, self._stop_tokens)
        self.last_completion = code_str

        if self._cfg['include_context'] and context != '':
            to_exec = f'{context}\n{code_str}'
            to_log = f'{context}\n{use_query}\n{code_str}'
        else:
            to_exec = code_str
            to_log = f'{use_query}\n{to_exec}'
        self._log(f'LMP {self._name} generated:', to_log)

        if not code_str.strip():
            raise PolicyValidationError([ValidationIssue('EMPTY', 'the LLM returned no code')])
        # Grammar check before spending LLM calls on helpers; undefined calls are allowed
        # here because LMPFGen is about to create them.
        try:
            pending = find_undefined_functions(code_str, self._validator.spec.reserved_names)
        except SyntaxError:
            pending = {}
        issues = self._validator.check_code(to_exec, pending=pending)
        if issues:
            raise PolicyValidationError(issues)

        new_fs = self._lmp_fgen.create_new_fs_from_code(code_str)

        if self._cfg['maintain_session']:
            self.exec_hist += f'\n{to_exec}'   # original appends after exec
        return to_exec, new_fs, code_str
