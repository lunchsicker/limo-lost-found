"""Shared fixtures: specs from the repository config/ and mock-LLM generators."""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.dirname(HERE)
sys.path.insert(0, PKG)   # run from the source tree without colcon
sys.path.insert(1, os.path.join(os.path.dirname(PKG), 'limo_policy'))

from limo_llm.llm_client import MockLLMClient  # noqa: E402
from limo_llm.policy_generator import DEFAULT_OBJECTS, PolicyGenerator  # noqa: E402
from limo_policy.specs import RobotApiSpec, SearchEnvironment  # noqa: E402
from limo_policy.validator import PolicyValidator  # noqa: E402
import pytest  # noqa: E402,I100

COMMON = os.path.join(os.path.dirname(PKG), 'config')
PATHS = {
    'robot_api_file': os.path.join(COMMON, 'robot_api.yaml'),
    'search_locations_file': os.path.join(COMMON, 'search_locations_sim.yaml'),
    'prompts_dir': os.path.join(PKG, 'prompts'),
}

# --- Completions used by the mock LLM (what a real LLM is expected to write) ----------

INLINE_POLICY = """target = 'umbrella'
found = False
say('우산을 찾아볼게요.')
for location in get_search_locations():
  if goto_loc(location):
    if is_obj_visible(target):
      report_found(target, location)
      found = True
      break
    if search_here(target):
      report_found(target, location)
      found = True
      break
if not found:
  say('모든 탐색 위치를 확인했지만 우산을 찾지 못했습니다.')
"""


def hierarchical_policy(obj, ko):
    return (f"say('{ko}을(를) 찾아볼게요.')\n"
            f"found_loc = find_obj_in_locs('{obj}', get_search_locations())\n"
            'if found_loc:\n'
            f"  say(f'{{found_loc}}에서 {ko}을(를) 찾았어요.')\n"
            'else:\n'
            f"  say('{ko}을(를) 찾지 못했어요.')\n")


FIND_OBJ_IN_LOCS = """def find_obj_in_locs(obj_name, loc_names):
  for loc_name in loc_names:
    if goto_loc(loc_name):
      if check_obj_here(obj_name):
        report_found(obj_name, loc_name)
        return loc_name
  return None
"""

CHECK_OBJ_HERE = """def check_obj_here(obj_name):
  if is_obj_visible(obj_name):
    return True
  return search_here(obj_name)
"""

FGEN_RESPONSES = [
    ('find_obj_in_locs(', FIND_OBJ_IN_LOCS),
    ('check_obj_here(', CHECK_OBJ_HERE),
]


@pytest.fixture(scope='session')
def spec():
    return RobotApiSpec.load(PATHS['robot_api_file'])


@pytest.fixture(scope='session')
def objects():
    return list(DEFAULT_OBJECTS)


@pytest.fixture(scope='session')
def env():
    return SearchEnvironment.load(PATHS['search_locations_file'])


@pytest.fixture
def validator(spec, objects, env):
    return PolicyValidator(spec, objects, env.locations)


@pytest.fixture
def make_generator():
    def _make(responses, cfg=None, fallback=None):
        llm = MockLLMClient(list(responses), fallback=fallback)
        gen = PolicyGenerator.from_files(llm=llm, cfg=cfg, **PATHS)
        return gen, llm
    return _make
