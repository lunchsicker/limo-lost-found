"""
Builds the Code as Policies prompts from templates + shared specs (no ROS dependency).

Prompt layout (same order as the original ``LMP.build_prompt``)::

    <API header: import lines + signatures + objects + rules>   <- {api_header}
    <few-shot examples: context lines, "# command.", policy code>
    <context: locations = [...] / objects = [...]>              <- runtime environment
    # <user command>.                                           <- query

The original prompts only had the import lines. The signature/return-value comments and
the rule comments are additions required for this project (docs/limo_llm_design.md).
Object names are the detector's COCO class names; the LLM maps the user's words to them
(no alias table).
"""

import os

API_PLACEHOLDER = '{api_header}'
TEMPLATE_FILES = {'policy': 'policy_ui.txt', 'fgen': 'fgen.txt', 'system': 'system.txt'}


def read_text(path):
    with open(path, 'r', encoding='utf-8') as f:
        return f.read()


class PromptBuilder:

    def __init__(self, spec, objects, env, prompts_dir):
        self.spec = spec
        self.objects = list(objects)
        self.env = env
        self.prompts_dir = prompts_dir
        self.templates = {k: read_text(os.path.join(prompts_dir, v)).strip()
                          for k, v in TEMPLATE_FILES.items()}
        for key in ('policy', 'fgen'):
            if API_PLACEHOLDER not in self.templates[key]:
                raise ValueError(f'{TEMPLATE_FILES[key]} lacks the {API_PLACEHOLDER} line')

    # ------------------------------------------------------------------ pieces

    def import_lines(self):
        lines = []
        for module in self.spec.modules:
            names = [f.name for f in self.spec.functions.values() if f.module == module]
            if names:
                lines.append(f'from {module} import {", ".join(names)}')
        return lines

    def api_doc_lines(self):
        lines = ['# Robot API (the only functions you may call, plus '
                 + ', '.join(f'{b}()' for b in self.spec.allowed_builtins) + '):']
        for fn in self.spec.functions.values():
            lines.append(f'#   {fn.signature}: {fn.description}')
        return lines

    def rule_lines(self):
        return [
            '# Rules:',
            '#   Use only location names from `locations` and object names from `objects`.',
            "#   Names in `objects` are detector class names: map the user's (Korean) words",
            '#   to one of them. Never write any other object name.',
            '#   Never invent locations or coordinates. The user usually does not know where',
            '#   the object is: search the registered locations with get_search_locations().',
            '#   Only look for an object after goto_loc() returned True. Check the current',
            '#   view with is_obj_visible() first, then search_here(). Stop once it is found.',
            '#   If the object is not in `objects`, say() that you cannot search for it.',
            '#   Allowed syntax: assignment, calls, if/elif/else, for over a list, break,',
            '#   top-level def with return, and/or/not, comparisons, + , list/tuple literals,',
            '#   f-strings. Not allowed: import, attribute access (a.b), while, lambda,',
            '#   comprehensions, try, with, class, global, recursion, other built-ins.',
        ]

    def api_header(self):
        return '\n'.join(['# Python robot policy script for a lost-and-found search robot.']
                         + self.import_lines() + [''] + self.api_doc_lines()
                         + self.rule_lines())

    def context(self):
        """Runtime environment as Python lines (CaP: ``objects = [...]``)."""
        return f'locations = {self.env.locations!r}\nobjects = {self.objects!r}'

    # ------------------------------------------------------------------ prompts

    def policy_base_prompt(self):
        return self.templates['policy'].replace(API_PLACEHOLDER, self.api_header())

    def fgen_base_prompt(self):
        return self.templates['fgen'].replace(API_PLACEHOLDER, self.api_header())

    def system_prompt(self):
        return self.templates['system']
