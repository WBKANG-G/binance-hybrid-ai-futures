# 저장된 손절·종료 이벤트 표 읽기

사용자는 이벤트 복사본 검토 성공 화면 후 다음 단계 진행을 요청했다.
추가 Docker 기동·복구·다운로드 없이 이미 생성된 보고서2개만 읽는다.
최대32개의 copy-event-review-20261116-* 폴더에서 고정 제작 소스의 성공 영수증이 정확히 하나여야 한다.
완료 영수증의 정지/원본 미기동/입력 해시 재검사 값을 확인하고, 동반 EVENT_TIMELINES의 요약과
계정 fingerprint를 대조한다. 원본 생성기의 private receipt는 요약을, EVENT_TIMELINES는 전체
projection을 담는다. summary에 뒤늦게 추가된 full_timelines_saved_in_private_report만 제외해 비교한다.
각 파일1MiB, 선택 요청EXIT/STOP_LOSS 각1, 요청별 전이 개수1~128과 저장 요약의 일치를 요구한다.
시간·종류·요청/주문/포지션/종료 상태·체결량·열린 수량만 출력한다. 과거 ABSENT 행의 선택 필드는
비어 있을 수 있으므로 안전한 선택 필드 접근을 사용한다. UTC는 저장된 표준화 시각이며 실제 wire
실행 순서로 승격하지 않는다. 파일/상태 변경·서비스 기동·재전송은 없다.

이 비교는 로컬 동반 기록의 일치 검사다. 콘솔 사진의 긴 해시를 임의 전사해 외부 pin으로 쓰지 않고,
독립 외부 보관/원본 DB/감사 체인 검증으로 처리하지 않는다. 현재 원본 및 기존 복구 사본 정지,
USB 분리를 유지한다. 실제 요청 해소 또는 NOT_SENT 판정은 자동 수행하지 않는다.

Linux PowerShell7 합성 파일 검증 5개(선택 필드가 없는 ABSENT 정상 행, 중복 폴더, 요약 불일치,
fingerprint 불일치, 전이 누락)가 통과했다. 모든 파일 SHA256/mtime가 유지됐다. 실제 Windows5.1
보고서 읽기는 사용자 실행 전이며 클라우드에서 실제 사용자 보고서에 접근하지 않았다.

사용자 명령(전체를 한 줄로 실행):

```cmd
powershell -NoProfile -Command "$ErrorActionPreference='Stop'; function F($o,$n){$p=$o.PSObject.Properties[$n];if($null -ne $p){return $p.Value};return $null}; $base=Join-Path $env:LOCALAPPDATA 'WBKANG-DEMO-Backups'; $dirs=@(Get-ChildItem -LiteralPath $base -Directory -Filter 'copy-event-review-20261116-*'); if($dirs.Count -gt 32){throw 'REPORT_SEARCH_LIMIT'}; $found=@(); foreach($d in $dirs){$p=Join-Path $d.FullName 'COPY_EVENT_REVIEW_RECEIPT.json';if(Test-Path -LiteralPath $p -PathType Leaf){if((Get-Item -LiteralPath $p).Length -gt 1048576){throw 'REPORT_SIZE_LIMIT'};$r=Get-Content -Raw -LiteralPath $p|ConvertFrom-Json;if($r.source_reference -ceq '68283b4e4d62fc491e26547b2cec28676128cdba' -and $r.isolated_restore -ceq 'COPY_RECOVERED_AND_ACCOUNT_READS_VERIFIED'){$found+=,[pscustomobject]@{dir=$d.FullName;receipt=$r}}}}; if($found.Count -ne 1){throw 'REPORT_NOT_UNIQUE'}; $r=$found[0].receipt; if($r.clone_stopped -ne $true -or $r.original_services_started -ne $false -or $r.input_hashes_reverified -ne $true){throw 'RECEIPT_STATE_INVALID'}; $p=Join-Path $found[0].dir 'EVENT_TIMELINES.json'; if((Get-Item -LiteralPath $p).Length -gt 1048576){throw 'REPORT_SIZE_LIMIT'}; $v=Get-Content -Raw -LiteralPath $p|ConvertFrom-Json; $a=$r.event_transition_diagnostic|Select-Object -Property * -ExcludeProperty full_timelines_saved_in_private_report; if(($a|ConvertTo-Json -Depth 16 -Compress) -cne ($v.summary|ConvertTo-Json -Depth 16 -Compress) -or $v.account_state_fingerprint_sha256 -cne $r.account.account_state_sha256){throw 'COMPANION_MISMATCH'}; $targets=@($v.summary.requests|Where-Object {$_.role -in @('EXIT','STOP_LOSS')}); if($targets.Count -ne 2 -or @($targets|Where-Object {$_.role -ceq 'EXIT'}).Count -ne 1){throw 'REQUEST_SCOPE_INVALID'}; foreach($q in $targets){$prop=$v.projection.request_local_state_timelines.PSObject.Properties[$q.request_reference_sha256];$t=@();if($null -ne $prop){$t=@($prop.Value)};if($t.Count -ne $q.stored_transition_count -or $t.Count -lt 1 -or $t.Count -gt 128){throw 'TIMELINE_COUNT_MISMATCH'}}; foreach($q in $targets){Write-Output ('ROLE='+$q.role);$v.projection.request_local_state_timelines.PSObject.Properties[$q.request_reference_sha256].Value|ForEach-Object {[pscustomobject]@{UTC=$_.as_of_utc;Event=$_.event_kind;Request=$_.local_fields.request_state;Order=(F $_.local_fields 'local_order_state');Pos=(F $_.local_fields 'local_position_state');Exit=(F $_.local_fields 'local_exit_state');Filled=(F $_.local_fields 'filled');Open=(F $_.local_fields 'open_quantity')}}|Format-Table -AutoSize -Wrap}"
```
