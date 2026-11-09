from glob import glob
import os

from setuptools import find_packages, setup

package_name = 'limo_llm'
# Shared specs live in the repository-level config/ directory (single source of truth for
# limo_llm, limo_executor and limo_perception); they are installed with this package.
# data_files must be relative to the package directory (setup.py is run from there).
# Pose files (search_poses_*.yaml) are for limo_executor only and are NOT installed here, so
# coordinates never reach the LLM side.
common_config = sorted(p for p in glob(os.path.join('..', 'config', '*.yaml'))
                       if not os.path.basename(p).startswith('search_poses_'))

setup(
    name=package_name,
    version='0.1.0',
    packages=find_packages(exclude=['test']),
    data_files=[
        ('share/ament_index/resource_index/packages',
            ['resource/' + package_name]),
        ('share/' + package_name, ['package.xml', 'README.md']),
        (os.path.join('share', package_name, 'launch'), glob('launch/*.launch.py')),
        (os.path.join('share', package_name, 'prompts'), glob('prompts/*.txt')),
        (os.path.join('share', package_name, 'config'), glob('config/*.yaml')),
        (os.path.join('share', package_name, 'config', 'common'), common_config),
    ],
    install_requires=['setuptools', 'PyYAML'],
    zip_safe=True,
    maintainer='judynoh',
    maintainer_email='nys827827@gmail.com',
    description='Code as Policies (LMP + hierarchical code generation) policy generator '
                'for the LIMO lost-and-found search robot',
    license='Apache-2.0',
    extras_require={
        'test': [
            'pytest',
        ],
    },
    entry_points={
        'console_scripts': [
            'llm_node = limo_llm.llm_node:main',
            'mock_executor = limo_llm.mock.mock_executor_node:main',
        ],
    },
)
