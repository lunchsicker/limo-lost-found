# limo_policy

LIMO 분실물 탐색 로봇의 **공용 정책 코어**입니다. 순수 Python이며 ROS 런타임 의존이 없습니다(`PyYAML`만 사용). colcon으로 빌드할 수 있도록 빌드 타입만 `ament_python`입니다.

`limo_llm`(정책 생성)과 `limo_executor`(재검증·실행)가 **같은 코드**로 같은 문법을 검사하고 해석하기 위한 패키지입니다.

| 모듈 | 내용 |
|---|---|
| `specs` | `config/robot_api.yaml`, `object_classes.yaml`, `search_locations_*.yaml` 로더 |
| `paths` | 공통 명세 디렉터리 찾기: 명시적 경로 → `$LIMO_CONFIG_DIR` → `share/limo_policy/config` → `AMENT_PREFIX_PATH` → 레포 `config/` |
| `validator` | 정책 문법 서브셋 정적 검증 (`PolicyValidator.check_bundle`) |
| `interpreter` | `exec` 없는 참조 인터프리터 (`PolicyInterpreter`) |
| `errors` | 실행 예외 (`DetectionError`, `UnsupportedObjectError`, `UnknownLocationError`, `PolicyCanceled`, `PolicyRuntimeError`) |
| `bundle` | `PolicyBundle`, `HelperFunction` (limo_llm → limo_executor) |
| `call_graph` | helper 호출 그래프, 순환 탐지, 의존성 순서 |

- 설치: 레포 루트 `config/`의 `robot_api.yaml`, `object_classes.yaml`, `search_locations_*.yaml`을 `share/limo_policy/config/`에 설치합니다. `search_poses_*.yaml`은 `limo_executor`가 설치합니다.
- 계약: [`../docs/interface_contract.md`](../docs/interface_contract.md)

```bash
colcon build --packages-select limo_policy
cd limo_policy && python3 -m pytest test
```

```python
from limo_policy.paths import config_file
from limo_policy.specs import RobotApiSpec, ObjectCatalog, SearchEnvironment
from limo_policy.validator import PolicyValidator
from limo_policy.interpreter import PolicyInterpreter
from limo_policy.bundle import PolicyBundle

spec = RobotApiSpec.load(config_file('robot_api.yaml'))
```
