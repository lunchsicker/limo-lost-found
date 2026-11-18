from launch import LaunchDescription
from launch_ros.actions import Node


def generate_launch_description():
    yolo = Node(
        package='limo_perception',
        executable='yolo_node',
        name='yolo_node',
        output='screen',
        parameters=[{
            'use_sim_time': True,
            'conf': 0.25,
            'max_range' : 3.0,
            'merge_dist' : 0.8
        }],
    )

    republish = Node(
        package='image_transport',
        executable='republish',
        name='yolo_republish',
        arguments=['raw', 'compressed'],
        remappings=[
            ('in', '/yolo/image_annotated'),
            ('out/compressed', '/yolo/image_annotated/compressed'),
        ],
        parameters=[{'use_sim_time': True}],
    )

    return LaunchDescription([yolo, republish])
