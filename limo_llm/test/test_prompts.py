"""Few-shot prompts: every example must be in the executable grammar subset."""

import ast
import re

from conftest import PATHS
from limo_llm.function_parser import find_undefined_functions
from limo_llm.prompt_builder import API_PLACEHOLDER, PromptBuilder
from limo_policy.validator import PolicyValidator
import pytest


@pytest.fixture
def builder(spec, objects, env):
    return PromptBuilder(spec, objects, env, PATHS['prompts_dir'])


def split_policy_examples(template):
    """[(locations, objects, query, code)] from policy_ui.txt."""
    body = template.split(API_PLACEHOLDER, 1)[1].strip('\n')
    blocks = re.split(r'(?m)^(?=locations = \[)', body)
    out = []
    for block in blocks:
        if not block.strip():
            continue
        lines = block.splitlines()
        locations = ast.literal_eval(lines[0].split('=', 1)[1].strip())
        objects = ast.literal_eval(lines[1].split('=', 1)[1].strip())
        assert lines[2].startswith('# '), lines[2]
        out.append((locations, objects, lines[2], '\n'.join(lines[3:]) + '\n'))
    return out


def test_policy_examples_parse_into_blocks(builder, objects):
    examples = split_policy_examples(builder.templates['policy'])
    assert len(examples) >= 8
    for _, example_objects, _, _ in examples:
        assert example_objects == objects == ['handbag', 'umbrella', 'cup', 'cell phone',
                                              'book']


def test_every_policy_example_is_valid_grammar(builder, spec):
    for locations, objects, query, code in split_policy_examples(builder.templates['policy']):
        validator = PolicyValidator(spec, objects, locations)
        context = f'locations = {locations!r}\nobjects = {objects!r}\n'
        pending = find_undefined_functions(code, spec.reserved_names)
        issues = validator.check_code(context + code, pending=pending)
        assert issues == [], f'{query}: {issues}'


def test_fgen_examples_are_valid_grammar(builder, spec, objects):
    body = builder.templates['fgen'].split(API_PLACEHOLDER, 1)[1]
    validator = PolicyValidator(spec, objects, ['desk', 'sofa'])
    assert validator.check_code(body) == []
    defs = [n for n in ast.parse(body).body if isinstance(n, ast.FunctionDef)]
    assert len(defs) >= 5
    # every def is preceded by its "# define function: <sig>." query line
    for d in defs:
        assert re.search(rf'# define function: .*\b{d.name}\(', body), d.name


def test_hierarchical_helpers_are_left_for_fgen(builder, spec):
    """find_obj_in_locs is used by policy examples but defined in no example."""
    policy = builder.templates['policy']
    fgen = builder.templates['fgen']
    assert 'find_obj_in_locs(' in policy
    assert 'def find_obj_in_locs' not in policy + fgen


def test_examples_check_view_before_search_here(builder):
    for _, _, query, code in split_policy_examples(builder.templates['policy']):
        if 'search_here(' in code:
            assert code.index('is_obj_visible(') < code.index('search_here('), query


def test_api_header_lists_every_api_function(builder, spec):
    header = builder.api_header()
    for fn in spec.functions.values():
        assert fn.signature in header
    imported = set()
    for line in builder.import_lines():
        imported |= {n.strip() for n in line.split('import', 1)[1].split(',')}
    assert imported == set(spec.functions)


def test_objects_come_from_the_parameter_not_an_alias_table(spec, env):
    custom = PromptBuilder(spec, ['backpack', 'cup'], env, PATHS['prompts_dir'])
    assert custom.context().splitlines()[1] == "objects = ['backpack', 'cup']"
    assert "-> '" not in custom.api_header()    # no 'user words -> name' alias lines


def test_full_prompt_layout(builder, env):
    base = builder.policy_base_prompt()
    assert API_PLACEHOLDER not in base and API_PLACEHOLDER not in builder.fgen_base_prompt()
    assert base.startswith('# Python robot policy script')
    ctx = builder.context()
    assert ctx.splitlines()[0] == f'locations = {env.locations!r}'
    assert builder.system_prompt()
