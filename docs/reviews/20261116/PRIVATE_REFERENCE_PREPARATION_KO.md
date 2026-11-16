# 최신 참조 해시의 로컬 준비

사용자는 별도 기기·오프라인 보관을 선택했고 D: USB를 사용한 뒤 분리했다. v3 합성 실행과
읽기 전용 영수증 검토까지 성공했으나 최신 참조는 아직 외부 보관되지 않았다. 다음 단계는
거래소 신원/실제 이력 없이 해결 가능한 참조 보관 준비다. 이번 패키지는 USB에 접근하지
않으며 새로운 로컬 전용 폴더에 작은 참조 파일 두 개만 만든다.

이미 성공한 읽기 전용 검토 core는 바이트 그대로 재사용한다. 알려진 producer의 성공 기록이
정확히 하나인지와 동반 네 기록을 검증한 뒤, 다섯 원본을 read sharing으로 다시 열고 검토 때
계산한 해시와 대조한다. 이 handle들을 유지한 채 제한된 필드만 새 REFERENCE_MANIFEST.json에
쓴다. 원본 JSON, 원문, 키, DB/백업 자료나 알 수 없는 추가 필드는 복사하지 않는다. manifest는
코드/원본 참조와 다섯 SHA256, 일부 기록된 검토 사실 및 미해결/권한 false 표시를 포함한다.

대상은 LOCALAPPDATA/WBKANG-DEMO-References/v3-reference-UUID이다. Windows NTFS와 여유
공간을 확인하고 현재 사용자/SYSTEM만 full control을 갖는 protected DACL로 생성한다. 기존
부모 ACL이 다르면 고치지 않고 거부한다. 기존 대상 디렉터리와 파일을 덮어쓰지 않는다.
파일은 32KiB 한도, CreateNew/Write/ShareNone/Flush(true)로 쓰며 다시 읽어 구조와 해시를
검증한다. 원본 source stream과 새 target stream의 길이/해시도 재확인한다. 도중 실패하면
부분 디렉터리를 남기고 자동 삭제·보정·재시도를 하지 않는다. 오류 시 원본에 썼다는 주장과
원본 무결성을 재확인했다는 주장을 구분해 표시한다.

RUN_CHECK는 읽기 전용 source 검사만 한다. RUN_CREATE_REFERENCE는 로컬 파일 두 개를
만드는 명시 모드다. Docker/외부 프로세스/거래소/키 입력/USB 복사/원본·사본 시작/기간 채택이
없다. 결과는 LOCAL_REFERENCE_VERIFIED_EXTERNAL_CUSTODY_PENDING이며 외부 보관 검증이나
복구 백업으로 승격하지 않는다. 실제 근거 8개에 필요한 자료와 조건은 동봉 안내문에 정리했다.

클라우드 PowerShell 시험에서는 새 합성 기록을 사용하며 Windows ACL 생성 부분만 Linux
전용 경로 함수로 대체한다. 따라서 실제 Windows NTFS ACL 검증을 수행한 것으로 표현하지
않는다. 대체하지 않은 Linux 실행에서는 Windows ACL 필요 오류로 실패해야 한다. 원본의
SHA256/mtime, 임의 원본 필드 비유출, 덮어쓰기 거부, source 변경과 readback 실패 시 부분
생성 보존, preflight 무쓰기 및 sealed launcher를 검증한다.

최종 ZIP 검사 18개(13.37초)가 통과했다. 성공한 Windows destination 생성 부분만 Linux용 새 경로 함수로 대체했고, 대체하지 않은 Linux에서는 ACL 필요 오류가 나는 것도 확인했다. 실제 Windows 생성 결과와 외부 보관은 아직 관측하지 않았다.
