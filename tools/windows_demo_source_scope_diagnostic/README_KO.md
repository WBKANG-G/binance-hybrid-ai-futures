# 원본 구성 차단 위치 진단

이 패키지는 기존 비교 도구에서 누락한 안전한 scope_probe 출력을 보완한다.
조건을 완화하거나 구성 차이를 보정하지 않는다. RUN_DIAGNOSE.cmd 한 번만 실행한다.
원본 비교 도구의 RUN_CHECK/RUN_COMPARE는 다시 실행하지 않는다.

고정 백업 입력 해시·NTFS ACL을 확인하고 같은 원본 구성 검사 함수를 호출한다.
검사에 막혀도 고정된 step/개수/역할/일치 여부만 화면에 기록한다.
원본 ID·이미지·볼륨 경로·환경변수·응답 원문은 표시하지 않는다.
보조 컨테이너 생성·start·stop·update·rm, DB/API 실행, 원본 파일 쓰기, API 호출은 없다.
새 비교·자동 재시도·기준금액 채택을 수행하지 않는다. 키 입력과 USB 연결은 필요 없다.

SOURCE_SCOPE_DIAGNOSTIC_COMPLETE_NO_CREATE_NO_START_NO_WRITES는 진단 출력 완료 표시다.
failure_category가 있으면 원본 구성 검사가 여전히 차단되었다는 뜻이다.
그 화면을 공유하고 원본은 중지 상태로 유지한다. 임의 컨테이너 정리나 재시작을 하지 않는다.
