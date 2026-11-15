# 콘솔 중첩 JSON 표시와 저장 영수증 검토

사용자 화면에서 Windows 합성 증거 경로의 성공을 확인했다. 기준 파일 89개·후보 10개, 감사 55→56→58건, 합성 sender 1회와 실제 거래소 HTTP/주문 0건이 보고됐다. 원장·34개 주문·미해결 요청 4개·저장 일자 2026-11-13 보존, 백업 해시 유지, 원본 미시작 및 새 복사본 정지가 함께 보고됐다. 이는 사용자 콘솔 관측이며 새로운 원격 Windows 도구 접근은 아니다.

화면의 content_review가 `@{...}` 문자열이 된 원인은 콘솔 성공 출력의 `ConvertTo-Json -Compress`다. 기본 깊이 2가 diagnostic → synthetic_evidence_rehearsal → content_review를 모두 객체로 담지 못했다. remaining_requirements도 해당 객체의 문자열 표현 안에서 System.Object[]로 보였다. 반면 같은 ZIP의 COMMON.ps1에 있는 Write-BackupJson은 깊이 16으로 저장한다.

이미 게시된 ZIP을 그대로 읽어 실제 PowerShell에서 이 차이를 재현했다. 과거 콘솔은 문자열, 저장 경로는 객체와 8개 원소의 배열을 보존했다. 생성기의 앞으로 사용할 콘솔 성공 출력만 명시적 깊이 16으로 수정했고 객체 보존을 확인했다. 과거 ZIP과 사용자 영수증을 수정하거나 합성 실행기를 다시 실행하지 않았다. 실제 사용자 저장 파일이 정상인지는 별도의 읽기 검사로 확인한다.

`windows-demo-evidence-receipt-review-20261115.zip`은 기존 COPY_EVIDENCE_RECEIPT.json, COPY_CLOCK_RESULT.json, COPY_EVIDENCE_RESULT.json만 읽는 별도 검사기다. 대상 실행기의 고정된 코드 참조, 기존 시계/진단/백업 해시 계보, 완료/권한 플래그와 예상 감사 수를 확인하고 content_review의 객체 타입, remaining_requirements의 정확한 배열 항목, 세 파일의 중첩 데이터 일치를 검사한다. 숫자는 정수 타입, 권한 플래그는 boolean 타입을 요구한다. 파일 크기·중복 후보·재분석 지점도 제한한다. 읽기 핸들을 유지한 채 해시를 다시 확인하고 파일을 수정하지 않는다.

이 검사기는 Docker를 사용하지 않으며 서비스 시작·새 복사본 생성·SQLite/DB 접근·재조회·수리·정규화 저장을 하지 않는다. 콘솔 문자열에서 값을 복구하지 않는다. 저장 파일이 문자열이거나 손상·불일치 상태이면 실패하고 원본을 보존한다. 출력의 receipt_sha256은 현재 읽은 파일의 해시다. 아직 텍스트로 고정하지 않은 외부 영수증 해시와의 대조 완료, 원격 계좌 신원, 현재 컨테이너 상태 또는 거래소 응답 진위를 주장하지 않는다.

관련 검사 40개가 통과했다(18.83초). 최종 읽기 검사 ZIP에서도 16개를 다시 실행해 통과했다(8.98초). 나머지 24개는 기존 실행기 생성·전달 파일 검사 회귀 검증이며 중복 실행을 새 사례로 집계하지 않았다. 정상 검사에서 합성 대상 파일의 내용 해시와 수정 시각 보존을 확인했고, 저장 문자열·잘못된 배열·권한 플래그·숫자 타입·계보·동반 파일 불일치·누락·중복·링크·크기 초과·JSON 오류를 거부했다. ruff도 통과했다. PowerShell의 자동 변수 Matches와 대소문자 구분 없이 충돌하던 로컬 후보 변수명은 테스트에서 발견해 고쳤으며 테스트를 약화하지 않았다.

소스는 `f7da922`이며 ZIP SHA256은 `34a923f89654f2e6302ba7b7d26f1c16745226b9cd3a33ef719899385bb20c48`다. SOURCE_MANIFEST.json 및 VALIDATION.json에 상세 범위를 기록했다. 실제 Windows 저장 영수증 검사는 아직 수행하지 않았다.

사용자는 새 ZIP을 바탕화면에 풀고 RUN_RECEIPT_CHECK.cmd만 실행한다. 기존 RUN_EVIDENCE_REHEARSAL.cmd는 실행하지 않는다. USB 분리 및 기존 컨테이너 정지를 유지한다. 성공 표시는 EVIDENCE_RECEIPT_STRUCTURE_VERIFIED_NO_WRITES_NO_DOCKER다. 출력 JSON을 검토하기 전 실제 거래소 조회나 날짜 채택으로 넘어가지 않는다.
