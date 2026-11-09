"""
Launch the limo_llm policy generator (optionally with the DEBUG mock executor).

Usage:
  # offline, no API key, no robot: mock LLM + mock executor
  ros2 launch limo_llm llm.launch.py provider:=mock mock_executor:=true
  # real LLM, policies only published for inspection / mock executor
  ros2 launch limo_llm llm.launch.py provider:=openai mock_executor:=true
"""

import os

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.conditions import IfCondition
from launch.substitutions import LaunchConfiguration, PythonExpression
from launch_ros.actions import Node
import yaml


def generate_launch_description():
    share = get_package_share_directory('limo_llm')
    params_file = os.path.join(share, 'config', 'llm_params.yaml')
    with open(params_file, 'r', encoding='utf-8') as f:   # one `objects` list for both nodes
        objects = yaml.safe_load(f)['limo_llm']['ros__parameters']['objects']

    provider = LaunchConfiguration('provider')
    mock_file = PythonExpression([
        "'", LaunchConfiguration('mock_responses_file'), "' if '", provider,
        "' == 'mock' else ''"])

    return LaunchDescription([
        DeclareLaunchArgument('provider', default_value='openai',
                              choices=['openai', 'mock']),
        DeclareLaunchArgument('model_name', default_value='gpt-4o-mini'),
        DeclareLaunchArgument('output_mode', default_value='debug_topic',
                              choices=['debug_topic', 'action', 'none']),
        DeclareLaunchArgument('search_locations_file', default_value='',
                              description='Empty: search_locations_sim.yaml found by '
                                          'limo_policy.paths ($LIMO_CONFIG_DIR first)'),
        DeclareLaunchArgument('mock_responses_file',
                              default_value=os.path.join(share, 'config',
                                                         'mock_llm_responses.yaml')),
        DeclareLaunchArgument('mock_executor', default_value='false',
                              description='Also start the DEBUG mock executor'),
        DeclareLaunchArgument('scenario_file',
                              default_value=os.path.join(share, 'config', 'mock_scenario.yaml')),
        Node(
            package='limo_llm', executable='llm_node', name='limo_llm', output='screen',
            emulate_tty=True,
            parameters=[params_file, {
                'provider': provider,
                'model_name': LaunchConfiguration('model_name'),
                'output_mode': LaunchConfiguration('output_mode'),
                'search_locations_file': LaunchConfiguration('search_locations_file'),
                'mock_responses_file': mock_file,
            }],
        ),
        Node(
            package='limo_llm', executable='mock_executor', name='mock_executor',
            output='screen', emulate_tty=True,
            condition=IfCondition(LaunchConfiguration('mock_executor')),
            parameters=[{
                'objects': objects,
                'search_locations_file': LaunchConfiguration('search_locations_file'),
                'scenario_file': LaunchConfiguration('scenario_file'),
            }],
        ),
    ])
