# 취소 주문 노출 정정 영수증과 수량 대조

사용자는 저장된 EXIT/STOP_LOSS 표를 제공한 뒤 다음 단계 진행을 요청했다.
표에서 취소된 로컬 진입에 뒤늦은 OPEN_UNPROTECTED가 기록되고 종료/손절 intent/effect 후
각각 SUBMITTING/UNKNOWN이 남은 채 10월23일 노출 정정으로 CLOSED가 기록된 것을 확인했다.
수량 열은 화면에서 보이지 않아 아직 정정 전후 실제 수치를 확인했다고 처리하지 않는다.

services/execution/demo_cancelled_correction.py의 apply는 원래 주문 객체를 영수증 original_orders에
보존한 뒤 open_quantity=0, actual_risk=0, position_state=CLOSED 세 필드만 변경한다.
진입 CANCELED/filled=0/exit UNKNOWN 및 요청 map은 유지하도록 작성되어 있다.
이번 명령은 그 코드 계약과 보존된 주문·영수증의 실제 필드를 대조하는 읽기만 수행한다.

고정 manifest pin과 runtime.tar hash를 검사하고 유일한 adoption intent의 old_row.state만 메모리로 읽는다.
주문번호를 수동 전사하지 않고, 유일한 SUBMITTING 요청에 exit_id로 연결된 주문을 찾고
같은 주문의 algo_id 요청이 UNKNOWN이며 BTCUSDT/DEMO인지 확인한다.
정정 영수증 original_orders의 key/client_id와 현재 보존 주문이 연결된 유일한 기록을 요구한다.
작업 marker와 review/evidence/fingerprint hash 형식만 확인하며 이 해시를 독립 증명으로 replay하지 않는다.
원문 Client ID·키·원장/영수증 전체·hash token은 출력하지 않는다.

Before는 정정 영수증에 남은 원래 주문, After는 날짜13 채택 이전 스냅샷의 주문이다.
After가 현재 원본 DB 상태를 뜻하지 않는다. 현재 기록의 메모리 복사본에서 허용된 세 필드만
되돌려 Before와 나머지 JSON 필드가 같은지 계산한다. 원래 메모리 객체 및 파일은 바꾸지 않는다.
OtherOrderFieldsUnchanged는 해당 보존 주문 비교 값이며 전체 금융 원장의 전후 검증이 아니다.
작업/요청 상태·서비스·거래소·기준값을 변경하지 않는다. 디스크 압축 해제·복구·새 기동도 없다.

합성 archive + Linux PowerShell7/native tar 검증7개가 통과했다: 정상, 허용 외 필드 변경의 false 표시,
중복 주문, 중복 영수증, 잘못된 marker, 잘못된 hash 형식, archive 변경.
전 파일 SHA256/mtime 유지 및 원문 비노출을 확인했고 메모리 복사로 인해 After 수량이 바뀌지 않는지 검사했다.
테스트에서만 합성 manifest pin 및 tar.exe 이름을 Linux tar로 대체했다. 실제 Windows5.1 사용자
보고서/보존 archive에는 클라우드가 접근하지 않았으며 사용자 출력 전까지 실제 수량은 미확인이다.

실제 실행 명령(한 줄):

```cmd
powershell -NoProfile -Command "$ErrorActionPreference='Stop'; $base=Join-Path $env:LOCALAPPDATA 'WBKANG-DEMO-Backups'; $files=@(Get-ChildItem -LiteralPath $base -Directory -Filter 'cold-*'|ForEach-Object {Get-ChildItem -LiteralPath $_.FullName -File -Filter 'manifest.json'}); if($files.Count -ne 1){throw 'BACKUP_NOT_UNIQUE'}; $f=$files[0]; if((Get-FileHash -LiteralPath $f.FullName -Algorithm SHA256).Hash.ToLowerInvariant() -cne '2a2af624a12c696868ec10ce2522a97b29c2824fe6571dc232840f22db3bdd9d'){throw 'MANIFEST_HASH_MISMATCH'}; $m=Get-Content -Raw -LiteralPath $f.FullName|ConvertFrom-Json; $a=Join-Path $f.DirectoryName 'runtime.tar'; if((Get-FileHash -LiteralPath $a -Algorithm SHA256).Hash.ToLowerInvariant() -cne $m.groups.runtime.archive_sha256){throw 'ARCHIVE_HASH_MISMATCH'}; $entries=@($m.groups.runtime.entries|Where-Object {$_.kind -ceq 'file' -and $_.path -cmatch '^demo-runtime-20261111/accounted-day13-adoption-[0-9a-f-]{36}/intent\.json$' -and $_.size -le 1048576}); if($entries.Count -ne 1){throw 'INTENT_NOT_UNIQUE'}; $t=(& tar.exe -xOf $a -- $entries[0].path|Out-String); if($LASTEXITCODE -ne 0){throw 'ARCHIVE_READ_FAILED'}; $j=$t|ConvertFrom-Json; if($j.policy -cne 'OPERATOR_ATTESTED_ACCOUNTED_DAY13_ADOPTION_V1' -or $j.old_row.account_id -cne 'DEMO-WINDOWS-ISOLATED-BTC-V1'){throw 'ACCOUNT_SCOPE_INVALID'}; $s=$j.old_row.state; $exits=@($s.requests.PSObject.Properties|Where-Object {$_.Value -ceq 'SUBMITTING'});if($exits.Count -ne 1){throw 'EXIT_REQUEST_NOT_UNIQUE'};$orders=@($s.orders.PSObject.Properties|Where-Object {$_.Value.exit_id -ceq $exits[0].Name}); if($orders.Count -ne 1){throw 'ORDER_NOT_UNIQUE'}; $now=$orders[0].Value;if($now.signal.symbol -cne 'BTCUSDT' -or $now.signal.environment -cne 'DEMO' -or $s.requests.PSObject.Properties[$now.algo_id].Value -cne 'UNKNOWN'){throw 'ORDER_SCOPE_INVALID'}; $found=@(); foreach($c in $s.cancelled_exposure_corrections.PSObject.Properties){foreach($o in $c.Value.original_orders.PSObject.Properties){if($o.Value.client_id -ceq $now.client_id -and $o.Name -ceq $orders[0].Name){$found+=,[pscustomobject]@{key=$c.Name;receipt=$c.Value;before=$o.Value}}}}; if($found.Count -ne 1){throw 'CORRECTION_NOT_UNIQUE'}; $c=$found[0]; $hashes=@($c.receipt.review_token,$c.receipt.evidence_hash,$c.receipt.exchange_fingerprint); if($s.operations.PSObject.Properties[$c.key].Value -cne 'CORRECT_CANCELLED_UNFILLED_EXPOSURE' -or @($hashes|Where-Object {$_ -cnotmatch '^[0-9a-f]{64}$'}).Count -ne 0){throw 'CORRECTION_RECEIPT_INVALID'}; $before=$c.before; $copy=$now.PSObject.Copy(); $copy.open_quantity=$before.open_quantity; $copy.actual_risk=$before.actual_risk; $copy.position_state=$before.position_state; $same=(($copy|ConvertTo-Json -Depth 24 -Compress) -ceq ($before|ConvertTo-Json -Depth 24 -Compress)); Write-Output ('CorrectedAtUTC='+$c.receipt.corrected_at); Write-Output ('OtherOrderFieldsUnchanged='+$same); $rows=@(foreach($name in @('state','position_state','exit_state','quantity','filled','remaining','open_quantity','actual_risk','protected')){[pscustomobject]@{Field=$name;Before=$before.$name;After=$now.$name}});$rows|Format-Table -AutoSize; Write-Output ('StoredExitRequest='+$s.requests.PSObject.Properties[$now.exit_id].Value); Write-Output ('StoredStopRequest='+$s.requests.PSObject.Properties[$now.algo_id].Value)"
```
