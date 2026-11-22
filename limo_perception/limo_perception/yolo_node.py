import threading
import time

import cv2
import numpy as np
import rclpy
from rclpy.callback_groups import ReentrantCallbackGroup
from rclpy.executors import MultiThreadedExecutor
from rclpy.node import Node
from sensor_msgs.msg import Image, CompressedImage, CameraInfo
from geometry_msgs.msg import PointStamped
from visualization_msgs.msg import Marker, MarkerArray
from std_msgs.msg import String
from tf2_ros import Buffer, TransformListener
import tf2_geometry_msgs  # noqa: F401
from ultralytics import YOLO

from limo_interfaces.srv import DetectObject


class YoloNode(Node):
    def __init__(self):
        super().__init__('yolo_node')
        # 공통
        self.declare_parameter('backend', 'yolov8n')
        self.declare_parameter('image_topic', '/camera/color/image_raw/compressed')
        self.declare_parameter('depth_topic', '/camera/depth/image_raw/compressedDepth')
        self.declare_parameter('depth_scale', 0.001)
        self.declare_parameter('target_frame', 'odom')
        self.declare_parameter('max_range', 3.0)
        self.declare_parameter('merge_dist', 0.8)
        self.declare_parameter('min_n', 5)
        self.declare_parameter('min_span', 0.5)
        self.declare_parameter('default_timeout', 5.0)
        # backend별
        self.declare_parameter('v8n.model', 'yolov8n.pt')
        self.declare_parameter('v8n.conf', 0.25)
        self.declare_parameter('v8n.register_conf', 0.5)
        self.declare_parameter('v8n.confirm_conf', 0.55)
        self.declare_parameter('v8n.scan_vocab', ['chair', 'couch', 'bed'])
        self.declare_parameter('world.model', 'yolov8s-world.pt')
        self.declare_parameter('world.conf', 0.10)
        self.declare_parameter('world.register_conf', 0.25)
        self.declare_parameter('world.confirm_conf', 0.30)
        self.declare_parameter('world.scan_vocab', ['desk', 'chair', 'sofa'])

        self.backend = self.get_parameter('backend').value
        p = 'world' if self.backend == 'world' else 'v8n'
        self.conf = self.get_parameter(f'{p}.conf').value
        self.register_conf = self.get_parameter(f'{p}.register_conf').value
        self.confirm_conf = self.get_parameter(f'{p}.confirm_conf').value
        self.scan_vocab = list(self.get_parameter(f'{p}.scan_vocab').value)

        if self.backend == 'world':
            from ultralytics import YOLOWorld
            self.model = YOLOWorld(self.get_parameter('world.model').value)
        else:
            self.model = YOLO(self.get_parameter('v8n.model').value)

        self.depth_scale = self.get_parameter('depth_scale').value
        self.target_frame = self.get_parameter('target_frame').value
        self.max_range = self.get_parameter('max_range').value
        self.merge_dist = self.get_parameter('merge_dist').value
        self.min_n = self.get_parameter('min_n').value
        self.min_span = self.get_parameter('min_span').value
        self.default_timeout = self.get_parameter('default_timeout').value

        self.cbg = ReentrantCallbackGroup()
        self.lock = threading.Lock()
        self.subs = []
        self.active = False
        self.active_classes = set()     # 이번 요청에서 탐지할 클래스 이름들
        self.depth = None
        self.objects = []
        self.next_id = 1
        self.last_frame_t = 0.0
        self.busy = False

        self.fx = self.fy = self.cx = self.cy = None
        self.create_subscription(CameraInfo, '/camera/color/camera_info',
                                 self.cb_info, 1, callback_group=self.cbg)

        self.tf_buffer = Buffer()
        self.tf_listener = TransformListener(self.tf_buffer, self)

        self.pub_markers = self.create_publisher(MarkerArray, '/detected_objects', 10)
        self.pub_img = self.create_publisher(Image, '/yolo/image_annotated', 1)
        self.pub_det = self.create_publisher(String, '/yolo/detections', 10)

        self.srv = self.create_service(
            DetectObject, '/detect_object', self.handle_detect,
            callback_group=self.cbg)
        self.get_logger().info(
            f'대기 중: /detect_object (backend={self.backend}) 요청 시에만 탐지합니다')

    # ---------- 구독 켜기/끄기 ----------
    def activate(self):
        if self.active:
            return
        self.depth = None
        self.last_frame_t = 0.0
        self.subs = [
            self.create_subscription(
                CompressedImage, self.get_parameter('image_topic').value,
                self.cb_color, 1, callback_group=self.cbg),
            self.create_subscription(
                CompressedImage, self.get_parameter('depth_topic').value,
                self.cb_depth, 1, callback_group=self.cbg),
        ]
        self.active = True

    def deactivate(self):
        for s in self.subs:
            self.destroy_subscription(s)
        self.subs = []
        self.active = False

    # ---------- 콜백 ----------
    def cb_info(self, msg):
        self.fx, self.fy = msg.k[0], msg.k[4]
        self.cx, self.cy = msg.k[2], msg.k[5]

    def cb_depth(self, msg):
        try:
            arr = np.frombuffer(msg.data[12:], np.uint8)
            depth = cv2.imdecode(arr, cv2.IMREAD_UNCHANGED)
            if depth is not None and depth.dtype == np.uint16:
                self.depth = depth
        except Exception as e:
            self.get_logger().error(f'depth decode: {e}')

    def cb_color(self, msg):
        if self.busy or not self.active:
            return
        self.busy = True
        try:
            arr = np.frombuffer(msg.data, np.uint8)
            frame = cv2.imdecode(arr, cv2.IMREAD_COLOR)
            if frame is not None:
                self.last_frame_t = time.time()
                self.process(frame, msg.header)
        except Exception as e:
            self.get_logger().error(str(e))
        finally:
            self.busy = False

    # ---------- 탐지 ----------
    def box_distance(self, box):
        if self.depth is None:
            return None
        x1, y1, x2, y2 = box.xyxy[0].tolist()
        cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
        w, h = (x2 - x1) * 0.2, (y2 - y1) * 0.2
        H, W = self.depth.shape[:2]
        xa, xb = int(max(cx - w, 0)), int(min(cx + w, W - 1))
        ya, yb = int(max(cy - h, 0)), int(min(cy + h, H - 1))
        roi = self.depth[ya:yb + 1, xa:xb + 1].astype(np.float32)
        roi = roi[roi > 0]
        if roi.size == 0:
            return None
        return (cx, cy, float(np.median(roi)) * self.depth_scale)

    def process(self, frame, header):
        if self.backend == 'world':
            classes = None      # set_classes로 이미 어휘가 제한됨
        else:
            name_to_id = {v: k for k, v in self.model.names.items()}
            classes = [name_to_id[t] for t in self.active_classes
                       if t in name_to_id] or [-1]
        result = self.model(frame, conf=self.conf, imgsz=320,
                            classes=classes, device=0, verbose=False)[0]

        dets = []
        for b in result.boxes:
            name = result.names[int(b.cls)]
            r = self.box_distance(b)
            if r is None:
                dets.append(f'{name}:{float(b.conf):.2f}@n/a')
                continue
            u, v, Z = r
            dets.append(f'{name}:{float(b.conf):.2f}@{Z:.2f}m')
            if Z <= self.max_range and self.fx and float(b.conf) >= self.register_conf:
                self.register_object(name, u, v, Z, float(b.conf))
        self.pub_det.publish(String(data=', '.join(dets)))

        if self.pub_img.get_subscription_count() > 0:
            annotated = result.plot()
            out = Image()
            out.header = header
            out.height, out.width = annotated.shape[:2]
            out.encoding = 'bgr8'
            out.step = out.width * 3
            out.data = annotated.tobytes()
            self.pub_img.publish(out)

    def register_object(self, name, u, v, Z, conf):
        p = PointStamped()
        p.header.frame_id = 'camera_color_optical_frame'
        p.header.stamp = rclpy.time.Time().to_msg()
        p.point.x = (u - self.cx) * Z / self.fx
        p.point.y = (v - self.cy) * Z / self.fy
        p.point.z = Z
        try:
            pm = self.tf_buffer.transform(
                p, self.target_frame, timeout=rclpy.duration.Duration(seconds=0.1))
        except Exception as e:
            self.get_logger().warn(f'TF 변환 실패: {e}', throttle_duration_sec=2.0)
            return
        x, y, z = pm.point.x, pm.point.y, pm.point.z
        now = time.time()

        for o in self.objects:
            if o['cls'] == name and np.hypot(o['x'] - x, o['y'] - y) < self.merge_dist:
                n = o['n']
                o['x'] = (o['x'] * n + x) / (n + 1)
                o['y'] = (o['y'] * n + y) / (n + 1)
                o['z'] = (o['z'] * n + z) / (n + 1)
                o['n'] = n + 1
                o['conf_sum'] += conf
                o['t1'] = now
                return
        self.objects.append({'id': self.next_id, 'cls': name, 'x': x, 'y': y, 'z': z,
                             'n': 1, 'conf_sum': conf, 't0': now, 't1': now})
        self.next_id += 1

    def is_confirmed(self, o):
        return (o['n'] >= self.min_n
                and (o['t1'] - o['t0']) >= self.min_span
                and o['conf_sum'] / o['n'] >= self.confirm_conf)

    def publish_markers(self):
        arr = MarkerArray()
        for o in self.objects:
            if o['n'] < self.min_n:
                continue
            m = Marker()
            m.header.frame_id = self.target_frame
            m.ns, m.id = 'obj', o['id']
            m.type, m.action = Marker.SPHERE, Marker.ADD
            m.pose.position.x, m.pose.position.y, m.pose.position.z = o['x'], o['y'], o['z']
            m.pose.orientation.w = 1.0
            m.scale.x = m.scale.y = m.scale.z = 0.15
            m.color.r, m.color.g, m.color.b, m.color.a = 1.0, 0.2, 0.2, 1.0
            arr.markers.append(m)
            t = Marker()
            t.header.frame_id = self.target_frame
            t.ns, t.id = 'txt', o['id']
            t.type, t.action = Marker.TEXT_VIEW_FACING, Marker.ADD
            t.pose.position.x, t.pose.position.y = o['x'], o['y']
            t.pose.position.z = o['z'] + 0.25
            t.pose.orientation.w = 1.0
            t.scale.z = 0.15
            t.color.r = t.color.g = t.color.b = t.color.a = 1.0
            t.text = f"{o['cls']} #{o['id']}"
            arr.markers.append(t)
        self.pub_markers.publish(arr)

    # ---------- 서비스 ----------
    def handle_detect(self, req, res):
        res.found = False
        res.confidence = []
        res.found_targets = []

        if not self.lock.acquire(blocking=False):
            self.get_logger().warn('이미 다른 탐지 요청을 처리 중 (BUSY)')
            return res
        try:
            targets = list(dict.fromkeys(t.strip() for t in req.targets if t.strip()))
            scan_mode = len(targets) == 0

            # YOLOv8n은 COCO 이름만 허용, YOLO-World는 임의 단어 허용
            if self.backend != 'world':
                valid = set(self.model.names.values())
                unknown = [t for t in targets if t not in valid]
                if unknown:
                    self.get_logger().warn(f'알 수 없는 클래스: {unknown} (UNKNOWN_CLASS)')
                    return res

            vocab = self.scan_vocab if scan_mode else targets
            self.active_classes = set(vocab)
            if self.backend == 'world':
                self.model.set_classes(list(vocab))   # 요청 시작 시 1회만

            self.objects = []
            self.next_id = 1
            self.activate()
            t_end = time.time() + self.default_timeout
            confirmed = {}

            while time.time() < t_end:
                names = vocab if scan_mode else targets
                for t in names:
                    if t in confirmed:
                        continue
                    cands = [o for o in self.objects
                             if o['cls'] == t and self.is_confirmed(o)]
                    if cands:
                        confirmed[t] = max(cands, key=lambda o: o['conf_sum'] / o['n'])
                if not scan_mode and len(confirmed) == len(targets):
                    break               # 요청한 것을 전부 찾음
                time.sleep(0.1)         # 스캔 모드는 timeout까지 수집

            res.found_targets = list(confirmed.keys())
            res.confidence = [float(o['conf_sum'] / o['n']) for o in confirmed.values()]
            if confirmed:
                self.publish_markers()

            res.found = (len(confirmed) >= 1) if scan_mode \
                else (len(confirmed) == len(targets))

            if not res.found:
                if time.time() - self.last_frame_t > 2.0:
                    self.get_logger().warn('카메라 프레임이 들어오지 않음 (CAMERA_TIMEOUT)')
                else:
                    self.get_logger().info('확정 못함 (NOT_FOUND)')
            return res
        finally:
            self.deactivate()
            self.lock.release()


def main():
    rclpy.init()
    node = YoloNode()
    ex = MultiThreadedExecutor()
    ex.add_node(node)
    try:
        ex.spin()
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()