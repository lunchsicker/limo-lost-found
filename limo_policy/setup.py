from glob import glob
import os

from setuptools import find_packages, setup

package_name = 'limo_policy'
# Shared specs: the originals stay in the repository-level config/ directory; this package
# installs them to share/limo_policy/config. Pose files (search_poses_*.yaml) belong to
# limo_executor and are NOT installed here. data_files must be relative (setup.py runs
# from the package directory).
SHARED_SPECS = ['robot_api.yaml', 'object_classes.yaml']
config_dir = os.path.join('..', 'config')
shared_config = [os.path.join(config_dir, f) for f in SHARED_SPECS]
shared_config += sorted(glob(os.path.join(config_dir, 'search_locations_*.yaml')))

setup(
    name=package_name,
    version='0.1.0',
    packages=find_packages(exclude=['test']),
    data_files=[
        ('share/ament_index/resource_index/packages', ['resource/' + package_name]),
        ('share/' + package_name, ['package.xml']),
        (os.path.join('share', package_name, 'config'), shared_config),
    ],
    install_requires=['setuptools', 'PyYAML'],
    zip_safe=True,
    maintainer='judynoh',
    maintainer_email='nys827827@gmail.com',
    description='Shared policy core (specs, validator, interpreter, PolicyBundle) for the '
                'LIMO lost-and-found robot; no ROS runtime dependency',
    license='Apache-2.0',
    extras_require={'test': ['pytest']},
    entry_points={'console_scripts': []},
)
