# limo_llm 설계 — Code as Policies 기반 분실물 탐색 정책 생성기

참고 원본: [Code as Policies](https://code-as-policies.github.io/) ([arXiv:2209.07753](https://arxiv.org/abs/2209.07753)),
`google-research/code_as_policies/Interactive_Demo.ipynb`, 공식 프롬프트 `fgen.txt`, `mobile_ui.txt`.

## 1. 흐름

```
"우산 찾아줘."
   │  PromptBuilder: API 헤더(import + 시그니처 + 규칙) + few-shot + context
   ▼
LMP('policy_ui')  ──LLM──►  정책 코드 (미정의 helper 호출 가능)
   │  PolicyValidator.check_code (pending helper 허용)
   ▼
LMPFGen.create_new_fs_from_code  ──LLM──►  "# define function: <sig>."  → def … (재귀)
   │  각 helper: 문법·시그니처·이름 충돌·재귀·깊이·개수 검사
   ▼
PolicyValidator.check_bundle (helper+정책 전체, 상수 전파로 물체·위치 이름 검사)
   ▼
PolicyBundle  ──(debug topic | ExecutePolicy action)──►  limo_executor
```

## 2. 모듈 ↔ 공식 CaP 대응

| 모듈 | 공식 CaP | 내용 |
|---|---|---|
| `lmp.py` | `LMP` | 프롬프트 구성, LLM 호출, include_context, maintain_session. **exec 제거** |
| `lmp_fgen.py` | `LMPFGen` | `# define function: <sig>.` 질의, 재귀 helper 생성. **exec 제거**, 소스 수집 |
| `function_parser.py` | `FunctionParser`, `var_exists` | 원본 Call/Assign 수집 로직 유지. `var_exists`(eval)는 이름 집합 조회로 대체 |
| `limo_policy/call_graph.py` | (없음) | 호출 그래프·순환 탐지·의존성 순서 (limo_policy로 분리) |
| `policy_generator.py` | `setup_LMP` | LMP·LMPFGen 구성, 재시도, 번들 생성 |
| `prompt_builder.py` | 프롬프트 import 줄 / `{variable_vars_imports}` | `robot_api.yaml`에서 API 헤더 생성 |
| `llm_client.py` | `openai.Completion` | Chat API(OpenAI) + Mock |
| `limo_policy/validator.py` | `exec_safe`의 문자열 금지 | 문법 서브셋 정적 검증(실행 안전 보장 아님). executor 재검증과 같은 구현 |
| `prompts/policy_ui.txt` | `tabletop_ui.txt` / `mobile_ui.txt` | 한국어 명령 few-shot 10개 |
| `prompts/fgen.txt` | `fgen.txt` | helper 정의 few-shot 6개 + example 2개 |
| `limo_policy/interpreter.py` | (없음) | exec 없는 참조 인터프리터 (mock executor와 limo_executor가 공용) |

## 3. 공식 구현과 다른 부분

| 항목 | 공식 CaP | limo_llm | 이유 |
|---|---|---|---|
| LLM API | `openai.Completion`, `code-davinci-002` | Chat Completions, 프롬프트 전체를 user 메시지로 + "코드를 이어 쓰라"는 system 메시지 | codex 모델 폐기됨. TurtleBot4 튜토리얼과 같은 방식 |
| 출력 정리 | 없음 | code fence 제거, 줄 시작 stop token에서 절단 | chat 모델이 fence를 붙이거나 stop을 무시하는 경우 대비 |
| 실행 | `exec_safe`로 즉시 실행 | **실행하지 않음** → PolicyBundle | LLM과 로봇 제어 분리, 무제한 exec 금지 |
| fgen | 함수를 exec해 함수 객체 생성 | 소스 문자열 수집, 의존성 순서로 정렬 | 같은 이유 |
| `var_exists` | `eval(name, vars)` | 이름 집합 조회 | eval 금지 |
| fgen 안전장치 | 없음 | 중복 방지, 순환 탐지, 깊이·개수 한도, 시그니처 인자 수 검사, API 이름 충돌 거부 | 생성 코드 안정성 |
| 프롬프트 헤더 | import 줄만 | import 줄 + 시그니처·반환값·설명 주석 + 규칙 주석 | 과제 요구사항(API·제약을 프롬프트에 제공). 물체는 원본처럼 `objects = [...]`(COCO 클래스 이름)로만 제공 |
| context | `objects = [...]` | `locations = [...]` + `objects = [...]` | 탐색 위치 제공. stop token에 `locations = [` 추가 |
| query suffix | 항상 `.` 추가 | 이미 `.?!`로 끝나면 생략 | 한국어 명령 "찾아줘." 중복 방지 |
| 하위 LMP (실행 시점 `parse_obj_name` 등) | 있음 | **MVP에서 제외** | 실행 중 LLM 호출은 executor→llm 역호출이 필요함(확장) |
| `fix_bugs` | `code-davinci-edit-001` | 제외 | API 폐기됨 |
| 검증 실패 시 | 없음 | 거부 사유를 주석으로 넣어 1회 재생성(`max_attempts`) | temperature 0이라 같은 프롬프트로는 같은 결과가 나옴 |
| 레이트리밋 | 무한 `while True: sleep(10)` | SDK의 제한된 재시도(`max_retries`) + 타임아웃 | 노드가 멈추지 않게 |
| 문법 | Python 전체 + numpy/shapely | grammar 0.1.0 서브셋, numpy/shapely 없음 | 좌표 API가 없고 executor가 해석 가능한 범위만 사용 |
| 명령 입력 | 그대로 | 공백 정규화로 한 줄 주석화, 300자 제한 | 줄바꿈을 이용한 프롬프트 구조 파괴 방지 |

## 4. MVP와 확장

| 기능 | MVP (구현) | 확장 (미구현) |
|---|---|---|
| 정책 생성 | 한국어 명령 → 정책 코드 | 다국어 |
| 계층적 생성 | 생성 시점 fgen, 재귀, 순환·깊이·개수 제한 | 실행 시점 하위 LMP(executor → `GeneratePolicy` 역호출) |
| 문법 | §6 서브셋 | `range(N)`, 상한 있는 `while`, 컴프리헨션 |
| 세션 | 명령별 독립(`maintain_session: false`) | "아까 거기 다시" — `maintain_session: true` 지원은 있으나 미검증 |
| LED | `led_on/off` API, mock은 False 반환 | 조도 API(`is_dark()` 등) 추가 후 자동 판단 |
| 인터페이스 | 디버그 String 토픽 + mock executor | `ExecutePolicy` action, `GeneratePolicy` service(`limo_interfaces` 합의 후) |

## 5. 위험과 대응

| 위험 | 대응 |
|---|---|
| LLM이 문법 밖 코드나 없는 위치·물체를 생성 | 정적 검증(상수 전파로 helper 인자까지 추적) → 재생성 1회 → 실패 보고. executor가 런타임에서 재검사 |
| 실제 LLM의 한국어 → 물체 이름 매핑 정확도 | LLM이 `objects`(COCO 클래스 이름) 중 하나로 매핑. 목록 밖 이름은 검증기가 거부. **실제 LLM 정확도는 측정하지 않음** |
| `stop`을 지원하지 않는 모델(일부 reasoning 모델) | 기본 모델은 `gpt-4o-mini`. 모델 교체 시 확인 필요 |
| 탐지 성능 미검증 | 클래스 이름은 COCO로 확인. 실내 탐지 성능은 perception에서 확인 필요 |
