"""Static grammar validation: accepted subset and rejected constructs."""

from conftest import CHECK_OBJ_HERE, FIND_OBJ_IN_LOCS, hierarchical_policy, INLINE_POLICY
import pytest


def codes(issues):
    return {i.code for i in issues}


def test_user_example_policy_is_valid(validator):
    assert validator.check_bundle(INLINE_POLICY, {}) == []


def test_hierarchical_bundle_is_valid(validator):
    helpers = {'check_obj_here': CHECK_OBJ_HERE, 'find_obj_in_locs': FIND_OBJ_IN_LOCS}
    assert validator.check_bundle(hierarchical_policy('cup', '컵'), helpers) == []


def test_undefined_helper_rejected_in_bundle(validator):
    issues = validator.check_bundle(hierarchical_policy('cup', '컵'), {})
    assert 'UNDEFINED_FUNCTION' in codes(issues)


def test_pending_helper_allowed_before_fgen(validator):
    code = hierarchical_policy('cup', '컵')
    assert validator.check_code(code, pending={'find_obj_in_locs'}) == []


@pytest.mark.parametrize('code, expected', [
    ('import os\n', 'UNSUPPORTED_SYNTAX'),
    ('from os import path\n', 'UNSUPPORTED_SYNTAX'),
    ('x = say.__class__\n', 'UNSUPPORTED_SYNTAX'),
    ("get_search_locations().append('x')\n", 'ATTRIBUTE_OR_DYNAMIC_CALL'),
    ('while True:\n  say("x")\n', 'UNSUPPORTED_SYNTAX'),
    ("eval('1')\n", 'FORBIDDEN_BUILTIN'),
    ("exec('x=1')\n", 'FORBIDDEN_BUILTIN'),
    ("f = open('/etc/passwd')\n", 'FORBIDDEN_BUILTIN'),
    ("__import__('os')\n", 'DUNDER'),
    ("print('x')\n", 'FORBIDDEN_BUILTIN'),
    ('x = getattr(say, "y")\n', 'FORBIDDEN_BUILTIN'),
    ('for i in range(3):\n  say("x")\n', 'UNSUPPORTED_SYNTAX'),
    ('f = lambda: 1\n', 'UNSUPPORTED_SYNTAX'),
    ('x = [l for l in get_search_locations()]\n', 'UNSUPPORTED_SYNTAX'),
    ('try:\n  say("x")\nexcept Exception:\n  pass\n', 'UNSUPPORTED_SYNTAX'),
    ('with x:\n  pass\n', 'UNSUPPORTED_SYNTAX'),
    ('class A:\n  pass\n', 'UNSUPPORTED_SYNTAX'),
    ('global x\n', 'UNSUPPORTED_SYNTAX'),
    ('del x\n', 'UNSUPPORTED_SYNTAX'),
    ('a, b = 1, 2\n', 'UNSUPPORTED_SYNTAX'),
    ('x = {"a": 1}\n', 'UNSUPPORTED_SYNTAX'),
    ('x = 1 * 2\n', 'UNSUPPORTED_SYNTAX'),
    ('x = [1, 2][0:1]\n', 'UNSUPPORTED_SYNTAX'),
    ('return 1\n', 'UNSUPPORTED_SYNTAX'),
    ('def f(x=1):\n  return x\n', 'UNSUPPORTED_SYNTAX'),
    ('def f(*args):\n  return 1\n', 'UNSUPPORTED_SYNTAX'),
    ('def f(x):\n  def g():\n    return 1\n  return g()\n', 'UNSUPPORTED_SYNTAX'),
    ('def goto_loc(x):\n  return True\n', 'NAME_COLLISION'),
    ('say = 1\n', 'SHADOWING'),
    ('for say in [1]:\n  pass\n', 'SHADOWING'),
    ('x = 1\nx\n', 'UNSUPPORTED_SYNTAX'),
    ("goto_loc('kitchen')\n", 'UNKNOWN_LOCATION'),
    ("is_obj_visible('wallet')\n", 'UNKNOWN_OBJECT'),
    ("search_here('backpack')\n", 'UNKNOWN_OBJECT'),     # detector class, not canonical
    ("target = 'umbrela'\nsearch_here(target)\n", 'UNKNOWN_OBJECT'),
    ("goto_loc('sofa', 'cup')\n", 'BAD_ARGUMENTS'),
    ('led_on(1)\n', 'BAD_ARGUMENTS'),
    ("x = f'{1!r}'\n", 'UNSUPPORTED_SYNTAX'),
    ('x = b"bytes"\n', 'UNSUPPORTED_SYNTAX'),
    ('async def f():\n  pass\n', 'UNSUPPORTED_SYNTAX'),
    ('x = (yield)\n', 'UNSUPPORTED_SYNTAX'),
    ('x = 1 if True else 2\n', 'UNSUPPORTED_SYNTAX'),
    ('x = undefined_thing(1)\n', 'UNDEFINED_FUNCTION'),
])
def test_rejected_constructs(validator, code, expected):
    assert expected in codes(validator.check_bundle(code, {}))


def test_object_name_followed_through_helper_parameter(validator):
    helpers = {'check_obj_here': CHECK_OBJ_HERE, 'find_obj_in_locs': FIND_OBJ_IN_LOCS}
    bad = hierarchical_policy('umbrela', '우산')
    issues = validator.check_bundle(bad, helpers)
    assert 'UNKNOWN_OBJECT' in codes(issues)
    assert {i.where for i in issues} <= {'check_obj_here', 'find_obj_in_locs', 'policy'}


def test_recursion_rejected(validator):
    helpers = {'f': 'def f(x):\n  return f(x)\n'}
    assert 'RECURSION' in codes(validator.check_bundle('y = f(1)\n', helpers))
    helpers = {'a': 'def a(x):\n  return b(x)\n', 'b': 'def b(x):\n  return a(x)\n'}
    assert 'RECURSION' in codes(validator.check_bundle('y = a(1)\n', helpers))


def test_helper_arity_mismatch_rejected(validator):
    helpers = {'check_obj_here': CHECK_OBJ_HERE}
    issues = validator.check_bundle("x = check_obj_here('cup', 'sofa')\n", helpers)
    assert 'BAD_ARGUMENTS' in codes(issues)


def test_helper_must_be_single_def(validator):
    src = CHECK_OBJ_HERE + "\nsay('extra')\n"
    assert 'BAD_HELPER' in codes(validator.check_bundle('x = 1\n',
                                                        {'check_obj_here': src}))


def test_too_many_helpers_rejected(validator, spec):
    n = spec.limits['max_helper_functions'] + 1
    helpers = {f'h{i}': f'def h{i}(x):\n  return x\n' for i in range(n)}
    policy = ''.join(f'y{i} = h{i}(1)\n' for i in range(n))
    assert 'TOO_MANY_FUNCTIONS' in codes(validator.check_bundle(policy, helpers))


def test_object_targets(validator):
    full = CHECK_OBJ_HERE + '\n' + FIND_OBJ_IN_LOCS + '\n' + hierarchical_policy('book', '책')
    assert validator.object_targets(full) == ['book']
    assert validator.object_targets(INLINE_POLICY) == ['umbrella']
