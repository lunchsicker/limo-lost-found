import os

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def generate_launch_description():
    cfg = os.path.join(get_package_share_directory('limo_perception'),
                       'config', 'yolo_params.yaml')
    return LaunchDescription([
        DeclareLaunchArgument('backend', default_value='world'),
        Node(
            package='limo_perception',
            executable='yolo_node',
            name='yolo_node',
            output='screen',
            parameters=[cfg, {'backend': LaunchConfiguration('backend')}],
        ),
    ])