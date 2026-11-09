#!/usr/bin/env python3
"""
DEBUG ONLY: mock executor that runs bundles from /limo_llm/policy_bundle on a MockRobot.

Not the robot's execution interface (that is the ExecutePolicy action of limo_executor).
Nothing moves and no detector runs; outcomes come from the scenario file.

Subscriptions  /limo_llm/policy_bundle (String, GenerationResult JSON), /limo_llm/cancel
Publications   ~/result (String, JSON), ~/say (String)
"""

from dataclasses import asdict
import json
import threading

from limo_llm.mock.mock_robot import MockRobot, MockScenario, run_bundle
from limo_llm.policy_generator import check_objects, DEFAULT_OBJECTS, default_paths
from limo_llm.resources import default_package_dir
from limo_policy.bundle import PolicyBundle
from limo_policy.specs import RobotApiSpec, SearchEnvironment
import rclpy
from rclpy.node import Node
from std_msgs.msg import String


class MockExecutorNode(Node):

    def __init__(self):
        super().__init__('mock_executor')
        d = default_paths()
        self.declare_parameter('robot_api_file', d['robot_api_file'])
        self.declare_parameter('objects', list(DEFAULT_OBJECTS))
        self.declare_parameter('search_locations_file', d['search_locations_file'])
        self.declare_parameter('scenario_file',
                               default_package_dir('config') + '/mock_scenario.yaml')
        self.declare_parameter('bundle_topic', '/limo_llm/policy_bundle')
        self.declare_parameter('cancel_topic', '/limo_llm/cancel')
        g = self.get_parameter
        self.spec = RobotApiSpec.load(g('robot_api_file').value or d['robot_api_file'])
        self.objects = check_objects(g('objects').value)
        self.env = SearchEnvironment.load(g('search_locations_file').value
                                          or d['search_locations_file'])
        self.scenario_file = g('scenario_file').value
        MockScenario.from_yaml(self.scenario_file)   # fail early on a bad file
        self.pub_result = self.create_publisher(String, '~/result', 10)
        self.create_subscription(String, g('bundle_topic').value, self.on_bundle, 10)
        self.create_subscription(String, g('cancel_topic').value, self.on_cancel, 10)
        self.robot = None
        self.get_logger().warn('MOCK executor: no robot motion, no real detection '
                               f'(scenario {self.scenario_file})')

    def on_cancel(self, _msg):
        if self.robot is not None:
            self.robot.cancel_requested = True
            self.get_logger().warn('cancel requested')

    def on_bundle(self, msg):
        data = json.loads(msg.data)
        if not data.get('success') or not data.get('bundle'):
            self.get_logger().info(f'no policy to run: {data.get("error_code")}')
            return
        bundle = PolicyBundle.from_dict(data['bundle'])
        threading.Thread(target=self._run, args=(bundle,), daemon=True).start()

    def _run(self, bundle):
        self.robot = MockRobot(MockScenario.from_yaml(self.scenario_file))
        res = run_bundle(bundle, self.robot, self.spec, self.objects, self.env.locations)
        for m in res.messages:
            self.get_logger().info(f'robot says: {m}')
        self.get_logger().info(f'[{bundle.request_id}] {res.outcome}: {res.message} '
                               f'visited={res.visited_locations} failed={res.failed_locations}')
        out = asdict(res)
        out['request_id'] = bundle.request_id
        self.pub_result.publish(String(data=json.dumps(out, ensure_ascii=False)))


def main(args=None):
    rclpy.init(args=args)
    node = None
    try:
        node = MockExecutorNode()
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        if node is not None:
            node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == '__main__':
    main()
