# 현재 설치본의 활성 TP 요청 예산 검증

판정: NOT_APPROVED_FOR_ACTUAL_TP_KEEP_PAUSED. 이번 파일은 검토 자료입니다. Windows에서 추가로 실행할 명령은 없습니다. 현재 계정, 기준값, 키 세션, 일시정지 상태, runtime을 변경하지 않았습니다.

## 확인한 범위

현재 설치 생산 코드4e967dc(조회 변경015fd20)를 그대로 사용했습니다. TP CLI 관측 상수는300초(5분)입니다. 기존 미설치 budget governor, income-filter candidate, operational closed-history omission은 합치지 않았습니다. 단위12개, stress10개, 실제300초 경과를 확인한 quiet2개로 총24개 검사가 통과했습니다. ‘검사 통과’는 실패 조건을 올바르게 검출했다는 뜻이며 실제 TP 실행 예산 통과가 아닙니다.

Quiet 두 경우는 관측 시간을 줄이지 않았고 각각300초 이상 실행했습니다. 모의 TP 체결은 원래 SL 취소와 full-history flat proof까지 완료됐습니다. 모의 TP 미체결도 원래 reduce-only 종료와 full-history flat proof를 완료했으며 원래 정책에 따라 EMERGENCY를 남겼습니다. 이는 실제 사용자 계정 위험 상태를 뜻하지 않습니다.

수입 pagination stress5개는 관측을0.8초로 명시적으로 줄여 준비·보호·종료 경로의 비용 반례를 찾았습니다. Burst/pulse/spike는300초 설정으로 시작했지만 모의429로 일찍 중단됐습니다. 해당 경우를300초 완주로 계산하지 않았습니다. 원래 frame 시간 검증과 event coalescing을 그대로 사용했고 SDK/store/서버의 테스트 시계는 실제 monotonic 경과와1:1로 진행했습니다.

## 결과 해석

정확한 숫자는 SUMMARY.json의 cases와 synthetic_reports를 봅니다. 적은 이벤트/1page 입력의300초 quiet 성공이 모든 이벤트 흐름의 예산을 보장하지 않습니다.50ms HTTP 왕복과 초당4개 힌트의 입력에서도 분당 모델 한도를 넘었고, 모의 포지션이 열린 상태에서 UNKNOWN/EMERGENCY로 종료됐습니다. 신규 진입은1회 이하이고 첫429 이후 네트워크 요청을 반복하지 않았습니다.

수입 이력이 종료 시10페이지로 늘어나는 경우에는 정리 뒤 flat 확인 단계에서 모델 한도를 넘었습니다. 또10페이지가 모두1000행인 경우에는 pagination이 완전하지 않아, 모의 position 수량이0이어도 VERIFIED_FLAT으로 표시하지 않았습니다. 이 두 실패를 구분해 기록했습니다. raw report의 full_history_snapshot_count는 호출 시도 수이며 실패한 호출도 포함할 수 있습니다. 완전한 flat 증거 여부는 flat_confirmation과 completion으로 판단합니다.

모델은 공식 SDK의 GET 문서 값과 앞선 읽기 관측을 참고합니다. 공개 REQUEST_WEIGHT 모델 한도는6000/분입니다. openOrders/openAlgoOrders는 symbol 없는 전체 계정 조회40, accountConfig5, income은 페이지당30 등 쿼리 범위를 검사합니다. 모르는 경로나 다른 public 쿼리 범위에 임의의 비용을 부여하지 않습니다.

모든 non-GET의 IP 비용은 낙관적으로0을 주었습니다. 별도 주문-count 제한을 검증한 것이 아니며, IP 전용 GET 모델이 통과해도 실제 한도 허용으로 볼 수 없습니다. 반대로 GET만으로도 한도를 넘는 반례는 추가 쓰기 비용을 더해 해결되지 않습니다. Shared IP의 여유량을 확보한 것도 아니고, 관측 counter 차이는 endpoint 상한을 증명하지 않습니다.5분 전체 가중치 합계를6000/분과 직접 비교하지 말고 분별 최대값을 보세요.

## 실제 실행 전 남는 작업

현재 코드는 SDK429/cooldown에 반응해 정리와 진입을 멈춥니다. 진입 전에 관측 전체와 원래 종료·보호 정리·full-history flat 증거를 위한 예산을 검증/확보하는 동작은 설치돼 있지 않습니다. 이벤트에 따른 반복 reconciliation과 pagination 변화가 포함된 예산 검사, 새 진입을 허용하기 전의 종료 여유량 판단이 필요합니다. 실패 사례를 통과하는 조건이 준비되기 전 실제 TP 재시험/거래 재개 명령은 제공하지 않습니다.

## 재현·자료

소스는 source/tests/ 및 source.patch에 있습니다. 감사 코드 commit과 원본 runtime 해시는 SOURCE_PROOF.json에, JUnit은 test-results에 있습니다. 원본 저장소의 개발 의존성과 격리 TEST_DATABASE_URL에서 tests/integration/test_installed_tp_budget_audit.py, tests/unit/test_installed_tp_budget_audit.py를 실행합니다. 두 quiet300초 검사는 각각 별도 pytest 프로세스로 병렬 실행했으며 각 fixture는 독립 PostgreSQL schema를 사용합니다. 단일 프로세스로 전체 실행하면 두 quiet가 순차 진행돼 약10분이 걸립니다. 사용자 DB를 TEST_DATABASE_URL로 사용하지 마세요.

클라우드에서는 합성 HTTP/주문과 로컬 WSS만 사용했습니다. 테스트의 모의 open position은 실제 DEMO/LIVE 포지션이 아닙니다. 사적 키/서명/query/body는 trace에 저장하지 않습니다. Windows readiness20회 성공은 별도로 보존하며, 이번 결과를 거래 재개 승인으로 바꾸지 않습니다.
