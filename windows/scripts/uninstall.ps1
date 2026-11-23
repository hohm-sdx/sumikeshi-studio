# Copyright (C) 2026 Hideki Ohmura
# SPDX-License-Identifier: AGPL-3.0-only
# 墨消しスタジオ（Sumikeshi Studio）のアンインストール：プログラムのフォルダ・ショートカット・アプリの一覧の登録を消す。
# 導入した場所（install.ps1 が入れた場所）の scripts\ に入っていて、アプリの一覧のアンインストールか uninstall.bat から
# 起動する。導入の種類は、scripts\ の親のフォルダ（導入した場所）で決める。
# このスクリプトのあるフォルダも消すので、プロセスの今いるフォルダを一時的なフォルダにしてから消す（PowerShell は起動の
# ときにスクリプトをすべて読むので、ファイルを消しても動き続ける）。
# すべてのユーザー向けでは、管理者権限でこのスクリプトを起動し直す（-Elevated）。利用者のデータを消すかは、起動し直す前に、
# アンインストールを始めた利用者の権限で尋ねて消す（管理者権限の段階は、ほかのアカウントとして動くことがあるため）。
# 問いの答えは引数でも渡せる（-Yes・-DeleteData。試験で人の操作なしに流すため）。渡さなかった問いは尋ねる。
param(
    [switch]$Elevated,
    [string]$Language = "",
    [switch]$Yes,
    [string]$DeleteData = "",
    [switch]$NoWait
)
$ErrorActionPreference = "Stop"

$AppDirName = "SumikeshiStudio"
$UninstallKey = "Software\Microsoft\Windows\CurrentVersion\Uninstall\SumikeshiStudio"
$PerUserDir = Join-Path $env:LOCALAPPDATA "Programs\$AppDirName"
$AllUsersDir = Join-Path $env:ProgramFiles $AppDirName
$AppNames = @("墨消しスタジオ", "Sumikeshi Studio")
$Here = Split-Path -Parent $PSScriptRoot
[IO.Directory]::SetCurrentDirectory($env:TEMP)
Set-Location -LiteralPath $env:TEMP

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

# パスとして読めない値（使えない文字を含むなど）は、違う場所とする
function Test-SamePathQuietly([string]$A, [string]$B) {
    try { return Test-SamePath $A $B } catch { return $false }
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

# 設定のファイル（Qt の QSettings が書く .ini）の値を、Qt と同じ決まりで読む。読むのは、Qt が文字列を書くときの形だけ：
# \ の後の a b f n r t v " ? ' \ を1文字に戻す。" で囲んだ部分はそのまま（Qt は、カンマ・セミコロン・= か前後の空白を含む
# 値を " で囲む）。" で囲んでいない値は末尾の空白を除く。先頭の @@ は @ にする（Qt は @ で始まる文字列の前に @ を足す）。
# ほかの形（\x などのエスケープ、" の外のカンマ・セミコロン、閉じていない "、@ で始まるほかの値）なら $null
function ConvertFrom-QtIniValue([string]$Value) {
    $EscapeKeys = 'abfnrtv"?''\'
    $EscapeValues = "`a`b`f`n`r`t`v`"?'\"
    $Out = New-Object Text.StringBuilder
    $InQuotes = $false
    $Quoted = $false
    $Value = $Value.TrimStart(" ", "`t")
    $i = 0
    while ($i -lt $Value.Length) {
        $c = $Value[$i]
        if ($c -eq [char]"\") {
            if ($i + 1 -ge $Value.Length) { return $null }
            $k = $EscapeKeys.IndexOf($Value[$i + 1])
            if ($k -lt 0) { return $null }
            [void]$Out.Append($EscapeValues[$k])
            $i += 2
            continue
        }
        if ($c -eq [char]'"') { $InQuotes = -not $InQuotes; $Quoted = $true; $i++; continue }
        if ((-not $InQuotes) -and ($c -eq [char]"," -or $c -eq [char]";")) { return $null }
        [void]$Out.Append($c)
        $i++
    }
    if ($InQuotes) { return $null }
    $Text = $Out.ToString()
    if (-not $Quoted) { $Text = $Text.TrimEnd(" ", "`t") }
    if ($Text.StartsWith("@@")) { return $Text.Substring(1) }
    if ($Text.StartsWith("@")) { return $null }
    return $Text
}

# 起動の画面で選んだ様式のフォルダ（設定のファイルの [forms] の folder）。選んでいなければ ""、読めなければ $null
function Read-FormsFolder([string]$Ini) {
    if (-not (Test-Path -LiteralPath $Ini)) { return "" }
    try { $Lines = Get-Content -LiteralPath $Ini -Encoding UTF8 -ErrorAction Stop } catch { return $null }
    $Section = ""
    $Value = $null
    foreach ($Line in $Lines) {
        $Text = $Line.Trim()
        if ($Text -match '^\[(.*)\]$') { $Section = $Matches[1]; continue }
        if ($Section -ceq "forms" -and $Text -cmatch '^folder\s*=(.*)$') { $Value = $Matches[1] }
    }
    if ($null -eq $Value) { return "" }
    return ConvertFrom-QtIniValue $Value
}

# 「Y」と答えたときだけ真（何も入力しなければ「いいえ」）
function Read-Yes([string]$Question) {
    $Answer = (Read-Host $Question).Normalize([Text.NormalizationForm]::FormKC).Trim().ToLower()
    return $Answer -in @("y", "yes")
}

# プログラムを消す（自分だけは管理者権限のない段階、すべてのユーザー向けは管理者権限の段階で呼ぶ）。消せたら $true
function Remove-Program([string]$Dir, [string]$Hive, [string]$Desktop, [string]$Programs, [switch]$AllUsers) {
    $Users = Get-ToolUsers $Dir -AllUsers:$AllUsers
    if ($Users.Count -gt 0) {
        Write-Host ((Msg "墨消しスタジオが動いているため、中止しました（起動している人：{0}）。ツールを閉じてから、もう一度実行してください。ほかの人がサインインしたまま起動しているときは、閉じてもらうか、サインアウトしてもらってください。" "Stopped because Sumikeshi Studio is running (started by: {0}). Close the tool, and then run this again. If another person left it running while signed in, ask them to close it or sign out.") -f ($Users -join ", "))
        return $false
    }
    # 名前を変えられなければ、使われているファイルがある。ここまでは何も消していない（アプリの一覧から消えて
    # ファイルだけが残ることを避けるため、一覧の登録は、名前を変えた後に消す）
    $Old = "$Dir.old"
    if (Test-Path -LiteralPath $Old) { Remove-Item -LiteralPath $Old -Recurse -Force }
    try { Rename-Item -LiteralPath $Dir -NewName (Split-Path $Old -Leaf) } catch {
        Write-Host ((Msg "導入先（{0}）のファイルが使われているため、中止しました。ツールやエクスプローラーなど、そのフォルダのファイルを開いているものを閉じてから、もう一度実行してください。" "Stopped because files in {0} are in use. Close the tool and anything else that has files in that folder open, and then run this again.") -f $Dir)
        return $false
    }
    foreach ($Folder in @($Desktop, $Programs)) {
        foreach ($Name in $AppNames) {
            $Link = Join-Path $Folder "$Name.lnk"
            if (Test-Path -LiteralPath $Link) { Remove-Item -LiteralPath $Link }
        }
    }
    if (Test-Path "${Hive}:\$UninstallKey") { Remove-Item -Path "${Hive}:\$UninstallKey" -Recurse -Force }
    try { Remove-Item -LiteralPath $Old -Recurse -Force } catch {
        Write-Host ((Msg "{0} を削除できませんでした。手で削除してください。" "Could not delete {0}. Delete it manually.") -f $Old)
    }
    return $true
}

function Invoke-Main {
    if (-not (Test-Choice "DeleteData" $DeleteData @("y", "n"))) { return }
    if (-not (Test-Choice "Language" $Language @("ja", "en"))) { return }
    if (Test-SamePath $Here $AllUsersDir) { $AllUsers = $true }
    elseif ((-not $Elevated) -and (Test-SamePath $Here $PerUserDir)) { $AllUsers = $false }
    else {
        Write-Host ((Msg "このスクリプトは、導入した場所（{0} か {1}）にないため、アンインストールしません。" "This script is not in an installation folder ({0} or {1}), so nothing is uninstalled.") -f $PerUserDir, $AllUsersDir)
        return
    }
    if ($Elevated) {
        if (Remove-Program $AllUsersDir "HKLM" ([Environment]::GetFolderPath("CommonDesktopDirectory")) ([Environment]::GetFolderPath("CommonPrograms")) -AllUsers) { $script:ExitCode = 0 }
        return
    }
    if (-not $Yes -and -not (Read-Yes ((Msg "墨消しスタジオ（{0}）をアンインストールしますか？ アンインストールするときは Y を入力して Enter を押してください（何も入力せずに Enter を押すとやめます）。[y/N]" "Uninstall Sumikeshi Studio ({0})? To uninstall, type Y and press Enter (pressing Enter alone cancels). [y/N]") -f $Here))) {
        Write-Host (Msg "アンインストールしませんでした。" "Nothing was uninstalled.")
        $script:ExitCode = 0
        return
    }
    $Data = Join-Path $env:LOCALAPPDATA "SumikeshiStudio"
    $Settings = Join-Path $env:APPDATA "SumikeshiStudio"
    $RemoveData = $false
    if ((Test-Path -LiteralPath $Data) -or (Test-Path -LiteralPath $Settings)) {
        # 起動の画面で既定の場所と違う様式のフォルダを選んでいれば、その場所を示す（消さないことを伝えるため）
        $Forms = Read-FormsFolder (Join-Path $Settings "SumikeshiStudio.ini")
        $DefaultForms = Join-Path $Data "forms"
        if ($null -eq $Forms) {
            Write-Host (Msg "起動の画面で既定の場所と違う様式のフォルダを選んでいたときは、そのフォルダは削除しません。" "If you selected a forms folder other than the default location on the start screen, that folder is not deleted.")
        } elseif ($Forms -and -not (Test-SamePathQuietly $Forms $DefaultForms)) {
            Write-Host ((Msg "様式のフォルダ（{0}）は、既定の場所でないので削除しません。" "The forms folder ({0}) is not the default location, so it is not deleted.") -f $Forms)
        }
        if ($DeleteData) { $RemoveData = $DeleteData -eq "y" } else {
            $RemoveData = Read-Yes ((Msg "このアカウントの作業ログ・設定・既定の場所の様式（{0}、{1}）も削除しますか？ 削除するときは Y を入力して Enter を押してください（何も入力せずに Enter を押すと残します）。[y/N]" "Also delete the work log, settings, and forms in the default location for this account ({0}, {1})? To delete them, type Y and press Enter (pressing Enter alone keeps them). [y/N]") -f $Data, $Settings)
        }
    }
    if ($AllUsers -and -not (Test-Elevated)) {
        Write-Host (Msg "管理者権限で続けます。表示される確認で「はい」を押すか、管理者のアカウントのパスワードを入力してください。" "Continuing with administrator rights. In the prompt that appears, click Yes or enter the password of an administrator account.")
        # -NoWait は引き継ぐ（管理者権限のウィンドウが Enter を待つと、引数で答えを渡した呼び出し元も止まるため）
        $Arguments = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "`"$PSCommandPath`"", "-Elevated", "-Language", $Lang)
        if ($NoWait) { $Arguments += "-NoWait" }
        try {
            $Process = Start-Process -FilePath "powershell.exe" -ArgumentList $Arguments -Verb RunAs -Wait -PassThru
        } catch {
            Write-Host (Msg "管理者権限での実行が取り消されたため、アンインストールしませんでした。" "Nothing was uninstalled because running with administrator rights was canceled.")
            return
        }
        if ($Process.ExitCode -ne 0) { return }
    } elseif ($AllUsers) {
        if (-not (Remove-Program $AllUsersDir "HKLM" ([Environment]::GetFolderPath("CommonDesktopDirectory")) ([Environment]::GetFolderPath("CommonPrograms")) -AllUsers)) { return }
    } else {
        if (-not (Remove-Program $PerUserDir "HKCU" ([Environment]::GetFolderPath("Desktop")) ([Environment]::GetFolderPath("Programs")))) { return }
    }
    if ($RemoveData) {
        foreach ($Path in @($Data, $Settings)) {
            try { if (Test-Path -LiteralPath $Path) { Remove-Item -LiteralPath $Path -Recurse -Force } } catch {
                Write-Host ((Msg "{0} を削除できませんでした。手で削除してください。" "Could not delete {0}. Delete it manually.") -f $Path)
            }
        }
    }
    Write-Host (Msg "アンインストールしました。" "Uninstalled.")
    if ($AllUsers) { Write-Host (Msg "ほかのユーザーの作業ログ・設定・様式は残っています。要らなければ、各ユーザーの C:\Users\（ユーザー名）\AppData\Local\SumikeshiStudio と C:\Users\（ユーザー名）\AppData\Roaming\SumikeshiStudio を削除してください。" "The work logs, settings, and forms of other users remain. If they are not needed, delete C:\Users\<user name>\AppData\Local\SumikeshiStudio and C:\Users\<user name>\AppData\Roaming\SumikeshiStudio for each user.") }
    $script:ExitCode = 0
}

# 終了の番号。最後まで済んだとき（やめると答えたときを含む）だけ 0 にする
$script:ExitCode = 1
try {
    Invoke-Main
} catch {
    Write-Host ((Msg "アンインストールの途中で、思わぬエラーが起きました（{0}）。この画面のスクリーンショットを撮っておくと、原因の調査に役立つ場合があります。" "An unexpected error occurred during uninstallation ({0}). A screenshot of this window may help investigate the cause.") -f $_.Exception.Message)
} finally {
    if (-not $NoWait) {
        Write-Host ""
        Read-Host (Msg "Enter を押すと閉じます。" "Press Enter to close.") | Out-Null
    }
}
exit $script:ExitCode
