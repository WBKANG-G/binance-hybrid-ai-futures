# 미사용 시계 복구·배포 검토 묶음

앞선 `SHARED_CLOCK_OR_BOOT_CHANGED` 차단을 처리하는 명시적 복구 묶음이다. 원장·기간 기준금액을 보존하고, 미사용 조건을 확인한 저널의 시계 메타데이터와 독립 체크포인트만 변경한다. 원본 백업을 먼저 보존한다. 사용 이력·pending·stopped·감사 바인딩·기간 불일치가 있으면 변경하지 않는다. 시계가 유효하면 무변경으로 확인한다.

- 후보 소스: `bef6e43c2f7207a342fa6029ea34994070d1224c`
- ZIP: `windows-demo-unused-clock-review-20261112.zip`
- SHA256: `62a1a8e3aae3b956d5a74a594f3f2f67d0b91ec75a23392c60218c228791a677`

바탕화면에 압축 해제하고 CMD에서 실행한다.

```cmd
cd /d "%USERPROFILE%\Desktop\windows-demo-unused-clock-review-20261112"
RUN_RECOVER.cmd
```

약 70초 대기 후 감사 연결 계획을 자동 확인한다. 대기 중 상태 출력은 별도 명령으로 입력하지 않는다. 최종 성공은 `UNUSED_CLOCK_RECOVERED_CANDIDATE_PLAN_VERIFIED_KEEP_PAUSED`다. API 교체·감사 테이블 생성·주문 활성화는 하지 않는다. 실패 시 출력 전체로 원인을 검토하며 자동 재복구하지 않는다.

관련 검사 87개 및 기존 Docker 이미지의 실제 ZIP 실행 검증을 통과했다. 합성 계정에서 35초 시점 대기 유지, 75.217초에 대기 종료와 계획 검증 완료를 확인했다. 금융 원장·이벤트·outbox·기간·설치 소스가 보존되고 독립 자원 체크포인트 1개만 추가됐으며 원본 저널 백업이 정확히 일치했다. 사용자 PC 실행·실제 거래소 호출은 하지 않았다. PowerShell 검사는 Linux PowerShell 7에서 수행했고 실제 Windows CMD/PowerShell 5.1 결과는 사용자 출력으로 확인한다.
