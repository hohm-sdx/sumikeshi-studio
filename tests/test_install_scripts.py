# Copyright (C) 2026 Hideki Ohmura
# SPDX-License-Identifier: AGPL-3.0-only
"""導入とアンインストールのスクリプト（windows/setup.bat・scripts/install.ps1・uninstall.bat・scripts/uninstall.ps1）のテスト

Windows PowerShell 5.1 と Windows の部品はこの環境で動かせないので（pwsh で構文の解析と関数の一部は動かせる）、スクリプトの
文を読んで、決めた動き（止める条件、出す文、アプリの一覧の鍵と値、ショートカットの場所、Python の起動のしかた）を確かめる。
動きそのもの（移動・コピー・UAC・レジストリ・ショートカット）は、Windows の PC で、試験用のパッケージで確かめる。
"""

import re
from pathlib import Path

import pytest
from powershell import PWSH, PWSH_MISSING, code_only, parse_errors, ps7_syntax
from test_setup_scripts import NOT_MESSAGES, message_problems, read

from sumikeshi_studio import datadir

ROOT = Path(__file__).resolve().parents[1]
WINDOWS = ROOT / "windows"
INSTALL = WINDOWS / "scripts" / "install.ps1"
# Msg の形でなくてよい、日本語の文字がある行と、その理由
ALLOWED = NOT_MESSAGES
NETWORK = (
    "Invoke-WebRequest",
    "Invoke-RestMethod",
    "WebClient",
    "BitsTransfer",
    "DownloadFile",
)


def script(path: Path = INSTALL) -> str:
    return read(path).replace("\r\n", "\n")


def body(text: str, name: str) -> str:
    """関数 name の本体（1文字目の「}」の行まで）"""
    start = text.index(f"function {name}")
    return text[start : text.index("\n}\n", start) + 2]


def test_install_messages_are_in_both_languages():
    """導入が出す文は、すべて Msg "日本語" "英語" の形で、英語の側に日本語がなく、値の番号が両方で同じ"""
    assert message_problems(script(), ALLOWED) == []


@pytest.mark.parametrize("name", ["install.ps1", "uninstall.ps1"])
def test_scripts_brackets_are_balanced(name):
    """文字列とコメントの外の (){}[] が対応している（pwsh がない環境でも、構文の誤りのうち、かっこの数え誤りを
    文から見つける。閉じかっこが1つ足りないだけで、Windows の PowerShell はスクリプトを1行も動かさない）"""
    stack = []
    for n, line in enumerate(
        code_only(script(WINDOWS / "scripts" / name)).split("\n"), 1
    ):
        for c in line:
            if c in "({[":
                stack.append((c, n))
            elif c in ")}]":
                want = {")": "(", "}": "{", "]": "["}[c]
                assert stack and stack[-1][0] == want, (
                    f"{n}行の {c}（開いたもの：{stack[-1:]}）"
                )
                stack.pop()
    assert not stack, f"閉じていない：{stack}"


@pytest.mark.skipif(PWSH is None, reason=PWSH_MISSING)
def test_scripts_parse_without_errors(tmp_path):
    """windows/scripts/ の .ps1 は、PowerShell の構文の解析で誤りがない（0.3.0rc4 では閉じかっこが1つ足りず、Windows の
    PowerShell が install.ps1 を1行も動かさなかった）"""
    paths = [WINDOWS / "scripts" / n for n in ("install.ps1", "uninstall.ps1")]
    assert parse_errors(paths, tmp_path) == {str(p): [] for p in paths}


@pytest.mark.skipif(PWSH is None, reason=PWSH_MISSING)
def test_parse_errors_finds_missing_parenthesis(tmp_path):
    """構文の解析は、閉じかっこの足りない行を誤りとして見つける（0.3.0rc4 の誤りと同じ形）"""
    bad = tmp_path / "bad.ps1"
    bad.write_text(
        'Write-Host ((Msg "日本語" "English") -f $a, ($b -join ", ")\n', "utf-8-sig"
    )
    (errors,) = parse_errors([bad], tmp_path / "work").values()
    assert errors and "MissingEndParenthesis" in " ".join(errors)


@pytest.mark.parametrize("name", ["install.ps1", "uninstall.ps1"])
def test_scripts_use_only_windows_powershell_51_syntax(name):
    """windows/scripts/ の .ps1 は、Windows に標準で入っている Windows PowerShell 5.1 にない演算子（PowerShell 7 で足されたもの）を使わない"""
    assert ps7_syntax(script(WINDOWS / "scripts" / name)) == []


def test_ps7_syntax_finds_operators_outside_strings_and_comments():
    """5.1 にない演算子を、文字列とコメントの外でだけ見つける（$? と、文字列の中の ? や && は数えない）"""
    lines = [
        "a && b",
        "a || b",
        "$x = $y ?? 1",
        "$x ??= 1",
        "${x}?.Name",
        "${x}?[0]",
        "$v = $ok ? 1 : 2",
        'Write-Host "a && b ?? c ? d"',
        "Write-Host 'x || y'",
        "# a && b",
        "if ($?) { $LASTEXITCODE }",
        "$a -match '^a?$'",
    ]
    found = ps7_syntax("\n".join(lines))
    assert [f.split("：")[0] for f in found] == [f"{n}行" for n in range(1, 8)]


def test_setup_bat_leaves_extracted_folder_before_powershell():
    """入口の .bat は、今いるフォルダを一時的なフォルダにし、(goto) でバッチ ファイルを抜けてから、同じ行で PowerShell を
    起動する（展開したフォルダを移した後に、cmd.exe がバッチ ファイルの続きを読まないように。読みにいくと「The system
    cannot find the path specified.」が出て、終了の番号が 1 になる）。PowerShell を行の最後のコマンドにして、その終了の
    番号をそのまま返す。-Source は末尾の \\ の後に . を付ける。受け取った引数（%*）は、そのまま install.ps1 に渡す。
    install.ps1 は scripts\\ にある。改行は CRLF"""
    data = (WINDOWS / "setup.bat").read_bytes()
    assert data.count(b"\r\n") == data.count(b"\n") > 0
    commands = [
        s
        for s in data.decode("ascii").splitlines()
        if s and not s.lower().startswith(("@echo", "rem "))
    ]
    expected = (
        'cd /d "%TEMP%" & (goto) 2>nul & powershell -NoProfile -ExecutionPolicy Bypass -File '
        '"%~dp0scripts\\install.ps1" -Source "%~dp0." %*'
    )
    assert commands == [expected]
    rems = [s for s in data.decode("ascii").splitlines() if s.startswith("rem ")]
    assert not [
        s for s in rems if re.search(r"[<>|&]", s)
    ]  # rem の行でも cmd が記号を読むことがある


def test_entrance_bat_files_are_setup_and_uninstall():
    """windows/ の一番上の .bat は、導入の入口の setup.bat、アンインストールの入口の uninstall.bat、起動の start.bat、
    コマンドラインの sumikeshi.bat の4つ。PowerShell のスクリプトは scripts\\ の install.ps1 と uninstall.ps1 の2つ"""
    assert sorted(p.name for p in WINDOWS.glob("*.bat")) == [
        "setup.bat",
        "start.bat",
        "sumikeshi.bat",
        "uninstall.bat",
    ]
    assert sorted(p.name for p in (WINDOWS / "scripts").glob("*.ps1")) == [
        "install.ps1",
        "uninstall.ps1",
    ]


def test_install_shows_previous_folder_only_when_it_remains():
    """前のデスクトップのショートカットが導入先と違う場所を指していれば、その場所（前の形で使っていたフォルダ）を示す。
    そのフォルダがないとき（前の形のフォルダに新しい ZIP を展開し直して実行し、そのフォルダを導入先に移したとき）は
    示さない"""
    text = script()
    per_user = body(text, "Install-PerUser")
    assert (
        '$Previous = New-Shortcuts $Dir ([Environment]::GetFolderPath("Desktop"))'
        in per_user
    )
    (line,) = [
        s for s in per_user.splitlines() if "前のデスクトップのショートカットは" in s
    ]
    assert line.lstrip().startswith(
        "if ($Previous -and (Test-Path -LiteralPath $Previous)) { Write-Host ("
    )
    assert per_user.index("Remove-Item -LiteralPath $Source") < per_user.index(line)
    shortcuts = body(text, "New-Shortcuts")
    assert (
        "if ($Target -and -not (Test-Under $Target $Dir)) { $Previous = Split-Path (Split-Path $Target) }"
        in shortcuts
    )


def test_install_places_and_app_list_values():
    """導入先とアプリの一覧の鍵はツール（datadir）と同じ。値の名前、発行元とリンク、種類ごとの表示の名前"""
    text = script()
    assert f'$AppDirName = "{datadir.INSTALL_DIR_NAME}"' in text
    assert f'$UninstallKey = "{datadir.UNINSTALL_KEY}"' in text
    assert '$PerUserDir = Join-Path $env:LOCALAPPDATA "Programs\\$AppDirName"' in text
    assert "$AllUsersDir = Join-Path $env:ProgramFiles $AppDirName" in text
    assert '$Publisher = "Hideki Ohmura"' in text
    assert '$InfoUrl = "https://github.com/hohm-sdx/sumikeshi-studio/releases"' in text
    register = body(text, "Register-App")
    for name in ("DisplayName", "DisplayVersion", "Publisher", "InstallLocation",
                 "InstallDate", "DisplayIcon", "UninstallString", "URLInfoAbout"):  # fmt: skip
        assert f'"{name}"' in register, name
    for name in ("NoModify", "NoRepair", "EstimatedSize"):
        assert f'"{name}" = ' in register, name
    assert "-PropertyType DWord" in register
    # アンインストールのコマンドは、導入先の scripts\ の uninstall.ps1 を指す（更新でも登録し直す）
    assert "$(Join-Path $Dir 'scripts\\uninstall.ps1')" in register
    assert (
        'Register-App "HKCU" $Dir (WinMsg "墨消しスタジオ" "Sumikeshi Studio")' in text
    )
    assert (
        'Register-App "HKLM" $Dir (WinMsg "墨消しスタジオ（すべてのユーザー）" "Sumikeshi Studio (all users)")'
        in text
    )


def test_install_stops_on_mixed_or_elevated_just_for_me():
    """自分だけ：管理者として実行されていれば止め、すべてのユーザー向けがあれば止める。すべてのユーザー：この利用者の
    自分だけの導入があれば止める。どちらも、導入を始める前に止める"""
    main = body(script(), "Invoke-Main")
    just_me = main[main.index('if ($Choice -eq "1")') :]
    just_me = just_me[: just_me.index("Install-PerUser")]
    assert "if (Test-Elevated)" in just_me
    assert (
        'if ((Test-Path "HKLM:\\$UninstallKey") -or (Test-Path -LiteralPath $AllUsersDir))'
        in just_me
    )
    rest = main[main.index("Install-PerUser $Version") :]
    mixed = 'if ((Test-Path "HKCU:\\$UninstallKey") -or (Test-Path -LiteralPath $PerUserDir))'
    assert (
        rest.index(mixed) < rest.index("Install-AllUsers") < rest.index("-Verb RunAs")
    )
    assert 'if (-not $Choice) { $Choice = "1" }' in main  # 何も入力しなければ自分だけ


def test_install_stops_on_installed_or_network_source():
    """導入したフォルダ（導入先と、その .old・.new）の中から実行したときは、何かをする前に止める。すべての
    ユーザー向けは、ネットワーク上のフォルダ（UNC パスか、ネットワークのドライブ）からなら、管理者権限に切り替える
    前に止める。管理者権限の段階が最後まで終わらなければ、元のウィンドウに終了の番号を示す"""
    text = script()
    installed = body(text, "Get-InstalledFolder")
    assert "@($PerUserDir, $AllUsersDir)" in installed
    assert '@($Dir, "$Dir.old", "$Dir.new")' in installed
    assert "(Test-SamePath $Path $Folder) -or (Test-Under $Path $Folder)" in installed
    network = body(text, "Test-NetworkPath")
    assert (
        '$Path.StartsWith("\\\\")' in network and "[IO.DriveType]::Network" in network
    )
    main = body(text, "Invoke-Main")
    assert main.index("if (Get-InstalledFolder $Source)") < main.index("$Python = ")
    rest = main[main.index("Install-PerUser $Version") :]
    assert rest.index("if (Test-NetworkPath $Source)") < rest.index("-Verb RunAs")
    after = rest[rest.index("-Verb RunAs") :]
    assert "最後まで終わりませんでした（終了の番号 {0}）" in after


def test_install_kinds_do_their_own_steps():
    """自分だけ：自分のプロセスを見て、同じドライブなら移動（UNC と別のドライブはコピー）、HKCU、その利用者の
    デスクトップとスタートメニュー、サンプルの様式の問い（名前と年分）。すべてのユーザー：PC 全体のプロセスを見て、
    コピー、HKLM、共通のデスクトップとすべてのユーザーのスタートメニュー。利用者ごとのことはしない"""
    text = script()
    per_user = body(text, "Install-PerUser")
    assert per_user.index("Get-ToolUsers $Dir\n") < per_user.index("Install-Files")
    assert '$Source.StartsWith("\\\\")' in per_user and "GetPathRoot" in per_user
    assert (
        'GetFolderPath("Desktop")' in per_user
        and 'GetFolderPath("Programs")' in per_user
    )
    assert '"sumikeshi_studio.forminstall", "--years"' in per_user
    all_users = body(text, "Install-AllUsers")
    assert all_users.index("Get-ToolUsers $Dir -AllUsers") < all_users.index(
        "Install-Files"
    )
    assert "Install-Files $Dir $false" in all_users  # コピー
    assert 'GetFolderPath("CommonDesktopDirectory")' in all_users
    assert 'GetFolderPath("CommonPrograms")' in all_users
    for per_user_thing in (
        "forminstall",
        "LOCALAPPDATA",
        "APPDATA",
        'GetFolderPath("Desktop")',
        "HKCU",
    ):
        assert per_user_thing not in all_users, per_user_thing


def test_install_files_keep_previous_until_new_is_in():
    """前の導入は .old に名前を変えてから入れ（変えられなければ使われているとして中止）、コピーは .new に書いてから
    名前を変える。プロセスは、管理者権限の段階では PC 全体を、そうでなければ自分のものを見る"""
    text = script()
    files = body(text, "Install-Files")
    assert (
        files.count("Rename-Item -LiteralPath $Dir -NewName (Split-Path $Old -Leaf)")
        == 2
    )
    assert "Copy-Item -LiteralPath $Source -Destination $New -Recurse" in files
    assert files.index("Copy-Item") < files.rindex("Rename-Item -LiteralPath $New")
    users = body(text, "Get-ToolUsers")
    assert "Win32_Process" in users and "GetOwner" in users and "Get-Process" in users


def test_install_starts_python_isolated_and_elevates_itself():
    """Python は -I で起動し、展開した場所で動かすとき（起動と部品の確認）は -B で .pyc を書かない。問いを出す
    Python は、出力を取り込まずに同じコンソールで動かす。すべてのユーザー向けは、言語と -NoWait を渡して管理者権限で
    自分を起動し直す。終わるときは、-NoWait がなければ Enter を待つ"""
    text = script()
    direct = re.findall(
        r'& (?:\$Python|\(Join-Path \$Dir "python\\python\.exe"\)) (\S+)', text
    )
    arrays = re.findall(r'Invoke-Python [^@\n]*@\(("[^)]*)\)', text)
    assert len(direct) >= 3 and len(arrays) == 1
    assert set(direct) == {"-I"}
    assert [a for a in arrays if not a.startswith('"-I"')] == []
    assert (
        '$FormArguments = @("-I", "-m", "sumikeshi_studio.forminstall", "--years")'
        in text
    )
    assert 'Invoke-Python (Join-Path $Dir "python\\python.exe") $FormArguments' in text
    assert (
        '& $Python -I -B -c "pass"' in text
        and '@("-I", "-B", "-m", "sumikeshi_studio.nativecheck")' in text
    )
    assert "-NoNewWindow -Wait -PassThru" in body(text, "Invoke-Python")
    assert '"-Elevated", "-Language", $Lang, "-WindowsLanguage", $WinLang)' in text
    assert 'if ($NoWait) { $Arguments += "-NoWait" }' in body(text, "Invoke-Main")
    assert "-Verb RunAs -Wait -PassThru" in text
    assert "NativeErrorCode -eq 4551" in text
    assert text.rstrip().endswith("exit $script:ExitCode")
    assert 'Read-Host (Msg "Enter を押すと閉じます。" "Press Enter to close.")' in text


def test_install_does_not_use_network():
    text = script()
    assert [w for w in NETWORK if w in text] == []


def ending(text: str) -> str:
    """スクリプトの終わりの finally から後"""
    return text[text.rindex("} finally {") :]


def test_install_takes_answers_as_arguments():
    """-Kind・-Samples・-NoWait を受け取る。値は、何をするより先に決まりに合うかを確かめ、合わなければ文を出して止める
    （既定の答えにしない）。-Kind は種類の問いの答えになるだけで（渡さなければ今のまま尋ねる）、管理者権限の確かめや
    起動し直しは変わらない。-Samples は forminstall に --samples で渡す。-NoWait なら、終わるときに Enter を待たない"""
    text = script()
    head = text[: text.index("$ErrorActionPreference")]
    for line in ('[string]$Kind = ""', '[string]$Samples = ""', "[switch]$NoWait"):
        assert line in head
    choice = body(text, "Test-Choice")
    assert "if (-not $Value -or $Value -in $Allowed) { return $true }" in choice
    assert choice.index("Write-Host") < choice.index("return $false")
    main = body(text, "Invoke-Main")
    checks = [
        'Test-Choice "Kind" $Kind @("1", "2")',
        'Test-Choice "Samples" $Samples @("y", "n")',
        'Test-Choice "Language" $Language @("ja", "en")',
        'Test-Choice "WindowsLanguage" $WindowsLanguage @("ja", "en")',
    ]
    for check in checks:
        assert f"if (-not ({check})) {{ return }}" in main
    assert max(main.index(c) for c in checks) < main.index("Get-InstalledFolder")
    assert [s.strip() for s in main.splitlines() if "$Kind" in s] == [
        'if (-not (Test-Choice "Kind" $Kind @("1", "2"))) { return }',
        "$Choice = $Kind",
    ]
    assert main.index("$Choice = $Kind") < main.index("while ($Choice -notin")
    assert (
        'if ($Samples) { $FormArguments += @("--samples", $Samples.ToLower()) }'
        in body(text, "Install-PerUser")
    )
    end = ending(text)
    assert end.index("if (-not $NoWait) {") < end.index("Read-Host")


def test_uninstall_takes_answers_as_arguments():
    """-Yes・-DeleteData・-NoWait を受け取る。値は、何をするより先に確かめる。-Yes ならアンインストールしてよいかを
    尋ねず、-DeleteData があればデータを消すかを尋ねずにその答えにする（どちらも管理者権限に切り替える前に決める
    ので、管理者権限の段階には渡さない）。-NoWait は管理者権限の段階に引き継ぎ、終わるときに Enter を待たない"""
    text = script(UNINSTALL)
    head = text[: text.index("$ErrorActionPreference")]
    for line in ("[switch]$Yes", '[string]$DeleteData = ""', "[switch]$NoWait"):
        assert line in head
    main = body(text, "Invoke-Main")
    checks = [
        'Test-Choice "DeleteData" $DeleteData @("y", "n")',
        'Test-Choice "Language" $Language @("ja", "en")',
    ]
    for check in checks:
        assert f"if (-not ({check})) {{ return }}" in main
    assert max(main.index(c) for c in checks) < main.index("Test-SamePath $Here")
    assert "if (-not $Yes -and -not (Read-Yes ((Msg" in main
    assert 'if ($DeleteData) { $RemoveData = $DeleteData -eq "y" } else {' in main
    order = ["if ($DeleteData)", "$RemoveData = Read-Yes", "-Verb RunAs"]
    assert [main.index(s) for s in order] == sorted(main.index(s) for s in order)
    assert '"-Elevated", "-Language", $Lang)' in main
    assert 'if ($NoWait) { $Arguments += "-NoWait" }' in main
    end = ending(text)
    assert end.index("if (-not $NoWait) {") < end.index("Read-Host")


UNINSTALL = WINDOWS / "scripts" / "uninstall.ps1"
# install.ps1 と uninstall.ps1 の両方に置く関数（どちらのスクリプトも単独で動くように、同じものを2つに書く）
SHARED = (
    "Get-SavedLanguage",
    "Test-Under",
    "Test-SamePath",
    "Test-Elevated",
    "Test-Choice",
    "Get-ToolUsers",
)


def test_uninstall_messages_and_bat():
    """アンインストールが出す文は、すべて Msg の形。uninstall.bat は setup.bat と同じ形の1行（アンインストールも、
    自分の入った導入先のフォルダの名前を変えて消すので、(goto) でバッチ ファイルを抜けてから PowerShell を起動する）で、
    受け取った引数をそのまま scripts\\ の uninstall.ps1 に渡す"""
    assert message_problems(script(UNINSTALL), ALLOWED) == []
    data = (WINDOWS / "uninstall.bat").read_bytes()
    assert data.count(b"\r\n") == data.count(b"\n") > 0
    commands = [
        s
        for s in data.decode("ascii").splitlines()
        if s and not s.lower().startswith(("@echo", "rem "))
    ]
    expected = (
        'cd /d "%TEMP%" & (goto) 2>nul & powershell -NoProfile -ExecutionPolicy Bypass -File '
        '"%~dp0scripts\\uninstall.ps1" %*'
    )
    assert commands == [expected]
    rems = [s for s in data.decode("ascii").splitlines() if s.startswith("rem ")]
    assert not [s for s in rems if re.search(r"[<>|&]", s)]


def test_shared_functions_are_the_same_in_both_scripts():
    """両方に置く関数は、同じ中身（片方だけ直して食い違わないように）。導入先とアプリの一覧の鍵も同じ"""
    install, uninstall = script(), script(UNINSTALL)
    for name in SHARED:
        assert body(install, name) == body(uninstall, name), name
    for line in (
        '$AppDirName = "',
        "$UninstallKey = ",
        "$PerUserDir = ",
        "$AllUsersDir = ",
    ):
        found = {s for s in (install + uninstall).splitlines() if s.startswith(line)}
        assert len(found) == 1, found


def test_uninstall_asks_first_and_removes_program_before_data():
    """自分の場所から種類を決め（導入した場所でなければ何もしない）、今いるフォルダを一時的なフォルダにする。アンイン
    ストールしてよいか（既定は「いいえ」）と、自分のデータを消すか（既定は残す）を、管理者権限に切り替える前に
    尋ねる。プログラムは、動いていれば中止し、フォルダの名前を変えられてから、ショートカットとアプリの一覧の登録を
    消す。データは、プログラムを消せた後に、尋ねた利用者の権限で消す"""
    text = script(UNINSTALL)
    head = text[: text.index("function ")]
    assert "[IO.Directory]::SetCurrentDirectory($env:TEMP)" in head
    # 自分は導入した場所の scripts\ にあるので、種類は scripts\ の親のフォルダで決める
    assert "$Here = Split-Path -Parent $PSScriptRoot" in head
    assert 'return $Answer -in @("y", "yes")' in body(text, "Read-Yes")
    main = body(text, "Invoke-Main")
    assert main.index("Test-SamePath $Here $AllUsersDir") < main.index("Read-Yes")
    order = ["[y/N]", "$RemoveData = Read-Yes", "-Verb RunAs", "Remove-Program $PerUserDir",
             "if ($RemoveData)"]  # fmt: skip
    assert [main.index(s) for s in order] == sorted(main.index(s) for s in order)
    assert '$Data = Join-Path $env:LOCALAPPDATA "SumikeshiStudio"' in main
    assert '$Settings = Join-Path $env:APPDATA "SumikeshiStudio"' in main
    remove = body(text, "Remove-Program")
    steps = ["Get-ToolUsers", "Rename-Item -LiteralPath $Dir", "Remove-Item -LiteralPath $Link",
             'Remove-Item -Path "${Hive}:\\$UninstallKey"', "try { Remove-Item -LiteralPath $Old -Recurse"]  # fmt: skip
    assert [remove.index(s) for s in steps] == sorted(remove.index(s) for s in steps)
    assert (
        'Remove-Program $AllUsersDir "HKLM"' in main
        and 'Remove-Program $PerUserDir "HKCU"' in main
    )
    assert (
        'GetFolderPath("CommonDesktopDirectory")' in main
        and 'GetFolderPath("Desktop")' in main
    )
    assert [w for w in NETWORK if w in text] == []
    assert text.rstrip().endswith("exit $script:ExitCode")


# ---------------------------------------------------------------- 起動の画面で選んだ様式のフォルダの読み方

# uninstall.ps1 の ConvertFrom-QtIniValue と同じ決まりを、Python で書いたもの（Windows PowerShell 5.1 はこの環境で動かせないので、
# 決まりそのものを、QSettings が書いた実物で確かめる）
ESCAPES = dict(zip("abfnrtv\"?'\\", "\a\b\f\n\r\t\v\"?'\\", strict=True))


def qt_ini_value(value: str) -> str | None:
    out, in_quotes, quoted, i = [], False, False, 0
    value = value.lstrip(" \t")
    while i < len(value):
        c = value[i]
        if c == "\\":
            if i + 1 >= len(value) or value[i + 1] not in ESCAPES:
                return None
            out.append(ESCAPES[value[i + 1]])
            i += 2
            continue
        if c == '"':
            in_quotes, quoted = not in_quotes, True
            i += 1
            continue
        if not in_quotes and c in ",;":
            return None
        out.append(c)
        i += 1
    if in_quotes:
        return None
    text = "".join(out)
    if not quoted:
        text = text.rstrip(" \t")
    if text.startswith("@@"):
        return text[1:]
    return None if text.startswith("@") else text


def read_forms_folder(text: str) -> str | None:
    """uninstall.ps1 の Read-FormsFolder と同じ：選んでいなければ ""、読めなければ None"""
    section, value = "", None
    for line in text.splitlines():
        line = line.strip()
        if m := re.fullmatch(r"\[(.*)\]", line):
            section = m[1]
            continue
        if section == "forms" and (m := re.fullmatch(r"folder\s*=(.*)", line)):
            value = m[1]
    return "" if value is None else qt_ini_value(value)


# Windows のフォルダの名前に使える文字（空白・カンマ・セミコロン・=・@・#・%・&・括弧・'・日本語、UNC パス）を含む値
FOLDERS = [
    r"C:\tools\forms",
    r"\\fileserver\共有\様式, 2026年",
    r"\\server\share\forms",
    r"C:\様式 フォルダ",
    r"D:\a;b",
    r"D:\a=b",
    r"D:\a,b;c=d",
    r"D:\@様式",
    r"D:\#a %TEMP% &(x) 'y' [z] {w} ~!^",
    "C:\\末尾の空白 ",
    " C:\\先頭の空白",
    "@先頭の@",
]


def test_forms_folder_written_by_qsettings_is_read_back(tmp_path):
    """QSettings が書いた設定のファイルの [forms] の folder を、uninstall.ps1 の決まりで読むと元の値に戻る"""
    QtCore = pytest.importorskip("PySide6.QtCore", exc_type=ImportError)
    for n, folder in enumerate(FOLDERS):
        ini = tmp_path / f"{n}.ini"
        settings = QtCore.QSettings(str(ini), QtCore.QSettings.Format.IniFormat)
        settings.setValue("ui/language", "ja")
        settings.setValue("forms/folder", folder)
        settings.setValue("forms/off_here", ["u-1", "u-2"])
        settings.sync()
        assert read_forms_folder(ini.read_text("utf-8")) == folder, folder
    assert read_forms_folder("[ui]\nlanguage=ja\n") == ""  # 選んでいない
    assert read_forms_folder("[forms]\nfolder=\n") == ""


def test_forms_folder_in_unexpected_form_is_not_read():
    """Qt が文字列を書く形でない値は読まない（場所なしの文を出す）"""
    for value in (
        "@Invalid()",
        '"閉じていない',
        "C:\\a, C:\\b",
        r"C:\x41",
        "C:\\a\\",
        "C:\\a;コメント",
    ):
        assert read_forms_folder(f"[forms]\nfolder={value}\n") is None, value


def test_uninstall_reads_forms_folder_with_the_same_rules():
    """uninstall.ps1 の読み方は、上の Python の決まりと同じ形で書いてある。既定の場所（データのフォルダの forms）と
    違う場所を選んでいれば場所を示し、読めなければ場所なしの文を出す"""
    from sumikeshi_studio import formstore

    text = script(UNINSTALL)
    convert = body(text, "ConvertFrom-QtIniValue")
    assert "$EscapeKeys = 'abfnrtv\"?''\\'" in convert
    assert '$EscapeValues = "`a`b`f`n`r`t`v`"?\'\\"' in convert
    assert (
        'if ((-not $InQuotes) -and ($c -eq [char]"," -or $c -eq [char]";")) { return $null }'
        in convert
    )
    assert 'if (-not $Quoted) { $Text = $Text.TrimEnd(" ", "`t") }' in convert
    assert 'if ($Text.StartsWith("@@")) { return $Text.Substring(1) }' in convert
    read = body(text, "Read-FormsFolder")
    assert "$Section -ceq \"forms\" -and $Text -cmatch '^folder\\s*=(.*)$'" in read
    main = body(text, "Invoke-Main")
    assert f'$DefaultForms = Join-Path $Data "{formstore.DEFAULT_FOLDER}"' in main
    assert main.index("Read-FormsFolder") < main.index("$RemoveData = Read-Yes")
    assert "は、既定の場所でないので削除しません。" in main
    assert "選んでいたときは、そのフォルダは削除しません。" in main
