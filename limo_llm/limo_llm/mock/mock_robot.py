"""
Mock Robot API + mock executor (tests and offline demos only).

``MockRobot`` simulates navigation, the camera view and the ``search_here`` rotation
contract with a scripted scenario. ``run_bundle`` re-validates a PolicyBundle, runs it
with the reference interpreter and maps the outcome to the ExecutePolicy result codes.

Nothing here moves a robot or runs a detector. LED calls return False unless the
scenario explicitly enables the *mock* LED, and the result always reports
``led_backend='mock'``.
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple

from limo_policy.errors import (DetectionError, PolicyCanceled, PolicyRuntimeError,
                                UnknownLocationError, UnsupportedObjectError)
from limo_policy.interpreter import PolicyInterpreter
from limo_policy.validator import PolicyValidator
import yaml


@dataclass
class MockScenario:
    locations: List[str]
    known_objects: List[str]
    # object -> (location, view index). View 0 is the view on arrival, view k is the view
    # after k rotation steps of search_here(). Location None = the start pose.
    placements: Dict[str, Tuple[Optional[str], int]] = field(default_factory=dict)
    unreachable: Set[str] = field(default_factory=set)
    # objects listed by get_known_objects() whose detector class is missing
    undetectable: Set[str] = field(default_factory=set)
    camera_error_at: Optional[Tuple[Optional[str], int]] = None   # (location, view)
    cancel_after_api_calls: Optional[int] = None
    rotation_step_deg: float = 60.0
    max_rotations: int = 5            # 6 views incl. the arrival view = 360 deg
    led_available: bool = False

    @classmethod
    def from_dict(cls, d):
        d = dict(d)
        d['placements'] = {k: (v[0], int(v[1])) for k, v in (d.get('placements') or {}).items()}
        d['unreachable'] = set(d.get('unreachable') or [])
        d['undetectable'] = set(d.get('undetectable') or [])
        if d.get('camera_error_at') is not None:
            d['camera_error_at'] = tuple(d['camera_error_at'])
        return cls(**d)

    @classmethod
    def from_yaml(cls, path):
        with open(path, 'r', encoding='utf-8') as f:
            return cls.from_dict(yaml.safe_load(f))


class MockRobot:

    def __init__(self, scenario: MockScenario):
        self.s = scenario
        self.location: Optional[str] = None
        self.view = 0
        self.calls: List[Tuple] = []          # every API call / internal event, in order
        self.messages: List[str] = []
        self.found: List[Tuple[str, str]] = []
        self.visited: List[str] = []
        self.failed: List[str] = []
        self.rotations = 0
        self.led = False
        self.cancel_requested = False
        self._api_calls = 0

    # ------------------------------------------------------------------ plumbing

    def _api(self, name, *args):
        self._api_calls += 1
        n = self.s.cancel_after_api_calls
        if n is not None and self._api_calls > n:
            self.cancel_requested = True
        if self.cancel_requested:
            raise PolicyCanceled(f'cancel requested before {name}()')
        self.calls.append((name,) + args)

    def _detect(self, obj_name):
        if obj_name not in self.s.known_objects or obj_name in self.s.undetectable:
            raise UnsupportedObjectError(obj_name)
        if self.s.camera_error_at == (self.location, self.view):
            raise DetectionError(f'camera error at {self.location} view {self.view}')
        self.calls.append(('detect', obj_name, self.location, self.view))
        return self.s.placements.get(obj_name) == (self.location, self.view)

    def api(self):
        return {name: getattr(self, name) for name in (
            'get_search_locations', 'get_known_objects', 'get_visible_obj_names',
            'is_obj_visible', 'goto_loc', 'search_here', 'led_on', 'led_off',
            'report_found', 'say')}

    # ------------------------------------------------------------------ Robot API

    def get_search_locations(self):
        self._api('get_search_locations')
        return list(self.s.locations)

    def get_known_objects(self):
        self._api('get_known_objects')
        return list(self.s.known_objects)

    def get_visible_obj_names(self):
        self._api('get_visible_obj_names')
        if self.s.camera_error_at == (self.location, self.view):
            raise DetectionError(f'camera error at {self.location} view {self.view}')
        return [o for o, p in self.s.placements.items()
                if p == (self.location, self.view) and o in self.s.known_objects]

    def is_obj_visible(self, obj_name):
        self._api('is_obj_visible', obj_name)
        return self._detect(obj_name)   # no motion

    def goto_loc(self, loc_name):
        self._api('goto_loc', loc_name)
        if loc_name not in self.s.locations:
            raise UnknownLocationError(loc_name)
        if loc_name in self.s.unreachable:
            self.failed.append(loc_name)
            return False
        self.location, self.view = loc_name, 0
        self.visited.append(loc_name)
        return True

    def search_here(self, obj_name):
        """Mock of the contract: current view, then bounded rotations, early exit."""
        self._api('search_here', obj_name)
        if self._detect(obj_name):
            return True
        for _ in range(self.s.max_rotations):
            if self.cancel_requested:
                raise PolicyCanceled('cancel requested during search_here()')
            self.view += 1
            self.rotations += 1
            self.calls.append(('rotate', self.s.rotation_step_deg))
            self.calls.append(('settle',))     # wait for a fresh, stable camera frame
            if self._detect(obj_name):
                return True                    # remaining rotations skipped
        return False

    def led_on(self):
        self._api('led_on')
        if not self.s.led_available:
            return False                       # no LED: never report success
        self.led = True
        return True

    def led_off(self):
        self._api('led_off')
        if not self.s.led_available:
            return False
        self.led = False
        return True

    def report_found(self, obj_name, loc_name):
        self._api('report_found', obj_name, loc_name)
        self.found.append((obj_name, loc_name))

    def say(self, msg):
        self._api('say', msg)
        self.messages.append(str(msg))


@dataclass
class MockExecutionResult:
    outcome: str        # FOUND | NOT_FOUND | DETECTION_ERROR | UNSUPPORTED_OBJECT | CANCELED |
    #                     VALIDATION_ERROR | RUNTIME_ERROR | LIMIT_EXCEEDED | UNKNOWN_LOCATION
    message: str
    found: List[Tuple[str, str]]
    visited_locations: List[str]
    failed_locations: List[str]
    messages: List[str]
    calls: List[Tuple]
    led_backend: str = 'mock'

    @property
    def api_sequence(self):
        """Robot API calls only (no internal detect/rotate events)."""
        internal = {'detect', 'rotate', 'settle'}
        return [c for c in self.calls if c[0] not in internal]


def run_bundle(bundle, robot: MockRobot, spec, object_names, location_names):
    """Re-validate and run a PolicyBundle on the mock robot (executor-side behaviour)."""
    validator = PolicyValidator(spec, object_names, location_names)
    issues = validator.check_bundle(bundle.policy_code, bundle.helper_sources)
    if bundle.api_version != spec.api_version or bundle.grammar_version != spec.grammar_version:
        issues = list(issues) + ['api/grammar version mismatch']
    if issues:
        return _result(robot, 'VALIDATION_ERROR', '; '.join(str(i) for i in issues))
    interp = PolicyInterpreter(spec, robot.api(), cancel_check=lambda: robot.cancel_requested)
    try:
        interp.run(bundle.helper_sources, bundle.policy_code)
    except PolicyRuntimeError as e:
        return _result(robot, e.code, str(e))
    if robot.found:
        return _result(robot, 'FOUND', f'found {robot.found}')
    if robot.failed and not robot.visited:
        return _result(robot, 'NAVIGATION_FAILED', 'no search location could be reached')
    return _result(robot, 'NOT_FOUND', 'policy finished without report_found()')


def _result(robot, outcome, message):
    return MockExecutionResult(outcome=outcome, message=message, found=list(robot.found),
                               visited_locations=list(robot.visited),
                               failed_locations=list(robot.failed),
                               messages=list(robot.messages), calls=list(robot.calls))
