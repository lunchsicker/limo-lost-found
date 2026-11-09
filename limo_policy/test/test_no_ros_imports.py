"""limo_policy must stay free of ROS runtime imports."""

import ast
import os
import subprocess
import sys

PKG_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       'limo_policy')
FORBIDDEN_PREFIXES = ('rclpy', 'rcl', 'ament', 'launch', 'rosidl', 'std_msgs', 'limo_llm',
                      'limo_interfaces', 'openai')
ALLOWED = {'ast', 'builtins', 'dataclasses', 'json', 'os', 'time', 'typing', 'yaml'}


def imported_modules(path):
    tree = ast.parse(open(path, encoding='utf-8').read())
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            yield from (a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            yield node.module


def test_static_imports_are_stdlib_yaml_or_own():
    for name in sorted(os.listdir(PKG_DIR)):
        if not name.endswith('.py'):
            continue
        for mod in imported_modules(os.path.join(PKG_DIR, name)):
            top = mod.split('.')[0]
            assert not top.startswith(FORBIDDEN_PREFIXES) and \
                (top in ALLOWED or top == 'limo_policy'), f'{name} imports {mod}'


def test_imports_without_ros_on_path():
    """Import every module in a clean interpreter with no ROS paths."""
    code = ('import sys; sys.path = [p for p in sys.path if "ros" not in p]; '
            'import limo_policy.specs, limo_policy.paths, limo_policy.validator, '
            'limo_policy.interpreter, limo_policy.bundle, limo_policy.errors, '
            'limo_policy.call_graph; '
            'assert not any(m.split(".")[0] in ("rclpy", "ament_index_python") '
            'for m in sys.modules)')
    env = {k: v for k, v in os.environ.items()
           if k not in ('PYTHONPATH', 'AMENT_PREFIX_PATH')}
    env['PYTHONPATH'] = os.path.dirname(PKG_DIR)
    subprocess.run([sys.executable, '-c', code], check=True, env=env)
