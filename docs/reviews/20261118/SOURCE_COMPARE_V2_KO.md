# 복원 영수증을 사용하는 원본 파일 비교 v2

사용자 화면에서 원본 프로젝트 9개가 모두 중지된 것으로 관측됐다. 보존 helper 구성은
원본 DB/runtime RO와 별도 output RW이고, 추가 helper 구성은 고정 백업 bind RO와
별도 demo-restore database/runtime RW였다. 후자는 원본 볼륨에 연결되지 않았다.
이전 비교 도구는 복원 이전의 원본7개+보존helper1개 구성을 재사용하여 남아 있는 복원
helper를 처리하지 못했다. 사용자에게 컨테이너 삭제·재시작을 요구할 이유가 없다.

v2는 기존 고정 성공 복원 영수증 SHA256과 연결된 post-restore scope guard를 재사용한다.
원본6개 저장 snapshot+key-session, 보존helper, 복원helper, 사본DB의 정확한 ID/역할/이미지/
mount/중지/재시작no/networknone/사본DB 정상 종료와 외부 원본 볼륨 소비자 부재를 검사한다.
프로젝트 label이 있는 helper도 영수증 ID로만 식별한다. 임의 이름·개수만 허용하지 않는다.
복원 /bundle bind Source는 고정 백업 폴더와 추가 대조한다. 영수증은 읽기 공유 스트림으로
유지하고 종료 시 닫는다. 사용자 실제 파일에서 영수증 검증이 완료된 것은 아직 아니다.

기존 물리 비교 worker·원본 마운트 RO·새 helper admission·두 번 전체 파일 비교·전후 상태
검사는 그대로 고정된다. 원본 구성 검사를 무시하거나 원본 컨테이너를 새로 시작하지 않는다.
새 패키지 RUN_CHECK 성공 후에만 RUN_COMPARE 한 번을 실행하도록 안내한다. 실패하면
비교를 진행하지 않는다. USB/외부 이관 및 고객지원/키/API 요청은 없다.

이 단계는 byte/metadata 동일성 비교다. SQL 원장 행 조회, 금융 diff 확정, 자정 평가액 검증,
기간 기준금액 채택, 미확정 요청 해소 및 거래 재개를 수행하지 않는다.
