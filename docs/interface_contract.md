# limo_llm ↔ limo_executor / limo_perception / limo_interfaces 인터페이스 계약 (초안)

- 상태: **DRAFT v0.1** — `limo_interfaces`, `limo_executor`, `limo_perception` 담당자 합의 전
- 버전: `api_version 0.1.0`, `grammar_version 0.1.0`, `bundle format_version 0.1.0`, `catalog_version 0.1.0`
- 단일 명세 원천: 레포 루트 `config/`
  - `config/robot_api.yaml` — Robot API 시그니처 + 정책 문법(허용 AST 노드, 연산자, 빌트인, 실행 한도)
  - `config/object_classes.yaml` — 한국어 별칭 → 표준 이름 → 탐지기 클래스 후보
  - `config/search_locations_sim.yaml` — 탐색 위치 **이름만** (좌표 없음)

`limo_llm`은 이 파일들을 빌드 시 `share/limo_llm/config/common/`으로 설치해 사용한다.
다른 패키지는 같은 파일을 읽어야 하며 사본을 만들지 않는다. 이 파일들을 `limo_interfaces`로 옮길지는 합의 사항이다(§7).

---

## 1. 역할 분리

```
user_command(String) ──► limo_llm ──PolicyBundle──► limo_executor ──► Nav2 / limo_perception / LED
                         (생성·정적검증, 실행 안 함)    (재검증·제한 인터프리터·Robot API, 최종 안전 책임)
```

- `limo_llm`은 생성된 코드를 **절대 실행하지 않는다** (`exec`/`eval` 미사용).
- `limo_executor`는 번들을 받으면 **반드시 다시 검증**하고, `exec` 없이 문법 서브셋만 해석하는 인터프리터로 실행한다.
  - 참고 구현: `limo_llm/limo_llm/mock/policy_interpreter.py`. 테스트용이며 보안 경계로서 검토되지 않았다.
- AST 정적 검증만으로 안전하다고 가정하지 않는다. 실행 시에도 다음을 강제해야 한다.
  - 인자 값 검사(등록된 위치·물체인지)
  - 스텝·반복·호출 깊이·시간 한도
  - 취소 처리

## 2. PolicyBundle (limo_llm → limo_executor)

`limo_llm/limo_llm/policy_bundle.py`

| 필드 | 타입 | 설명 |
|---|---|---|
| `request_id` | str | 요청 ID (결과 매칭용) |
| `command` | str | 원문 한국어 명령 (한 줄로 정규화됨) |
| `policy_code` | str | context 줄(`locations = [...]`, `objects = [...]`) + LLM이 생성한 정책 코드. `def`는 포함하지 않음 |
| `helpers[]` | list | `{name, source, signature, calls[], origin}`. **의존성 순서**(호출되는 함수가 먼저). `source`는 `def name(...)` 하나 |
| `target_objects[]` | list[str] | 코드에서 정적으로 확인된 표준 물체 이름 |
| `api_version`, `grammar_version` | str | `robot_api.yaml` 버전. 불일치 시 executor는 실행을 거부해야 함 |
| `provider`, `model` | str | 생성에 쓴 LLM |
| `warnings[]` | list[str] | 예: 탐지기 미검증 클래스, 명령과 대상 불일치 |
| `format_version` | str | 번들 포맷 버전 |

실행 의미: helper `def`를 모두 등록한 뒤 `policy_code`를 위에서부터 실행한다. 즉 `full_source()`와 같다.

## 3. ROS2 인터페이스 (제안 — `limo_interfaces` 담당자 승인 필요)

현재 `limo_interfaces`에는 정의가 없다. 아래 정의는 **추가 제안**이며, `limo_llm`은 이 파일들을 만들지 않았다.

### 3.1 `limo_interfaces/action/ExecutePolicy.action` (최종 실행 인터페이스)

서버는 `limo_executor`, 클라이언트는 `limo_llm`이다. 기본 이름은 `/limo_executor/execute_policy`.

```
# Goal
string request_id
string command
string policy_code
string[] helper_names        # 의존성 순서
string[] helper_sources      # helper_names와 같은 순서
string[] target_objects
string api_version
string grammar_version
---
# Result
string request_id
string outcome               # §5 결과 코드
bool found
string found_object
string found_location
string[] visited_locations   # goto_loc 성공
string[] failed_locations    # goto_loc 실패
string message
---
# Feedback
string state                 # VALIDATING | RUNNING | NAVIGATING | SEARCHING | DONE
string current_location
string current_api_call      # 예: "search_here('cup')"
int32 step
string message
```

- 취소: 사용자가 "멈춰/취소/정지" 등을 말하면 `limo_llm`이 `cancel_goal`을 보낸다. executor는 Nav2 goal(NavigateToPose/Spin)을 즉시 취소하고 `CANCELED`로 끝낸다.
- `limo_llm/llm_node.py`의 `ExecutePolicyClient`는 위 필드 이름으로 작성되어 있다. 인터페이스가 없어 **미검증**이다.

### 3.2 `limo_interfaces/srv/GeneratePolicy.srv` (선택)

서버는 `limo_llm`이다. 생성만 요청하는 디버깅·통합용이다.

```
string command
string request_id            # 비우면 limo_llm이 생성
---
bool success
string error_code            # LLM_ERROR | VALIDATION_ERROR | FGEN_ERROR | INVALID_REQUEST
string error_message
string request_id
string policy_code
string[] helper_names
string[] helper_sources
string[] target_objects
string api_version
string grammar_version
string[] warnings
```

인터페이스가 정의되면 `llm_node`에 서비스 서버를 추가한다. 현재는 Python API `PolicyGenerator.generate()`로 같은 기능을 제공한다.

### 3.3 개발용 임시 토픽 (실제 로봇 실행 인터페이스 아님)

| 토픽 | 타입 | 용도 |
|---|---|---|
| `/user_command` | `std_msgs/String` | 한국어 명령 입력 |
| `/ready_for_input` | `std_msgs/Bool` (latched) | 명령 수신 가능 여부 |
| `/limo_llm/policy_bundle` | `std_msgs/String` (GenerationResult JSON) | **디버그 전용** — mock executor 입력 |
| `/limo_llm/generated_code` | `std_msgs/String` | 사람이 읽는 정책 코드 |
| `/limo_llm/cancel` | `std_msgs/String` | mock executor 취소 |
| `/mock_executor/result` | `std_msgs/String` (JSON) | mock 실행 결과 |

## 4. Robot API 계약 (`config/robot_api.yaml`)

| 함수 | 반환 | 의미 / executor 구현 요구사항 |
|---|---|---|
| `get_search_locations()` | `list[str]` | 등록된 탐색 위치 이름. `search_locations_*.yaml`의 이름 집합에 속해야 함. 순서는 executor가 결정(예: 거리순) |
| `get_known_objects()` | `list[str]` | `object_classes.yaml`의 표준 이름 |
| `get_visible_obj_names()` | `list[str]` | 현재 시야에서 탐지된 표준 이름. 로봇을 움직이지 않음 |
| `is_obj_visible(obj_name)` | `bool` | §4.1 |
| `goto_loc(loc_name)` | `bool` | 이름 → 좌표 변환은 executor가 소유. 도착하면 True, Nav2 실패·타임아웃이면 False. 등록되지 않은 이름은 오류(`UNKNOWN_LOCATION`) |
| `search_here(obj_name)` | `bool` | §4.2 |
| `led_on()` / `led_off()` | `bool` | **실제로 켜고 끈 경우에만 True.** LED 하드웨어나 드라이버가 없으면 False(성공으로 보고 금지). mock 사용 시 결과에 `led_backend: mock` 명시 |
| `report_found(obj_name, loc_name)` | `None` | 발견 기록. Result의 `found=true`로 이어짐 |
| `say(msg)` | `None` | 사용자 안내(상태 토픽 / TTS) |

### 4.1 `is_obj_visible(obj_name)`

- 로봇을 이동하거나 회전하지 않는다.
- 호출 시점 이후의 **새 카메라 프레임**(또는 짧은 관찰 구간)에서 탐지를 수행한다.
- True: 탐지됨. False: **정상 관찰 후** 미탐지.
- 다음은 False가 아니라 **오류**로 구분해야 한다.
  - 카메라 없음·프레임 타임아웃·모델 오류 → `DETECTION_ERROR`
  - 탐지기가 지원하지 않는 클래스 → `UNSUPPORTED_OBJECT`
- 표준 이름 → 탐지기 클래스 변환은 `object_classes.yaml`의 `detector_classes`를 따른다. 예: `bag` → `backpack|handbag|suitcase` 중 하나라도 탐지되면 True.

### 4.2 `search_here(obj_name)` — executor가 Navigation + Perception을 오케스트레이션

1. 현재 시야에서 `is_obj_visible(obj_name)` → True면 즉시 반환
2. 제자리에서 한 단계 회전
3. 회전 완료 확인 후 **영상 안정화 대기**(회전 종료 시각 + `settle_s` 이후의 프레임만 사용)
4. 탐지 → 발견하면 즉시 True(남은 회전 생략)
5. 정해진 관찰 방향을 모두 확인하면 False

권장 구현(확인한 사실과 미확인 사항을 구분함):

- **Nav2 `Spin` 동작 사용 권장**: `nav2_msgs/action/Spin` (`float32 target_yaw`, `Duration time_allowance`)
  - 확인함: Humble 설치본에 존재함
  - 확인함: `webots_ros2_limo/resource/{diff,mecanum}/nav2_params.yaml`의 `behavior_server`에 `nav2_behaviors/Spin`이 설정되어 있음. 값은 `max_rotational_vel 1.0`, `min_rotational_vel 0.4`, `rotational_acc_lim 3.2`, local costmap 기반 충돌 검사 `simulate_ahead_time 2.0`
  - **미확인**: Webots에서 실제로 실행해 보지 않았다. 실물 LIMO(`wego_2d_nav`) 설정은 이 머신에 없어 확인하지 못했다
- Nav2 충돌 방지: `NavigateToPose`가 끝난 뒤에만 Spin을 보낸다. 한 번에 하나의 Nav2 goal만 둔다. `/cmd_vel` 직접 발행과 섞지 않는다.
- 기본값 제안: `rotation_step_deg 60`, `max_rotations 5`(도착 방향 포함 6방향 = 360°), `settle_s 0.5`, step당 `time_allowance 10 s`, `search_here` 전체 타임아웃 60 s.
  - 카메라 수평 화각에 맞춰 조정한다. Orbbec DaBai DC1의 화각은 확인하지 않았다.
- Spin이 충돌 검사로 실패하면 남은 방향을 건너뛰고 False와 경고를 반환한다.
- 취소 요청 시 Spin goal을 즉시 취소하고 `CANCELED`로 끝낸다.
- 구동 방식: `drive:=diff`(4륜 차동)와 `drive:=mecanum` 모두 제자리 회전이 가능하다는 것은 README에 근거한 추정이다. 실제 동작은 미확인.

## 5. 결과 코드 (Action Result `outcome`)

| 코드 | 의미 |
|---|---|
| `FOUND` | `report_found()`가 호출됨 |
| `NOT_FOUND` | 정책이 정상 종료했고 미발견(일부 위치는 이동 실패일 수 있음 → `failed_locations`) |
| `NAVIGATION_FAILED` | 어떤 탐색 위치에도 도달하지 못함 |
| `DETECTION_ERROR` | 카메라·탐지 모델 오류(미발견과 구분) |
| `UNSUPPORTED_OBJECT` | 탐지기가 해당 물체를 지원하지 않음 |
| `UNKNOWN_LOCATION` | 등록되지 않은 위치 이름 |
| `VALIDATION_ERROR` | executor 재검증 실패 / 버전 불일치 |
| `RUNTIME_ERROR` | 정의되지 않은 이름, 잘못된 타입 등 |
| `LIMIT_EXCEEDED` | 스텝·반복·호출 깊이 한도 초과 |
| `TIMEOUT` | 전체 실행 시간 초과 |
| `CANCELED` | 사용자 취소 |

`limo_llm` 생성 단계의 오류 코드(`GenerationResult.error_code`): `LLM_ERROR`, `VALIDATION_ERROR`, `FGEN_ERROR`, `INVALID_REQUEST`.

## 6. 정책 문법 (grammar 0.1.0) — 프롬프트·limo_llm 검증기·executor 인터프리터 공통

- 허용
  - `name = expr`(단일 이름), 호출 문장, `if/elif/else`
  - `for name in <이름 | 리스트·튜플 리터럴 | get_search_locations()/get_known_objects()/get_visible_obj_names()>`(`else` 없음), `break`, `pass`
  - 최상위 `def f(a, b):`(위치 인자만), helper 안의 `return`
  - 식: 상수(str/int/float/bool/None), 리스트·튜플, 비교(`== != < <= > >= in not in is is not`), `and/or/not`, 단항 `-`, `+`, 상수 정수 인덱스, 단순 f-string(`{name}`)
  - 호출 대상: Robot API, `len`, 번들 helper만
- 금지
  - `import`, 속성 접근 `a.b`, `while`, `lambda`, 컴프리헨션, `try`, `with`, `class`, `global`, `del`, `async`, `yield`, 데코레이터
  - 기본값·가변·키워드 전용 인자, `*`/`**`, `__` 포함 이름
  - 그 밖의 빌트인(`eval`, `exec`, `open`, `print`, `range`, `getattr` …)
  - Robot API·빌트인·helper 이름의 재정의
  - 직접·상호 재귀
- 실행 한도(`grammar.limits`): `max_steps 2000`, `max_loop_iterations 100`, `max_call_depth 8`, `max_helper_functions 8`, `max_fgen_depth 3`, `max_policy_chars 6000`

## 7. 합의가 필요한 항목

**`limo_interfaces` 담당자**
1. `ExecutePolicy.action`과 `GeneratePolicy.srv` 추가 여부와 필드(§3)
2. 공통 명세(`config/*.yaml`)를 `limo_interfaces/share`로 옮길지, 레포 루트 `config/`에 둘지
3. 버전 정책: `api_version`·`grammar_version`이 다르면 실행 거부

**`limo_executor` 담당자**
1. §4 API 의미, §5 결과 코드, §6 문법·한도 수용
2. 위치 이름 ↔ 좌표 매핑 소유. Webots용 좌표는 `config/search_poses_sim.yaml`에 있으며, 6곳 모두 Nav2 주행 검증을 마쳤다(2026-11-08, 오차 0.05–0.09 m). 각 지점에서 카메라에 무엇이 보이는지는 미검증. 실물 LIMO 지도용 좌표는 별도로 만들어야 한다
3. `search_here` 구현 방식(Nav2 Spin)과 파라미터(§4.2)
4. 인터프리터 구현(참고 구현 사용 여부)과 취소·타임아웃 처리
5. LED 미구현 시 `led_on()`이 False를 반환할 것

**`limo_perception` 담당자** (§8)

## 8. Vision(limo_perception) 계약

- 표준 대상 5종: `bag`, `umbrella`, `cup`, `cell phone`, `book`
- `config/object_classes.yaml`의 `detector_classes`는 **COCO-80 라벨 기준 후보**이며 모두 `verified: false`이다.
  - COCO-80에는 `bag`이 없어 `backpack`, `handbag`, `suitcase`로 매핑했다
  - 확인할 것: 실제 사용할 모델의 정확한 클래스 문자열, 각 클래스의 탐지 성능(실내, LIMO 카메라 높이, 거리)
  - 확인 후 `detector.name`·`verified`를 갱신한다. 미검증 상태에서는 `limo_llm`이 매 번들에 경고를 남긴다
- executor가 `is_obj_visible`·`get_visible_obj_names`를 구현할 수 있도록 다음 중 하나를 합의한다.
  - (a) 토픽: `vision_msgs/Detection2DArray`(Humble에 설치되어 있음 확인), 예: `/limo_perception/detections`. header stamp는 원본 이미지 stamp. 상태·오류는 별도 토픽
  - (b) 서비스: 요청은 클래스 목록·관찰 시간, 응답은 `status`(OK / CAMERA_ERROR / MODEL_ERROR / UNSUPPORTED_CLASS)와 탐지 목록(class, score, bbox, stamp)
- 입력 카메라: `/camera/color/image_raw`(rgb8 640×480, `camera_color_optical_frame`). Webots README에 실물과 같다고 명시되어 있으나 실물은 확인하지 못했다.
- 신뢰도 임계값과 연속 프레임 수(오탐 억제)는 perception 측 파라미터로 둔다.
