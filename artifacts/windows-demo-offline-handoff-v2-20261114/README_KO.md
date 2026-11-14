# D: NTFS/exFAT 외장 매체 복사 및 해시 대조 v2 — 2026-11-14

사용자가 지정한 D:에 보존 파일 6개, 검증된 복구 영수증 1개, 보존/복구 도구 ZIP 2개와 오프라인 검증 스크립트를 복사합니다. 영수증·파일 해시는 사용자 화면에서 확인한 값에 고정합니다. 원본과 복구 사본의 정지·구성을 확인하며 Docker는 메타데이터 조회만 합니다. 원본 컨테이너를 시작하거나 원본/복구 볼륨을 마운트하지 않습니다.

D:는 USB 연결 외장 디스크, 비부팅/비시스템, 쓰기 가능한 NTFS 또는 exFAT여야 합니다. 내장 파티션·네트워크·부팅 디스크·지원하지 않는 파일시스템은 검사 단계에서 중단합니다. 포맷·변환·파티션 변경·잠금 해제는 수행하지 않습니다. 다른 조건이라면 콘솔 결과로 확인한 뒤 별도 검토합니다. 기존 드라이브의 파일과 ACL은 바꾸지 않습니다.

바탕화면에 압축을 풀고 CMD:

```bat
cd /d "%USERPROFILE%\Desktop\windows-demo-offline-handoff-v2-20261114"
RUN_CHECK.cmd
```

`OFFLINE_HANDOFF_CHECK_PASSED_NO_COPY_NO_ORDERS`가 나온 경우에만:

```bat
RUN_HANDOFF.cmd
```

출력 대상은 `D:\WBKANG-DEMO-Offline-20261114-<새 UUID>`입니다. 새 폴더만 만들고 덮어쓰기는 없습니다. NTFS에서는 현재 사용자와 SYSTEM만 접근하도록 권한을 설정하고 확인합니다. exFAT는 이러한 권한을 지원하지 않으므로 ACL 검증을 적용하지 않고 destination_ntfs_acl_verified=false로 기록합니다. 원본 PC 백업 폴더의 NTFS 권한 검사는 그대로 유지합니다. 입력 10개는 검사 동안 읽기 공유로만 엽니다. 파일을 새로 생성하고 Flush(true)로 기록한 다음 SHA256/길이를 확인합니다. 파일마다 외장 디스크·볼륨 정체성을 다시 확인하고 끝에 원본과 복사본 해시, 원본/사본 정지 상태를 재확인합니다.

복사한 10개 외에 OFFLINE_REFERENCE.json, HANDOFF_RECEIPT.json, VERIFY_OFFLINE.cmd을 생성합니다. 전자는 파일별 길이·해시, 보존/복구 영수증의 고정 해시를 포함합니다. 콘솔의 reference_sha256은 USB와 분리된 위치에도 보관하세요. exFAT 복사본에는 NTFS 접근 권한 보호나 파일 암호화가 추가되지 않습니다. 매체를 다른 사람이 접근하지 못하도록 별도로 보관하세요. NTFS의 ACL도 파일 암호화가 아니며, 별도 PC의 Windows 계정에서 읽을 때 관리자 소유권 복구가 필요할 수 있습니다. 다른 PC에서 앱 전체를 복구하는 검사는 이 단계에 포함되지 않습니다.

성공 표시는 `EXTERNAL_COPY_VERIFIED_SAFE_EJECT_AND_SEPARATE_CUSTODY_PENDING_NO_ORDERS`입니다. 연결된 외장 매체의 파일 검증 성공을 뜻합니다. **Windows에서 안전하게 제거한 후 물리적으로 분리해 별도 보관해야 오프라인 보관 단계가 마무리됩니다.** 도구는 자동 꺼내기/분리나 별도 기기의 재검증을 수행했다고 표시하지 않습니다. 별도 Windows PC에서 보관 폴더의 VERIFY_OFFLINE.cmd를 실행하면 Docker·인터넷 없이 참조 파일에 고정된 해시를 재검증할 수 있습니다. 별도로 보관한 reference_sha256과 OFFLINE_REFERENCE.json의 해시도 대조해야 합니다.

실패하면 부분 폴더를 남기며 삭제·자동 재시도·원본 재시작은 하지 않습니다. 콘솔 JSON만 공유하고 실제 백업 파일과 계좌 내용은 업로드하지 마세요. D:를 복사 도중 뽑거나 문자를 바꾸지 마세요. 원본과 복구 사본은 계속 정지 상태로 유지하고 RUN_RESTORE.cmd를 다시 실행하지 마세요.

Linux 시험에서는 Windows 디스크 조회와 NTFS ACL을 대체 함수로만 검증합니다. 실제 D:의 조건·ACL·디스크 분리 상태는 사용자 PC의 결과가 필요합니다. 암호화 설정 변경, 클라우드 전송, 거래소 요청, 주문 쓰기, 설치/거래 활성화는 수행하지 않습니다.

소스 커밋: `de05e4dce4b61dec1f45c7741ac79c49334ff2b1`

ZIP SHA256: `bcf1b99b3c9a040169b4cf6a079985ac9b4b228b90f295e11d8491e3367cc36d`

배포 ZIP 검사: 123개 통과. 사용자 화면에서 D:의 exFAT 형식을 확인했습니다. 실제 복사·물리적 분리 결과는 아직 확인되지 않았습니다.
