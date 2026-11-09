"""
Generated policies on the MOCK robot: search scenarios and API call order.

Uses limo_llm's reference interpreter and MockRobot, not limo_executor, Nav2 or a real
detector. The outcomes only show that the generated code follows the search contract.
"""

from conftest import FGEN_RESPONSES, hierarchical_policy, INLINE_POLICY
from limo_llm.mock.mock_robot import MockRobot, MockScenario, run_bundle
import pytest

LOCS = ['fridge', 'cardboard_boxes', 'armchair', 'sofa', 'cabinet', 'door']
OBJS = ['handbag', 'umbrella', 'cup', 'cell phone', 'book']


@pytest.fixture(params=['inline', 'hierarchical'])
def bundle(request, make_generator):
    if request.param == 'inline':
        gen, _ = make_generator([('우산', INLINE_POLICY)])
    else:
        gen, _ = make_generator([('우산', hierarchical_policy('umbrella', '우산'))]
                                + FGEN_RESPONSES)
    r = gen.generate('우산 찾아줘.')
    assert r.success, r.error_message
    return r.bundle


def run(bundle, spec, robot=None, **scenario):
    robot = robot or MockRobot(MockScenario(locations=LOCS, known_objects=OBJS, **scenario))
    return run_bundle(bundle, robot, spec, OBJS, LOCS), robot


def seq(result):
    """Return API call names (and first arg) without say()/internal events."""
    return [(c[0],) + tuple(c[1:2]) for c in result.api_sequence if c[0] != 'say']


def test_found_in_current_view_without_rotation(bundle, spec):
    res, robot = run(bundle, spec, placements={'umbrella': ('fridge', 0)})
    assert res.outcome == 'FOUND' and res.found == [('umbrella', 'fridge')]
    assert seq(res) == [('get_search_locations',), ('goto_loc', 'fridge'),
                        ('is_obj_visible', 'umbrella'), ('report_found', 'umbrella')]
    assert robot.rotations == 0          # search_here never called


def test_found_after_rotation(bundle, spec):
    res, robot = run(bundle, spec, placements={'umbrella': ('fridge', 2)})
    assert res.outcome == 'FOUND'
    assert seq(res)[2:] == [('is_obj_visible', 'umbrella'), ('search_here', 'umbrella'),
                            ('report_found', 'umbrella')]
    assert robot.rotations == 2          # remaining rotations skipped


def test_not_found_in_any_direction_or_location(bundle, spec):
    res, robot = run(bundle, spec, unreachable={'armchair'})
    assert res.outcome == 'NOT_FOUND' and res.found == []
    assert res.visited_locations == [loc for loc in LOCS if loc != 'armchair']
    assert robot.rotations == 5 * len(res.visited_locations)   # all directions checked
    assert res.messages[-1].startswith(('모든 탐색 위치', '우산'))
    # at every reached location: view check first, then search_here
    api = seq(res)
    for i, call in enumerate(api):
        if call[0] == 'search_here':
            assert api[i - 1] == ('is_obj_visible', 'umbrella')
            assert api[i - 2][0] == 'goto_loc'


def test_found_at_first_location_stops_search(bundle, spec):
    res, _ = run(bundle, spec, placements={'umbrella': ('fridge', 1)})
    assert res.visited_locations == ['fridge']


def test_found_at_second_location(bundle, spec):
    res, _ = run(bundle, spec, placements={'umbrella': ('cardboard_boxes', 0)})
    assert res.outcome == 'FOUND' and res.found == [('umbrella', 'cardboard_boxes')]
    assert res.visited_locations == ['fridge', 'cardboard_boxes']
    assert [c for c in seq(res) if c[0] == 'goto_loc'] == \
        [('goto_loc', 'fridge'), ('goto_loc', 'cardboard_boxes')]


def test_navigation_failure_skips_detection(bundle, spec):
    res, _ = run(bundle, spec, unreachable={'fridge'},
                 placements={'umbrella': ('cardboard_boxes', 1)})
    assert res.outcome == 'FOUND' and res.failed_locations == ['fridge']
    api = seq(res)
    assert api[1:3] == [('goto_loc', 'fridge'), ('goto_loc', 'cardboard_boxes')]
    assert not any(c[0] == 'detect' and c[2] == 'fridge' for c in res.calls)


def test_all_locations_unreachable(bundle, spec):
    res, _ = run(bundle, spec, unreachable=set(LOCS))
    assert res.outcome == 'NAVIGATION_FAILED' and res.failed_locations == LOCS
    assert not any(c[0] in ('is_obj_visible', 'search_here') for c in res.calls)


def test_unsupported_target_is_an_error_not_not_found(bundle, spec):
    res, _ = run(bundle, spec, undetectable={'umbrella'})
    assert res.outcome == 'UNSUPPORTED_OBJECT'


def test_camera_error_is_an_error_not_not_found(bundle, spec):
    res, _ = run(bundle, spec, camera_error_at=('cardboard_boxes', 0),
                 placements={'umbrella': ('sofa', 0)})
    assert res.outcome == 'DETECTION_ERROR'
    assert res.visited_locations == ['fridge', 'cardboard_boxes']   # stopped there


def test_cancel_between_api_calls(bundle, spec):
    res, _ = run(bundle, spec, cancel_after_api_calls=4)
    assert res.outcome == 'CANCELED'
    assert len(res.api_sequence) == 4


def test_cancel_during_search_here_rotation(bundle, spec):
    class CancelOnSecondView(MockRobot):

        def _detect(self, obj_name):
            seen = super()._detect(obj_name)
            if self.view == 2:
                self.cancel_requested = True   # e.g. user said "멈춰" while rotating
            return seen
    robot = CancelOnSecondView(MockScenario(locations=LOCS, known_objects=OBJS))
    res, _ = run(bundle, spec, robot=robot)
    assert res.outcome == 'CANCELED'
    assert robot.rotations == 2 and res.visited_locations == ['fridge']


def test_led_unavailable_is_not_reported_as_success(make_generator, spec):
    code = ("led_ok = led_on()\nif led_ok:\n  say('on')\nelse:\n  say('LED 없음')\n")
    gen, _ = make_generator([('불', code)])
    b = gen.generate('불 켜줘').bundle
    res, _ = run(b, spec)
    assert res.messages == ['LED 없음'] and res.led_backend == 'mock'
    res, robot = run(b, spec, led_available=True)
    assert res.messages == ['on'] and robot.led


def test_executor_revalidates_tampered_bundle(bundle, spec):
    bundle.policy_code += '\nimport os\n'
    res, robot = run(bundle, spec)
    assert res.outcome == 'VALIDATION_ERROR' and robot.calls == []


def test_version_mismatch_rejected(bundle, spec):
    bundle.api_version = '9.9.9'
    res, robot = run(bundle, spec)
    assert res.outcome == 'VALIDATION_ERROR' and robot.calls == []


def test_loop_budget_enforced(make_generator, spec):
    code = ('for a in get_known_objects():\n'
            '  for b in get_known_objects():\n'
            '    for c in get_known_objects():\n'
            "      say('x')\n")
    gen, _ = make_generator([('반복', code)])
    res, _ = run(gen.generate('반복').bundle, spec)
    assert res.outcome == 'LIMIT_EXCEEDED'
