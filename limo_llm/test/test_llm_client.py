"""LLM client helpers (spec loader tests moved to limo_policy/test/test_specs.py)."""

from limo_llm.llm_client import clean_completion, create_llm_client, LLMError, MockLLMClient
import pytest


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
