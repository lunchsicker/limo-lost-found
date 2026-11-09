# limo_llm

LIMO 분실물 탐색 로봇의 **Code as Policies 정책 생성기**(ROS 2 Humble, `ament_python`).

한국어 명령을 받아 few-shot LMP로 Python 정책 코드를 생성합니다. 정의되지 않은 helper 함수는 계층적으로 재귀 생성(LMPFGen)하고, 공통 문법 서브셋으로 검증한 뒤 `PolicyBundle`로 executor에 넘깁니다. **이 패키지는 생성된 코드를 실행하지 않습니다.**

- 설계와 공식 CaP 대비 변경점: [`../docs/limo_llm_design.md`](../docs/limo_llm_design.md)
- executor·perception·interfaces 계약: [`../docs/interface_contract.md`](../docs/interface_contract.md)
- 공통 명세(단일 원천): [`../config/robot_api.yaml`](../config/robot_api.yaml), [`../config/search_locations_sim.yaml`](../config/search_locations_sim.yaml)

## 구조

```
limo_llm/
├── limo_llm/
│   ├── llm_node.py            # ROS 2 노드 (ROS 의존은 여기와 mock_executor_node뿐)
│   ├── lmp.py                 # CaP LMP 포트 (exec 제거)
│   ├── lmp_fgen.py            # CaP LMPFGen 포트: 계층적 함수 생성
│   ├── function_parser.py     # CaP FunctionParser + 호출 그래프 / 순환 탐지
│   ├── prompt_builder.py      # 프롬프트 조립 (robot_api.yaml → API 헤더)
│   ├── policy_generator.py    # setup_LMP 역할, generate(command)
│   ├── llm_client.py          # OpenAI Chat / Mock
│   ├── resources.py           # limo_llm prompts/config 경로
│   └── mock/                  # 테스트 전용: MockRobot, mock executor 노드
├── prompts/  policy_ui.txt · fgen.txt · system.txt
├── config/   llm_params.yaml · mock_scenario.yaml · mock_llm_responses.yaml
├── launch/llm.launch.py
└── test/
```

## 빌드

```bash
cd ~/limo_ws
source /opt/ros/humble/setup.bash
colcon build --packages-select limo_llm
source install/setup.bash
```

공통 명세 로더·검증기·인터프리터·`PolicyBundle`은 [`../limo_policy`](../limo_policy)에 있습니다(ROS 의존 없음). 레포 루트 `config/`의 공통 명세는 `limo_policy`가 `share/limo_policy/config/`에 설치하며, `LIMO_CONFIG_DIR` 환경변수로 다른 디렉터리를 지정할 수 있습니다.

```bash
colcon build --packages-up-to limo_llm      # limo_policy가 함께 빌드됨
```

## LLM 설정

- `provider:=openai`(기본값): `export OPENAI_API_KEY=...` 또는 `openai_api_key:=` 파라미터. `pip install openai`(>= 1.0) 필요.
  - 모델은 `model_name`(기본 `gpt-4o-mini`)으로 지정하며, **`stop` 시퀀스를 지원하는 모델**이어야 합니다.
- `provider:=mock`: API 키 없이 `config/mock_llm_responses.yaml`의 스크립트 응답을 사용합니다(데모·테스트 전용).

## 실행

```bash
# 터미널 1: API 키·로봇 없이 (mock LLM + mock executor)
ros2 launch limo_llm llm.launch.py provider:=mock mock_executor:=true

# 실제 LLM으로 생성하고 mock executor로 확인
export OPENAI_API_KEY=sk-...
ros2 launch limo_llm llm.launch.py provider:=openai mock_executor:=true

# 터미널 2: 명령 입력 / 결과 확인
ros2 topic pub --once /user_command std_msgs/msg/String "{data: '우산 찾아줘.'}"
ros2 topic echo /limo_llm/generated_code
ros2 topic echo /mock_executor/result
ros2 topic pub --once /user_command std_msgs/msg/String "{data: '멈춰'}"   # 취소
```

`output_mode`:
- `debug_topic`(기본값): `/limo_llm/policy_bundle`로 발행합니다. 개발 전용입니다.
- `action`: `ExecutePolicy` 액션으로 보냅니다. `limo_interfaces`에 정의가 없으면 전송하지 않고 오류를 기록합니다.
- `none`: 생성만 합니다.

## 테스트

```bash
cd ~/limo_ws/src/limo-lost-found/limo_llm
source /opt/ros/humble/setup.bash
python3 -m pytest test -p no:anyio          # -p no:anyio: 아래 참고
# 또는
cd ~/limo_ws && colcon test --packages-select limo_llm && colcon test-result --verbose
```

이 머신의 `~/.local`에 설치된 `anyio` pytest 플러그인은 시스템 pytest 6.2.5와 호환되지 않습니다. 그래서 `setup.cfg`에 `-p no:anyio`를 넣어 두었습니다.

## Webots 연동 (executor 통합 후)

```bash
ros2 launch webots_ros2_limo robot_launch.py nav:=true     # Webots + Nav2 + RViz
ros2 launch limo_llm llm.launch.py output_mode:=action      # limo_executor 필요
```

`limo_llm`이 Webots에서 쓰는 것은 위치 **이름**뿐입니다(`config/search_locations_sim.yaml`). 좌표·회전·탐지는 `limo_executor`와 `limo_perception`이 담당합니다.
