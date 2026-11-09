"""
Locate the shared specification directory without any ROS import.

Search order (first directory that contains ``robot_api.yaml`` wins):

1. an explicitly passed directory
2. the ``LIMO_CONFIG_DIR`` environment variable
3. ``<prefix>/share/limo_policy/config`` next to this installed module (normal build)
4. ``<p>/share/limo_policy/config`` for every ``p`` in ``AMENT_PREFIX_PATH``
   (covers ``colcon build --symlink-install``, where the module lives in build/)
5. the repository ``config/`` directory next to the limo_policy source tree

Only environment variables and file paths are used, so limo_policy keeps no ROS runtime
dependency. Explicit file parameters of the nodes still override all of this.
"""

import os

from limo_policy.specs import SpecError

ENV_VAR = 'LIMO_CONFIG_DIR'
PACKAGE = 'limo_policy'
MARKER = 'robot_api.yaml'


def _installed_share():
    # <prefix>/lib/pythonX.Y/site-packages/limo_policy/paths.py -> <prefix>
    here = os.path.dirname(os.path.abspath(__file__))
    prefix = os.path.abspath(os.path.join(here, '..', '..', '..', '..'))
    return os.path.join(prefix, 'share', PACKAGE, 'config')


def _ament_prefix_shares():
    for prefix in os.environ.get('AMENT_PREFIX_PATH', '').split(os.pathsep):
        if prefix:
            yield os.path.join(prefix, 'share', PACKAGE, 'config')


def _source_config():
    # <repo>/limo_policy/limo_policy/paths.py -> <repo>/config (follows symlinks)
    here = os.path.dirname(os.path.realpath(__file__))
    return os.path.abspath(os.path.join(here, '..', '..', 'config'))


def candidate_config_dirs(explicit=None):
    """Return the directories searched, in order, with the source of each."""
    out = []
    if explicit:
        out.append(('explicit', explicit))
    if os.environ.get(ENV_VAR):
        out.append((ENV_VAR, os.environ[ENV_VAR]))
    out.append(('installed', _installed_share()))
    out += [('AMENT_PREFIX_PATH', d) for d in _ament_prefix_shares()]
    out.append(('source', _source_config()))
    return out


def default_config_dir(explicit=None):
    """Return the first candidate directory that contains robot_api.yaml."""
    tried = candidate_config_dirs(explicit)
    for source, path in tried:
        if os.path.isfile(os.path.join(path, MARKER)):
            return path
        if source in ('explicit', ENV_VAR):
            # An explicit choice that is wrong must not silently fall back.
            raise SpecError(f'{source} config directory has no {MARKER}: {path!r}')
    raise SpecError(f'no config directory with {MARKER}; tried '
                    + ', '.join(f'{s}={p}' for s, p in tried))


def config_file(name, config_dir=None):
    """Return the path of a shared spec file such as ``object_classes.yaml``."""
    return os.path.join(default_config_dir(config_dir), name)
