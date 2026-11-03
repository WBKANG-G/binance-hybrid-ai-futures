# Windows 격리 만료 처리 검사

사용자의 운영 API 수정 적용은 Healthy, EXPIRY_WAIT_FIX_SOURCE_VERIFIED 및 READ_ONLY_READY_ENTRIES_STILL_PAUSED로 확인됐다. 일간2026-11-03/주간2026-11-02 일치, NORMAL/paused, 원격 empty, 준비 검사 DB 변경 없음, 주문/LIVE 비활성이다. 실제 TP 미통과는 유지한다.

이 단계는 새 주문 없이 Windows Docker에서 합성96개를 재현한다. 이전13개 장애 검사를 위해 만든 hybrid-phase4-fault-check:local 이미지와 PostgreSQL 이미지를 재사용한다. 이미지가 없거나 원본273개 파일 해시가 다르면 중단하며 자동으로 재빌드/풀하지 않는다. 운영 API의 수정 소스는 변경하지 않는다.

ZIP을 별도 새 폴더에 풀고 RUN-ISOLATED-EXPIRY-CHECK.cmd와 docker-compose.expiry-check.yml이 보이는 폴더에서 CMD를 열어 다음을 실행한다. 기존 소스/infra 폴더를 찾거나 옮길 필요가 없다.

```bat
RUN-ISOLATED-EXPIRY-CHECK.cmd
```

전체 결과를 공유한다. ISOLATED_SYNTHETIC_EXPIRY_CHECK_PASSED와 collected/passed96, failed/skipped0이 수락 조건이다. 별도 hybrid-phase4-expiry-check 프로젝트만 사용하고 완료/실패 후 해당 임시 DB와 네트워크를 정리한다. Ctrl+C/창 닫기로 중단한 경우 이 폴더에서 다음으로 해당 자원만 정리한다.

```bat
docker compose -p hybrid-phase4-expiry-check -f docker-compose.expiry-check.yml down
```

키를 입력하지 않는다. 내부 네트워크, DB tmpfs, 외부 포트/운영 볼륨/키 마운트 없음, 읽기 전용 검사 컨테이너 및 임시 /tmp 소스 복사본을 사용한다. 수정 stream과 신규 시험2개를 내장해 복사본에만 적용한다. 기존 이미지/운영 API/DB/키/이력/기준금은 변경하지 않는다. 실제 거래소 호출0, 실제 원문 WSS 재생/실계좌 검증 아님. 신호 유효기간10초 및 수량/보호 정책 유지. 정확한 거래소 취소 완료 시한이나 실제 TP 체결을 증명하지 않으며 Phase4 전체 완료/LIVE/Phase5로 확대하지 않는다.
