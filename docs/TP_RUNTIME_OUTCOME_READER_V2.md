# 별도 V2 오프라인 결과 판독

원래 시험 결과 UNKNOWN / NOT_ACCEPTED와 이후 전체 금융 확인의 보고값 VERIFIED_FLAT은 서로 다른 단계의 결과입니다. 기존 V1은 이를 RESULT_PROOF_MISMATCH로 거부합니다. V1의 판정·정책·영수증 버전을 바꾸지 않고 별도 V2 CLI와 영수증 버전을 추가합니다.

전체 소스 저장소에서 저장된 합성 보고서만 오프라인으로 검토합니다.

```bash
python -m scripts.review_runtime_outcomes report.json --save-receipt receipt-v2.json
python -m scripts.review_runtime_outcomes report.json --verify-receipt receipt-v2.json
```

V2도 DB·HTTP·API 키·현재 계정 상태에 접근하지 않습니다. 새로운 실행·배포·거래 승인 경로도 아닙니다. 이 검토 ZIP은 독립 실행 설치본이 아니며, 현재 Windows 설치본에 이 명령이 존재한다고 가정하지 않습니다.

V2는 original_trial_* 필드에 원래 시험의 flat 상태·TP 표시·실패 코드를 보존하고, later_returned_flat_confirmation 및 later_reported_outcome에 후속 보고값을 표시합니다. original_trial_reported_accepted는 원본의 TP 표시를 읽은 값이며 새로 계산하거나 인증한 금융 사실이 아닙니다. 모호한 기존 반환 flat 필드는 V2 출력에 복제하지 않습니다. original_trial_reclassified는 항상 false입니다. outcomes_separated는 두 flat 상태가 다른, 좁게 지원하는 수신 단절 사례를 뜻합니다. flat 상태가 같아도 원본 TP가 NOT_ACCEPTED이면 별도 필드에 그대로 표시하고 추가 검토로 처리합니다.

지원 범위를 좁게 제한합니다. mode=receiver_failure, 대기·소켓 단절 주입 표시, RECEIVE/SOCKET_CLOSED 진단, 원래 시험 UNKNOWN/NOT_ACCEPTED/DEMO_SESSION_OR_QUERY_FAILED, 후속 세션 실패 표시, 봉인 실패·bridge 열화·미봉인 없음, 기록 보존·CLOSED·정지/실행 비활성화, 원래 대기 정책과 비금융 watch 표시 등이 모두 맞아야 서로 다른 flat 상태를 검토합니다. 반대 방향 또는 다른 종류의 불일치는 계속 거부합니다. 여러 오류가 겹친 임의의 다른 사례까지 지원한다고 주장하지 않습니다.

기존 V1의 확인 소유권·완료 여부·시작 기한·시도 수·사유·trace 비용·요청 수·정지/비활성화 조건은 그대로 검증합니다. 분리 사례에서는 입력을 변경하지 않고 비공개 복사본의 원래 flat 필드만 후속 값에 맞춘 뒤 공통 조건을 검증합니다. 이 복사본을 원본이나 성공한 시험 결과로 저장하지 않습니다. trace 불일치나 여유 부족의 기존 검토 사유도 숨기지 않습니다.

종료 코드 0은 V2 정책 아래에서 일치하는 보고 자료라는 뜻입니다. 추가 검토는 3, 입력·정책·영수증·저장 오류는 2입니다. 현재 저장된 TP·부분 체결·봉인 실패·수신 단절 자료 4개는 각각 V2 코드 0/3/3/3입니다. 수신 단절 보고서는 반드시 추가 검토이며 원래 시험은 UNKNOWN, reported accepted=false로 남습니다. 부분 체결 정리도 원래 TP가 NOT_ACCEPTED이므로 추가 검토가 됩니다. 봉인 실패는 원본 TP 표시가 성공이어도 별도 봉인 실패를 보존합니다.

V2 영수증은 version=2와 SYNTHETIC_SEPARATE_OUTCOMES_V2 정책을 고정하고, 한 번 읽은 원본 바이트의 SHA-256·크기 및 전체 재계산 결과와 연결합니다. V1과 V2 영수증은 교차 검증하지 못합니다. 둘 중 하나를 자동 선택하거나 V1 거부 후 조용히 V2로 재시도하지 않습니다. 출력의 승인·정책·버전·원래 시험 수락 표시·후속 상태 또는 원본 공백만 바꾸어도 기존 연결은 거부됩니다.

V1 저장 함수의 기존 배타적 게시 절차를 공통 내부 helper로 분리해 두 CLI가 사용합니다. 원본·기존 파일·링크를 덮어쓰지 않고, 비공개 임시 파일을 완성·동기화한 뒤 새 이름을 배타적으로 게시합니다. 불확실한 게시나 정리 실패는 코드 2로 보고하며 이미 만들어졌을 수 있는 결과를 삭제하지 않습니다. 이 내부 파일 쓰기 helper 자체가 임의의 Python 객체를 인증하는 것은 아닙니다. 실제 판독은 각 CLI가 저장 전에 수행합니다. 부모 디렉터리·하드웨어 장애 내구성이나 셸 리다이렉션에 의한 사전 잘림을 인증하지 않습니다.

해시·정합성 검사는 서명·원본 출처·금융 증명 재실행·현재 거래소 상태를 인증하지 않습니다. financial_proof_reverified와 execution_approved는 항상 false이며 다음 동작은 KEEP_PAUSED_REVIEW_ONLY입니다. 원본과 영수증을 모두 새로 작성할 수 있는 사람은 새로 일치하는 쌍을 만들 수 있습니다. 알 수 없는 원본 필드와 진단 URL·개인 데이터는 출력하지 않습니다.

현재 검증은 새 V2 및 기존 V1 검사, 회귀 검사, 네트워크 없는 Docker 검증입니다. 해시가 검증된 이전 GitHub ZIP의 모의 보고서 4개를 새 CLI로 저장·대조하고, V1의 기존 세 영수증 검증 및 수신 단절 거부도 유지되는지 확인합니다. 이전 금융 JUnit 4개는 출처를 표시한 재사용 기록입니다. 이번에 별도의 TP 청산 시나리오 4개·새 300초 관찰·거래소 시험을 다시 실행했다고 계산하지 않습니다. 금융 런타임과 관찰 중단·비용 정책·기존 운영 파일은 변경하지 않습니다.

Windows 설치·DB 변경·실제 거래소 요청·주문·거래 재개는 수행하지 않습니다. 실제 계정 전체 주문 사용량·외부 IP 여유·실제 비용 상한은 여전히 별도 검증 대상입니다. 실제 검사 수·통과 여부와 원본 출처는 ZIP의 VALIDATION.json 및 evidence/에 기록됩니다.
