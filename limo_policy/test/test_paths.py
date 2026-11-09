"""Config directory search order: explicit > $LIMO_CONFIG_DIR > installed > ament > source."""

import os
import shutil

from conftest import COMMON
from limo_policy import paths
from limo_policy.specs import SpecError
import pytest


@pytest.fixture
def clean_env(monkeypatch):
    monkeypatch.delenv(paths.ENV_VAR, raising=False)
    monkeypatch.delenv('AMENT_PREFIX_PATH', raising=False)
    return monkeypatch


def make_config(tmp_path, name):
    d = tmp_path / name
    d.mkdir(parents=True)
    shutil.copy(os.path.join(COMMON, 'robot_api.yaml'), d / 'robot_api.yaml')
    return str(d)


def test_explicit_dir_wins(tmp_path, clean_env):
    explicit = make_config(tmp_path, 'explicit')
    clean_env.setenv(paths.ENV_VAR, make_config(tmp_path, 'env'))
    assert paths.default_config_dir(explicit) == explicit


def test_env_var_before_installed_and_source(tmp_path, clean_env):
    env_dir = make_config(tmp_path, 'env')
    clean_env.setenv(paths.ENV_VAR, env_dir)
    assert paths.default_config_dir() == env_dir
    expected = os.path.join(env_dir, 'object_classes.yaml')
    assert paths.config_file('object_classes.yaml') == expected


def test_wrong_explicit_or_env_dir_does_not_fall_back(tmp_path, clean_env):
    with pytest.raises(SpecError):
        paths.default_config_dir(str(tmp_path / 'missing'))
    clean_env.setenv(paths.ENV_VAR, str(tmp_path / 'missing'))
    with pytest.raises(SpecError):
        paths.default_config_dir()


def test_ament_prefix_path_share(tmp_path, clean_env):
    prefix = tmp_path / 'install' / 'limo_policy'
    make_config(prefix, os.path.join('share', 'limo_policy', 'config'))
    clean_env.setenv('AMENT_PREFIX_PATH', f'/nonexistent{os.pathsep}{prefix}')
    found = paths.default_config_dir()
    if os.path.isfile(os.path.join(paths._installed_share(), paths.MARKER)):
        pytest.skip('running from an installed limo_policy: installed share wins')
    assert found == str(prefix / 'share' / 'limo_policy' / 'config')


def test_source_tree_fallback(clean_env):
    if os.path.isfile(os.path.join(paths._installed_share(), paths.MARKER)):
        pytest.skip('running from an installed limo_policy: installed share wins')
    assert os.path.realpath(paths.default_config_dir()) == os.path.realpath(COMMON)


def test_candidate_order(tmp_path, clean_env):
    clean_env.setenv(paths.ENV_VAR, '/env')
    clean_env.setenv('AMENT_PREFIX_PATH', '/a')
    sources = [s for s, _ in paths.candidate_config_dirs('/x')]
    assert sources == ['explicit', paths.ENV_VAR, 'installed', 'AMENT_PREFIX_PATH', 'source']
