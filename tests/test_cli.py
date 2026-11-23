# Copyright (C) 2026 Hideki Ohmura
# SPDX-License-Identifier: AGPL-3.0-only
"""コマンドラインからの出力（cli.py）のテスト

解析は、画面のテストと同じく、解析のプロセスの代わりにスレッドで行う（gui_env）。本物の解析のプロセスでの動きと、
画面の部品（QtWidgets）を読み込まないことは、最後のテストで、別のプロセスで確かめる。
"""

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pdfcheck
import pytest
from conftest import cached_analyze
from gui import log_rows
from PIL import Image
from scan import page_image
from test_english_screens import japanese_left
from test_variants import warped
from userforms import install_bundled

from sumikeshi_studio import align, cli, export, i18n, imaging, pipeline


@pytest.fixture
def forms(tmp_path):
    """配布用パッケージの3様式を入れた様式のフォルダ"""
    folder = tmp_path / "forms"
    install_bundled(folder)
    return folder


@pytest.fixture
def named(files, tmp_path):
    """記入した扶養控除等申告書と、記入なしの保険料控除申告書を、別々の名前にコピーしたもの（試験画像はどれも scan.pdf
    なので、出力の名前に「_2」が付かないように）"""
    folder = tmp_path / "in"
    folder.mkdir()
    copies = (folder / "fuyou.pdf", folder / "hoken.pdf")
    for src, dst in zip(files, copies):
        shutil.copy(src, dst)
    return copies


@pytest.fixture
def out(tmp_path):
    folder = tmp_path / "out"
    folder.mkdir()
    return folder


def run(capsys, *args, language="ja") -> tuple[int, list[str], list[str]]:
    """cli.run を呼び、終了の番号と、標準出力・標準エラー出力の行を返す"""
    code = cli.run(["--language", language] + [str(a) for a in args])
    captured = capsys.readouterr()
    return code, captured.out.splitlines(), captured.err.splitlines()


def test_usage_errors(gui_env, out, tmp_path, monkeypatch, capsys):
    """引数の誤りは、理由と「--help」の案内を標準エラー出力に出して番号 2 で終える。--help は使い方を出して 0"""
    usage = "使い方を見るには --help を付けてください。"
    for args, reason in [
        ((), "入力のファイルかフォルダを指定してください。"),
        (("a.pdf",), "出力先のフォルダを --out で指定してください。"),
        (
            ("a.pdf", "--out", tmp_path / "ない"),
            f"出力先のフォルダがありません：{tmp_path / 'ない'}",
        ),
        (("a.pdf", "--out", out, "--bogus"), "知らない引数です：--bogus"),
        (("a.pdf", "--forms"), "--forms の後に値を指定してください。"),
        (("a.pdf", "--out="), "--out の後に値を指定してください。"),
        (("a.pdf", "--depth"), "--depth の後に値を指定してください。"),
        (("a.pdf", "--depth", "-1"), "--depth には 0 以上の数を指定してください。"),
        (("a.pdf", "--depth=x"), "--depth には 0 以上の数を指定してください。"),
        (("a.pdf", "--depth=１"), "--depth には 0 以上の数を指定してください。"),
        (
            ("a.pdf", "--depth", "1", "--recursive"),
            "--depth と --recursive は、一緒には使えません。",
        ),
    ]:
        assert run(capsys, *args) == (2, [], [reason, usage]), args
    code, lines, _ = run(capsys, "--help")
    assert code == 0 and lines[0].startswith(
        "使い方：<入力のファイル・フォルダ> … --out <出力のフォルダ>"
    )
    assert lines[-1].startswith("終了の番号：0 すべてのファイルを出力した")
    # 言語の指定は、引数の誤りの文にも効く。知らない言語は、ほかの誤りより先に知らせない（画面と同じ言語で出す）
    assert run(capsys, language="en") == (
        2,
        [],
        ["Specify input files or folders.", "For usage, add --help."],
    )
    monkeypatch.setenv(i18n.LANGUAGE_ENV, i18n.JA)
    assert cli.run(["a.pdf", "--out", str(out), "--language", "fr"]) == 2
    assert (
        capsys.readouterr().err.splitlines()[0]
        == "--language には ja か en を指定してください。"
    )


def test_exports_named_and_writes_work_log(
    gui_env, named, forms, out, tmp_path, capsys
):
    """開いたファイルを1つずつ解析して出力し、作業ログに書く。出力は画像だけの PDF で、範囲が黒い"""
    code, lines, err = run(capsys, *named, "--out", out, "--forms", forms)
    assert (code, err) == (0, [])
    names = [f.stem + "_redacted.pdf" for f in named]
    assert sorted(p.name for p in out.iterdir()) == sorted(names)
    for f, name in zip(named, names):
        page = imaging.open_pages(f)[0]
        job = export.PageJob.of(page, cached_analyze(page))
        assert pdfcheck.problems(out / name, [job]) == []
    assert lines[0].startswith("墨消しスタジオ ") and lines[0].endswith(
        "：2件のファイル（全2ページ）を処理します。"
    )
    assert lines[1] == f"様式のフォルダ：{forms}（使う様式 3件）"
    assert lines[2] == "準備しています…"
    assert lines[3] == f"[1／2] {named[0].name}（1ページ）"
    assert lines[4:9] == [
        "　　1ページ目を解析しました（1／1）",
        "　　1ページ目を墨消ししています",
        "　　1ページ目を墨消ししました",
        "　　出力しました",
        f"　　✓ {names[0]}　墨消しした範囲：10か所",
    ]
    assert (
        lines[-2]
        == "出力したファイル：2件、出力しなかったファイル：0件、確かめるべきページのあるファイル：0件"
    )
    assert lines[-1].startswith("作業ログ：") and lines[-1].endswith("worklog.csv")
    rows = log_rows(tmp_path)
    assert [Path(r["Input file"]).name for r in rows] == [f.name for f in named]
    assert rows[0]["Pages not viewed"] == "1" and rows[0]["Redacted areas"] == "10"


def test_relative_paths_are_made_absolute(
    gui_env, named, forms, out, tmp_path, monkeypatch, capsys
):
    """相対パスで渡しても、絶対パスにしてから使い、作業ログの入力と出力のファイルと、標準出力に絶対パスで書く"""
    monkeypatch.chdir(named[0].parent)
    code, lines, err = run(capsys, ".", "--out", "../out", "--forms", "../forms")
    assert (code, err) == (0, [])
    assert lines[1] == f"様式のフォルダ：{forms.resolve()}（使う様式 3件）"
    rows = log_rows(tmp_path)
    assert sorted(r["Input file"] for r in rows) == sorted(
        str(f.resolve()) for f in named
    )
    assert sorted(r["Output file"] for r in rows) == sorted(
        str(out.resolve() / f"{f.stem}_redacted.pdf") for f in named
    )


def test_previous_outputs_in_folder_are_skipped_and_listed(
    gui_env, scan, forms, out, tmp_path, capsys
):
    """フォルダの中の墨消し済みのPDF は開かず、始めの行の次に、件数と相対パスをすべて出す。開けるファイルが
    なければ番号 1"""
    folder = tmp_path / "in"
    folder.mkdir()
    for name in ("a_redacted.pdf", "a_redacted_2.pdf"):
        shutil.copy(scan("2026bun_04"), folder / name)
    code, lines, err = run(capsys, folder, "--out", out, "--forms", forms)
    assert (code, err) == (1, [])
    assert lines[0].endswith("：0件のファイル（全0ページ）を処理します。")
    assert lines[1:4] == [
        "墨消し済みのPDFを2件除きました。",
        "　　a_redacted.pdf",
        "　　a_redacted_2.pdf",
    ]
    assert lines[4] == f"様式のフォルダ：{forms}（使う様式 3件）"
    assert lines[5] == "開ける PDF・画像のファイルがありませんでした。"
    assert list(out.iterdir()) == []


def test_two_folders_and_long_paths(
    gui_env, named, forms, out, tmp_path, monkeypatch, capsys
):
    """2つ以上のフォルダを渡すと、出力先の下に、渡したフォルダの名前のフォルダを作って出力する。長いパスを使えない
    PC で出力先のパスが長すぎるファイルは、出力せずに理由を出し、番号 1 で終える"""
    second = tmp_path / "second" / "in"
    second.mkdir(parents=True)
    shutil.copy(named[1], second / "x.pdf")
    first = named[0].parent
    (first / named[1].name).unlink()
    code, lines, err = run(capsys, first, second, "--out", out, "--forms", forms)
    assert (code, err) == (0, [])
    assert sorted(p.relative_to(out).as_posix() for p in out.rglob("*.pdf")) == [
        f"in/{named[0].stem}_redacted.pdf",
        "in_2/x_redacted.pdf",
    ]
    monkeypatch.setattr(cli.exporttext, "long_paths", lambda: False)
    deep = tmp_path / ("o" * (259 - len(str(tmp_path)) - 1))
    deep.mkdir()
    code, lines, _ = run(capsys, named[0], "--out", deep, "--forms", forms)
    assert code == 1 and list(deep.iterdir()) == []
    target = deep / f"{named[0].stem}_redacted.pdf"
    assert (
        f"　　✗ {named[0].name}：出力しませんでした。出力先のパスが長すぎます（{target}）。"
        "出力先のフォルダを浅い場所（ドライブのすぐ下など）にすると出力できます。"
    ) in lines


def test_depth_and_recursive_open_subfolders(
    gui_env, named, forms, out, tmp_path, capsys
):
    """--depth はその深さまで、--recursive はすべての深さまでサブフォルダの中も開き、出力先に相対パスのフォルダを作る。
    どちらもなければ直下だけ。ファイルごとの見出しは、開いたフォルダからの相対パス"""
    root = tmp_path / "root"
    (root / "A" / "B").mkdir(parents=True)
    shutil.copy(named[0], root / "1.pdf")
    shutil.copy(named[1], root / "A" / "B" / "2.pdf")

    def outputs():
        found = sorted(p.relative_to(out).as_posix() for p in out.rglob("*.pdf"))
        for p in out.rglob("*.pdf"):
            p.unlink()
        return found

    code, lines, _ = run(capsys, root, "--out", out, "--forms", forms)
    assert code == 0 and outputs() == ["1_redacted.pdf"]
    assert lines[0].endswith("：1件のファイル（全1ページ）を処理します。")
    code, _, _ = run(capsys, root, "--out", out, "--forms", forms, "--depth", "1")
    assert code == 0 and outputs() == ["1_redacted.pdf"]
    code, lines, _ = run(capsys, root, "--out", out, "--forms", forms, "--depth=2")
    assert code == 0 and outputs() == ["1_redacted.pdf", "A/B/2_redacted.pdf"]
    nested = Path("A", "B", "2.pdf")
    assert f"[2／2] {nested}（1ページ）" in lines
    # 出力したファイルは、出力先のフォルダからの相対パス
    done = f"　　✓ {Path('A', 'B', '2_redacted.pdf')}　"
    assert any(line.startswith(done) for line in lines)
    code, lines, _ = run(capsys, root, "--out", out, "--forms", forms, "--recursive")
    assert code == 0 and outputs() == ["1_redacted.pdf", "A/B/2_redacted.pdf"]
    assert lines[0].endswith("：2件のファイル（全2ページ）を処理します。")


def test_pages_to_check_are_listed(gui_env, scan, forms, out, tmp_path, capsys):
    """確かめるべきページ（ここでは様式不明のページ）のあるファイルも出力し、種類ごとにページを出す。終了の番号は 0"""
    unknown = scan("2026bun_08")  # どの様式の見本にも類似しない用紙
    code, lines, _ = run(capsys, unknown, "--out", out, "--forms", forms)
    assert code == 0 and (out / f"{unknown.stem}_redacted.pdf").exists()
    assert lines[lines.index("　　確かめるべきページ：") + 1 :][:3] == [
        "　　　　⚠ 要確認：1",
        "　　　　様式不明：1",
        "　　　　墨消しなし：1",
    ]
    assert lines[-2].endswith("確かめるべきページのあるファイル：1件")
    [row] = log_rows(tmp_path)
    assert (row["Pages to review"], row["Pages without redaction areas"]) == ("1", "1")


def test_match_columns_and_variant_page(
    gui_env, named, forms, out, tmp_path, monkeypatch, capsys
):
    """見本との位置合わせで判別したページは、作業ログの「Alignment points」に1段目の点の数を書く。バリエーションと
    決めたページは、範囲を置いて墨消しし、確かめるべきページの「⚠ 要確認」に挙げ（終了の番号は 0）、「Line match」に
    罫線の一致度を書く。見本と位置合わせできて、罫線の対応で置き直したページは、両方の列に書き、「⚠ 要確認」に挙げる"""
    code, _, _ = run(capsys, named[0], "--out", out, "--forms", forms)
    assert code == 0
    [row] = log_rows(tmp_path)
    head, points = row["Alignment points"].split(": ")
    assert head == "1" and int(points) >= align.MIN_INLIERS and row["Line match"] == ""
    img, _ = warped("2026bun_04", "nta-hoken-r8")
    replaced = tmp_path / "in" / "replaced.png"
    Image.fromarray(img).save(replaced, dpi=(200, 200))
    # 字の形が見本と同じで、行の高さと列の幅だけが違う用紙：位置合わせが成り立ち、罫線の対応で置き直す
    code, lines, _ = run(capsys, replaced, "--out", out, "--forms", forms)
    assert code == 0 and (out / "replaced_redacted.pdf").exists()
    assert lines[lines.index("　　確かめるべきページ：") + 1] == "　　　　⚠ 要確認：1"
    row = log_rows(tmp_path)[-1]
    head, points = row["Alignment points"].split(": ")
    assert head == "1" and int(points) >= align.MIN_INLIERS
    assert re.fullmatch(r"1: 0\.\d\d/0\.\d\d", row["Line match"])
    path = tmp_path / "in" / "variant.png"
    Image.fromarray(img).save(path, dpi=(200, 200))
    # 字の形が違って位置合わせが成り立たない用紙（給与ソフトの用紙など）に見立てる
    monkeypatch.setattr(align, "upright_group", lambda *a, **k: None)
    code, lines, _ = run(capsys, path, "--out", out, "--forms", forms)
    assert code == 0 and (out / "variant_redacted.pdf").exists()
    assert lines[lines.index("　　確かめるべきページ：") + 1] == "　　　　⚠ 要確認：1"
    row = log_rows(tmp_path)[-1]
    assert row["Alignment points"] == "" and re.fullmatch(
        r"1: 0\.\d\d/0\.\d\d", row["Line match"]
    )
    assert row["Line match below limit"] == ""
    # 表の作りが扶養控除等申告書に似た別の帳票（公的年金等の受給者の扶養親族等申告書）：様式不明のままになり、
    # 下限に届かなかった一致度と下限を「Line match below limit」に書く
    other = tmp_path / "in" / "nenkin.png"
    Image.fromarray(page_image("2027bun_09")).save(other, dpi=(200, 200))
    code, lines, _ = run(capsys, other, "--out", out, "--forms", forms)
    row = log_rows(tmp_path)[-1]
    assert row["Line match"] == "" and re.fullmatch(
        r"1: 0\.\d\d/0\.\d\d < 0\.65 \(nta-fuyou-r9 r0\)",
        row["Line match below limit"],
    )


def test_named_not_opened_or_already_exported(
    gui_env, named, forms, out, tmp_path, capsys
):
    """開けないファイルと、出力先に同じ名前があるファイルは出力せず（番号 1）、ほかのファイルは出力する。
    --overwrite を付けると上書きする"""
    broken = tmp_path / "壊れた.pdf"
    broken.write_bytes(b"x")
    existing = out / f"{named[1].stem}_redacted.pdf"
    existing.write_bytes(b"old")
    code, lines, _ = run(
        capsys, named[0], named[1], broken, "--out", out, "--forms", forms
    )
    assert code == 1 and existing.read_bytes() == b"old"
    assert (out / f"{named[0].stem}_redacted.pdf").exists()
    assert "次のファイルは開けませんでした。ほかのファイルは開きます。" in lines
    assert (
        f"　　✗ {named[1].name}：出力しませんでした。出力先に同じ名前のファイルがあります（{existing}）。"
        "上書きするときは --overwrite を付けてください。"
    ) in lines
    assert (
        lines[-2]
        == "出力したファイル：1件、出力しなかったファイル：2件、確かめるべきページのあるファイル：0件"
    )
    code, _, _ = run(capsys, named[1], "--out", out, "--forms", forms, "--overwrite")
    assert code == 0 and existing.read_bytes().startswith(b"%PDF")


def test_forms_problems_stop_before_any_file(gui_env, files, out, tmp_path, capsys):
    """様式のフォルダを読めない、使う様式に読めないものがある、使う様式がないときは、どのファイルも処理せずに番号 2"""
    code, lines, err = run(capsys, files[0], "--out", out, "--forms", tmp_path / "ない")
    assert (code, lines) == (2, []) and err[
        0
    ] == "様式のフォルダを読めないため、ファイルを開けません。"
    broken = tmp_path / "broken"
    install_bundled(broken)
    (broken / "u-broken.json").write_text("{", encoding="utf-8")
    code, lines, err = run(capsys, files[0], "--out", out, "--forms", broken)
    assert (code, lines) == (2, []) and err[
        0
    ] == "次の様式を読めないため、ファイルを開けません。"
    empty = tmp_path / "empty"
    empty.mkdir()
    code, lines, err = run(capsys, files[0], "--out", out, "--forms", empty)
    assert (code, lines) == (2, [])
    assert err == [
        f"使う様式が1つもないため、どのファイルも処理しません（様式のフォルダ：{empty}）。"
    ]
    assert list(out.iterdir()) == []


def test_failed_page_is_exported_without_redaction(
    gui_env, two_pages, forms, out, tmp_path, monkeypatch, capsys
):
    """解析が失敗したページは、墨消しせずに出力し、確かめるべきページとして理由と一緒に出す（番号 0）"""

    def analyze(page, *args, **kwargs):
        if page.index == 1:
            raise RuntimeError("試験用の失敗")
        return cached_analyze(page, *args, **kwargs)

    monkeypatch.setattr(pipeline, "analyze", analyze)
    code, lines, _ = run(capsys, two_pages, "--out", out, "--forms", forms)
    assert code == 0
    i = lines.index("　　確かめるべきページ：")
    assert lines[i + 1 :][-4:-2] == [
        "　　　　✕ 処理できません（墨消しせずに出力しました）：2",
        "　　　　　　2ページ目：RuntimeError: 試験用の失敗",
    ]
    output = out / f"{two_pages.stem}_redacted.pdf"
    assert pdfcheck.text_left_in(output) == []
    with imaging.PDF_LOCK, imaging.open_pdf(output) as pdf:
        assert len(pdf) == 2
    [row] = log_rows(tmp_path)
    assert row["Pages not analyzed"] == "2" and row["Pages"] == "2"


def test_page_that_cannot_be_rendered_stops_the_file(
    gui_env, files, forms, out, monkeypatch, capsys
):
    """ページを画像にできなければ、そのファイルは出力しない（一時的なファイルも残さない。番号 1）"""

    def broken(job):
        raise OSError("読めない")

    monkeypatch.setattr(export, "mask_page", broken)
    code, lines, _ = run(capsys, files[0], "--out", out, "--forms", forms)
    assert code == 1 and list(out.iterdir()) == []
    assert f"　　✗ {files[0].name}：出力できませんでした。OSError: 読めない" in lines


def test_results_keep_no_work_image(
    gui_env, two_pages, forms, out, monkeypatch, capsys
):
    """1つのファイルのページの解析の結果は、作業画像を持たずに、高さ・幅だけを持って出力まで待つ（ページが多くても
    メモリが増え続けないように）。出力は高さ・幅で範囲を換算するので、今までどおり墨消しする"""
    kept = []
    export_file = cli.Runner.export

    def keep_then_export(self, doc):
        kept.extend(st.result for st in doc.pages)
        export_file(self, doc)

    monkeypatch.setattr(cli.Runner, "export", keep_then_export)
    code, _, err = run(capsys, two_pages, "--out", out, "--forms", forms)
    assert (code, err) == (0, [])
    assert len(kept) == 2
    for res, page in zip(kept, imaging.open_pages(two_pages)):
        assert res.work is None
        assert res.work_shape == cached_analyze(page).work.shape
    (pdf,) = out.iterdir()
    jobs = [
        export.PageJob.of(p, r) for p, r in zip(imaging.open_pages(two_pages), kept)
    ]
    assert pdfcheck.problems(pdf, jobs) == []


def test_stops_after_current_file_on_ctrl_c(
    gui_env, named, forms, out, monkeypatch, capsys
):
    """Ctrl+C を受け取ると、今のファイルの出力が終わってから止め、残りのファイルは処理しない（番号 3）"""
    export_file = cli.Runner.export

    def export_then_stop(self, doc):
        self.stop_requested = (
            True  # 1つ目のファイルの解析が終わった後に Ctrl+C を押したことにする
        )
        export_file(self, doc)

    monkeypatch.setattr(cli.Runner, "export", export_then_stop)
    code, lines, _ = run(capsys, *named, "--out", out, "--forms", forms)
    assert code == 3
    assert [p.name for p in out.iterdir()] == [f"{named[0].stem}_redacted.pdf"]
    assert "途中で止めました。残りの1件のファイルは処理していません。" in lines


def test_stops_during_analysis_without_exporting(
    gui_env, two_pages, forms, out, monkeypatch, capsys
):
    """解析中に Ctrl+C を受け取ると、そのファイルは出力せずに止める（番号 3）"""
    page_done = cli.Runner.page_done

    def stop_on_first_page(self, *args):
        self.stop_requested = True
        page_done(self, *args)

    monkeypatch.setattr(cli.Runner, "page_done", stop_on_first_page)
    code, lines, _ = run(capsys, two_pages, "--out", out, "--forms", forms)
    assert code == 3 and list(out.iterdir()) == []
    assert "途中で止めました。残りの1件のファイルは処理していません。" in lines


def test_english_output_has_no_japanese(gui_env, scan, named, forms, out, capsys):
    """英語の指定では、出力の文に日本語が残らない（ファイルの名前・様式の名前・パスを除く）"""
    unknown = scan("2026bun_08")
    code, lines, _ = run(
        capsys, named[0], unknown, "--out", out, "--forms", forms, language="en"
    )
    assert code == 0 and lines[2] == "Preparing…" and "    Pages to check:" in lines
    names = [named[0].name, unknown.name, str(forms), str(out)]
    names += [str(out / f"{f.stem}_redacted.pdf") for f in (named[0], unknown)]
    found = [("cli", "line", line) for line in lines]
    assert japanese_left(found, names + [str(Path(os.environ["LOCALAPPDATA"]))]) == []


def test_command_line_in_its_own_process(scan, tmp_path):
    """別のプロセスで、本物の解析のプロセスを使って出力する。画面の部品（QtWidgets）は読み込まない"""
    forms = tmp_path / "forms"
    install_bundled(forms)
    out = tmp_path / "out"
    out.mkdir()
    script = tmp_path / "run_cli.py"
    script.write_text(
        "import sys\n"
        "from sumikeshi_studio import cli\n"
        "if __name__ == '__main__':\n"
        "    code = cli.run(sys.argv[1:])\n"
        "    print('QtWidgets loaded:', 'PySide6.QtWidgets' in sys.modules)\n"
        "    sys.exit(code)\n",
        encoding="utf-8",
    )
    page = scan("2027bun_01", filled=True)
    env = os.environ | {
        "LOCALAPPDATA": str(tmp_path / "appdata"),
        "XDG_CONFIG_HOME": str(tmp_path / "config"),
        "SUMIKESHI_STUDIO_ANALYSIS_PROCESSES": "1",
    }
    done = subprocess.run(
        [
            sys.executable,
            "-I",
            str(script),
            str(page),
            "--out",
            str(out),
            "--forms",
            str(forms),
            "--language",
            "ja",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
        timeout=600,
        check=False,
    )
    assert done.returncode == 0, done.stdout + done.stderr
    lines = done.stdout.splitlines()
    assert lines[-1] == "QtWidgets loaded: False"
    assert f"　　✓ {page.stem}_redacted.pdf　墨消しした範囲：10か所" in lines
    assert (out / f"{page.stem}_redacted.pdf").exists()


def test_module_entry_point_reports_not_exported(scan, tmp_path):
    """python -m sumikeshi_studio.cli で起動できる（配布用パッケージの sumikeshi.bat と同じ呼び方）。出力先に同じ名前の
    ファイルがあるファイルだけなら、解析せずに何も出力しないで、番号 1 で終える"""
    forms = tmp_path / "forms"
    install_bundled(forms)
    out = tmp_path / "out"
    out.mkdir()
    page = scan("2027bun_01")
    existing = out / f"{page.stem}_redacted.pdf"
    existing.write_bytes(b"old")
    env = os.environ | {
        "LOCALAPPDATA": str(tmp_path / "appdata"),
        "XDG_CONFIG_HOME": str(tmp_path / "config"),
        "SUMIKESHI_STUDIO_ANALYSIS_PROCESSES": "1",
    }
    done = subprocess.run(
        [sys.executable, "-I", "-m", "sumikeshi_studio.cli", str(page), "--out",
         str(out), "--forms", str(forms), "--language", "ja"],
        capture_output=True, text=True, encoding="utf-8", env=env, timeout=600,
        check=False,
    )  # fmt: skip
    assert done.returncode == cli.EXIT_NOT_EXPORTED == 1, done.stdout + done.stderr
    assert existing.read_bytes() == b"old"
    assert "出力先に同じ名前のファイルがあります" in done.stdout
