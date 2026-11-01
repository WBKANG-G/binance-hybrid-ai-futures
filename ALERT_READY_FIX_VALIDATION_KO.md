# 준비 상태 revision 불일치 수정 검증

부모는 windows-demo-urgent-alert-queue-20261101 / dfe9fe4, ZIP SHA256 24ef56abeadc9e78d7af007c9ad3c7befff869c2b75ca9f1845ccc150c6febb8이다. 사용자 캡처는 이미지 빌드 성공, db/key-session Healthy, api unhealthy와 alerts 의존 시작 실패를 보였다. 코드에서 준비 상태 판정이0003_demo에 고정된 반면 현재 head는0004_demo_urgent_alerts인 결함을 확인했다. 실제 Windows DB 로그를 별도 수집한 것은 아니다.

부모463개 파일 중 health.py 하나만 수정하고 나머지462개를 바이트 동일하게 보존한다. 새 테스트2개 파일과 한국어 안내/검증2개 파일을 추가한다. 데이터 migration/Compose/키·이메일 설정/SMTP worker/위험 정책/SDK/리스/펜스/exports는 이번 수정에서 변경하지 않는다.

변경은 EXPECTED_SCHEMA_REVISION=0004_demo_urgent_alerts이며 DB probe가 이 값만 허용하게 한다. 새 단위 시험은 이 값과 현재 Alembic script head가 일치하는지 확인한다. 미래 head가 바뀌고 준비 검사가 누락되면 시험이 실패한다.

실제 격리 PostgreSQL과 FastAPI TestClient로 현재0004에서 /api/v1/system/ready HTTP200/database ready/주문·LIVE false를 확인했다. 장부가 있는0003에서는503, 원래 head로 업그레이드한 뒤200 및 장부 완전 일치를 확인했다. 알 수 없는/누락/복수 revision은 모두503을 유지한다. 기존 API 실패 비밀값 차단과 이메일 큐·TLS SMTP·RAM 암호·worker 검증도 다시 실행했다.

이번 수정 검증은63개 통과(11.44초), Ruff 검사/포맷 검사 및 mypy121개 소스 통과다. 부모 버전의 전체1339개 통과는 부모 당시 결과이며 이번 소규모 health 수정 후 전체 suite를 다시 실행했다고 주장하지 않는다. 이번 변경과 직접 관련된 API/DB migration/이메일 경로63개를 검증했다. 기존 테스트는 약화하거나 삭제하지 않았다.

실제 Windows에서 수정 빌드 및 Healthy, 실제 네이버 SMTP 인증/수신은 아직 확인 전이다. 클라우드에서는 실제 사용자 키·메일 인증·외부 전송을 사용하지 않았다. API unhealthy 문제를 메일 비밀번호 문제로 단정하지 않으며 앱 비밀번호를 채팅에 요구하지 않는다. main과 기존 금융 이력·기준금을 유지한다.
