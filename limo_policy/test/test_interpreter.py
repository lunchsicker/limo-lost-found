"""Reference interpreter run directly with a minimal fake Robot API (no MockRobot)."""

from dataclasses import replace

from conftest import CHECK_OBJ_HERE, FIND_OBJ_IN_LOCS, hierarchical_policy
from limo_policy.errors import (DetectionError, PolicyCanceled, PolicyRuntimeError,
                                UnknownLocationError, UnsupportedObjectError)
from limo_policy.interpreter import PolicyInterpreter
import pytest


class FakeApi:
    """Records calls; the object is visible only at `found_at`."""

    def __init__(self, spec, found_at=None, unreachable=(), raise_on_detect=None):
        self.calls = []
        self.found_at = found_at
        self.unreachable = set(unreachable)
        self.raise_on_detect = raise_on_detect
        self.here = None
        self.cancel = False
        self.names = list(spec.functions)

    def table(self):
        def rec(name, result=None):
            def f(*args):
                self.calls.append((name,) + args)
                return result(*args) if callable(result) else result
            return f
        api = {n: rec(n) for n in self.names}
        api['get_search_locations'] = rec('get_search_locations', lambda: ['a', 'b', 'c'])
        api['goto_loc'] = rec('goto_loc', self._goto)
        api['is_obj_visible'] = rec('is_obj_visible', self._detect)
        api['search_here'] = rec('search_here', self._detect)
        return api

    def _goto(self, loc):
        if loc in self.unreachable:
            return False
        self.here = loc
        return True

    def _detect(self, obj):
        if self.raise_on_detect:
            raise self.raise_on_detect
        return self.here == self.found_at


def run(spec, code, helpers=None, **kw):
    fake = FakeApi(spec, **kw)
    interp = PolicyInterpreter(spec, fake.table(), cancel_check=lambda: fake.cancel)
    scope = interp.run(helpers or {}, code)
    return fake, scope


HELPERS = {'check_obj_here': CHECK_OBJ_HERE, 'find_obj_in_locs': FIND_OBJ_IN_LOCS}


def test_hierarchical_policy_finds_object(spec):
    fake, scope = run(spec, hierarchical_policy('cup', '컵'), HELPERS, found_at='b')
    assert scope['found_loc'] == 'b'
    assert ('report_found', 'cup', 'b') in fake.calls
    assert not any(c == ('goto_loc', 'c') for c in fake.calls)   # stopped after finding


def test_navigation_failure_skips_detection(spec):
    fake, scope = run(spec, hierarchical_policy('cup', '컵'), HELPERS, unreachable={'a'})
    assert scope['found_loc'] is None
    detect_after_a = [c for c in fake.calls if c[0] in ('is_obj_visible', 'search_here')]
    assert len(detect_after_a) == 4      # b and c only: visible + search_here each


def test_semantics_break_return_boolop_fstring(spec):
    code = ('out = []\n'
            "for x in ['p', 'q', 'r']:\n"
            "  if x == 'q':\n"
            '    break\n'
            '  out = out + [x]\n'
            'first = out[0]\n'
            "flag = False or 'yes'\n"
            "msg = f'{first}-{flag}'\n")
    _, scope = run(spec, code)
    assert scope['out'] == ['p'] and scope['msg'] == 'p-yes'


def test_detection_error_is_not_not_found(spec):
    with pytest.raises(DetectionError) as e:
        run(spec, "goto_loc('a')\nx = is_obj_visible('cup')\n",
            raise_on_detect=DetectionError('camera'))
    assert e.value.code == 'DETECTION_ERROR'


def test_error_codes():
    assert UnsupportedObjectError('cup').code == 'UNSUPPORTED_OBJECT'
    assert UnknownLocationError('x').code == 'UNKNOWN_LOCATION'
    assert PolicyCanceled().code == 'CANCELED'
    assert issubclass(DetectionError, PolicyRuntimeError)


def test_cancel_before_statement(spec):
    fake = FakeApi(spec)
    fake.cancel = True
    interp = PolicyInterpreter(spec, fake.table(), cancel_check=lambda: fake.cancel)
    with pytest.raises(PolicyCanceled):
        interp.run({}, "say('x')\n")
    assert fake.calls == []


def test_unsupported_node_refused_at_run_time(spec):
    with pytest.raises(PolicyRuntimeError) as e:
        run(spec, 'while True:\n  pass\n')
    assert e.value.code == 'VALIDATION_ERROR'


def test_undefined_name_is_runtime_error(spec):
    with pytest.raises(PolicyRuntimeError) as e:
        run(spec, "x = open('f')\n")
    assert e.value.code == 'RUNTIME_ERROR'


def test_api_implementation_must_be_complete(spec):
    with pytest.raises(ValueError):
        PolicyInterpreter(spec, {'say': print})


def test_step_limit_stops_execution(spec):
    small = replace(spec, limits=dict(spec.limits, max_steps=3))
    fake = FakeApi(small)
    interp = PolicyInterpreter(small, fake.table())
    with pytest.raises(PolicyRuntimeError) as e:
        interp.run({}, "say('1')\nsay('2')\nsay('3')\nsay('4')\nsay('5')\n")
    assert e.value.code == 'LIMIT_EXCEEDED'
    assert fake.calls == [('say', '1'), ('say', '2'), ('say', '3')]   # stopped at once


def test_call_depth_limit(spec):
    helpers = {'h1': 'def h1():\n  return h2()\n',
               'h2': 'def h2():\n  return h3()\n',
               'h3': "def h3():\n  say('deep')\n  return 1\n"}
    deep = replace(spec, limits=dict(spec.limits, max_call_depth=2))
    fake = FakeApi(deep)
    with pytest.raises(PolicyRuntimeError) as e:
        PolicyInterpreter(deep, fake.table()).run(helpers, 'x = h1()\n')
    assert e.value.code == 'LIMIT_EXCEEDED' and fake.calls == []
    _, scope = run(spec, 'x = h1()\n', helpers)   # default max_call_depth 8 is enough
    assert scope['x'] == 1


def test_loop_iteration_limit(spec):
    small = replace(spec, limits=dict(spec.limits, max_loop_iterations=2))
    fake = FakeApi(small)
    with pytest.raises(PolicyRuntimeError) as e:
        PolicyInterpreter(small, fake.table()).run(
            {}, 'for loc in get_search_locations():\n  goto_loc(loc)\n')
    assert e.value.code == 'LIMIT_EXCEEDED'
    assert [c for c in fake.calls if c[0] == 'goto_loc'] == [('goto_loc', 'a'),
                                                             ('goto_loc', 'b')]


def test_error_in_robot_api_stops_policy(spec):
    code = "goto_loc('a')\nx = search_here('cup')\nsay('after')\n"
    with pytest.raises(DetectionError):
        run(spec, code, raise_on_detect=DetectionError('timeout'))
    fake = FakeApi(spec, raise_on_detect=DetectionError('timeout'))
    with pytest.raises(DetectionError):
        PolicyInterpreter(spec, fake.table()).run({}, code)
    assert ('say', 'after') not in fake.calls


def test_builtin_without_implementation_refused(spec):
    widened = replace(spec, allowed_builtins=['len', 'open'])
    with pytest.raises(ValueError, match='open'):
        PolicyInterpreter(widened, FakeApi(spec).table())
