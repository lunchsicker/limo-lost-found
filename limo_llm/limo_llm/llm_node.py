#!/usr/bin/env python3
"""Korean command -> Code as Policies PolicyBundle -> executor."""

import threading

from limo_llm.llm_client import create_llm_client, LLMError
from limo_llm.policy_generator import DEFAULT_OBJECTS, default_paths, PolicyGenerator
from limo_llm.prompt_builder import read_text
from limo_policy.specs import SpecError
import rclpy
from rclpy.node import Node
from rclpy.qos import DurabilityPolicy, QoSProfile
from std_msgs.msg import Bool, String

STOP_WORDS = {'stop', 'cancel', '멈춰', '정지', '그만', '취소', '스톱', '멈춰줘', '그만해'}
OUTPUT_MODES = ('debug_topic', 'action', 'none')


class LlmNode(Node):

    def __init__(self):
        super().__init__('limo_llm')
        defaults = default_paths()
        p = {
            'provider': 'openai',
            'model_name': 'gpt-4o-mini',
            'openai_api_key': '',
            'temperature': 0.0,
            'max_tokens': 512,
            'request_timeout': 30.0,
            'max_attempts': 2,
            'max_fgen_depth': 0,          # 0 -> robot_api.yaml limit
            'max_helper_functions': 0,    # 0 -> robot_api.yaml limit
            'maintain_session': False,
            'robot_api_file': defaults['robot_api_file'],
            'objects': list(DEFAULT_OBJECTS),    # detector (COCO) class names
            'search_locations_file': defaults['search_locations_file'],
            'prompts_dir': defaults['prompts_dir'],
            'mock_responses_file': '',
            'output_mode': 'debug_topic',
            'execute_policy_action': '/limo_executor/execute_policy',
        }
        for name, value in p.items():
            self.declare_parameter(name, value)
        g = self.get_parameter

        mode = g('output_mode').value
        if mode not in OUTPUT_MODES:
            raise ValueError(f'output_mode must be one of {OUTPUT_MODES}, got {mode!r}')
        self.output_mode = mode

        # Empty path parameters fall back to the defaults (limo_policy.paths / share dir).
        spec_path = {k: g(k).value or defaults[k] for k in defaults}
        prompts_dir = spec_path['prompts_dir']
        llm = create_llm_client(
            g('provider').value, model=g('model_name').value,
            api_key=g('openai_api_key').value or None,
            system_prompt=read_text(f'{prompts_dir}/system.txt'),
            mock_responses_file=g('mock_responses_file').value,
            timeout=float(g('request_timeout').value))
        cfg = {
            'policy': {'temperature': float(g('temperature').value),
                       'max_tokens': int(g('max_tokens').value),
                       'maintain_session': bool(g('maintain_session').value)},
            'fgen': {'temperature': float(g('temperature').value),
                     'max_tokens': int(g('max_tokens').value),
                     'max_depth': int(g('max_fgen_depth').value) or None,
                     'max_functions': int(g('max_helper_functions').value) or None},
            'max_attempts': int(g('max_attempts').value),
        }
        self.generator = PolicyGenerator.from_files(
            spec_path['robot_api_file'], spec_path['search_locations_file'], prompts_dir, llm,
            objects=list(g('objects').value), cfg=cfg, log=self._log_code)
        self.info(f'LLM provider={llm.provider} model={llm.model} output_mode={mode}')
        self.info(f'locations={self.generator.env.locations}')
        self.info(f'objects={self.generator.objects}')

        latched = QoSProfile(depth=1, durability=DurabilityPolicy.TRANSIENT_LOCAL)
        self.pub_ready = self.create_publisher(Bool, 'ready_for_input', latched)
        self.pub_bundle = self.create_publisher(String, '~/policy_bundle', 10)
        self.pub_code = self.create_publisher(String, '~/generated_code', 10)
        self.pub_cancel = self.create_publisher(String, '~/cancel', 10)
        self.create_subscription(String, 'user_command', self.on_command, 10)

        self._action = None
        if mode == 'action':
            self._action = ExecutePolicyClient(self, g('execute_policy_action').value)
        self._busy = False
        self._lock = threading.Lock()
        self.publish_ready(True)

    # ------------------------------------------------------------------ callbacks

    def on_command(self, msg):
        text = msg.data.strip()
        if not text:
            return
        if text.lower() in STOP_WORDS:
            self.warn('cancel requested')
            self.pub_cancel.publish(String(data='cancel'))
            if self._action is not None:
                self._action.cancel()
            return
        with self._lock:
            if self._busy:
                self.warn(f'busy, ignoring command <{text}>')
                return
            self._busy = True
        self.publish_ready(False)
        threading.Thread(target=self._generate, args=(text,), daemon=True).start()

    def _generate(self, text):
        try:
            self.info(f'command <{text}>')
            result = self.generator.generate(text)
            if self.output_mode == 'debug_topic':
                self.pub_bundle.publish(String(data=result.to_json()))
            if not result.success:
                self.error(f'generation failed [{result.error_code}] {result.error_message}')
                return
            bundle = result.bundle
            self.pub_code.publish(String(data=f'# {bundle.command}\n{bundle.full_source()}'))
            for w in bundle.warnings:
                self.warn(w)
            self.info(f'policy {bundle.request_id}: targets={bundle.target_objects} '
                      f'helpers={[h.name for h in bundle.helpers]}')
            if self._action is not None:
                self._action.send(bundle)
        except LLMError as e:
            self.error(f'LLM error: {e}')
        except Exception as e:  # never let one command kill the node
            self.error(f'unexpected error: {e!r}')
        finally:
            with self._lock:
                self._busy = False
            self.publish_ready(True)

    # ------------------------------------------------------------------ utils

    def publish_ready(self, ready):
        self.pub_ready.publish(Bool(data=ready))

    def _log_code(self, header, body=''):
        self.get_logger().info(f'{header}\n{body}' if body else header)

    def info(self, m):
        self.get_logger().info(m)

    def warn(self, m):
        self.get_logger().warn(m)

    def error(self, m):
        self.get_logger().error(m)


class ExecutePolicyClient:

    def __init__(self, node, action_name):
        self.node = node
        self._goal_handle = None
        try:
            from limo_interfaces.action import ExecutePolicy
            from rclpy.action import ActionClient
        except ImportError:
            self._client = None
            node.get_logger().error(
                'output_mode=action but limo_interfaces/action/ExecutePolicy does not exist '
                'Policies will NOT be sent to the robot.')
            return
        self._type = ExecutePolicy
        self._client = ActionClient(node, ExecutePolicy, action_name)

    def send(self, bundle):
        if self._client is None:
            self.node.get_logger().error('no ExecutePolicy interface: policy not sent')
            return
        if not self._client.wait_for_server(timeout_sec=2.0):
            self.node.get_logger().error('ExecutePolicy action server not available')
            return
        goal = self._type.Goal()
        goal.request_id = bundle.request_id
        goal.command = bundle.command
        goal.policy_code = bundle.policy_code
        goal.helper_names = [h.name for h in bundle.helpers]
        goal.helper_sources = [h.source for h in bundle.helpers]
        goal.target_objects = list(bundle.target_objects)
        goal.api_version = bundle.api_version
        goal.grammar_version = bundle.grammar_version
        future = self._client.send_goal_async(goal, feedback_callback=self._on_feedback)
        future.add_done_callback(self._on_goal_response)

    def cancel(self):
        if self._goal_handle is not None:
            self._goal_handle.cancel_goal_async()

    def _on_feedback(self, msg):
        fb = msg.feedback
        self.node.get_logger().info(f'executor: {fb.state} {fb.current_location} {fb.message}')

    def _on_goal_response(self, future):
        handle = future.result()
        if not handle.accepted:
            self.node.get_logger().error('ExecutePolicy goal rejected')
            return
        self._goal_handle = handle
        handle.get_result_async().add_done_callback(self._on_result)

    def _on_result(self, future):
        r = future.result().result
        self.node.get_logger().info(f'executor result: {r.outcome} {r.found_location} '
                                    f'{r.message}')
        self._goal_handle = None


def main(args=None):
    rclpy.init(args=args)
    node = None
    try:
        node = LlmNode()
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    except (LLMError, SpecError, ValueError, OSError) as e:
        rclpy.logging.get_logger('limo_llm').fatal(f'startup failed: {e}')
        raise SystemExit(1)
    finally:
        if node is not None:
            node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == '__main__':
    main()
