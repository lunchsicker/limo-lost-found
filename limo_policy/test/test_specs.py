"""Spec loaders (moved from limo_llm/test/test_specs_and_client.py)."""

from conftest import PATHS
from limo_policy.specs import SearchEnvironment, SpecError
import pytest
import yaml


def test_robot_api_matches_agreed_functions(spec):
    assert set(spec.functions) == {
        'get_search_locations', 'get_known_objects', 'get_visible_obj_names',
        'is_obj_visible', 'goto_loc', 'search_here', 'led_on', 'led_off',
        'report_found', 'say'}
    assert spec.functions['search_here'].signature == 'search_here(obj_name: str) -> bool'
    assert spec.functions['report_found'].signature == \
        'report_found(obj_name: str, loc_name: str) -> None'
    assert spec.api_version and spec.grammar_version


def test_locations_have_no_coordinates(env):
    raw = yaml.safe_load(open(PATHS['search_locations_file'], encoding='utf-8'))
    for entry in raw['locations']:
        assert set(entry) <= {'name', 'description'}
    assert 'sofa' in env.locations


def test_bad_location_file_rejected(tmp_path):
    p = tmp_path / 'loc.yaml'
    p.write_text('locations:\n  - name: "x y"\n')
    with pytest.raises(SpecError):
        SearchEnvironment.load(str(p))


def test_list_apis_derived_from_spec(spec):
    # for-loop iterables of the grammar (robot_api.yaml grammar.rules.for_iterables)
    assert spec.list_apis == ('get_search_locations', 'get_known_objects',
                              'get_visible_obj_names')
