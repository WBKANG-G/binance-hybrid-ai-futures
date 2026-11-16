"""Read-only receipt discovery by unique producer reference, without OCR UUID pins."""

import argparse
import hashlib
import json
from pathlib import Path

from scripts.build_demo_read_review_bundle import archive, git, hashes
from scripts.build_windows_copy_transition_bundle import once
from scripts.build_windows_private_receipt_review_bundle import package_files as old_package

FOLDER = "windows-demo-private-receipt-review-v2-20261116"


def package_files(ref):
    revision = git("rev-parse", "--verify", ref + "^{commit}").decode().strip()
    files = {
        p: b
        for p, b in old_package(revision).items()
        if p not in {"TRANSFER_MANIFEST.json", "RUN_RECEIPT_CHECK.cmd"}
    }
    core = files["REVIEW_CORE.ps1"].decode()
    start = core.index("        Receipt-Require ($Config.expected_directory_name")
    end = core.index("        $root=$records[", start)
    core = (
        core[:start]
        + """        Receipt-Require ($Config.expected_directory_prefix -ceq 'copy-private-week-v3-20261116-') 'RECEIPT_CONFIG_INVALID'
        $baseItem=Get-Item -LiteralPath $Base
        Receipt-Require ($baseItem.PSIsContainer -and ($baseItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -eq 0) 'RECEIPT_BASE_INVALID'
        $folders=@(Get-ChildItem -LiteralPath $Base -Directory -Filter ($Config.expected_directory_prefix+'*'))
        Receipt-Require ($folders.Count -le 32) 'RECEIPT_SEARCH_LIMIT'
        $candidates=@()
        foreach ($folder in $folders) {
            Receipt-Require ($folder.Name -cmatch '^copy-private-week-v3-20261116-[0-9a-f]{32}$' -and ($folder.Attributes -band [IO.FileAttributes]::ReparsePoint) -eq 0) 'RECEIPT_DIRECTORY_INVALID'
            $path=Join-Path $folder.FullName 'COPY_PRIVATE_WEEK_V3_RECEIPT.json'
            if ([IO.File]::Exists($path)) {
                $record=Receipt-Open $path;$opened+=,$record
                if ($record.value.source_reference -ceq $Config.expected_source_reference -and $record.value.isolated_restore -ceq 'COPY_RECOVERED_AND_ACCOUNT_READS_VERIFIED') {
                    $candidates+=,[pscustomobject]@{folder=$folder;receipt=$record}
                }
            }
        }
        Receipt-Require ($candidates.Count -eq 1) 'RECEIPT_NOT_UNIQUE'
        $selected=$candidates[0].folder
        $records=@{'COPY_PRIVATE_WEEK_V3_RECEIPT.json'=$candidates[0].receipt}
        foreach ($name in @('PRIVATE_WEEK_RESULT.json','PRIVATE_RESOURCES.json','RESTORE_RESOURCES.json','CLONE_CREATED.json')) {
            $record=Receipt-Open (Join-Path $selected.FullName $name);$opened+=,$record;$records[$name]=$record
        }
"""
        + core[end:]
    )
    core = core.replace(
        "$Config.expected_directory_name.Substring($Config.expected_directory_name.Length-32)",
        "$selected.Name.Substring($selected.Name.Length-32)",
    )
    core = once(
        core,
        "private_receipt_review='V3_STORED_RECEIPT_AND_FOUR_COMPANIONS_VERIFIED';",
        "private_receipt_review='V3_STORED_RECEIPT_AND_FOUR_COMPANIONS_VERIFIED';selected_directory_name=$selected.Name;selection_method='UNIQUE_EXPECTED_PRODUCER_SUCCESS_RECEIPT';",
    )
    files["REVIEW_CORE.ps1"] = core.encode()
    cfg = json.loads(files["CONFIG.json"])
    cfg.pop("expected_directory_name")
    cfg["expected_directory_prefix"] = "copy-private-week-v3-20261116-"
    files["CONFIG.json"] = (json.dumps(cfg, indent=2, sort_keys=True) + "\n").encode()
    files["README_KO.md"] = """# v3 成功 영수증 읽기 전용 검토 v2

CMD에서 RUN_RECEIPT_CHECK.cmd만 실행합니다. 성공 표시는 입력 명령이 아니라 결과로 자동 출력됩니다.
Docker와 원본·사본은 시작하지 않으며 USB는 분리 상태로 둡니다.

폴더 UUID를 화면에서 옮겨 고정하지 않습니다. 16일자 v3 폴더를 최대 32개까지 읽고,
알려진 소스 a70f103b3a5cd764ea43838d9a6e565637dafa32의 성공 영수증이 하나일 때만 선택합니다.
여러 개거나 없으면 중단하며 최신/첫 폴더를 임의 선택하지 않습니다. 선택한 폴더 이름을 출력합니다.

성공 영수증과 동반 기록 4개, 101.004초/Decimal/75초 제한, 기간·카운터·식별자와 승인 범위를
대조하고 파일 길이·해시를 다시 확인합니다. 파일 변경·Docker·키 파일·원문 조회는 없습니다.
현재 컨테이너 상태와 외부 해시 pin은 검증하지 않으며 승인·원격 신원 확인으로 승격하지 않습니다.
출력 전체를 보내주세요.
""".replace("成功", "성공").encode()
    manifest = (json.dumps(dict(files=hashes(files)), indent=2, sort_keys=True) + "\n").encode()
    files["TRANSFER_MANIFEST.json"] = manifest
    files["RUN_RECEIPT_CHECK.cmd"] = (
        '@echo off\r\nsetlocal DisableDelayedExpansion\r\npowershell -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "%~dp0REVIEW.ps1" '
        + f"-ManifestSHA256 {hashlib.sha256(manifest).hexdigest()}\r\nexit /b %errorlevel%\r\n"
    ).encode("ascii")
    return files


def build(ref, output):
    files = package_files(ref)
    output.mkdir(parents=True, exist_ok=True)
    target = output / (FOLDER + ".zip")
    with target.open("xb") as stream:
        stream.write(archive({FOLDER + "/" + p: b for p, b in files.items()}))
    print(
        json.dumps(
            dict(artifact=str(target), sha256=hashlib.sha256(target.read_bytes()).hexdigest())
        )
    )
    return target


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--ref", required=True)
    p.add_argument("--output", type=Path, required=True)
    a = p.parse_args()
    build(a.ref, a.output)
