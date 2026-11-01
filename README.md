# 실제 TP 체결과 잔여 SL 정리 관찰

새 windows-demo-tp-trigger-check 폴더에 ZIP을 풀고 TP_TRIGGER_START_HERE_KO.md를 따릅니다. 같은 hybrid-windows 프로젝트로 빌드하고 읽기 전용 준비 확인이 정상일 때만 명시적 플래그와 전용 UUID로 단일 DEMO 시험을 실행합니다. 유효한 RAM 키는 API 재빌드 후 재사용합니다. 기존 DB/export/RAM 볼륨을 지우지 않습니다.

시험은 LONG/PASSIVE 지정가의 현재 최소 수량 한 건, cap최대0.0007 BTC/명목100 USDT 이하, ISOLATED BTCUSDT/2배로 제한합니다. 기존 SL0.5%/TP1% 거리와 위험·신선도·시계·리스/펜스 정책을 유지합니다. 기존 validate_once의 관찰 시간만300초로 지정하며 이후 원래 정리/flat 확인을 수행합니다. 관찰 시간 안에 TP 체결을 보장하지 않습니다. TP가 없으면 안전 종료 후 NOT_ACCEPTED로 남기고 반복 주문하지 않습니다.

TP 실제 성공은 자기 TP의 FINISHED 및 연결 실제 주문 FILLED/SELL/MARKET/reduceOnly/수량, 당시 잔여 SL NEW, 서명 포지션0과 원래 SL DELETE 후 CANCELED 조회, 운영자 종료 POST0, 기존 최종 VERIFIED_FLAT과 NORMAL/paused·이력 보존을 함께 요구합니다. TP 발생 없이 운영자 종료된 FINISHED_FLAT을 TP 성공으로 보지 않습니다. 새 서명 조회에서 포지션이 남은 것이 확인되면 SL 삭제를 차단합니다. 증거가 부족하면 성공으로 표시하지 않습니다.

기존 배포431개 파일 모두 바이트 동일합니다. 실행 모듈/CLI/시험/한국어 안내/실제 증거8개 파일만 추가했습니다. 전체1278개·집중18개 시험과 Ruff/mypy115개 소스 통과. 격리 PostgreSQL/로컬 WSS/합성 SDK로 검증했으며 실제 키나 인증 거래소 요청은 클라우드에서 사용하지 않았습니다. 실제 Windows TP 관측은 아직 미검증입니다.

직전1049e143은 전량 체결·보호·운영자 종료/flat 통과였으며 부분체결/실제 TP 통과가 아닙니다. 후속3efd448c 복구는 진입 정지를 유지하며 완료됐습니다. 과거 실패·UNKNOWN/NOT_SENT·기준금·고점을 임의 초기화하지 않습니다. 부분체결 강제/체결을 위한 수량 증가/가격 추적/시장가 신규 진입/자동 재시도는 없습니다. 이 배포는 main/LIVE/Phase5 또는 Phase4 전체 완료 판정을 변경하지 않습니다.
