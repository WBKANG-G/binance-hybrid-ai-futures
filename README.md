# Phase4 익절 로컬 조립 실패·SL 유지·진입 차단·운영자 비상 정리

ZIP을 새 windows-demo-tp-fault-check 폴더에 풀고 TP_FAULT_START_HERE_KO.md 순서대로 진행하세요. CMD 하나: build Healthy → DEMO 키 RAM 등록 → 새 UUID로 한 번 실행. 최신 ask의 LONG LIMIT 최소 cap/100 USDT 이내 신규 DEMO 주문1건을 제출합니다.

기존 코드의 SL 생성·검증 뒤 signed GET으로 SL을 다시 확인하고, 같은 의도의 TP 조립에만 ValueError를1회 주입합니다. 원래 coordinator가 NOT_SENT/LOCAL_VALIDATION_FAILED를 기록하고 신규 진입을 멈추며 SL을 유지하는 것을 확인합니다. 자동 즉시 TP-failure 청산이라고 주장하지 않습니다. 이 확인 뒤 도구가 기존 비상 정리 명령을 실행해 잔량 취소·reduce-only 종료·FILLED/VERIFIED_FLAT를 확인합니다. UNKNOWN exit는 재POST하지 않습니다. 최종 EMERGENCY/paused 잠금은 그대로 유지하고 별도 읽기 전용 복구 검토로 진행합니다.

임시 함수 교체는 별도 CLI 프로세스 안의 own TP에만 적용되고 모든 세션/DB worker drain 후 원상 복원됩니다. 파일·다른 프로세스를 바꾸지 않습니다. 뜻밖의 own TP 전송은 별도 장벽이 SDK 전에 차단하고 성공을 거부합니다. 기존363개 manifest 파일/테스트/SDK/리스크/주문/보호/청산/기준금/키/Compose/exports는 모두 동일합니다. 새 도구·테스트·문서·최근 실제 증거만 추가했습니다.

이는 로컬 TP 조립 실패에 대한 DEMO 대응 검증이며 실제 거래소 Algo 거부, SL 실패를 직접 일으킨 테스트나 부분체결 전체 보호 통과로 확대하지 않습니다. 새 실제 검증은 Windows 실행 전입니다. 이전30분 WSS 갱신 통과와 추가1회 미체결·flat 결과를 별도 증거로 포함했습니다. Phase4 전체/main/LIVE/Phase5 변경 없음.
