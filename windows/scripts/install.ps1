# Copyright (C) 2026 Hideki Ohmura
# SPDX-License-Identifier: AGPL-3.0-only
# 墨消しスタジオ（Sumikeshi Studio）配布用パッケージの導入と更新：導入の種類（自分だけ・この PC のすべてのユーザー）を
# 選び、決まった場所に入れて、アプリの一覧とショートカットに登録する。通信しない。
# 入口の .bat は、今いるフォルダを一時的なフォルダにしてから、このスクリプトを起動する（展開したフォルダを移すため）。
# このスクリプトは展開したフォルダの scripts\ にあり、展開したフォルダ（.bat のあるフォルダ）は -Source で受け取る。
# すべてのユーザー向けでは、管理者権限でこのスクリプトを起動し直す（-Elevated）。管理者権限の段階は、UAC で入力した
# 管理者のアカウントとして動くことがあるので、利用者ごとのこと（サンプルの様式・設定・その利用者のデスクトップ）はしない。
# 問いの答えは引数でも渡せる（-Kind・-Samples。試験で人の操作なしに流すため）。渡さなかった問いは尋ねる。
param(
    [Parameter(Mandatory = $true)][string]$Source,
    [switch]$Elevated,
    [string]$Language = "",
    [string]$WindowsLanguage = "",
    [string]$Kind = "",
    [string]$Samples = "",
    [switch]$NoWait
)
$ErrorActionPreference = "Stop"

$AppDirName = "SumikeshiStudio"
$UninstallKey = "Software\Microsoft\Windows\CurrentVersion\Uninstall\SumikeshiStudio"
$Publisher = "Hideki Ohmura"
$InfoUrl = "https://github.com/hohm-sdx/sumikeshi-studio/releases"
# ショートカットとアプリの一覧が指すアイコンのファイル（導入先からの相対パス。ツールのパッケージの中にある）
$IconFile = "python\Lib\site-packages\sumikeshi_studio\icons\sumikeshi-studio.ico"
$PerUserDir = Join-Path $env:LOCALAPPDATA "Programs\$AppDirName"
$AllUsersDir = Join-Path $env:ProgramFiles $AppDirName
# "C:\x\." の形で渡される（.bat の %~dp0 は \ で終わり、"…\" と渡すと末尾の \" が引用符の文字と読まれるため）
$Source = [IO.Path]::GetFullPath($Source).TrimEnd("\")

# 出す文の言語。ツールと同じ順に決める：環境変数 SUMIKESHI_STUDIO_LANGUAGE、ツールの起動の画面で選んだ言語（設定の
# ファイルの [ui] の language）、Windows の表示の言語。管理者権限の段階は、起動し直す前に決めた言語を受け取る
function Get-SavedLanguage {
    $Ini = Join-Path $env:APPDATA "SumikeshiStudio\SumikeshiStudio.ini"
    try {
        $Section = ""
        foreach ($Line in Get-Content -LiteralPath $Ini -Encoding UTF8 -ErrorAction Stop) {
            $Line = $Line.Trim()
            if ($Line -match '^\[(.*)\]$') { $Section = $Matches[1]; continue }
            if ($Section -eq "ui" -and $Line -match '^language\s*=\s*"?(ja|en)"?$') { return $Matches[1] }
        }
    } catch { }
    return $null
}
$Lang = $Language
if ($Lang -notin @("ja", "en")) { $Lang = $env:SUMIKESHI_STUDIO_LANGUAGE }
if ($Lang -notin @("ja", "en")) { $Lang = Get-SavedLanguage }
if (-not $Lang) { $Lang = if ((Get-UICulture).TwoLetterISOLanguageName -eq "ja") { "ja" } else { "en" } }
function Msg([string]$Ja, [string]$En) { if ($Lang -eq "ja") { return $Ja } return $En }
$env:SUMIKESHI_STUDIO_LANGUAGE = $Lang
# ショートカットとアプリの一覧の名前は、Windows の表示の言語で決める（管理者権限の段階は、起動し直す前の値を受け取る。
# 管理者のアカウントの表示の言語が違うことがあるため）
$WinLang = $WindowsLanguage
if ($WinLang -notin @("ja", "en")) { $WinLang = if ((Get-UICulture).TwoLetterISOLanguageName -eq "ja") { "ja" } else { "en" } }
function WinMsg([string]$Ja, [string]$En) { if ($WinLang -eq "ja") { return $Ja } return $En }
$AppName = WinMsg "墨消しスタジオ" "Sumikeshi Studio"
$AppNames = @("墨消しスタジオ", "Sumikeshi Studio")
$Tell = Msg "この画面のスクリーンショットを撮っておくと、原因の調査に役立つ場合があります。" "A screenshot of this window may help investigate the cause."

# path が folder の下にあるか（大文字と小文字を区別しない）
function Test-Under([string]$Path, [string]$Folder) {
    if (-not $Path -or -not $Folder) { return $false }
    $p = [IO.Path]::GetFullPath($Path)
    $f = [IO.Path]::GetFullPath($Folder).TrimEnd("\") + "\"
    return $p.StartsWith($f, [StringComparison]::OrdinalIgnoreCase)
}

function Test-SamePath([string]$A, [string]$B) {
    if (-not $A -or -not $B) { return $false }
    return [IO.Path]::GetFullPath($A).TrimEnd("\").Equals([IO.Path]::GetFullPath($B).TrimEnd("\"), [StringComparison]::OrdinalIgnoreCase)
}

# path が導入先（とその .old・.new）と同じかその中なら、そのフォルダ。どれでもなければ $null
function Get-InstalledFolder([string]$Path) {
    foreach ($Dir in @($PerUserDir, $AllUsersDir)) {
        foreach ($Folder in @($Dir, "$Dir.old", "$Dir.new")) {
            if ((Test-SamePath $Path $Folder) -or (Test-Under $Path $Folder)) { return $Folder }
        }
    }
    return $null
}

# ネットワーク上のフォルダ（UNC パスか、割り当てたネットワークのドライブ）か
function Test-NetworkPath([string]$Path) {
    if ($Path.StartsWith("\\")) { return $true }
    try { return (New-Object IO.DriveInfo([IO.Path]::GetPathRoot($Path))).DriveType -eq [IO.DriveType]::Network } catch { return $false }
}

function Test-Elevated {
    $Principal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
    return $Principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

# 引数の値が、渡していない（空）か、決まった値のどれか（大文字と小文字を区別しない）か。合わなければ文を出して $false
# （既定の答えに置き換えると、試験で意図と違う導入を流したことに気づけないため）
function Test-Choice([string]$Name, [string]$Value, [string[]]$Allowed) {
    if (-not $Value -or $Value -in $Allowed) { return $true }
    Write-Host ((Msg "引数 -{0} の値（{1}）が決まりに合わないため、中止しました。-{0} は {2} にしてください。" "Stopped because the value of -{0} ({1}) is not valid. Use {2} for -{0}.") -f $Name, $Value, ($Allowed -join (Msg " か " " or ")))
    return $false
}

# スマート アプリ コントロールや会社のアプリケーション制御に止められたか（Windows のエラーの番号 4551）
function Test-Blocked($ErrorRecord) {
    $e = $ErrorRecord.Exception
    while ($e) {
        if ($e -is [System.ComponentModel.Win32Exception] -and $e.NativeErrorCode -eq 4551) { return $true }
        if ($e.Message -match "アプリケーション制御|Application Control") { return $true }
        $e = $e.InnerException
    }
    return $false
}

# 動いているツールのプロセスを起動した人の名前（なければ空）。ツールのプロセスは、実行ファイルが folder の下にある
# python.exe・pythonw.exe。管理者権限がなければ、ほかの利用者のプロセスのパスは読めないので、自分のプロセスだけを見る
function Get-ToolUsers([string]$Folder, [switch]$AllUsers) {
    $Users = @()
    if ($AllUsers) {
        foreach ($p in Get-CimInstance Win32_Process -Filter "Name='pythonw.exe' OR Name='python.exe'") {
            if (-not (Test-Under $p.ExecutablePath $Folder)) { continue }
            $Owner = Invoke-CimMethod -InputObject $p -MethodName GetOwner
            $Users += if ($Owner.User) { "$($Owner.Domain)\$($Owner.User)" } else { "?" }
        }
    } else {
        foreach ($p in Get-Process -Name python, pythonw -ErrorAction SilentlyContinue) {
            if (Test-Under $p.Path $Folder) { $Users += $env:USERNAME }
        }
    }
    return @($Users | Sort-Object -Unique)
}

# 前に途中で止まったときに残ったフォルダ（.new・.old）を消す
function Remove-Leftovers([string]$Dir) {
    foreach ($Path in @("$Dir.new", "$Dir.old")) {
        if (Test-Path -LiteralPath $Path) { Remove-Item -LiteralPath $Path -Recurse -Force }
    }
}

# 展開したフォルダを導入先 Dir に入れる。前の導入は .old に名前を変えてから入れ、失敗したら戻す（前の導入を消すのは、
# 新しいものが入った後だけ）。.old に名前を変えられなければ（使われているファイルがある）、何もせずに $false を返す。
# Move なら移動し（同じボリュームの中の移動は名前の変更になる）、移動に失敗したらコピーに切り替える
function Install-Files([string]$Dir, [bool]$Move) {
    $Old = "$Dir.old"
    $New = "$Dir.new"
    New-Item -ItemType Directory -Force -Path (Split-Path $Dir) | Out-Null
    if ($Move) {
        if (Test-Path -LiteralPath $Dir) {
            try { Rename-Item -LiteralPath $Dir -NewName (Split-Path $Old -Leaf) } catch {
                Write-Host ((Msg "導入先（{0}）のファイルが使われているため、中止しました。ツールやエクスプローラーなど、そのフォルダのファイルを開いているものを閉じてから、もう一度実行してください。" "Stopped because files in {0} are in use. Close the tool and anything else that has files in that folder open, and then run this again.") -f $Dir)
                return $false
            }
        }
        try {
            Move-Item -LiteralPath $Source -Destination $Dir
            return $true
        } catch {
            if (Test-Path -LiteralPath $Old) { Rename-Item -LiteralPath $Old -NewName (Split-Path $Dir -Leaf) }
            Write-Host ((Msg "導入先（{0}）にコピーしています…" "Copying the files to {0}…") -f $Dir)
        }
    }
    Copy-Item -LiteralPath $Source -Destination $New -Recurse
    if (Test-Path -LiteralPath $Dir) {
        try { Rename-Item -LiteralPath $Dir -NewName (Split-Path $Old -Leaf) } catch {
            Remove-Item -LiteralPath $New -Recurse -Force
            Write-Host ((Msg "導入先（{0}）のファイルが使われているため、中止しました。ツールやエクスプローラーなど、そのフォルダのファイルを開いているものを閉じてから、もう一度実行してください。" "Stopped because files in {0} are in use. Close the tool and anything else that has files in that folder open, and then run this again.") -f $Dir)
            return $false
        }
    }
    Rename-Item -LiteralPath $New -NewName (Split-Path $Dir -Leaf)
    return $true
}

# 初回の起動を速くするため、ライブラリとツール本体のコードを中間の形（.pyc）に変換しておく（導入先で行う）。
# 失敗しても、ツールは変換なしで動く
function Invoke-Compile([string]$Dir) {
    Write-Host (Msg "初回の起動を速くする準備をしています（数十秒かかります）…" "Preparing to make the first start faster (this takes tens of seconds)…")
    & (Join-Path $Dir "python\python.exe") -I -W ignore::SyntaxWarning -m compileall -q -j 0 (Join-Path $Dir "python\Lib\site-packages") | Out-Null
    if ($LASTEXITCODE -ne 0) { Write-Host (Msg "準備の一部ができませんでした。ツールは起動できます。" "Part of the preparation could not be done. The tool can still be started.") }
}

# アプリの一覧に登録する（Hive は HKCU か HKLM）
function Register-App([string]$Hive, [string]$Dir, [string]$Name, [string]$Version) {
    $Key = "${Hive}:\$UninstallKey"
    New-Item -Path $Key -Force | Out-Null
    $PowerShell = Join-Path ([Environment]::SystemDirectory) "WindowsPowerShell\v1.0\powershell.exe"
    $Size = [int]((Get-ChildItem -LiteralPath $Dir -Recurse -File | Measure-Object -Property Length -Sum).Sum / 1KB)
    $Values = [ordered]@{
        "DisplayName"     = $Name
        "DisplayVersion"  = $Version
        "Publisher"       = $Publisher
        "InstallLocation" = $Dir
        "InstallDate"     = (Get-Date -Format "yyyyMMdd")
        "DisplayIcon"     = "$(Join-Path $Dir $IconFile),0"
        "UninstallString" = "`"$PowerShell`" -NoProfile -ExecutionPolicy Bypass -File `"$(Join-Path $Dir 'scripts\uninstall.ps1')`""
        "URLInfoAbout"    = $InfoUrl
    }
    foreach ($Item in $Values.GetEnumerator()) {
        New-ItemProperty -Path $Key -Name $Item.Key -Value $Item.Value -PropertyType String -Force | Out-Null
    }
    foreach ($Item in ([ordered]@{ "NoModify" = 1; "NoRepair" = 1; "EstimatedSize" = $Size }).GetEnumerator()) {
        New-ItemProperty -Path $Key -Name $Item.Key -Value $Item.Value -PropertyType DWord -Force | Out-Null
    }
}

# デスクトップとスタートメニューにショートカットを作る（もう一方の言語の名前のものは消す）。前のデスクトップの
# ショートカットが導入先と違う場所を指していれば、その場所（展開した場所で使っていた古いフォルダ）を返す
function New-Shortcuts([string]$Dir, [string]$Desktop, [string]$Programs) {
    $Shell = New-Object -ComObject WScript.Shell
    $Previous = $null
    $DesktopLink = Join-Path $Desktop "$AppName.lnk"
    if (Test-Path -LiteralPath $DesktopLink) {
        $Target = $Shell.CreateShortcut($DesktopLink).TargetPath
        if ($Target -and -not (Test-Under $Target $Dir)) { $Previous = Split-Path (Split-Path $Target) }
    }
    foreach ($Folder in @($Desktop, $Programs)) {
        foreach ($Name in $AppNames | Where-Object { $_ -ne $AppName }) {
            $Other = Join-Path $Folder "$Name.lnk"
            if (Test-Path -LiteralPath $Other) { Remove-Item -LiteralPath $Other }
        }
        New-Item -ItemType Directory -Force -Path $Folder | Out-Null
        $Link = $Shell.CreateShortcut((Join-Path $Folder "$AppName.lnk"))
        $Link.TargetPath = Join-Path $Dir "python\pythonw.exe"
        $Link.Arguments = "-I -m sumikeshi_studio"
        $Link.WorkingDirectory = $Dir
        $Link.IconLocation = "$(Join-Path $Dir $IconFile),0"
        $Link.Description = WinMsg "スキャンした帳票の PDF・画像の、様式で決めた欄を墨消しする" "Redact the fields defined in the forms on scanned PDF and image documents"
        $Link.Save()
    }
    return $Previous
}

function Get-InstalledVersion([string]$Hive) {
    try { return (Get-ItemProperty -Path "${Hive}:\$UninstallKey" -ErrorAction Stop).DisplayVersion } catch { return $null }
}

function Show-Update([string]$Old, [string]$New) {
    if ($Old) { Write-Host ((Msg "バージョン {0} を {1} に更新します。" "Updating version {0} to {1}.") -f $Old, $New) }
}

function Show-Running([string[]]$Users) {
    Write-Host ((Msg "墨消しスタジオが動いているため、中止しました（起動している人：{0}）。ツールを閉じてから、もう一度実行してください。ほかの人がサインインしたまま起動しているときは、閉じてもらうか、サインアウトしてもらってください。" "Stopped because Sumikeshi Studio is running (started by: {0}). Close the tool, and then run this again. If another person left it running while signed in, ask them to close it or sign out.") -f ($Users -join ", "))
}

function Remove-Old([string]$Dir) {
    try { if (Test-Path -LiteralPath "$Dir.old") { Remove-Item -LiteralPath "$Dir.old" -Recurse -Force } } catch {
        Write-Host ((Msg "前の導入のフォルダ（{0}）を削除できませんでした。手で削除してください。" "Could not delete the folder of the previous installation ({0}). Delete it manually.") -f "$Dir.old")
    }
}

# ---------------------------------------------------------------- 自分だけ（管理者権限なし）
function Install-PerUser([string]$Version) {
    $Dir = $PerUserDir
    Remove-Leftovers $Dir
    $Users = Get-ToolUsers $Dir
    if ($Users.Count -gt 0) { Show-Running $Users; return }
    Show-Update (Get-InstalledVersion "HKCU") $Version
    # 同じドライブなら移動、別のドライブ（UNC パスを含む）ならコピーして元を消す
    $Move = (-not $Source.StartsWith("\\")) -and ([IO.Path]::GetPathRoot($Source) -eq [IO.Path]::GetPathRoot($Dir))
    if ($Move) { Write-Host ((Msg "導入先（{0}）に移しています…" "Moving the files to {0}…") -f $Dir) } else { Write-Host ((Msg "導入先（{0}）にコピーしています…" "Copying the files to {0}…") -f $Dir) }
    if (-not (Install-Files $Dir $Move)) { return }
    Invoke-Compile $Dir
    Register-App "HKCU" $Dir (WinMsg "墨消しスタジオ" "Sumikeshi Studio") $Version
    $Previous = New-Shortcuts $Dir ([Environment]::GetFolderPath("Desktop")) ([Environment]::GetFolderPath("Programs"))
    # 様式のフォルダに様式がなければ、サンプルの様式（名前と年分を示す）を入れるかを、この画面で尋ねる（-Samples を
    # 渡したときは尋ねずにその答えにする）
    $FormArguments = @("-I", "-m", "sumikeshi_studio.forminstall", "--years")
    if ($Samples) { $FormArguments += @("--samples", $Samples.ToLower()) }
    $Code = Invoke-Python (Join-Path $Dir "python\python.exe") $FormArguments
    if ($Code -ne 0) { Write-Host ((Msg "サンプルの様式を入れる処理が、終了の番号 {0} で終わりました。導入は続けます。" "The step that adds the sample forms ended with exit code {0}. Setup continues.") -f $Code) }
    Remove-Old $Dir
    if (Test-Path -LiteralPath $Source) {
        try { Remove-Item -LiteralPath $Source -Recurse -Force } catch {
            Write-Host ((Msg "展開したフォルダ（{0}）を削除できませんでした。手で削除してください。" "Could not delete the extracted folder ({0}). Delete it manually.") -f $Source)
        }
    }
    Write-Host ((Msg "導入しました（{0}）。スタートメニューとデスクトップの「{1}」から起動できます。" "Installed in {0}. Start the tool from `"{1}`" on the Start menu or the desktop.") -f $Dir, $AppName)
    # 前の形で使っていたフォルダに新しい ZIP を展開し直して実行したときは、そのフォルダがいま移した（か消した）展開した
    # フォルダなので、もうないフォルダを示さない
    if ($Previous -and (Test-Path -LiteralPath $Previous)) { Write-Host ((Msg "前のデスクトップのショートカットは、{0} を指していました。そのフォルダはもう使わないので、削除してかまいません。" "The previous desktop shortcut pointed to {0}. That folder is no longer used, and you can delete it.") -f $Previous) }
    $script:ExitCode = 0
}

# ---------------------------------------------------------------- すべてのユーザー（管理者権限の段階）
function Install-AllUsers([string]$Version) {
    $Dir = $AllUsersDir
    Remove-Leftovers $Dir
    $Users = Get-ToolUsers $Dir -AllUsers
    if ($Users.Count -gt 0) { Show-Running $Users; return }
    Show-Update (Get-InstalledVersion "HKLM") $Version
    # 移動せずにコピーする（新しく書いたファイルは、Program Files の権限を引き継ぐため）
    Write-Host ((Msg "導入先（{0}）にコピーしています…" "Copying the files to {0}…") -f $Dir)
    if (-not (Install-Files $Dir $false)) { return }
    Invoke-Compile $Dir
    Register-App "HKLM" $Dir (WinMsg "墨消しスタジオ（すべてのユーザー）" "Sumikeshi Studio (all users)") $Version
    New-Shortcuts $Dir ([Environment]::GetFolderPath("CommonDesktopDirectory")) ([Environment]::GetFolderPath("CommonPrograms")) | Out-Null
    Remove-Old $Dir
    $script:ExitCode = 0
}

# 同梱の Python を、同じコンソールで、出力を取り込まずに動かし、終了の番号を返す（問いを出す処理は、出力を取り込むと
# 問いの文が画面に出ないため）
function Invoke-Python([string]$Exe, [string[]]$Arguments) {
    $Process = Start-Process -FilePath $Exe -ArgumentList $Arguments -NoNewWindow -Wait -PassThru
    return $Process.ExitCode
}

# ---------------------------------------------------------------- 始めの確認と、種類の選び方
function Invoke-Main {
    if (-not (Test-Choice "Kind" $Kind @("1", "2"))) { return }
    if (-not (Test-Choice "Samples" $Samples @("y", "n"))) { return }
    if (-not (Test-Choice "Language" $Language @("ja", "en"))) { return }
    if (-not (Test-Choice "WindowsLanguage" $WindowsLanguage @("ja", "en"))) { return }
    # 導入したフォルダの中から実行すると、移すものと入れる先が重なり、入れたばかりのフォルダを消すおそれがある
    if (Get-InstalledFolder $Source) {
        Write-Host ((Msg "導入したフォルダ（{0}）の中からは、導入できません。配布用パッケージの ZIP を展開したフォルダの setup.bat を実行してください。" "Setup cannot be run from the installed folder ({0}). Run setup.bat in the folder where you extracted the ZIP of the package.") -f $Source)
        return
    }
    $Python = Join-Path $Source "python\python.exe"
    if (-not (Test-Path -LiteralPath (Join-Path $Source "python\pythonw.exe"))) {
        Write-Host (Msg "python\pythonw.exe が見つかりません。ZIP をすべて展開してから実行してください" "python\pythonw.exe was not found. Extract all files from the ZIP, and then run this again.")
        return
    }
    # 同梱の Python を起動できるかを確かめる。展開した場所に .pyc を書かない（-B。.pyc の中の元のファイルのパスが、
    # 移す前の場所のままになるため）
    try {
        & $Python -I -B -c "pass"
    } catch {
        if (Test-Blocked $_) {
            Write-Host ((Msg "Windows のスマート アプリ コントロール（または会社のアプリケーション制御）が、同梱の Python（python\python.exe）の起動を止めました。このパソコンでは、このままではツールを使えません。{0}" "Windows Smart App Control (or your organization's application control) blocked the bundled Python (python\python.exe) from starting. The tool cannot be used on this PC as it is. {0}") -f $Tell)
            return
        }
        throw
    }
    if ($LASTEXITCODE -ne 0) {
        Write-Host ((Msg "同梱の Python（python\python.exe）が、終了の番号 {0} で終わりました。ZIP をすべて展開し直してから、もう一度実行してください。{1}" "The bundled Python (python\python.exe) ended with exit code {0}. Extract all files from the ZIP again, and then run this again. {1}") -f $LASTEXITCODE, $Tell)
        return
    }
    $Version = (& $Python -I -B -c "from sumikeshi_studio import __version__; print(__version__)").Trim()
    if ($Elevated) { Install-AllUsers $Version; return }

    Write-Host (Msg "ツールの部品を読み込めるかを確かめています…" "Checking that the components of the tool can be loaded…")
    $Code = Invoke-Python $Python @("-I", "-B", "-m", "sumikeshi_studio.nativecheck")
    if ($Code -eq 20) { return }
    if ($Code -ne 0 -and $Code -ne 10) { Write-Host ((Msg "部品を確かめられませんでした（終了の番号 {0}）。導入は続けます。" "Could not check the components (exit code {0}). Setup continues.") -f $Code) }

    $Choice = $Kind
    while ($Choice -notin @("1", "2")) {
        Write-Host (Msg "導入の種類を選んでください。" "Choose the type of installation.")
        Write-Host (Msg "  1：自分だけ（このアカウントだけが使えます。管理者権限は要りません）" "  1: Just for me (only this account can use it; no administrator rights needed)")
        Write-Host (Msg "  2：この PC のすべてのユーザー（管理者権限が要ります）" "  2: All users of this PC (administrator rights needed)")
        $Choice = (Read-Host (Msg "1 か 2 を入力して Enter を押してください（何も入力せずに Enter を押すと 1 になります）。[1/2]" "Type 1 or 2 and press Enter (pressing Enter alone chooses 1). [1/2]")).Normalize([Text.NormalizationForm]::FormKC).Trim()
        if (-not $Choice) { $Choice = "1" }
    }
    if ($Choice -eq "1") {
        if (Test-Elevated) {
            Write-Host (Msg "管理者として実行されているため、自分だけの導入はできません。setup.bat を、右クリックの「管理者として実行」でなく、ダブルクリックで実行してください。" "Just-for-me installation cannot be done because setup is running as administrator. Run setup.bat by double-clicking it, not with `"Run as administrator`".")
            return
        }
        if ((Test-Path "HKLM:\$UninstallKey") -or (Test-Path -LiteralPath $AllUsersDir)) {
            Write-Host ((Msg "この PC には、すべてのユーザー向けの墨消しスタジオがあります（{0}）。自分だけの導入はしません。すべてのユーザー向けのものを使ってください。" "Sumikeshi Studio for all users is already installed on this PC ({0}). Just-for-me installation is not done. Use the installation for all users.") -f $AllUsersDir)
            return
        }
        Install-PerUser $Version
        return
    }
    if ((Test-Path "HKCU:\$UninstallKey") -or (Test-Path -LiteralPath $PerUserDir)) {
        Write-Host ((Msg "このアカウントには、自分だけの墨消しスタジオがあります（{0}）。すべてのユーザー向けに導入する前に、「インストールされているアプリ」から、自分だけのものをアンインストールしてください。" "Sumikeshi Studio is installed just for this account ({0}). Before installing for all users, uninstall it from `"Installed apps`".") -f $PerUserDir)
        return
    }
    if (Test-Elevated) { Install-AllUsers $Version } else {
        # 割り当てたネットワークのドライブはログオン セッションごとにあり、管理者権限のプロセス（UAC で入力した別の
        # アカウントや、別のログオン セッション）からは見えないことがある
        if (Test-NetworkPath $Source) {
            Write-Host (Msg "ネットワーク上のフォルダからは、すべてのユーザー向けに導入できません（管理者権限では、ネットワークのドライブを読めないことがあるため）。ダウンロードなど、この PC の中のフォルダに ZIP を展開してから、もう一度実行してください。" "Setup for all users cannot be run from a network folder (the network drive may not be readable with administrator rights). Extract the ZIP to a folder on this PC, such as Downloads, and then run this again.")
            return
        }
        Write-Host (Msg "管理者権限で続けます。表示される確認で「はい」を押すか、管理者のアカウントのパスワードを入力してください。" "Continuing with administrator rights. In the prompt that appears, click Yes or enter the password of an administrator account.")
        # -NoWait は引き継ぐ（管理者権限のウィンドウが Enter を待つと、引数で答えを渡した呼び出し元も止まるため）
        $Arguments = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "`"$PSCommandPath`"", "-Source", "`"$Source`"", "-Elevated", "-Language", $Lang, "-WindowsLanguage", $WinLang)
        if ($NoWait) { $Arguments += "-NoWait" }
        try {
            $Process = Start-Process -FilePath "powershell.exe" -ArgumentList $Arguments -Verb RunAs -Wait -PassThru
        } catch {
            Write-Host (Msg "管理者権限での実行が取り消されたため、導入しませんでした。" "Setup was not done because running with administrator rights was canceled.")
            return
        }
        $script:ExitCode = $Process.ExitCode
        if ($script:ExitCode -ne 0) {
            Write-Host ((Msg "すべてのユーザー向けの導入は、最後まで終わりませんでした（終了の番号 {0}）。管理者権限のウィンドウに出た文を確かめてください。" "Setup for all users did not finish (exit code {0}). Check the messages in the administrator window.") -f $script:ExitCode)
        }
    }
    if ($script:ExitCode -ne 0) { return }
    Write-Host ((Msg "この PC のすべてのユーザー向けに導入しました（{0}）。各ユーザーが最初に起動したときに、サンプルの様式を入れるかを尋ねます。展開したフォルダ（{1}）は、削除してかまいません。" "Installed for all users of this PC in {0}. When each user starts the tool for the first time, it asks whether to add the sample forms. You can delete the extracted folder ({1}).") -f $AllUsersDir, $Source)
}

# 終了の番号。導入が最後まで済んだときだけ 0 にする。Invoke-Main の出力は取り込まない（Python の出力が画面に出るように）
$script:ExitCode = 1
try {
    Invoke-Main
} catch {
    Write-Host ((Msg "導入の途中で、思わぬエラーが起きました（{0}）。{1}" "An unexpected error occurred during setup ({0}). {1}") -f $_.Exception.Message, $Tell)
} finally {
    if (-not $NoWait) {
        Write-Host ""
        Read-Host (Msg "Enter を押すと閉じます。" "Press Enter to close.") | Out-Null
    }
}
exit $script:ExitCode
