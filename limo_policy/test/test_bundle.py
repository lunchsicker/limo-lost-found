"""PolicyBundle JSON format (contract with limo_executor) stays unchanged."""

import json

from conftest import CHECK_OBJ_HERE, FIND_OBJ_IN_LOCS, hierarchical_policy
from limo_policy.bundle import BUNDLE_FORMAT_VERSION, HelperFunction, PolicyBundle

FIELDS = {'request_id', 'command', 'policy_code', 'helpers', 'target_objects', 'api_version',
          'grammar_version', 'provider', 'model', 'warnings', 'created_at', 'format_version'}


def make_bundle():
    return PolicyBundle(
        request_id='r1', command='컵 찾아줘', policy_code=hierarchical_policy('cup', '컵'),
        helpers=[HelperFunction('check_obj_here', CHECK_OBJ_HERE, 'check_obj_here(obj_name)', []),
                 HelperFunction('find_obj_in_locs', FIND_OBJ_IN_LOCS, 'f(...)',
                                ['check_obj_here'])],
        target_objects=['cup'], api_version='0.1.0', grammar_version='0.1.0')


def test_fields_and_versions_unchanged():
    data = json.loads(make_bundle().to_json())
    assert set(data) == FIELDS
    assert set(data['helpers'][0]) == {'name', 'source', 'signature', 'calls', 'origin'}
    assert BUNDLE_FORMAT_VERSION == '0.1.0'


def test_roundtrip_and_full_source_order():
    b = make_bundle()
    b2 = PolicyBundle.from_json(b.to_json())
    assert b2 == b
    src = b.full_source()
    assert src.index('def check_obj_here') < src.index('def find_obj_in_locs') \
        < src.index('found_loc =')
    assert list(b.helper_sources) == ['check_obj_here', 'find_obj_in_locs']
