"""
Locate limo_llm's own resources (prompts/, config/).

Moved from the former limo_llm/specs.py. The shared specs (robot_api.yaml, ...) are found
by ``limo_policy.paths`` instead.
"""

import os

PACKAGE_NAME = 'limo_llm'


def _source_root():
    """Directory of the limo_llm package source tree (…/limo_llm)."""
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _share_dir():
    try:
        from ament_index_python.packages import get_package_share_directory
        return get_package_share_directory(PACKAGE_NAME)
    except Exception:  # not installed / no ament (plain pytest from source)
        return None


def default_package_dir(subdir):
    """Package-owned resource directory: 'prompts' or 'config'."""
    share = _share_dir()
    if share and os.path.isdir(os.path.join(share, subdir)):
        return os.path.join(share, subdir)
    return os.path.join(_source_root(), subdir)
