# Phase4 WSS 단절 관측 후 세션 정리 완료 대기 수정

ZIP을 새 windows-demo-disconnect-drain-fix 폴더에 풀고 DISCONNECT_DRAIN_START_HERE_KO.md 순서대로 진행하세요. CMD 하나: build Healthy → DEMO 키 RAM 등록 → 새 UUID로 1회 검증. 이 명령은 100 USDT 이내 신규 DEMO 지정가 주문 1건을 제출합니다.

이전 실제 시도는 SOCKET_CLOSED 기록과 주문 CANCELED/체결0/flat/NORMAL을 확인했지만 재연결0으로 미통과했습니다. 통합45초 타이머가 단절 후 finally 정리를 중단시킬 수 있는 경로를 재현했고 수정했습니다. 최초 연결/준비/단절 recv 관측의45초 제한은 유지하며, 이미 관측된 단절의 원래 세션 정리는 완전히 drain한 뒤 기존 차단 게이트 검사와 재연결을 진행합니다. recv 단일 위임 wrapper는 예외를 원래 수신기에 그대로 전달합니다. 초기 제한 만료는 PHASE_TIMEOUT으로 구분하고 UTC/경과 시간을 출력합니다.

기존 실행 모듈의 run 수정과 drain helper 추가만 포함합니다. SDK/기존 수신기/coordinator/주문/리스크/보호/청산/기준금/키/Compose/exports 및 이전 테스트는 그대로입니다. 이전 실패를 통과로 바꾸지 않았으며 새로운 실제 Windows 검증이 필요합니다. Phase4 전체 완료, 부분체결 실제 통과, LIVE 또는 Phase5 승인으로 확대하지 않습니다. main은 변경하지 않았습니다.
