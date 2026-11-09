"""
Loaders for the shared specification files (no ROS dependency).

* ``robot_api.yaml``          -> :class:`RobotApiSpec`  (API + policy grammar)
* ``object_classes.yaml``     -> :class:`ObjectCatalog` (Korean aliases -> canonical names)
* ``search_locations_*.yaml`` -> :class:`SearchEnvironment` (location names only)
"""

from dataclasses import dataclass, field
import os
from typing import Dict, List, Optional, Tuple

import yaml


class SpecError(ValueError):
    """A specification file is missing or malformed."""


def _load_yaml(path):
    if not path or not os.path.isfile(path):
        raise SpecError(f'spec file not found: {path!r}')
    with open(path, 'r', encoding='utf-8') as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict):
        raise SpecError(f'spec file is not a mapping: {path}')
    return data


# --------------------------------------------------------------------------- Robot API


@dataclass(frozen=True)
class ApiParam:
    name: str
    type: str  # noqa: A003 (same key as robot_api.yaml)
    kind: str = 'value'   # object | location | text | value


@dataclass(frozen=True)
class ApiFunction:
    name: str
    module: str
    params: Tuple[ApiParam, ...]
    returns: str
    description: str

    @property
    def signature(self):
        args = ', '.join(f'{p.name}: {p.type}' for p in self.params)
        return f'{self.name}({args}) -> {self.returns}'


@dataclass
class RobotApiSpec:
    api_version: str
    grammar_version: str
    modules: List[str]
    functions: Dict[str, ApiFunction]
    allowed_builtins: List[str]
    statements: frozenset
    expressions: frozenset
    operators: Dict[str, frozenset]
    contexts: frozenset
    limits: Dict[str, int]
    source_path: Optional[str] = None

    @classmethod
    def load(cls, path):
        data = _load_yaml(path)
        try:
            functions = {}
            for name, entry in data['functions'].items():
                params = tuple(ApiParam(p['name'], p.get('type', 'any'), p.get('kind', 'value'))
                               for p in (entry.get('params') or []))
                functions[name] = ApiFunction(
                    name=name, module=entry['module'], params=params,
                    returns=str(entry.get('returns', 'None')),
                    description=' '.join(str(entry.get('description', '')).split()))
            grammar = data['grammar']
            spec = cls(
                api_version=str(data['api_version']),
                grammar_version=str(data['grammar_version']),
                modules=list(data['modules']),
                functions=functions,
                allowed_builtins=list(data.get('allowed_builtins') or []),
                statements=frozenset(grammar['statements']),
                expressions=frozenset(grammar['expressions']),
                operators={k: frozenset(v) for k, v in grammar['operators'].items()},
                contexts=frozenset(grammar.get('contexts') or []),
                limits={k: int(v) for k, v in grammar['limits'].items()},
                source_path=path,
            )
        except (KeyError, TypeError) as e:
            raise SpecError(f'malformed robot API spec {path}: {e!r}') from e
        unknown = {f.module for f in functions.values()} - set(spec.modules)
        if unknown:
            raise SpecError(f'functions use undeclared modules: {sorted(unknown)}')
        return spec

    @property
    def api_names(self):
        return frozenset(self.functions)

    @property
    def reserved_names(self):
        """Names a policy may not define or assign (API + built-ins)."""
        return frozenset(self.functions) | frozenset(self.allowed_builtins)

    @property
    def list_apis(self):
        """
        Zero-argument APIs returning a list, in spec order (``for_iterables`` rule).

        A policy ``for`` loop may iterate directly over a call to one of these. Derived
        from ``params``/``returns`` so the validator does not keep its own copy.
        """
        return tuple(f.name for f in self.functions.values()
                     if not f.params and f.returns.startswith('list'))


# --------------------------------------------------------------------------- objects


@dataclass(frozen=True)
class ObjectClass:
    name: str
    aliases_ko: Tuple[str, ...]
    aliases_en: Tuple[str, ...]
    detector_classes: Tuple[str, ...]
    verified: bool


@dataclass
class ObjectCatalog:
    version: str
    detector_name: str
    detector_verified: bool
    objects: Dict[str, ObjectClass]
    source_path: Optional[str] = None

    @classmethod
    def load(cls, path):
        data = _load_yaml(path)
        try:
            objects = {}
            for name, entry in data['objects'].items():
                objects[name] = ObjectClass(
                    name=name,
                    aliases_ko=tuple(entry.get('aliases_ko') or []),
                    aliases_en=tuple(entry.get('aliases_en') or []),
                    detector_classes=tuple(entry.get('detector_classes') or []),
                    verified=bool(entry.get('verified', False)))
            detector = data.get('detector') or {}
            return cls(version=str(data.get('catalog_version', '0')),
                       detector_name=str(detector.get('name', 'UNCONFIRMED')),
                       detector_verified=bool(detector.get('verified', False)),
                       objects=objects, source_path=path)
        except (KeyError, TypeError, AttributeError) as e:
            raise SpecError(f'malformed object catalog {path}: {e!r}') from e

    @property
    def names(self):
        return list(self.objects)

    def detector_classes_for(self, name):
        """Detector class candidates for a canonical name (KeyError if unknown)."""
        return list(self.objects[name].detector_classes)

    def resolve_from_text(self, text):
        """
        Canonical names whose aliases occur in ``text`` (longest alias wins on overlap).

        Used only as a consistency check on what the LLM generated; it does not replace
        the LLM's interpretation of the command.
        """
        lowered = text.lower()
        hits = []
        for obj in self.objects.values():
            for alias in sorted(obj.aliases_ko + obj.aliases_en + (obj.name,),
                                key=len, reverse=True):
                pos = lowered.find(alias.lower())
                if pos >= 0:
                    hits.append((pos, -len(alias), obj.name))
                    break
        hits.sort()
        found = []
        covered = []
        for pos, neg_len, name in hits:
            span = (pos, pos - neg_len)
            if any(s <= span[0] < e for s, e in covered):
                continue
            covered.append(span)
            if name not in found:
                found.append(name)
        return found


# --------------------------------------------------------------------------- locations


@dataclass
class SearchEnvironment:
    environment: str
    locations: List[str]
    descriptions: Dict[str, str] = field(default_factory=dict)
    source_path: Optional[str] = None

    @classmethod
    def load(cls, path):
        data = _load_yaml(path)
        names, descriptions = [], {}
        for entry in data.get('locations') or []:
            if isinstance(entry, str):
                name, desc = entry, ''
            else:
                name, desc = entry['name'], entry.get('description', '')
            if not name.isidentifier():
                raise SpecError(f'location name must be an identifier-like word: {name!r}')
            if name in descriptions:
                raise SpecError(f'duplicate location {name!r}')
            names.append(name)
            descriptions[name] = desc
        if not names:
            raise SpecError(f'no locations in {path}')
        return cls(environment=str(data.get('environment', 'unknown')), locations=names,
                   descriptions=descriptions, source_path=path)
