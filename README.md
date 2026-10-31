# Phase4 주문 없는 개인 WSS30분 관찰·listenKey 갱신

ZIP을 새 windows-demo-private-stability-check 폴더에 풀고 PRIVATE_STABILITY_START_HERE_KO.md 순서대로 실행하세요. CMD 하나: build Healthy → DEMO 키 RAM 등록 → 새 UUID로 한 번 실행. 최초 OBSERVING 이후 실제30분을 관찰하며 준비/정리는 추가로 걸립니다. 신규 주문과 장애 주입은 없습니다.

원래 session 한 번을 실행하고 약1분마다 경과/성공 갱신 횟수를 표시합니다. 원래25분 listenKey 갱신 분기를 그대로 사용합니다. 기존 ObservationClient로 일반 request의 GET 이외를 거부하며 listenKey POST/PUT/DELETE만 별도 수명 관리로 허용합니다. 단일 키 생성/갱신 성공1회 이상/단일 키 삭제, 실제1800초 관찰, 세션 오류 없음, 빈 계좌/이력·기준 보존 및 NORMAL/paused 종료를 모두 확인해야 통과합니다. 오류/키 삭제/감사 실패는 성공으로 올리지 않으며, 같은 작업 ID의 시작/실패 결과 기록이 있으면 재실행을 차단합니다. 원래 session의 정리는 drain_session으로 끝까지 기다립니다.

SDK/원래 session/coordinator/리스크/보호/청산/기준금/키/Compose/exports 및 이전 테스트를 포함한 기존 manifest 파일356개는 모두 동일합니다. 추가 파일만 제공합니다. 로컬 테스트는 실제 PG와 localhost WSS, synthetic SDK 및 가속 fixture 시각을 사용해 원래 갱신 분기를 실행했습니다. 실제 Binance30분 관찰을 클라우드에서 수행한 것은 아니며 Windows 실행이 필요합니다. 이전 빈 계좌 Worker 강제 종료·자연 만료·리더 복구 실제 통과 증거도 포함합니다.

30분 세션 안정성 범위이며24시간 운용, 모든 원문 WSS 영구 저장, 실제 부분체결 보호, 보유 주문/포지션 Worker 종료 또는 Phase4 전체 통과로 확대하지 않습니다. main/LIVE/Phase5 변경 없음.
