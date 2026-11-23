# Copyright (C) 2026 Hideki Ohmura
# SPDX-License-Identifier: AGPL-3.0-only
"""導入でサンプルの様式を入れる処理（sumikeshi_studio.forminstall）のテスト

問いの答えは、置き換えた ask で渡す。main（install.ps1 が呼ぶ形）は、別のプロセスで、標準入力から答えを渡して確かめる。
Windows のコンソールでの問いと答え（日本語の文の表示、Enter だけで入れること）は、Windows の PC で確かめる。
"""

import csv
import json
import os
import subprocess
import sys

import pytest
from userforms import write_form

from sumikeshi_studio import forminstall, formsave, formstext, formstore, i18n

NAMES = "保険料控除申告書、基・配・特・所の申告書、扶養控除等申告書"
IDS = ["nta-hoken-r8", "nta-kihai-r8", "nta-fuyou-r9"]


def run(folder, answers, origin=formstore.ORIGIN_DEFAULT, years=False, answer=None):
    """answers を順に答えて入れる。(入れた様式, 出した問い, 出した文) を返す。答えが尽きたら入力が閉じたものとする。
    answer は引数で渡す答え（--samples）"""
    asked, said, rest = [], [], list(answers)

    def ask(question):
        asked.append(question)
        if not rest:
            raise EOFError
        answer = rest.pop(0)
        return answer(folder) if callable(answer) else answer

    saved = forminstall.install(
        formstore.FolderChoice(folder, origin),
        ask,
        said.append,
        user="yamada",
        years=years,
        answer=answer,
    )
    return saved, asked, said


def change_rows(folder):
    with (folder / formsave.CHANGE_LOG).open(encoding="utf-8-sig") as f:
        return list(csv.reader(f))[1:]


@pytest.mark.parametrize("answer", ["", "y", "Y", "yes", " YES ", "ｙ"])
def test_empty_folder_gets_samples(answer, tmp_path):
    """様式がなければ尋ね、空の答え（Enter だけ）・Y（大文字・小文字、全角、前後の空白）で入れる。入れた様式は
    様式のフォルダから読めて状態が「問題なし」になり、版は1、変更の記録に setup の行が足される"""
    folder = tmp_path / "forms"
    saved, asked, said = run(folder, [answer])
    assert folder.is_dir()  # 既定の場所は、なければ作る
    assert asked == [
        (
            f"様式のフォルダ（{folder}）に様式がありません。サンプルとして、国税庁の様式（{NAMES}）を入れますか？ "
            "入れるときは Y、入れないときは N を入力して Enter を押してください（何も入力せずに Enter を押すと"
            "入れます）。[Y/n] "
        )
    ]
    assert said == [
        f"様式のフォルダ（{folder}）に、サンプルの様式を入れました：{NAMES}"
    ]
    assert [s.id for s in saved] == IDS
    loaded = formstore.load(formstore.FolderChoice(folder, formstore.ORIGIN_USER))
    assert loaded.problems == [] and [f.id for f in loaded.active] == IDS
    for spec in loaded.forms:
        assert formstext.form_status(spec) == ("✓ 問題なし", "good")
        assert (spec.revision, spec.updated_by, spec.image) == (
            1,
            "yamada",
            f"{spec.id}-r1.png",
        )
        assert spec.saved_with and spec.updated_at
    assert [r[1:] for r in change_rows(folder)] == [
        ["yamada", "setup", "保険料控除申告書", "1", "1"],
        ["yamada", "setup", "基・配・特・所の申告書", "1", "4"],
        ["yamada", "setup", "扶養控除等申告書", "1", "10"],
    ]
    # 配布用パッケージの .json の作り方の記録（source など）は、保存した .json に書かない
    data = json.loads((folder / "nta-fuyou-r9.json").read_text("utf-8"))
    assert "source" not in data and data["revision"] == 1


@pytest.mark.parametrize(
    "answers", [["n"], ["NO"], ["ｎ"], []], ids=["n", "NO", "全角", "EOF"]
)
def test_no_or_closed_input_adds_nothing(answers, tmp_path):
    """N と答えたとき・入力が閉じているとき（画面から実行していない）は入れない"""
    saved, asked, said = run(tmp_path, answers)
    assert saved == [] and len(asked) == 1
    assert said == [
        (
            "サンプルの様式は入れませんでした。あとで入れるときは、様式のフォルダに様式がない状態で、起動の画面の"
            "「サンプルの様式を入れる」を押してください。"
        )
    ]
    assert list(tmp_path.iterdir()) == []


def test_other_answer_asks_again(tmp_path):
    """読めない答えなら、同じ問いをもう一度出す"""
    saved, asked, _ = run(tmp_path, ["はい", "x", "y"])
    assert len(asked) == 3 and asked[0] == asked[1] == asked[2]
    assert [s.id for s in saved] == IDS


@pytest.mark.parametrize("name", ["u-aaaa.json", "nta-hoken-r8.json"])
def test_folder_with_forms_is_not_asked(name, tmp_path):
    """様式が1つでもあれば（読めない .json も数える）、尋ねず何も入れない"""
    (tmp_path / name).write_text("{ 読めない", encoding="utf-8")
    saved, asked, said = run(tmp_path, ["y"])
    assert saved == [] and asked == []
    assert said == [
        f"様式のフォルダ（{tmp_path}）に様式があるため、サンプルの様式は入れません。"
    ]
    assert sorted(p.name for p in tmp_path.iterdir()) == [name]


def test_files_that_are_not_forms_do_not_count(tmp_path):
    """識別子の形に合わないファイル（x-….json）や、様式でないファイルだけなら、様式がないものとして尋ねる"""
    write_form(tmp_path, "x-fuyou", "扶養控除等申告書")
    (tmp_path / "メモ.json").write_text("{}", encoding="utf-8")
    saved, asked, _ = run(tmp_path, ["y"])
    assert len(asked) == 1 and [s.id for s in saved] == IDS


def test_forms_saved_while_asking_are_kept(tmp_path):
    """答えるまでのあいだに、ほかの PC が様式を保存したら、入れずに理由を出す"""

    def other_pc_saves(folder):
        write_form(folder, "u-0001", "ほかの PC で登録した様式")
        return "y"

    saved, asked, said = run(tmp_path, [other_pc_saves])
    assert saved == [] and len(asked) == 1
    assert said == [
        f"尋ねているあいだに様式のフォルダ（{tmp_path}）に様式が保存されたため、サンプルの様式は入れませんでした。"
    ]
    assert sorted(p.name for p in tmp_path.glob("*.json")) == ["u-0001.json"]


def test_unreachable_chosen_folder_is_not_created(tmp_path):
    """起動の画面で選んだ様式のフォルダに届かなければ、尋ねずに理由を出して終える。フォルダは作らない"""
    folder = tmp_path / "共有" / "様式"
    saved, asked, said = run(folder, ["y"], origin=formstore.ORIGIN_USER)
    assert saved == [] and asked == []
    (text,) = said
    assert text.startswith(
        f"起動の画面で選んだ様式のフォルダ（{folder}）に届かないため、サンプルの様式は入れませんでした（"
        "FileNotFoundError: "
    )
    assert text.endswith(
        "）。導入は続けます。そのフォルダにつながる状態で様式がなければ、起動の画面の「サンプルの様式を入れる」で"
        "入れられます。"
    )
    assert not (tmp_path / "共有").exists()


def test_unreadable_default_folder(tmp_path):
    """既定の場所を作れない・読めないときも、尋ねずに理由を出して終える"""
    (tmp_path / "ファイル").write_text("", encoding="utf-8")
    folder = tmp_path / "ファイル" / "forms"  # ファイルの下には作れない
    saved, asked, said = run(folder, ["y"])
    assert saved == [] and asked == []
    (text,) = said
    assert text.startswith(
        f"既定の様式のフォルダ（{folder}）を読めないため、サンプルの様式は入れませんでした（NotADirectoryError: "
    )
    assert text.endswith("）。導入は続けます。")


def test_failure_on_the_way_leaves_no_form(tmp_path, monkeypatch):
    """保存の途中で失敗したら、この導入で書いた .json を消して様式のない状態に戻し、理由を出す。もう一度導入すると、
    また尋ねる"""
    real = formsave.save
    calls = []

    def fail_second(folder, spec, *args):
        calls.append(spec.id)
        if len(calls) == 2:
            raise PermissionError(13, "アクセスが拒否されました")
        return real(folder, spec, *args)

    monkeypatch.setattr(formsave, "save", fail_second)
    saved, asked, said = run(tmp_path, ["y"])
    assert saved == [] and calls == IDS[:2]
    assert said == [
        (
            "サンプルの様式を入れられませんでした（PermissionError: アクセスが拒否されました）。途中まで入れた"
            "様式は削除しました。導入は続けます。"
        )
    ]
    assert not formstore.has_forms(tmp_path)
    monkeypatch.setattr(formsave, "save", real)
    saved, asked, _ = run(tmp_path, ["y"])
    assert len(asked) == 1 and [s.id for s in saved] == IDS
    # 消した様式の版の画像（-r1）が残っていても、上書きせずに版を進める
    assert {s.id: s.revision for s in saved} == {
        "nta-hoken-r8": 2,
        "nta-kihai-r8": 1,
        "nta-fuyou-r9": 1,
    }


def test_revision_follows_history(tmp_path):
    """history に同じ識別子の前の版があれば、版はその次にする（前に入れて削除したフォルダ）"""
    saved, _, _ = run(tmp_path, ["y"])
    for spec in saved:
        formsave.move_to_history(tmp_path, spec.id, formsave.OP_DELETE, "yamada")
    assert not formstore.has_forms(tmp_path)
    saved, _, _ = run(tmp_path, ["y"])
    assert [(s.revision, s.image) for s in saved] == [
        (2, f"{form_id}-r2.png") for form_id in IDS
    ]


def test_warnings_after_saving_are_shown(tmp_path, monkeypatch):
    """保存した後の処理（変更の記録に書く）ができなかったときは、入れた文に、できなかったことを添える"""

    def locked(*a, **k):
        raise PermissionError(13, "Permission denied", formsave.CHANGE_LOG)

    monkeypatch.setattr(formsave, "append_change", locked)
    saved, _, said = run(tmp_path, ["y"])
    assert len(saved) == 3
    (text,) = said
    lines = text.splitlines()
    assert lines[0] == (
        f"様式のフォルダ（{tmp_path}）に、サンプルの様式を入れました：{NAMES}。ただし、次のことができませんでした。"
    )
    assert len(lines) == 4 and all("changes.csv" in line for line in lines[1:])


def test_english_messages(tmp_path):
    """英語の導入では、文を英語にし、様式の名前は日本語のまま「, 」でつなぐ"""
    i18n.set_language(i18n.EN)
    _, asked, said = run(tmp_path / "forms", [""])
    names = "保険料控除申告書, 基・配・特・所の申告書, 扶養控除等申告書"
    assert asked[0].startswith(
        f"The forms folder ({tmp_path / 'forms'}) has no forms. Add the National Tax Agency forms ({names}) "
        "as samples? Type Y to add them"
    )
    assert asked[0].endswith("[Y/n] ")
    assert said == [
        f"Added the sample forms to the forms folder ({tmp_path / 'forms'}): {names}"
    ]
    _, _, said = run(tmp_path / "forms", ["y"])
    assert said == [
        f"The sample forms are not added because the forms folder ({tmp_path / 'forms'}) already has forms."
    ]


def test_sample_items_are_names_and_years_from_bundled_json():
    """入れる様式は、配布用パッケージの3様式の .json の name と year から、名前と年分だけを示す（名前の順）"""
    assert forminstall.sample_items() == [
        "保険料控除申告書（令和8年分）",
        "基・配・特・所の申告書（令和8年分）",
        "扶養控除等申告書（令和9年分）",
    ]
    i18n.set_language(i18n.EN)
    try:
        assert forminstall.sample_items()[2] == "扶養控除等申告書 (Reiwa 9)"
    finally:
        i18n.set_language(i18n.JA)


def test_years_option_shows_years_in_question_and_result(tmp_path):
    """years（install.ps1 が --years で付ける）なら、問いと入れた後の文の名前に年分を添える。付けなければ今のまま"""
    with_years = "、".join(forminstall.sample_items())
    _, asked, said = run(tmp_path / "a", [""], years=True)
    assert f"国税庁の様式（{with_years}）を入れますか？" in asked[0]
    assert said[-1].endswith(with_years)
    _, asked, _ = run(tmp_path / "b", [""])
    assert f"国税庁の様式（{NAMES}）を入れますか？" in asked[0]


def test_answer_from_argument_skips_question(tmp_path):
    """引数の答え（install.ps1 が -Samples から --samples で渡す）があれば尋ねない。True なら入れ、False なら入れない。
    様式があれば、答えによらず何も入れない"""
    folder = tmp_path / "yes"
    saved, asked, said = run(folder, [], answer=True, years=True)
    assert asked == [] and [s.id for s in saved] == IDS
    assert said == [
        f"様式のフォルダ（{folder}）に、サンプルの様式を入れました："
        + "、".join(forminstall.sample_items())
    ]
    saved, asked, said = run(tmp_path / "no", [], answer=False)
    assert saved == [] and asked == [] and said == [forminstall.NOT_ADDED]
    assert list((tmp_path / "no").iterdir()) == []
    has = tmp_path / "has"
    has.mkdir()
    write_form(has, "u-0001", "登録した様式")
    saved, asked, said = run(has, [], answer=True)
    assert (
        saved == []
        and asked == []
        and said == [forminstall.HAS_FORMS.format(folder=has)]
    )


@pytest.mark.parametrize(
    ("args", "expected"),
    [
        ([], (False, None)),
        (["--years"], (True, None)),
        (["--years", "--samples", "y"], (True, True)),
        (["--samples", "N", "--years"], (True, False)),
        (["--samples", "n"], (False, False)),
        (["--samples"], None),  # 値がない
        (["--samples", "yes"], None),
        (["--samples", ""], None),
        (["--sample", "y"], None),  # 知らない引数
        (["--years", "x"], None),
    ],
)
def test_parse_arguments(args, expected):
    """--years と --samples y|n だけを読む（大文字と小文字は区別しない）。決まりに合わなければ None"""
    assert forminstall.parse_arguments(args) == expected


def test_main_with_samples_argument_does_not_ask(tmp_path):
    """--samples y|n を付けると、入力を読まずにその答えにする。値が決まりに合わなければ、何も入れずに 2 で終わる"""
    env = {
        **os.environ,
        "LOCALAPPDATA": str(tmp_path / "appdata"),
        "XDG_CONFIG_HOME": str(tmp_path / "config"),
        i18n.LANGUAGE_ENV: i18n.EN,
    }
    cmd = [sys.executable, "-m", "sumikeshi_studio.forminstall", "--years"]
    default = tmp_path / "appdata" / "SumikeshiStudio" / formstore.DEFAULT_FOLDER

    def main(*args):
        return subprocess.run(
            [*cmd, *args],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            env=env,
            timeout=120,
            check=False,
        )

    out = main("--samples", "x")
    assert out.returncode == 2, out.stderr
    assert out.stdout == (
        "The sample forms were not added because the arguments (--years --samples x) are not valid. "
        "Use y or n for --samples.\n"
    )
    assert not default.exists() or list(default.iterdir()) == []
    out = main("--samples", "n")
    assert out.returncode == 0, out.stderr
    assert "Add the National Tax Agency forms" not in out.stdout  # 問いを出さない
    assert out.stdout.startswith("The sample forms were not added.")
    assert list(default.iterdir()) == []
    out = main("--samples", "y")
    assert out.returncode == 0, out.stderr
    assert out.stdout.startswith(
        f"Added the sample forms to the forms folder ({default})"
    )
    assert sorted(p.name for p in default.glob("*.json")) == sorted(
        f"{i}.json" for i in IDS
    )


def test_add_samples_from_start_screen(tmp_path):
    """起動の画面から入れる（add_samples）：様式がなければ入れて (様式, できなかったこと) を返し、あれば入れずに
    (None, [])。途中で失敗したら、入れた分を消して例外を伝える"""
    folder = tmp_path / "forms"
    folder.mkdir()
    saved, warnings = forminstall.add_samples(folder, user="yamada")
    assert [s.id for s in saved] == IDS and warnings == []
    assert {r[2] for r in change_rows(folder)} == {formsave.OP_SETUP}
    assert forminstall.add_samples(folder) == (None, [])
    other = tmp_path / "other"
    other.mkdir()
    real_save = formsave.save
    calls = []

    def save_twice_then_fail(*a, **k):
        calls.append(a)
        if len(calls) == 3:
            raise OSError(28, "ディスクの空き領域が不足しています。")
        return real_save(*a, **k)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(formsave, "save", save_twice_then_fail)
        with pytest.raises(OSError):
            forminstall.add_samples(other)
    assert list(other.glob("*.json")) == []


def test_main_reads_forms_folder_like_the_tool(tmp_path):
    """install.ps1 が呼ぶ形（python -m sumikeshi_studio.forminstall）で動かす：様式のフォルダはツールと同じく設定の
    ファイルの起動の画面で選んだフォルダ、なければ既定の場所。言語は環境変数で決め、答えは標準入力から読み、0 で
    終わる"""
    env = {
        **os.environ,
        "LOCALAPPDATA": str(tmp_path / "appdata"),
        "XDG_CONFIG_HOME": str(tmp_path / "config"),
        i18n.LANGUAGE_ENV: i18n.EN,
    }
    cmd = [sys.executable, "-m", "sumikeshi_studio.forminstall"]
    default = tmp_path / "appdata" / "SumikeshiStudio" / formstore.DEFAULT_FOLDER
    out = subprocess.run(
        cmd,
        input="\n",
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
        check=False,
    )
    assert out.returncode == 0, out.stderr
    assert out.stdout.startswith(f"The forms folder ({default}) has no forms.")
    assert f"Added the sample forms to the forms folder ({default})" in out.stdout
    assert sorted(p.name for p in default.glob("*.json")) == sorted(
        f"{i}.json" for i in IDS
    )
    # 起動の画面で選んだフォルダ（設定のファイルの [forms] の folder）
    chosen = tmp_path / "chosen"
    chosen.mkdir()
    ini = tmp_path / "config" / "SumikeshiStudio" / "SumikeshiStudio.ini"
    ini.parent.mkdir(parents=True, exist_ok=True)
    ini.write_text(f"[forms]\nfolder={chosen}\n", encoding="utf-8")
    out = subprocess.run(
        cmd,
        input="n\n",
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
        check=False,
    )
    assert out.returncode == 0, out.stderr
    assert out.stdout.startswith(f"The forms folder ({chosen}) has no forms.")
    assert "The sample forms were not added." in out.stdout
    assert list(chosen.iterdir()) == []
    # 入力が閉じている
    out = subprocess.run(
        cmd,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
        check=False,
    )
    assert out.returncode == 0 and "The sample forms were not added." in out.stdout
    # install.ps1 が呼ぶ形（--years）では、問いの名前に年分を添える
    out = subprocess.run(
        [*cmd, "--years"],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
        check=False,
    )
    assert out.returncode == 0 and "扶養控除等申告書 (Reiwa 9)" in out.stdout
