# DEMO 조회 전송과 영속 공유 예산 연결

기존 운영 확인은 완료됐다. Windows에서 2026-11-12 일간 적용과 READ_ONLY_READY_ENTRIES_STILL_PAUSED가 확인됐으며, 사용자는 같은 DEMO 계정의 다른 봇과 같은 공인 IP의 다른 Binance API 프로그램이 모두 없다고 답했다. 이 답변은 운영 범위 확인이다. 거래소의 주문 사용량 증거, 실제 IP 용량 예약 또는 주문 실행 승인으로 취급하지 않는다.

이번 변경은 `services/execution/demo_runtime_read_transport.py`의 `AccountedDemoReadTransport`다. 기존 합성 실행용 예산을 완화하는 대신, 실제 DEMO GET 전송을 독립 PostgreSQL 체크포인트에 연결된 공유 SQLite 예산과 결합했다. 기본은 비활성이며 생성 시 `enable_reads=True`를 명시해야 한다. 계정 리스나 주문 writer session을 시작하지 않고, 주문 한도 슬롯을 부여하지 않는다.

허용된 BTC/계정 전체 조회 모양, 고정 DEMO 호스트, LIVE·혼합 키 문맥 차단, 문서 비용 계약을 먼저 검사한다. 매 GET은 기존 공유 저장소의 ORDINARY 범위에서 비용을 예약한 뒤에만 전송한다. 하위 HTTP 경로에도 요청 1개당 1회만 쓸 수 있는 내부 문맥을 두어 미기록 호출·중복 전송을 차단한다. SDK의 시간 동기화와 서명된 계정 조회도 이 경로로 연결할 수 있다. 리다이렉트를 따라가지 않고 네트워크 대기 시간과 응답 크기를 제한한다.

성공 응답은 공유 SQLite와 독립 PostgreSQL 체크포인트 기록을 마친 뒤 반환한다. 응답 유실, 잘못된 메타데이터, 중복된 weight 헤더, 418/429는 비용을 환불하지 않고 공유 저장소를 차단한다. HTTP 오류는 자동 재시도하지 않는다. 완료 체크포인트 커밋이 실패하면 응답을 반환하지 않으며 불일치 상태를 자동으로 추측·수정하지 않는다. 전송 직전에 시간이 만료됐으면 확실히 미전송인 요청만 완료 처리하되 모델 비용은 한 윈도 전체 동안 유지한다. 완료가 늦어진 응답도 비용은 유지하고 호출자에게 성공으로 넘기지 않는다. bookTicker 헤더는 권위 있는 weight 증거로 삼지 않으며 문서 기준 비용은 기록한다.

인스턴스마다 최대 48회·75초를 제한하고, 독립 인스턴스 사이의 용량은 기존 공유 저장소가 예약한다. 외부 환경·전송 함수·제공자·정책·시계·설정 변경을 검사한다. POST/PUT/DELETE는 주문·취소·reduce-only·listen-key를 포함해 모두 예산 쓰기와 네트워크 전에 거부한다. 이 기능을 통해 사용자 계정 주문은 전송하지 않았다.

진단은 로컬 요청 수·모델 비용·완료 여부만 보고한다. 계정 주문 사용량 검증, 실제 IP 용량 예약, 거래소 비용 상한 증명, 금융 증거 검증, 주문·실거래·실행 승인은 모두 false다. 해당 값은 사용자의 다른 프로그램 없음 답변으로 true가 되지 않는다. 임의 응답 본문·URL·서명 쿼리·키·DB 예외 원문을 진단에 포함하지 않는다.

이번 코드는 개발 브랜치에만 추가했다. 현재 Windows의 소스/저장소/계정에는 배포하거나 호출하지 않았다. 현재 초기 설정 검증은 공유 저널 미사용을 전제로 하므로, 정상 조회 이력을 검증하는 경로는 [감사 연결 단계](RUNTIME_READ_AUDIT_KO.md)에 추가했다. 재시작 정책은 [후속 구현](RUNTIME_READ_RECOVERY_KO.md)에 추가했다. PC 배포와 운영 진입점 연결은 아직 남아 있으므로 현재 PC에서 이 경로를 시작하지 않는다. 실제 주문 전송 연결도 포함하지 않는다. 기존 런타임의 실제 실행 기본 차단은 유지했다.

검증: 격리 PostgreSQL·실제 SQLite·합성 HTTP에서 새 테스트 41개 통과. 실제 네트워크 전송은 사용하지 않았다. 기본 차단, 예약 후 전송, 독립 체크포인트 완료, 응답/커밋 유실, 네트워크 전송 문맥, 제한·오류·중복 메타데이터, 별도 reader의 공유 여유 중복 소비 방지, SDK 연결, 늦은 증거 및 설정 변화 차단을 확인했다. Ruff와 변경 모듈의 strict mypy 검사도 통과했다. 테스트 위치는 `tests/integration/test_runtime_read_accounting.py`다. 관련 공유 예산·체크포인트·생애주기·요청/비용 계약 회귀 검사는 신규 41개를 포함해 총 254개가 통과했다(250.61초).

```sh
PYTHONPATH="$PWD" TEST_DATABASE_URL=postgresql+psycopg://dev:dev@127.0.0.1:55435/hybrid pytest -q tests/integration/test_runtime_read_accounting.py
mypy services/execution/demo_runtime_read_transport.py --follow-imports=silent
```
