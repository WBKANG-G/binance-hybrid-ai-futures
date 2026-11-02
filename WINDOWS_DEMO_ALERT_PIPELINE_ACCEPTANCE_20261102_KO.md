# Windows TEST 알림 큐 자동 전달 실제 수신

사용자가 공유한 CMD 결과, 자동 alerts worker 로그 및 네이버 받은메일함 화면을 대조한 기록이다. 메일 주소·비밀번호·API 키·원본 캡처는 포함하지 않는다.

배포: windows-demo-alert-pipeline-check-20261102 / be7862b161f1e4e41a8666a383bcbe998df5e786.
실행 ZIP SHA256: 6e48b22ba23bead2cc90834f7bc25d32c640a0c58090a5ce9e9ef911201a6231.
작업/알림 ID: 4e5d7b91-23a7-4612-b8dc-23542cc63ff0.

## 확인된 전달 경로

- 기존 hybrid-windows 프로젝트의 api/db/web/key-session/alerts 모두 Healthy.
- 명시적 큐 등록 결과 QUEUE_DELIVERY_ACKNOWLEDGED, newly_registered true.
- severity TEST, code DELIVERY_PIPELINE_TEST, attempt_count1, attempt_results ACKNOWLEDGED, last_failure NONE.
- 자동 로그: component DEMO_EMAIL_WORKER, channel NAVER_SMTP, alert_delivery DELIVERED, result ACKNOWLEDGED, attempt1, network_attempted true 및 같은 alert_id.
- 메일 제목 [DEMO] 알림 큐 자동 전달 테스트. 본문 유형 TEST, 같은 알림 ID 및 큐와 자동 worker를 거친 전달 테스트라는 설명이 일치했다.
- 메일 화면 표시 시각은 한국시간2026-11-02 월요일 오후4:20이었다.
- CMD는 trading_state_modified/financial_audit_modified/orders_enabled/live_trading_enabled 모두 false를 보고했다. 테스트가 실제 긴급 사건·주문·취소·청산을 만들었다는 근거는 없다. 클라우드에서 사용자 거래소 상태를 별도 관측한 것으로 확대하지 않는다.

이 근거로 TEST 큐 등록→기존 자동 이메일 worker→실제 네이버 수신 경로를 수락한다. 과거 직접 SMTP self-test만으로 큐를 수락한 것이 아니다. 정확히 한 번 전달, 실제 위험 사건의 전체 대응, 실제 부분체결 보호·TP 체결·보호 중 worker/DB 장애 및 Phase4 전체 완료는 수락하지 않는다.

## 시간 증거의 별도 미해결 항목

CMD 캡처의 delivered_at은 2026-11-01T07:20:18.351856+00:00으로 보이나 메일 화면은2026-11-02 오후4:20이다. KST=UTC+9 변환만으로는 날짜 차이가 설명되지 않는다. 원본 날짜 전사/표시 문제, 저장 시각, Windows/Docker 시각 중 무엇 때문인지는 미확정이다. 같은 ID의 실제 수신을 뒤집지는 않으나, 시각 정확성·기준일 일치를 입증한 결과로 사용하지 않는다. 저장 값을 메일 화면에 맞춰 수정하거나 추정하지 않는다.

UUID를 단독 CMD 명령으로 입력해 발생한 '명령이 아닙니다' 오류는 별도 관측이다. 이는 새 큐 등록/SMTP 재발송이 아니며 이미 확인된 전송 결과를 변경하지 않는다.

## 다음 범위

기존 실행 폴더에서 Windows/API/DB의 현재 시각, 같은 알림의 DB 저장 시각, 기존 로컬 일간·주간 기준일을 읽기 전용으로 확인한다. 기준금 변경·새 주문·리더 획득·키 재입력·메일 재발송·시계 변경을 하지 않는다. 조회 결과를 받기 전 현재 기준일이나 새 잔고를 추정하지 않는다. 예전 '다른 활동 없음' 답변을 새 기간의 확인으로 재사용하지 않는다.
