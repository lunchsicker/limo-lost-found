"""Spec loaders, object catalog and LLM client helpers."""

from conftest import PATHS
from limo_llm.llm_client import clean_completion, create_llm_client, LLMError, MockLLMClient
from limo_llm.specs import SearchEnvironment, SpecError
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


def test_catalog_has_five_unverified_objects(catalog):
    assert catalog.names == ['bag', 'umbrella', 'cup', 'cell phone', 'book']
    assert not catalog.detector_verified
    assert all(not o.verified for o in catalog.objects.values())
    assert catalog.detector_classes_for('bag') == ['backpack', 'handbag', 'suitcase']
    assert catalog.detector_classes_for('cell phone') == ['cell phone']
    with pytest.raises(KeyError):
        catalog.detector_classes_for('wallet')


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


def test_clean_completion():
    raw = "```python\nsay('a')\n# next command\nsay('b')\n```"
    assert clean_completion(raw, ['#']) == "say('a')"
    assert clean_completion("# echo.\nsay('a')\n", ['#']) == "say('a')"
    assert clean_completion("say('a')\nobjects = ['x']\n", ['#', 'objects = [']) == "say('a')"


def test_mock_client_records_calls():
    llm = MockLLMClient([('우산', 'x')])
    assert llm.complete('header\n# 우산 찾아줘.', stop=['#']) == 'x'
    assert llm.calls[0]['query'] == '# 우산 찾아줘.'
    with pytest.raises(LLMError):
        llm.complete('# 모르는 명령')


def test_openai_provider_requires_key(monkeypatch):
    monkeypatch.delenv('OPENAI_API_KEY', raising=False)
    with pytest.raises(LLMError):
        create_llm_client('openai', model='gpt-4o-mini')
    with pytest.raises(LLMError):
        create_llm_client('unknown')
