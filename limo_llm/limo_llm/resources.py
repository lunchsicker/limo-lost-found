"""Locate limo_llm's own resources (prompts/, config/)."""

import os

PACKAGE_NAME = 'limo_llm'


def _source_root():
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _share_dir():
    try:
        from ament_index_python.packages import get_package_share_directory
        return get_package_share_directory(PACKAGE_NAME)
    except Exception:
        return None


def default_package_dir(subdir):
    share = _share_dir()
    if share and os.path.isdir(os.path.join(share, subdir)):
        return os.path.join(share, subdir)
    return os.path.join(_source_root(), subdir)
