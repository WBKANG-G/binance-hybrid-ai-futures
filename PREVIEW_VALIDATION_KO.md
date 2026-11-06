# DEMO 읽기 전용 주문조건 미리보기 검증 기록

Python 전체: 317 passed / 0 failed / 0 skipped (45.19초).
명령: TEST_DATABASE_URL=<dedicated local PostgreSQL> python -m pytest -q -p no:cacheprovider.
실제 PostgreSQL 격리 schema + localhost WebSocket + 합성 거래소 응답/자격증명을 사용했습니다.
Ruff check/format 및 변경 source 2개 mypy 통과.
기존 301개에 미리보기/CLI 거부 검증 16개를 추가했습니다.
미리보기 성공 경로에서 거래소 호출이 전부 GET이고 원장에 신규 주문이 추가되지 않음을 검사했습니다.
오래된/미래 호가, 시장 종목 불일치, 역전 호가, 쓰기 활성 client, 계좌 변경,
증거금 부족, 최종 대조 후 호가 만료, 실행 플래그 혼용을 거부했습니다.
의존성 lockfile/API·웹 설정/실거래 위험값/MASTER_SPEC/AGENTS는 변경하지 않았습니다.
이 클라우드에서 Binance 인증 또는 주문을 실행하지 않았습니다. Windows 실제 미리보기는 후속 검증 대상입니다.
미리보기 계산은 실제 손절/익절 지원·체결 가능성·수익성을 검증하지 않습니다. Phase 5는 시작하지 않았습니다.
main과 기존 ZIP은 변경하지 않았습니다.
