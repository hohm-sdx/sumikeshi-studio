# Copyright (C) 2026 Hideki Ohmura
# SPDX-License-Identifier: AGPL-3.0-only
"""ツールの名前と、配布用パッケージのバージョンのファイルのテスト"""

import sys
from pathlib import Path

from sumikeshi_studio import APP_NAME, __version__, i18n, version

ROOT = Path(__file__).resolve().parents[1]


def read(path: Path) -> str:
    return path.read_bytes().decode("utf-8-sig")


def test_version_text_reads_version_file(tmp_path, monkeypatch):
    """配布用パッケージでは、python フォルダの隣の version.txt から作った日時を読む"""
    python = tmp_path / "python"
    python.mkdir()
    monkeypatch.setattr(sys, "base_prefix", str(python))
    assert version.version_text() == f"{__version__} (no version.txt)"
    (tmp_path / "version.txt").write_text(
        f"Sumikeshi Studio {__version__}\r\nBuilt: 2026-11-08 08:10\r\n", "utf-8-sig"
    )
    assert version.version_text() == f"{__version__} (built 2026-11-08 08:10)"


def test_setup_removes_other_language_shortcut():
    """導入では、もう一方の言語の名前のショートカットがあれば消してから、今の名前で作る。ショートカットは、導入先の
    pythonw.exe を -I を付けて起動する（ショートカットを作る Windows の部品はこの環境で動かせないので、書いてあることだけを
    確かめる。動きは Windows で確かめる）"""
    text = read(ROOT / "windows" / "scripts" / "install.ps1")
    # 名前は Windows の表示の言語で決め（APP_NAME か、その英語の名前）、もう一方の言語の名前を消す
    i18n.set_language(i18n.EN)
    english = i18n.tr(APP_NAME)
    i18n.set_language(i18n.JA)
    assert f'$AppName = WinMsg "{APP_NAME}" "{english}"' in text
    assert f'$AppNames = @("{APP_NAME}", "{english}")' in text
    assert "foreach ($Name in $AppNames | Where-Object { $_ -ne $AppName }) {" in text
    assert '$Other = Join-Path $Folder "$Name.lnk"' in text
    assert (
        "if (Test-Path -LiteralPath $Other) { Remove-Item -LiteralPath $Other }" in text
    )
    assert '$Link = $Shell.CreateShortcut((Join-Path $Folder "$AppName.lnk"))' in text
    assert '$Link.TargetPath = Join-Path $Dir "python\\pythonw.exe"' in text
    assert text.count("$Link.Arguments = ") == 1
    assert '$Link.Arguments = "-I -m sumikeshi_studio"' in text


def test_command_line_bat_runs_cli_in_console():
    """コマンドラインからの出力の .bat は、コンソールのある python.exe で cli を呼び（start を使わずに終わるまで待つ）、
    引数を渡し、終了の番号を返す。ファイルなどに書き出したときも UTF-8 にする（-X utf8）。-I で起動する。
    改行は CRLF（Windows のコマンドプロンプトで読むため）"""
    data = (ROOT / "windows" / "sumikeshi.bat").read_bytes()
    assert data.count(b"\n") == data.count(b"\r\n") > 0
    lines = data.decode("ascii").splitlines()
    run = '"%~dp0python\\python.exe" -I -X utf8 -m sumikeshi_studio.cli %*'
    assert lines[0] == "@echo off" and run in lines
    assert lines[-1] == "exit /b %ERRORLEVEL%" and "start " not in data.decode()


def test_bat_files_start_python_isolated():
    """windows/ の下の .bat で、python.exe・pythonw.exe を呼ぶ行は、どれも -I を付ける（実行したフォルダに
    ある同じ名前のモジュールや、PC の PYTHONPATH などの環境変数を読み込まない）"""
    calls = []
    for path in sorted((ROOT / "windows").glob("**/*.bat")):
        for line in path.read_text("ascii").splitlines():
            for exe in ("python.exe", "pythonw.exe"):
                at = line.lower().find(exe)
                if at >= 0:
                    after = line[at + len(exe) :].lstrip('"').split()
                    calls.append((path.relative_to(ROOT).as_posix(), after[:1]))
    assert {p for p, _ in calls} >= {
        "windows/sumikeshi.bat",
        "windows/start.bat",
    }
    assert [c for c in calls if c[1] != ["-I"]] == []
