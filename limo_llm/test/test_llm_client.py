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


def test_openai_client_does_not_ask_for_brotli(monkeypatch):
    # httpx2 decodes 'br' with Decompressor.process(..., output_buffer_limit=...), which
    # Ubuntu 22.04's python3-brotli 1.0.9 lacks -> every completion failed (TypeError).
    pytest.importorskip('openai')
    httpx2 = pytest.importorskip('httpx2')
    from limo_llm.llm_client import OpenAIChatClient
    monkeypatch.delenv('OPENAI_API_KEY', raising=False)
    client = OpenAIChatClient('gpt-4o-mini', api_key='sk-test')
    seen = {}

    def handler(request):
        seen['accept-encoding'] = request.headers.get('accept-encoding')
        return httpx2.Response(200, json={
            'id': 'x', 'object': 'chat.completion', 'created': 1, 'model': 'gpt-4o-mini',
            'choices': [{'index': 0, 'finish_reason': 'stop',
                         'message': {'role': 'assistant', 'content': "say('ok')"}}]})

    client._client = client._client.with_options(
        http_client=httpx2.Client(transport=httpx2.MockTransport(handler)), max_retries=0)
    assert client.complete('# test.', stop=['#']) == "say('ok')"
    assert 'br' not in seen['accept-encoding']
