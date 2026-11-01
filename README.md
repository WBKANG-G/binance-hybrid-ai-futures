# 이메일 버전 API unhealthy 수정

원인은 DB head0004_demo_urgent_alerts에 비해 API 준비 검사가0003_demo에 고정돼 있던 코드 누락입니다. 현재0004만 정확히 허용하며 이전/누락/알 수 없는/복수 revision과 DB 장애는 계속503으로 차단합니다. 메일 비밀번호 문제나 이미지 빌드 실패와 구분합니다.

새 windows-demo-alert-ready-fix 폴더에 압축을 풀고 같은 hybrid-windows 프로젝트로 up -d --build --wait를 실행합니다. 기존 DB/export/RAM 키·메일 설정을 유지합니다. api/db/web/key-session/alerts Healthy를 확인한 뒤 이전 이메일 설정/단일 self-test를 계속합니다. 자세한 명령은 ALERT_READY_FIX_START_HERE_KO.md를 참고합니다.

기존463개 파일 중 health.py만 변경했고 나머지462개는 그대로입니다. 새 회귀 테스트2개 파일과 안내/검증2개를 추가했습니다. 실제 PG/FastAPI에서 현재 head200,0003은503→업그레이드200/장부 불변, 잘못된 revision503을 확인했습니다. API·DB migration·이메일 경로63개 통과와 Ruff/mypy121개 소스 통과입니다. 부모의 전체1339개 통과는 부모 당시 결과입니다.

이번 수정은 금융/주문/SLTP/키/SMTP/migration/Compose/리스/펜스/exports를 변경하지 않습니다. 실제 Windows Healthy와 네이버 받은메일함 도착은 아직 확인 전입니다. 클라우드 실제 키/메일 인증/외부 전송은 없었습니다. main/LIVE/Phase5와 Phase4 전체 완료 판정을 변경하지 않습니다.
