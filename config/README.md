# config/ — 공통 명세 (원본)

이 디렉터리의 파일이 원본입니다. 다른 패키지에 사본을 만들지 마세요.

| 파일 | 소유자 (수정 책임) | 읽는 곳 | 설치 |
|---|---|---|---|
| `robot_api.yaml` | limo_llm + limo_executor 공동 (변경 시 `api_version`/`grammar_version` 합의) | limo_policy (limo_llm, limo_executor) | `limo_policy` → `share/limo_policy/config/` |
| `object_classes.yaml` | limo_perception (`detector_classes`, `verified`), limo_llm (`aliases_ko/en`) | limo_policy, limo_perception | `limo_policy` |
| `search_locations_*.yaml` | limo_executor (이름은 poses 파일과 일치해야 함) | limo_policy (LLM 프롬프트·검증) | `limo_policy` |
| `search_poses_*.yaml` | **limo_executor** | limo_executor만 (LLM은 읽지 않음) | **limo_executor** (limo_policy는 설치하지 않음) |

- `limo_interfaces`는 msg/srv/action 정의만 담당하며 이 파일들을 설치하지 않습니다.
- 경로 탐색: `limo_policy.paths.default_config_dir()`. 순서는 명시적 경로 → `$LIMO_CONFIG_DIR` → `share/limo_policy/config` → `AMENT_PREFIX_PATH` → 이 디렉터리.
- 계약 전체: [`../docs/interface_contract.md`](../docs/interface_contract.md)
