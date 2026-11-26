# Copyright (C) 2026 Hideki Ohmura
# SPDX-License-Identifier: AGPL-3.0-only
"""導入（install.bat が動かす install.ps1）が出す文の言語のテスト

Windows PowerShell 5.1 はこの環境で動かせないので、スクリプトの文を読んで確かめる。動き（Windows の PowerShell で日本語・英語が
出ること、設定のファイルの言語を読むこと）は、配布用パッケージで、Windows の PC で確かめる。
設定のファイルの読み方は、スクリプトに書いた正規表現を取り出し、QSettings に書かせた .ini の実物に当てて確かめる。
"""

import re
from pathlib import Path

import pytest
from test_structure import JAPANESE_TEXT

ROOT = Path(__file__).resolve().parents[1]
SETUP = ROOT / "windows" / "scripts" / "install.ps1"
# Msg "日本語の文" "英語の文"（PowerShell の二重引用符の文字列。` はエスケープ文字）。WinMsg は、Windows の
# 表示の言語で選ぶもの（ショートカットの名前と説明）
MSG = re.compile(r'(?:Win)?Msg "((?:[^"`]|`.)*)" "((?:[^"`]|`.)*)"')
PLACE = re.compile(r"\{\d+\}")
# Msg の形でなくてよい、日本語の文字がある行と、その理由
NOT_MESSAGES = {
    '-match "アプリケーション制御': (
        "アプリケーション制御に止められたことを見分ける正規表現（Test-Blocked）。日本語の Windows の文に合わせる"
    ),
    "$AppNames = @(": (
        "ショートカットの名前（もう一方の言語の名前のものを消すので、言語によらず両方の言語の名前を並べる）"
    ),
}


def read(path: Path) -> str:
    data = path.read_bytes()
    assert data.startswith(b"\xef\xbb\xbf"), (
        path
    )  # Windows PowerShell 5.1 が日本語を読めるように、BOM を付ける
    return data.decode("utf-8-sig")


def message_problems(text: str, allowed=NOT_MESSAGES) -> list[str]:
    """日本語の文字がある行（説明の行を除く）のうち、Msg の形でないもの、英語の側に日本語の文字があるもの、
    日本語と英語で {0} などの番号が違うものの一覧。allowed は、Msg の形でなくてよい行の目印と、その理由"""
    problems = []
    for n, line in enumerate(text.splitlines(), 1):
        code = line.strip()
        if not code or code.startswith("#"):
            continue
        for ja, en in MSG.findall(code):
            if JAPANESE_TEXT.search(en):
                problems.append(f"{n}行：英語の文に日本語の文字があります：{en}")
            if sorted(PLACE.findall(ja)) != sorted(PLACE.findall(en)):
                problems.append(f"{n}行：日本語と英語で値の番号が違います：{ja} / {en}")
        rest = MSG.sub("", code)
        if JAPANESE_TEXT.search(rest) and not any(k in code for k in allowed):
            problems.append(f"{n}行：日本語の文が Msg の形ではありません：{code}")
    return problems


def test_message_problems_finds_mistakes():
    """Msg の形でない文、英語の側の日本語、値の番号の違いを見つけ、説明の行・外してよい行・WinMsg の形の行は数えない"""
    lines = (
        "# 説明の行は数えない",
        'Write-Host "日本語だけの文"',
        'Write-Host (Msg "文{0}" "Text {0}")',
        'Write-Host (Msg "文{0}" "Text 文")',
        'Write-Host ((Msg "{0}と{1}" "{1} and") -f $a, $b)',
        '$AppNames = @("墨消しスタジオ", "Sumikeshi Studio")',
        '$AppName = WinMsg "墨消しスタジオ" "Sumikeshi Studio"',
        'Write-Host (Msg "「`"x`"」" "`"x`"")',
    )
    text = "\n".join(lines)
    assert [p.split("：")[0] for p in message_problems(text)] == [
        "2行",
        "4行",
        "4行",
        "5行",
    ]


def test_setup_chooses_language_like_the_tool():
    """導入の言語は、ツールと同じ順に決める：引数（管理者権限で起動し直したとき）、環境変数、起動の画面で選んだ言語
    （設定のファイル）、Windows の表示の言語。Msg は、言語を決めた後に定義する"""
    text = read(SETUP)
    order = [
        "$Lang = $Language",
        "$Lang = $env:SUMIKESHI_STUDIO_LANGUAGE",
        "$Lang = Get-SavedLanguage",
        "(Get-UICulture).TwoLetterISOLanguageName",
        "function Msg(",
    ]
    places = [text.index(s) for s in order]
    assert places == sorted(places)
    assert 'Join-Path $env:APPDATA "SumikeshiStudio\\SumikeshiStudio.ini"' in text
    assert text.index("function Msg(") < text.index("(Msg ")


def test_dist_setup_passes_language_to_nativecheck():
    """導入は、部品の確認（nativecheck）を起動する前に、決めた言語を環境変数に入れる"""
    text = read(SETUP)
    assert text.index("$env:SUMIKESHI_STUDIO_LANGUAGE = $Lang") < text.index(
        '"sumikeshi_studio.nativecheck")'
    )


def test_setup_runs_forminstall_and_continues():
    """自分だけの導入は、決めた言語を環境変数に入れた後、導入先の Python で、サンプルの様式を入れる処理
    （sumikeshi_studio.forminstall）を呼ぶ。終了の番号が 0 でなくても止めず、その番号を示して続ける。すべてのユーザー
    向けの管理者権限の段階では呼ばない（利用者ごとのことは、各利用者の最初の起動で行う）"""
    text = read(SETUP)
    call = '$Code = Invoke-Python (Join-Path $Dir "python\\python.exe") $FormArguments'
    assert text.count(call) == 1
    assert text.index("$env:SUMIKESHI_STUDIO_LANGUAGE = $Lang") < text.index(call)
    per_user = text[
        text.index("function Install-PerUser") : text.index("function Install-AllUsers")
    ]
    assert call in per_user
    assert (
        "forminstall"
        not in text[
            text.index("function Install-AllUsers") : text.index(
                "function Invoke-Python"
            )
        ]
    )
    line = text[text.index(call) :].splitlines()[1]
    assert line.lstrip().startswith("if ($Code -ne 0) { Write-Host (")
    assert not re.search(
        r"\b(exit|throw|return)\b", MSG.sub("", line)
    )  # 文の外に、止める命令がない
    assert "-f $Code" in line  # 終了の番号を文に入れて示す


def ps_patterns(text: str) -> tuple[re.Pattern, re.Pattern]:
    """スクリプトの Get-SavedLanguage から、節の見出しと language の行の正規表現を取り出す（PowerShell の -match は
    大文字と小文字を区別しないので、IGNORECASE にする）"""
    section, language = re.findall(r"-match '([^']+)'", text)[:2]
    return re.compile(section, re.IGNORECASE), re.compile(language, re.IGNORECASE)


def saved_language(text: str, ini: str) -> str | None:
    """スクリプトの Get-SavedLanguage と同じ読み方で、.ini の中身から言語を読む"""
    section_re, language_re = ps_patterns(text)
    section = ""
    for line in ini.splitlines():
        line = line.strip()
        if m := section_re.match(line):
            section = m[1]
            continue
        if section == "ui" and (m := language_re.match(line)):
            return m[1]
    return None


def test_setup_reads_language_written_by_tool(tmp_path):
    """QSettings に書かせた設定のファイルの実物から、起動の画面で選んだ言語を読む。ほかの節の language、ja と en 以外の
    値、言語のないファイルは読まない（Windows の表示の言語に進む）"""
    QtCore = pytest.importorskip("PySide6.QtCore", exc_type=ImportError)

    from sumikeshi_studio import i18n, prefs

    text = read(SETUP)
    QtCore.QSettings.setPath(
        QtCore.QSettings.Format.IniFormat,
        QtCore.QSettings.Scope.UserScope,
        str(tmp_path),
    )
    s = prefs.settings()
    s.setValue(prefs.FORMS_FOLDER_KEY, "\\\\server\\共有\\様式 フォルダ")
    s.setValue(prefs.FORMS_OFF_KEY, ["u-1", "x-hoken"])
    s.setValue("windows/forms_window", QtCore.QByteArray(b"\x01\xd9\xd0"))
    for lang in (i18n.EN, i18n.JA):
        prefs.save_language(lang)
        s.sync()
        ini = Path(s.fileName()).read_text("utf-8")
        assert "[ui]" in ini
        assert saved_language(text, ini) == lang
    s.setValue(prefs.LANGUAGE_KEY, "fr")
    s.sync()
    assert saved_language(text, Path(s.fileName()).read_text("utf-8")) is None
    s.remove(prefs.LANGUAGE_KEY)
    s.sync()
    assert saved_language(text, Path(s.fileName()).read_text("utf-8")) is None
    assert saved_language(text, "[General]\nlanguage=en\n") is None
