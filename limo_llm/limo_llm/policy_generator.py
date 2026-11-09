"""
PolicyGenerator: wires the LMPs together (role of ``setup_LMP`` in the original).

``generate(command)`` -> :class:`GenerationResult` with a validated :class:`PolicyBundle`.
No ROS dependency; the ROS node is a thin wrapper around this class.
"""

import ast
import copy
from dataclasses import asdict, dataclass
import json
import os
from typing import Optional
import uuid

from limo_llm.llm_client import LLMError
from limo_llm.lmp import LMP
from limo_llm.lmp_fgen import FGenError, LMPFGen
from limo_llm.prompt_builder import PromptBuilder
from limo_llm.resources import default_package_dir
from limo_policy.bundle import HelperFunction, PolicyBundle
from limo_policy.call_graph import build_call_graph, called_names, topological_order
from limo_policy.paths import default_config_dir
from limo_policy.specs import RobotApiSpec, SearchEnvironment
from limo_policy.validator import PolicyValidationError, PolicyValidator


@dataclass
class GenerationResult:
    """Outcome of one generate() call (limo_llm only; not part of the executor contract)."""

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


# Searchable objects for the demo. The names are the detector's COCO class names, so no
# word -> class table is needed: the LLM maps the user's Korean words to one of these, and
# the validator rejects any other object name. Overridden by the ROS parameter `objects`.
DEFAULT_OBJECTS = ('handbag', 'umbrella', 'cup', 'cell phone', 'book')


def check_objects(objects):
    """Return ``objects`` as a list of unique, non-empty names (ValueError otherwise)."""
    names = [str(o).strip() for o in (objects or [])]
    if not names or not all(names) or len(set(names)) != len(names):
        raise ValueError(f'objects must be unique non-empty names, got {objects!r}')
    return names


# Same values as cfg_tabletop in the original notebook, except the extra stop token for
# the `locations = [` context line and the limits.
DEFAULT_CFG = {
    'policy': {
        'max_tokens': 512,
        'temperature': 0.0,
        'query_prefix': '# ',
        'query_suffix': '.',
        'stop': ['#', 'objects = [', 'locations = ['],
        'maintain_session': False,
        'include_context': True,
    },
    'fgen': {
        'max_tokens': 512,
        'temperature': 0.0,
        'query_prefix': '# define function: ',
        'query_suffix': '.',
        'stop': ['# define', '# example'],
        'max_depth': None,        # None -> robot_api.yaml grammar.limits.max_fgen_depth
        'max_functions': None,    # None -> robot_api.yaml grammar.limits.max_helper_functions
    },
    'max_attempts': 2,            # 1 = no regeneration after a rejected policy
    'max_command_chars': 300,
}


def merge_cfg(base, override):
    out = copy.deepcopy(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = merge_cfg(out[k], v)
        elif v is not None:
            out[k] = v
    return out


def split_inline_defs(code):
    """
    Move module-level ``def`` statements out of the policy code.

    Returns ``(policy_code_without_defs, {name: source})``. Module-level defs have no side
    effects, so running them before the policy (as helpers) keeps the semantics.
    """
    tree = ast.parse(code)
    lines = code.splitlines()
    defs, drop = {}, set()
    for node in tree.body:
        if isinstance(node, ast.FunctionDef):
            defs[node.name] = ast.get_source_segment(code, node)
            drop.update(range(node.lineno - 1, node.end_lineno))
    kept = [ln for i, ln in enumerate(lines) if i not in drop]
    return '\n'.join(kept).strip('\n'), defs


class PolicyGenerator:

    def __init__(self, spec, objects, env, prompts_dir, llm, cfg=None, log=None):
        self.spec = spec
        self.objects = check_objects(objects)
        self.env = env
        self.llm = llm
        self.cfg = merge_cfg(DEFAULT_CFG, cfg)
        fcfg = self.cfg['fgen']
        if fcfg['max_depth'] is None:
            fcfg['max_depth'] = spec.limits.get('max_fgen_depth', 3)
        if fcfg['max_functions'] is None:
            fcfg['max_functions'] = spec.limits.get('max_helper_functions', 8)
        self._log = log or (lambda header, body='': None)
        self.builder = PromptBuilder(spec, self.objects, env, prompts_dir)
        self.validator = PolicyValidator(spec, self.objects, env.locations)
        self.lmp_fgen = LMPFGen(fcfg, self.builder.fgen_base_prompt(), llm, self.validator,
                                log=self._log)
        self.lmp_policy = LMP('policy_ui', self.cfg['policy'], self.builder.policy_base_prompt(),
                              self.lmp_fgen, llm, self.validator, log=self._log)

    @classmethod
    def from_files(cls, robot_api_file, search_locations_file, prompts_dir, llm,
                   objects=DEFAULT_OBJECTS, cfg=None, log=None):
        return cls(RobotApiSpec.load(robot_api_file), objects,
                   SearchEnvironment.load(search_locations_file), prompts_dir, llm, cfg, log)

    # ------------------------------------------------------------------ public

    def generate(self, command, request_id=None):
        request_id = request_id or uuid.uuid4().hex[:12]
        command = ' '.join((command or '').split())   # one line: it becomes a # comment
        if not command:
            return GenerationResult(False, error_code='INVALID_REQUEST',
                                    error_message='empty command')
        if len(command) > self.cfg['max_command_chars']:
            return GenerationResult(False, error_code='INVALID_REQUEST',
                                    error_message='command too long')

        context = self.builder.context()
        extra, last_code, last_msg, raw = '', '', '', ''
        for attempt in range(1, int(self.cfg['max_attempts']) + 1):
            self.lmp_fgen.reset()
            try:
                to_exec, new_fs, raw = self.lmp_policy(command, context=context, extra=extra)
                bundle = self._make_bundle(request_id, command, to_exec, new_fs)
                return GenerationResult(True, bundle=bundle, raw_completion=raw)
            except LLMError as e:
                return GenerationResult(False, error_code='LLM_ERROR', error_message=str(e),
                                        raw_completion=raw)
            except PolicyValidationError as e:
                last_code, last_msg = 'VALIDATION_ERROR', str(e)
            except FGenError as e:
                last_code, last_msg = 'FGEN_ERROR', str(e)
            raw = getattr(self.lmp_policy, 'last_completion', raw)
            self._log(f'attempt {attempt} rejected: {last_msg}')
            # Retry feedback (not in the original CaP): with temperature 0 the same prompt
            # would give the same code, so the rejection reason is added as a comment.
            extra = ('# (previous code for this command was rejected: '
                     + last_msg.replace('\n', ' ')[:300]
                     + '. Follow the rules and the examples.)')
        return GenerationResult(False, error_code=last_code, error_message=last_msg,
                                raw_completion=raw)

    # ------------------------------------------------------------------ internal

    def _make_bundle(self, request_id, command, to_exec, new_fs):
        policy_code, inline = split_inline_defs(to_exec)
        clash = set(inline) & set(new_fs)
        if clash:
            raise FGenError('NAME_COLLISION', f'helpers defined twice: {sorted(clash)}')
        sources = dict(inline)
        sources.update(new_fs)
        # Validates grammar, resolution, arity, recursion and limits of the whole bundle.
        self.validator.validate_bundle(policy_code, sources)

        graph = build_call_graph(sources)
        order = topological_order(graph)
        helpers = [HelperFunction(name=n, source=sources[n],
                                  signature=self.lmp_fgen.signatures.get(n, ''),
                                  calls=sorted(graph[n]),
                                  origin='policy' if n in inline else 'fgen')
                   for n in order]
        # Unused helpers are kept out of the bundle.
        used = self._reachable(policy_code, graph)
        helpers = [h for h in helpers if h.name in used]

        full = '\n\n'.join([h.source for h in helpers] + [policy_code])
        targets = self.validator.object_targets(full)
        bundle = PolicyBundle(
            request_id=request_id, command=command, policy_code=policy_code,
            helpers=helpers, target_objects=targets,
            api_version=self.spec.api_version, grammar_version=self.spec.grammar_version,
            provider=getattr(self.llm, 'provider', ''), model=getattr(self.llm, 'model', ''),
            warnings=list(self.lmp_fgen.warnings))
        return bundle

    @staticmethod
    def _reachable(policy_code, graph):
        todo = list(called_names(policy_code) & set(graph))
        seen = set()
        while todo:
            n = todo.pop()
            if n not in seen:
                seen.add(n)
                todo.extend(graph[n] - seen)
        return seen


def default_paths(config_dir=None):
    """
    Return the default spec/prompt paths.

    Shared specs come from ``limo_policy.paths.default_config_dir`` (explicit dir, then
    $LIMO_CONFIG_DIR, then the installed limo_policy share, then the repository config/).
    """
    common = default_config_dir(config_dir)
    return {
        'robot_api_file': os.path.join(common, 'robot_api.yaml'),
        'search_locations_file': os.path.join(common, 'search_locations_sim.yaml'),
        'prompts_dir': default_package_dir('prompts'),
    }
