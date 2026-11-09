"""
PolicyBundle: everything the executor needs to run one generated policy.

The bundle carries Python SOURCE only. limo_llm never executes it. See
docs/interface_contract.md for the field-by-field contract with limo_executor.
"""

from dataclasses import asdict, dataclass, field
import json
import time
from typing import Dict, List, Optional

BUNDLE_FORMAT_VERSION = '0.1.0'


@dataclass
class HelperFunction:
    name: str
    source: str              # exactly one `def name(...)` in the policy grammar
    signature: str           # call signature that triggered generation (CaP f_sig)
    calls: List[str]         # other helpers it calls
    origin: str = 'fgen'     # 'fgen' (LMPFGen) or 'policy' (def written in the policy)


@dataclass
class PolicyBundle:
    request_id: str
    command: str
    policy_code: str                      # context lines + generated policy (CaP style)
    helpers: List[HelperFunction]         # dependency order: callees before callers
    target_objects: List[str]             # canonical names used as literals in the code
    api_version: str
    grammar_version: str
    provider: str = ''
    model: str = ''
    warnings: List[str] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    format_version: str = BUNDLE_FORMAT_VERSION

    @property
    def helper_sources(self) -> Dict[str, str]:
        return {h.name: h.source for h in self.helpers}

    def full_source(self):
        """Helpers first, then the policy: a single program in the policy grammar."""
        parts = [h.source.strip() for h in self.helpers] + [self.policy_code.strip()]
        return '\n\n'.join(parts) + '\n'

    def to_dict(self):
        return asdict(self)

    def to_json(self, indent=None):
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent)

    @classmethod
    def from_dict(cls, data):
        data = dict(data)
        data['helpers'] = [HelperFunction(**h) for h in data.get('helpers', [])]
        return cls(**data)

    @classmethod
    def from_json(cls, text):
        return cls.from_dict(json.loads(text))


@dataclass
class GenerationResult:
    success: bool
    bundle: Optional[PolicyBundle] = None
    error_code: str = ''        # LLM_ERROR | VALIDATION_ERROR | FGEN_ERROR | INVALID_REQUEST
    error_message: str = ''
    raw_completion: str = ''    # last policy completion, for debugging rejected code

    def to_dict(self):
        d = asdict(self)
        d['bundle'] = self.bundle.to_dict() if self.bundle else None
        return d

    def to_json(self, indent=None):
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent)
