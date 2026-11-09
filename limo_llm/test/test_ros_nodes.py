"""
ROS 2 integration: llm_node (mock LLM) -> debug topic -> mock_executor -> result.

Needs a sourced ROS 2 Humble environment; skipped otherwise. No API key, no robot.
"""

import json
import os
import threading
import time

from conftest import PATHS, PKG
import pytest

rclpy = pytest.importorskip('rclpy')


def test_command_to_mock_execution():
    from limo_llm.llm_node import LlmNode
    from limo_llm.mock.mock_executor_node import MockExecutorNode
    from rclpy.executors import MultiThreadedExecutor
    from rclpy.node import Node
    from std_msgs.msg import Bool, String

    params = {
        'provider': 'mock',
        'mock_responses_file': os.path.join(PKG, 'config', 'mock_llm_responses.yaml'),
        'scenario_file': os.path.join(PKG, 'config', 'mock_scenario.yaml'),
        **PATHS,
    }
    args = ['--ros-args']
    for k, v in params.items():
        args += ['-p', f'{k}:={v}']
    rclpy.init(args=args)
    executor = MultiThreadedExecutor()
    nodes = []
    try:
        llm, mock_exec, probe = LlmNode(), MockExecutorNode(), Node('probe')
        nodes = [llm, mock_exec, probe]
        results, bundles, ready = [], [], []
        probe.create_subscription(String, '/mock_executor/result',
                                  lambda m: results.append(json.loads(m.data)), 10)
        probe.create_subscription(String, '/limo_llm/policy_bundle',
                                  lambda m: bundles.append(json.loads(m.data)), 10)
        probe.create_subscription(Bool, 'ready_for_input', lambda m: ready.append(m.data), 10)
        pub = probe.create_publisher(String, 'user_command', 10)
        for n in nodes:
            executor.add_node(n)
        threading.Thread(target=executor.spin, daemon=True).start()

        deadline = time.time() + 10.0
        while pub.get_subscription_count() == 0 and time.time() < deadline:
            time.sleep(0.05)
        pub.publish(String(data='우산 찾아줘.'))
        while not results and time.time() < deadline:
            time.sleep(0.05)

        assert bundles and bundles[0]['success'], bundles
        b = bundles[0]['bundle']
        assert b['target_objects'] == ['umbrella']
        assert [h['name'] for h in b['helpers']] == ['check_obj_here', 'find_obj_in_locs']
        assert results, 'no result from mock executor'
        r = results[0]
        assert r['request_id'] == b['request_id']
        assert r['outcome'] == 'FOUND' and r['found'] == [['umbrella', 'sofa']]
        assert r['failed_locations'] == ['armchair']      # unreachable in the scenario
        assert r['led_backend'] == 'mock'
        assert True in ready
    finally:
        executor.shutdown()
        for n in nodes:
            n.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()
