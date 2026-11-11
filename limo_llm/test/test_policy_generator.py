"""
End-to-end generation with the mock LLM (Korean commands, 5 target objects).

The mock LLM returns what a correct LLM is expected to write. These tests check the
pipeline around the LLM: the prompt sent for a Korean command, hierarchical generation,
validation and the bundle. They do NOT measure how well a real
LLM maps Korean words to object names (needs provider:=openai, not run here).
"""

import json

from conftest import FGEN_RESPONSES, hierarchical_policy, INLINE_POLICY, PATHS
from limo_policy.bundle import PolicyBundle
import pytest

CASES = [
    ('가방 찾아줘.', 'handbag', '가방'),
    ('우산이 어디 있는지 찾아줘.', 'umbrella', '우산'),
    ('컵 좀 찾아줘.', 'cup', '컵'),
    ('핸드폰 잃어버렸어. 찾아줘.', 'cell phone', '휴대폰'),
    ('휴대폰 찾아줘.', 'cell phone', '휴대폰'),
    ('내 책 찾아줘.', 'book', '책'),
]


@pytest.mark.parametrize('command, obj, ko', CASES)
def test_korean_commands_for_five_objects(make_generator, command, obj, ko):
    gen, llm = make_generator([(command, hierarchical_policy(obj, ko))] + FGEN_RESPONSES)
    r = gen.generate(command)
    assert r.success, r.error_message
    b = r.bundle
    assert b.target_objects == [obj]
    assert b.command == command
    # the Korean command is the CaP query line, right after the runtime context
    prompt = llm.calls[0]['prompt']
    assert prompt.endswith(f'objects = {gen.objects!r}\n# {command}')
    assert b.policy_code.startswith('locations = [')    # include_context like the original


def test_objects_parameter(make_generator):
    gen, _ = make_generator([])
    assert gen.objects == ['handbag', 'umbrella', 'cup', 'cell phone', 'book']
    with pytest.raises(ValueError):
        gen.__class__(gen.spec, ['cup', 'cup'], gen.env, PATHS['prompts_dir'], None)
    with pytest.raises(ValueError):
        gen.__class__(gen.spec, [], gen.env, PATHS['prompts_dir'], None)


def test_unlisted_object_is_searched_with_a_warning(make_generator):
    gen, _ = make_generator([('지갑', hierarchical_policy('wallet', '지갑'))]
                            + FGEN_RESPONSES)
    r = gen.generate('지갑 찾아줘.')
    assert r.success, r.error_message
    assert r.bundle.target_objects == ['wallet']
    assert any("'wallet' is not in objects" in w for w in r.bundle.warnings)


def test_listed_object_has_no_unlisted_warning(make_generator):
    gen, _ = make_generator([('우산', hierarchical_policy('umbrella', '우산'))]
                            + FGEN_RESPONSES)
    assert not any('not in objects' in w for w in gen.generate('우산 찾아줘.').bundle.warnings)


def test_partial_object_name_is_rejected(make_generator):
    gen, llm = make_generator([('핸드폰', "found = search_here('phone')\n")])
    r = gen.generate('핸드폰 찾아줘.')
    assert not r.success and r.error_code == 'VALIDATION_ERROR'
    assert 'UNKNOWN_OBJECT' in r.error_message and "'cell phone'" in r.error_message
    assert len(llm.calls) == 2    # rejected, regenerated once with feedback, rejected again
    assert 'previous code for this command was rejected' in llm.calls[1]['prompt']


def test_hallucinated_location_rejected(make_generator):
    gen, _ = make_generator([('우산', "goto_loc('bathroom')\n")], cfg={'max_attempts': 1})
    r = gen.generate('화장실에서 우산 찾아줘.')
    assert not r.success and 'UNKNOWN_LOCATION' in r.error_message


def test_retry_with_feedback_can_recover(make_generator):
    def fallback(prompt, query):
        if 'previous code for this command was rejected' in prompt:
            return INLINE_POLICY
        return 'import os\n'
    gen, llm = make_generator([], fallback=fallback)
    r = gen.generate('우산 찾아줘')
    assert r.success and len(llm.calls) == 2


def test_user_example_inline_policy(make_generator):
    gen, llm = make_generator([('우산', INLINE_POLICY)])
    r = gen.generate('우산 찾아줘.')
    assert r.success and r.bundle.helpers == [] and r.bundle.target_objects == ['umbrella']
    assert len(llm.calls) == 1          # nothing undefined -> no fgen call


def test_inline_def_moved_to_helpers(make_generator):
    code = ('def look(obj_name):\n'
            '  return is_obj_visible(obj_name)\n'
            "if look('cup'):\n"
            "  say('컵이 보여요.')\n")
    gen, _ = make_generator([('컵', code)])
    r = gen.generate('컵 보여?')
    assert r.success
    assert [(h.name, h.origin) for h in r.bundle.helpers] == [('look', 'policy')]
    assert 'def ' not in r.bundle.policy_code


def test_llm_error_reported(make_generator):
    gen, _ = make_generator([])
    r = gen.generate('우산 찾아줘')
    assert not r.success and r.error_code == 'LLM_ERROR'


def test_empty_and_multiline_commands(make_generator):
    gen, llm = make_generator([('우산', INLINE_POLICY)])
    assert gen.generate('   ').error_code == 'INVALID_REQUEST'
    r = gen.generate('우산\n찾아줘\nimport os')
    assert r.success
    assert llm.calls[-1]['query'] == '# 우산 찾아줘 import os.'   # one comment line


def test_bundle_json_roundtrip(make_generator):
    gen, _ = make_generator([('컵', hierarchical_policy('cup', '컵'))] + FGEN_RESPONSES)
    b = gen.generate('컵 찾아줘').bundle
    data = json.loads(b.to_json())
    assert data['api_version'] == gen.spec.api_version
    assert data['grammar_version'] == gen.spec.grammar_version
    b2 = PolicyBundle.from_json(b.to_json())
    assert b2.full_source() == b.full_source()
