"""LLM providers for the LMPs (no ROS dependency)."""

import os
import re
from typing import Callable, List, Optional, Sequence, Tuple

import yaml


class LLMError(RuntimeError):
    """The LLM call failed or returned nothing usable."""


_FENCE_RE = re.compile(r'^\s*```')


# Return the last non-empty line of a prompt (the ``# <query>.`` line).
def last_query_line(prompt):
    for line in reversed(prompt.splitlines()):
        if line.strip():
            return line.strip()
    return ''


def clean_completion(text, stop: Optional[Sequence[str]] = None):
    lines = [ln for ln in (text or '').splitlines() if not _FENCE_RE.match(ln)]
    out = []
    for ln in lines:
        stripped = ln.lstrip()
        if stop and out and any(stripped.startswith(s) for s in stop):
            break
        if stop and not out and any(stripped.startswith(s) for s in stop):
            continue  # leading echo of the query comment / context line
        out.append(ln.rstrip())
    while out and not out[0].strip():
        out.pop(0)
    return '\n'.join(out).rstrip()


class BaseLLMClient:
    provider = 'base'

    def __init__(self, model=''):
        self.model = model

    def complete(self, prompt, stop=None, temperature=0.0, max_tokens=512):
        raise NotImplementedError


class OpenAIChatClient(BaseLLMClient):
    provider = 'openai'

    def __init__(self, model, api_key=None, system_prompt='', timeout=30.0, max_retries=2,
                 token_param='max_tokens'):
        super().__init__(model)
        api_key = api_key or os.environ.get('OPENAI_API_KEY')
        if not api_key:
            raise LLMError('no OpenAI API key: set OPENAI_API_KEY or the openai_api_key '
                           'parameter, or use provider:=mock')
        try:
            from openai import OpenAI
        except ImportError as e:
            raise LLMError('the "openai" Python package is not installed '
                           '(pip install openai)') from e
        self._client = OpenAI(api_key=api_key, timeout=timeout, max_retries=max_retries,
                              default_headers={'Accept-Encoding': 'gzip, deflate'})
        self._system_prompt = system_prompt
        self._token_param = token_param

    def complete(self, prompt, stop=None, temperature=0.0, max_tokens=512):
        messages = []
        if self._system_prompt:
            messages.append({'role': 'system', 'content': self._system_prompt})
        messages.append({'role': 'user', 'content': prompt})
        kwargs = {'model': self.model, 'messages': messages, 'temperature': temperature,
                  self._token_param: max_tokens}
        if stop:
            kwargs['stop'] = list(stop)[:4]  # API limit: 4 stop sequences
        try:
            resp = self._client.chat.completions.create(**kwargs)
        except Exception as e:
            raise LLMError(f'OpenAI request failed: {e!r}') from e
        content = resp.choices[0].message.content if resp.choices else None
        if not content:
            raise LLMError('OpenAI returned an empty completion')
        return content


class MockLLMClient(BaseLLMClient):
    provider = 'mock'

    def __init__(self, responses: Optional[List[Tuple[str, str]]] = None,
                 fallback: Optional[Callable[[str, str], str]] = None, model='mock'):
        super().__init__(model)
        self.responses = list(responses or [])
        self.fallback = fallback
        self.calls = []

    @classmethod
    def from_yaml(cls, path):
        with open(path, 'r', encoding='utf-8') as f:
            data = yaml.safe_load(f) or {}
        return cls([(r['match'], r['completion']) for r in data.get('responses') or []])

    def complete(self, prompt, stop=None, temperature=0.0, max_tokens=512):
        query = last_query_line(prompt)
        self.calls.append({'prompt': prompt, 'query': query, 'stop': stop})
        for pattern, completion in self.responses:
            if pattern in query:
                return completion
        if self.fallback is not None:
            return self.fallback(prompt, query)
        raise LLMError(f'mock LLM has no scripted completion for query {query!r}')


def create_llm_client(provider, *, model='', api_key=None, system_prompt='',
                      mock_responses_file='', timeout=30.0, max_retries=2,
                      token_param='max_tokens'):
    provider = (provider or '').lower()
    if provider == 'openai':
        return OpenAIChatClient(model, api_key=api_key, system_prompt=system_prompt,
                                timeout=timeout, max_retries=max_retries,
                                token_param=token_param)
    if provider == 'mock':
        if mock_responses_file:
            return MockLLMClient.from_yaml(mock_responses_file)
        return MockLLMClient()
    raise LLMError(f'unknown LLM provider {provider!r} (expected "openai" or "mock")')
