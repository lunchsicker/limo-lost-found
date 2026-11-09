"""
One Robot API contract across prompt, validator, interpreter and mock robot.

Also covers the object / location boundaries: the LLM uses canonical object names and
semantic location names only; attributes (colour, size) are not verified by the
detector, and location -> pose resolution stays in the executor (mock here).
"""

import ast
import os

from conftest import FGEN_RESPONSES, hierarchical_policy, PATHS
from limo_llm.mock.mock_robot import MockRobot, MockScenario, run_bundle
from limo_llm.prompt_builder import PromptBuilder
from limo_llm.resources import default_package_dir
from limo_policy.interpreter import PolicyInterpreter
import pytest
import yaml


@pytest.fixture
def builder(spec, catalog, env):
    return PromptBuilder(spec, catalog, env, PATHS['prompts_dir'])


def prompt_api_names(builder):
    names = []
    for line in builder.import_lines():
        names += [n.strip() for n in line.split(' import ', 1)[1].split(',')]
    return names


def test_prompt_validator_interpreter_and_mock_share_one_api(builder, spec, validator):
    api = set(spec.functions)
    assert set(prompt_api_names(builder)) == api                      # 1. LMP prompt
    assert api <= validator.spec.reserved_names                       # 2. validator
    robot = MockRobot(MockScenario(locations=['sofa'], known_objects=['cup']))
    assert set(robot.api()) == api                                    # mock implementation
    assert set(PolicyInterpreter(spec, robot.api()).api) == api       # 3. interpreter


def test_prompt_signatures_come_from_spec(builder, spec):
    header = builder.api_header()
    for fn in spec.functions.values():
        assert f'#   {fn.signature}: {fn.description}' in header
    for name in ('goto_loc', 'is_obj_visible', 'search_here'):   # agreed search API
        assert [p.name for p in spec.functions[name].params] == \
            ['loc_name' if name == 'goto_loc' else 'obj_name']


def test_prompt_never_contains_coordinates(builder):
    # search_poses_sim.yaml is owned/read by limo_executor only; read here just to check
    # that none of its numbers leak into what the LLM sees.
    poses = yaml.safe_load(open(os.path.join(os.path.dirname(PATHS['robot_api_file']),
                                             'search_poses_sim.yaml'), encoding='utf-8'))
    full = builder.policy_base_prompt() + builder.fgen_base_prompt() + builder.context()
    assert 'search_poses' not in full and 'look_at' not in full
    for entry in poses['poses'].values():
        x, y = entry['pose'][:2]
        assert f'{x}, {y}' not in full and f'{x:.2f}, {y:.2f}' not in full


def test_mock_scenario_matches_shared_specs(catalog, env):
    s = MockScenario.from_yaml(default_package_dir('config') + '/mock_scenario.yaml')
    assert s.locations == env.locations
    assert s.known_objects == catalog.names


# ---------------------------------------------------------------- objects


def test_attribute_request_targets_class_only(make_generator):
    # "black umbrella": the policy may search for the class 'umbrella' (what the
    # detector supports); it must not claim the colour was checked.
    gen, _ = make_generator([('우산', hierarchical_policy('umbrella', '우산'))]
                            + FGEN_RESPONSES)
    r = gen.generate('검은색 우산 찾아줘.')
    assert r.success and r.bundle.target_objects == ['umbrella']
    assert '검은' not in r.bundle.full_source()


def test_attribute_phrase_as_object_name_is_rejected(make_generator):
    gen, _ = make_generator([('우산', "found = search_here('black umbrella')\n")],
                            cfg={'max_attempts': 1})
    r = gen.generate('검은색 우산 찾아줘.')
    assert not r.success and r.error_code == 'VALIDATION_ERROR'
    assert 'UNKNOWN_OBJECT' in r.error_message


@pytest.mark.parametrize('command, target', [
    ('백팩 찾아줘.', 'bag'), ('스마트폰 어디 있어?', 'cell phone'), ('머그컵 찾아줘.', 'cup'),
])
def test_alias_resolves_to_canonical_name(catalog, command, target):
    assert catalog.resolve_from_text(command) == [target]


# ---------------------------------------------------------------- locations


def _bundle(make_generator, code):
    gen, _ = make_generator([('찾아', code)])
    r = gen.generate('찾아줘.')
    assert r.success, r.error_message
    return r.bundle


def test_valid_semantic_location(make_generator, spec, catalog, env):
    b = _bundle(make_generator, "if goto_loc('sofa'):\n  x = search_here('cup')\n")
    robot = MockRobot(MockScenario(env.locations, catalog.names,
                                   placements={'cup': ('sofa', 1)}))
    res = run_bundle(b, robot, spec, catalog.names, env.locations)
    assert res.visited_locations == ['sofa']
    assert [c[:2] for c in res.api_sequence] == [('goto_loc', 'sofa'), ('search_here', 'cup')]


def test_location_unknown_to_executor_is_an_error_not_a_remap(make_generator, spec,
                                                              catalog, env):
    # The prompt/validator know 'sofa', but the executor's own mapping does not:
    # the executor raises UNKNOWN_LOCATION instead of driving somewhere else.
    b = _bundle(make_generator, "if goto_loc('sofa'):\n  x = search_here('cup')\n")
    others = [loc for loc in env.locations if loc != 'sofa']
    robot = MockRobot(MockScenario(others, catalog.names))
    res = run_bundle(b, robot, spec, catalog.names, env.locations)
    assert res.outcome == 'UNKNOWN_LOCATION' and res.visited_locations == []
    assert not any(c[0] in ('detect', 'search_here') for c in res.calls)


def test_policy_examples_use_location_names_not_poses():
    text = open(PATHS['prompts_dir'] + '/policy_ui.txt', encoding='utf-8').read()
    for node in ast.walk(ast.parse(text.split('\n', 1)[1])):
        if isinstance(node, ast.Call) and getattr(node.func, 'id', '') == 'goto_loc':
            arg = node.args[0]
            assert isinstance(arg, ast.Name) or isinstance(arg.value, str)
