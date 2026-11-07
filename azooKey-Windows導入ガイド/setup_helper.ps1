# azooKey-Windows (v0.1.0-alpha1) を使えるようにする補助スクリプト
#   セットアップ補助.bat から呼ばれる。やめるとき.bat からは -Uninstall 付きで呼ばれる。
#
# 行うこと (セットアップ):
#   1. azooKey が入っているか、スマートアプリコントロールがオフかを確かめる
#   2. 日本語のキーボード一覧に azooKey を追加する (このユーザーだけ。管理者権限は不要)
#   3. Zenzai (AIの変換) をオンにする (計算方式は今の設定のまま。初期値は CPU)
#   4. ログイン時に、変換エンジンを管理者として自動で起動するタスクを登録する (許可画面が1回出る)
#   5. その場でエンジンを起動し直して、動いているか確かめる
#
# -DryRun を付けると、確認だけして何も変更しない。
param([switch]$Uninstall, [switch]$DryRun)

$ErrorActionPreference = "Stop"
$TaskName = "Azookey Engine"
$Dir = Join-Path $env:APPDATA "Azookey"
$User = "$env:USERDOMAIN\$env:USERNAME"     # 許可画面より前に決めておく (別の管理者で許可しても、このユーザーで動かす)
$Log = "C:\Users\Public\azookey_helper.log"

function Say($m) { Write-Host $m }
function Step($n, $m) { Write-Host ""; Write-Host "[$n] $m" -ForegroundColor Cyan }
function Fail($m) { Write-Host ""; Write-Host $m -ForegroundColor Yellow; exit 1 }

function Find-AzookeyTip {
    # Windows に登録された入力方式の中から、azooKey の DLL を指すものを探す
    $root = "HKLM:\SOFTWARE\Microsoft\CTF\TIP"
    foreach ($k in Get-ChildItem $root -ErrorAction SilentlyContinue) {
        $clsid = $k.PSChildName
        $dll = (Get-ItemProperty "Registry::HKEY_CLASSES_ROOT\CLSID\$clsid\InprocServer32" -ErrorAction SilentlyContinue).'(default)'
        if ($dll -and $dll -like "*\Azookey\*") {
            $prof = Get-ChildItem "$root\$clsid\LanguageProfile\0x00000411" -ErrorAction SilentlyContinue | Select-Object -First 1
            if ($prof) { return "0411:$clsid$($prof.PSChildName)" }
        }
    }
    return $null
}

function Invoke-Elevated($script) {
    # 管理者の処理は、命令を直接渡して実行する (スクリプトファイルを読ませない)
    $enc = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($script))
    Remove-Item $Log -ErrorAction SilentlyContinue
    try {
        Start-Process powershell.exe -Verb RunAs -Wait -WindowStyle Hidden `
            -ArgumentList "-NoProfile -ExecutionPolicy Bypass -EncodedCommand $enc"
    } catch {
        Fail "管理者の許可が得られませんでした。もう一度実行して、許可画面で「はい」を押してください。"
    }
    if (Test-Path $Log) {
        $out = Get-Content $Log -Encoding UTF8
        Remove-Item $Log -ErrorAction SilentlyContinue
        return $out
    }
    return @()
}

# ---------------------------------------------------------------- やめるとき
if ($Uninstall) {
    Step 1 "キーボード一覧から azooKey を外します"
    $tip = Find-AzookeyTip
    $list = Get-WinUserLanguageList
    $ja = $list | Where-Object { $_.LanguageTag -eq "ja" }
    if ($tip -and $ja -and ($ja.InputMethodTips -contains $tip)) {
        if ($DryRun) { Say "  (確認のみ) 外します: $tip" }
        else { [void]$ja.InputMethodTips.Remove($tip); Set-WinUserLanguageList $list -Force -WarningAction SilentlyContinue; Say "  外しました" }
    } else { Say "  一覧に入っていません" }

    Step 2 "自動起動のタスクを削除し、エンジンを止めます (許可画面で「はい」を押してください)"
    if ($DryRun) { Say "  (確認のみ) タスク '$TaskName' を削除します"; exit 0 }
    $cmd = @"
`$log = '$Log'
try {
  Unregister-ScheduledTask -TaskName '$TaskName' -Confirm:`$false -ErrorAction SilentlyContinue
  Get-Process -Name launcher, azookey-server, ui -ErrorAction SilentlyContinue | Where-Object { `$_.Path -like '$Dir\*' } | Stop-Process -Force
  Add-Content `$log 'ok' -Encoding UTF8
} catch { Add-Content `$log ('error: ' + `$_.Exception.Message) -Encoding UTF8 }
"@
    $r = Invoke-Elevated $cmd
    if ($r -contains "ok") { Say "  削除しました" } else { Fail "  削除できませんでした: $r" }
    Write-Host ""
    Say "最後に「設定」→「アプリ」→「インストールされているアプリ」から、"
    Say "「Azookey バージョン 0.1.0-alpha.1」と「Azookey」をアンインストールしてください。"
    exit 0
}

# ---------------------------------------------------------------- セットアップ
Step 1 "azooKey が入っているか確かめます"
if (-not (Test-Path (Join-Path $Dir "launcher.exe"))) {
    Fail "azooKey が見つかりません ($Dir)。先に azookey-setup.exe でインストールしてください。"
}
Say "  見つかりました: $Dir"

$sac = (Get-ItemProperty "HKLM:\SYSTEM\CurrentControlSet\Control\CI\Policy" -Name VerifiedAndReputablePolicyState -ErrorAction SilentlyContinue).VerifiedAndReputablePolicyState
if ($sac -eq 1) {
    Fail ("スマート アプリ コントロールがオンです。このままでは azooKey の変換エンジンが止められて動きません。`n" +
          "ガイドの「スマート アプリ コントロール」の説明を読み、オフにするかどうかを決めてください。")
}
Say "  スマート アプリ コントロール: オフ"

$tip = Find-AzookeyTip
if (-not $tip) { Fail "azooKey が入力方式として登録されていません。インストールをやり直してください。" }

Step 2 "日本語のキーボード一覧に azooKey を追加します"
$list = Get-WinUserLanguageList
$ja = $list | Where-Object { $_.LanguageTag -eq "ja" }
if (-not $ja) { Fail "言語の一覧に日本語がありません。「設定」→「時刻と言語」→「言語と地域」で日本語を追加してください。" }
if ($ja.InputMethodTips -contains $tip) { Say "  すでに追加されています" }
elseif ($DryRun) { Say "  (確認のみ) 追加します: $tip" }
else { $ja.InputMethodTips.Add($tip); Set-WinUserLanguageList $list -Force -WarningAction SilentlyContinue; Say "  追加しました" }

Step 3 "Zenzai (AIの変換) をオンにします"
$settingsPath = Join-Path $Dir "settings.json"
$settings = if (Test-Path $settingsPath) { Get-Content $settingsPath -Raw | ConvertFrom-Json } else { $null }
if ($settings -and $settings.zenzai) {
    if ($settings.zenzai.enable) { Say "  すでにオンです (計算方式: $($settings.zenzai.backend))" }
    elseif ($DryRun) { Say "  (確認のみ) オンにします (計算方式: $($settings.zenzai.backend))" }
    else {
        Copy-Item $settingsPath "$settingsPath.bak" -Force
        $settings.zenzai.enable = $true
        [IO.File]::WriteAllText($settingsPath, ($settings | ConvertTo-Json -Depth 5), (New-Object Text.UTF8Encoding $false))
        Say "  オンにしました (計算方式: $($settings.zenzai.backend)。元の設定は settings.json.bak)"
    }
} else { Say "  設定ファイルが見つからないので、変更しませんでした" }

Step 4 "ログイン時に自動で起動するよう登録します (許可画面で「はい」を押してください)"
Say "  azooKey の候補窓は、管理者として動かないと起動できない作りです。"
if ($DryRun) { Say "  (確認のみ) タスク '$TaskName' を $User 用に登録し、エンジンを起動し直します"; exit 0 }
$cmd = @"
`$log = '$Log'
function Log(`$m) { Add-Content -Path `$log -Value `$m -Encoding UTF8 }
try {
  `$action = New-ScheduledTaskAction -Execute "`$env:WINDIR\System32\wscript.exe" -Argument '"$Dir\launch.vbs"' -WorkingDirectory '$Dir'
  `$trigger = New-ScheduledTaskTrigger -AtLogOn -User '$User'
  `$principal = New-ScheduledTaskPrincipal -UserId '$User' -LogonType Interactive -RunLevel Highest
  `$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Seconds 0) -MultipleInstances IgnoreNew
  Register-ScheduledTask -TaskName '$TaskName' -Action `$action -Trigger `$trigger -Principal `$principal -Settings `$settings -Description 'azooKey-Windows engine and candidate window' -Force | Out-Null
  Log 'registered'
  Get-Process -Name launcher, azookey-server, ui -ErrorAction SilentlyContinue | Where-Object { `$_.Path -like '$Dir\*' } | Stop-Process -Force
  Start-Sleep 2
  Start-ScheduledTask -TaskName '$TaskName'
  Log 'started'
} catch { Log ('error: ' + `$_.Exception.Message) }
"@
$r = Invoke-Elevated $cmd
if (-not ($r -contains "registered")) { Fail "  登録できませんでした: $r" }
Say "  登録しました"

Step 5 "エンジンが動いているか確かめます"
$ok = $false
for ($i = 0; $i -lt 20; $i++) {
    Start-Sleep 1
    $running = @(Get-Process -Name launcher, azookey-server, ui -ErrorAction SilentlyContinue | ForEach-Object { $_.ProcessName })
    if (($running -contains "azookey-server") -and ($running -contains "ui")) { $ok = $true; break }
}
if (-not $ok) { Fail "  エンジンが起動しませんでした。パソコンを再起動してから、もう一度試してください。" }
Say "  変換エンジンと候補窓が動いています"

Write-Host ""
Write-Host "完了しました。" -ForegroundColor Green
Say "メモ帳などで Windowsキー + Space を押して「Azookey」を選び、ローマ字で打ってみてください。"
Say "英字のまま出るときは、半角/全角 キーで「あ」に切り替えます。"
