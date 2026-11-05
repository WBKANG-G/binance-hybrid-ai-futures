# 복구 심사의 미해결 요청 상세 분류 — 잠금 유지

사용자의 전체 읽기 전용 복구 심사는78개 요청/10 batch, signed336회, unresolved4, risk_gates PASS, 현재 포지션·활성주문0, EMERGENCY/paused를 보고했다. 이는 아직 명시적 과거 미해결 이력 인정이나 잠금 해제를 수행한 결과가 아니다.

앞선 출력은 미해결 건수만 담고 있어, 이 도구가 같은 기존 심사를 한 번 실행하며 세부 관측을 수집한다. 정상 수락 경로/손실/기간/설정/이력/조회 예산/동일성 검사는 변경하지 않는다. 과거 기록을 삭제·해결 처리·자동 인정하지 않는다. 새 export/asyn 요청 및 주문은 차단한다.

ZIP을 새 폴더에 풀고 REVIEW-UNRESOLVED-DETAILS.cmd가 보이는 CMD에서 실행한다.

```bat
REVIEW-UNRESOLVED-DETAILS.cmd
```

기존 키 세션을 사용하며 API 재설치/재시작이나 키 입력은 없다. 조회 시간은 이전 심사의 약20초와 비슷할 수 있으며 원래40초/600 signed/6 clock/128 history bound를 유지한다. 출력은 unresolved-review.json에도 저장하고 기존 파일을 덮어쓰지 않는다. 출력 전체를 공유한다. BLOCKED면 반복하거나 잠금을 해제하지 않는다.

각 미해결 건은 요청 종류DE(진입)/DX(종료)/DS(손절)/DT(목표), 해시 기반 참조, 로컬 요청 상태, 연결된 로컬 작업ID/역할/시각/체결·잔량·포지션 상태와 로컬 미전송 증거 유무를 표시한다. 요청ID·API키/Secret·응답 원문·review token은 출력하지 않는다. 연결이 없으면 unlinked_local_request=true이며, 이것이 전송되지 않았다는 뜻은 아니다.

실제 수집된 전체 관측 사전과 원장의 canonical JSON 및 최종 스냅샷 지문을 사용해 기존 review_token을 재계산한다. 원래 토큰과 일치하고 count가 동일할 때만 상세를 반환한다. 기존 심사의 read-client/snapshot hook은 이 새 Python 프로세스에서 기록 용도로만 임시 래핑하고 finally에서 복원한다. 판단/응답/기존 예산은 그대로 전달한다. 계정 전체row(owner/fence/lease/state)를 전후 비교한다. SQL 조회는 READ ONLY다.

NotFound는 현재 해당ID의 주문을 조회하지 못했다는 뜻이다. 과거 미전송/미체결 또는 요청이 없었다는 증거가 아니다. 원문WSS/실제TP/부분체결 성공과도 별개다. 현재 PASS/flat이어도 과거4건을 아직 확인하지 않았으므로 unlock_applied=false 및 unresolved_history_acknowledged=false를 유지한다.

직전 작업243598bb-900a-45d5-8af3-a776d543a504의 별도 서명 조회는 진입·reduceOnly 종료 FILLED, SL/TP EXPIRED, 현재 원격 empty 및 finish EMERGENCY 명령을 확인했다. 이 제한된 현재 종료 증거를 과거 모든 요청의 해결 증거로 확대하지 않는다. LIVE/Phase5/main 병합/Phase4 전체 완료를 수행하지 않는다.
