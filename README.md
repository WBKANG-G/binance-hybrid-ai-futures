# Phase4 미체결 진입 주문의 WSS 단절·잔량 취소·재연결

ZIP을 새 windows-demo-open-disconnect-check 폴더에 풀고 OPEN_DISCONNECT_START_HERE_KO.md 순서대로 실행하세요. CMD 하나: build Healthy → 메모리 DEMO 키 등록 → 명시적 DEMO/장애 주입 플래그 및 새 UUID로 1회 실행. 새로운 DEMO 진입 주문 1건을 제출하는 검증입니다.

기존 취소 정책을 유지합니다. 서명 NEW/체결0 확인 → 로컬 WSS close1011 1회 → 기존 session의 잔량 취소와 진입 게이트 차단 → 새 WSS 연결 및 전체 REST 대조 1회 → 같은 주문이 CANCELED/체결0인지 조회 → 같은 의도 재호출이 새 POST를 만들지 않는지 확인 → 최종 flat 확인. 체결·모호한 상태·장애는 통과 처리하지 않고 기존 보호/조건부 비상 정리 경로를 사용합니다. 기존 SDK/리스크/보호/청산/기준/키/Compose/exports 소스와 이전 테스트는 모두 부모 ZIP과 바이트가 같습니다.

실제 사용자가 통과한 미체결 주문 중 API 재시작, LONG/SHORT 전량 체결·보호·flat, 마지막 복구 결과를 포함했습니다. 새로운 WSS 주문 단절 검증은 Windows 실행 전이며 실제 부분체결, worker/호스트/DB 중단, 장기 운영 또는 Phase4 전체 완료로 확대하지 않습니다. LIVE/Phase5/main 변경 없음.
