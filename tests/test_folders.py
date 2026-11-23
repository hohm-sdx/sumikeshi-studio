# Copyright (C) 2026 Hideki Ohmura
# SPDX-License-Identifier: AGPL-3.0-only
"""選ばれたファイルとフォルダから開くファイルの一覧を作る処理（folders.py）のテスト

隠し・システムの属性とジャンクションは Windows でしか作れないので、属性とジャンクションを読む部分を置き換えて確かめる。
"""

import os
import stat
from pathlib import Path, PurePath, PureWindowsPath

from sumikeshi_studio import filetypes, folders, i18n


def touch(root: Path, *names: str) -> None:
    for name in names:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"")


def rel(root: Path, inputs: folders.Inputs) -> list[str]:
    """開くファイルの、root からの相対パス（区切りは /）"""
    return [f.path.relative_to(root).as_posix() for f in inputs.files]


def shown(*parts: str) -> str:
    """画面に出す相対パス（区切りは OS のもの）"""
    return str(PurePath(*parts))


def test_file_filter_matches_supported_suffixes(tmp_path):
    """ファイルを選ぶダイアログの形式の文字列は、開ける形式の一覧から作り、フォルダから集めるファイルの形式と同じ。
    拡張子の大文字・小文字は問わない"""
    assert (
        filetypes.file_filter()
        == "PDF・画像 (*.pdf *.jpg *.jpeg *.png *.tif *.tiff *.bmp)"
    )
    i18n.set_language(i18n.EN)  # 形式の名前は画面の言語の文
    assert (
        filetypes.file_filter()
        == "PDF and image files (*.pdf *.jpg *.jpeg *.png *.tif *.tiff *.bmp)"
    )
    i18n.set_language(i18n.JA)
    for suffix in [*filetypes.SUPPORTED_SUFFIXES, ".heic", ".txt", ".lnk"]:
        (tmp_path / f"a{suffix.upper()}").write_bytes(b"")
    got = folders.collect([tmp_path]).files
    assert sorted(f.path.suffix.lower() for f in got) == sorted(
        filetypes.SUPPORTED_SUFFIXES
    )


def test_folder_is_expanded_to_files_directly_under_it(tmp_path):
    """深さを渡さなければ、フォルダの直下の PDF・画像だけを名前の順（数字は数の大きさの順）に開く。前に出力した
    墨消しPDF は除いて、名前を skipped に入れる。直接選んだファイルは選んだ順に並べ、重ねて選んだファイルは1つにする"""
    folder = tmp_path / "書類"
    touch(
        folder,
        "10.pdf",
        "2.pdf",
        "1.JPG",
        "memo.txt",  # 対応していない種類
        "3.HEIC",  # 対応していない種類
        "1_redacted.pdf",  # 墨消し済みのPDF
        "1_redacted_2.pdf",  # 同じ名前を避けて出力したPDF
        "下のフォルダ/3.pdf",
    )
    other = tmp_path / "other.pdf"
    other.write_bytes(b"")
    got = folders.collect([folder, other, folder / "2.pdf"])
    assert [f.label for f in got.files] == ["1.JPG", "2.pdf", "10.pdf", "other.pdf"]
    assert got.skipped == ["1_redacted.pdf", "1_redacted_2.pdf"]
    assert got.errors == []


def test_selected_files_are_kept(tmp_path):
    """直接選ばれたファイルは、種類や名前を問わず残す（開けない理由は開くときに示す）"""
    files = [
        tmp_path / "memo.txt",
        tmp_path / "a_redacted.pdf",
        tmp_path / "b_redacted_2.pdf",
    ]
    got = folders.collect(files, depth=None)
    assert ([f.path for f in got.files], got.skipped) == (files, [])


def test_depth_and_order(tmp_path):
    """深さは開いたフォルダを 0 として数える。フォルダの直下のファイルを名前の順に並べ、続けてサブフォルダを名前の順
    （数字は数の大きさ、大文字と小文字を区別しない）に1つずつ、その中を同じ決まりで並べる"""
    touch(
        tmp_path,
        "10.pdf",
        "2.pdf",
        "1.JPG",
        "B/1.pdf",
        "B/C/1.pdf",
        "B/C/D/1.pdf",
        "a/1.pdf",
        "10/x.pdf",
        "9/x.pdf",
    )
    top = ["1.JPG", "2.pdf", "10.pdf"]
    first = ["9/x.pdf", "10/x.pdf", "a/1.pdf", "B/1.pdf"]
    assert rel(tmp_path, folders.collect([tmp_path], depth=0)) == top
    assert rel(tmp_path, folders.collect([tmp_path], depth=1)) == top + first
    assert rel(tmp_path, folders.collect([tmp_path], depth=2)) == [
        *top,
        *first,
        "B/C/1.pdf",
    ]
    for depth in (3, 4, None):
        assert rel(tmp_path, folders.collect([tmp_path], depth=depth)) == [
            *top,
            *first,
            "B/C/1.pdf",
            "B/C/D/1.pdf",
        ]


def test_folders_are_read_from_shallow_depth_up_to_needed_depth(tmp_path, monkeypatch):
    """フォルダは浅い深さから順に（幅優先で）読み、開く深さより深いフォルダは読まない"""
    touch(tmp_path, "A/B/1.pdf", "A/2.pdf", "C/3.pdf", "4.pdf")
    read = []
    real_scan = folders._scan

    def scan(folder):
        read.append(folder.rel.as_posix())
        real_scan(folder)

    monkeypatch.setattr(folders, "_scan", scan)
    folders.collect([tmp_path])
    assert read == ["."]
    read.clear()
    folders.collect([tmp_path], depth=1)
    assert read == [".", "A", "C"]
    read.clear()
    walk = folders.Walk([tmp_path])
    assert not walk.done(0)
    walk.step()
    assert walk.done(0) and not walk.done(1) and not walk.done(None)
    walk.run(None)
    assert read == [".", "A", "C", "A/B"] and walk.done(None) and walk.step() is None


def test_previous_outputs_are_skipped_in_subfolders(tmp_path):
    """名前（拡張子を除く）の末尾が「_redacted」か「_redacted_<数字>」のファイルは、サブフォルダの中でも除き、
    開いたフォルダからの相対パスを並べる順に skipped に入れる"""
    touch(
        tmp_path,
        "x_redacted.pdf",
        "A/x_redacted_2.pdf",
        "A/x_redacted_12.PDF",
        "A/y_redacted.png",
        "A/x_redacted_a.pdf",  # 数字でない
        "A/x_redacted2.pdf",  # 「_」がない
        "A/redacted.pdf",  # 「_」がない
        "A/x_redacted_2_copy.pdf",  # 末尾でない
        "A/x_REDACTED.pdf",  # 大文字（出力の名前は小文字だけ）
        "A/x_redacted_2.txt",  # 対応していない種類は数えない
    )
    got = folders.collect([tmp_path], depth=None)
    assert set(rel(tmp_path, got)) == {
        "A/redacted.pdf",
        "A/x_redacted_2_copy.pdf",
        "A/x_redacted_a.pdf",
        "A/x_redacted2.pdf",
        "A/x_REDACTED.pdf",
    }
    assert got.skipped == [
        "x_redacted.pdf",
        shown("A", "x_redacted_2.pdf"),
        shown("A", "x_redacted_12.PDF"),
        shown("A", "y_redacted.png"),
    ]
    assert folders.is_output("a_redacted_2.pdf") and not folders.is_output("a.pdf")


def test_hidden_dot_and_link_folders_are_not_followed(tmp_path, monkeypatch):
    """名前が「.」で始まるフォルダ、隠しかシステムの属性のフォルダ、シンボリックリンクとジャンクションのフォルダは
    たどらない。再解析ポイントの属性だけのフォルダ（OneDrive のプレースホルダーなど）はたどる。リンクのファイルは
    開く。選んだフォルダは、そのどれでも開く"""
    root = tmp_path / "in"
    touch(
        root,
        "1.pdf",
        ".git/1.pdf",
        "hidden/1.pdf",
        "System Volume Information/1.pdf",
        "junction/1.pdf",
        "onedrive/1.pdf",
        "real/1.pdf",
    )
    touch(tmp_path, "outside/x.pdf")
    (root / "link").symlink_to(root / "real", target_is_directory=True)
    (root / "up").symlink_to(root, target_is_directory=True)  # 上のフォルダを指す
    (root / "real" / "2.pdf").symlink_to(tmp_path / "outside" / "x.pdf")
    attributes = {
        "hidden": stat.FILE_ATTRIBUTE_HIDDEN,
        "System Volume Information": stat.FILE_ATTRIBUTE_HIDDEN
        | stat.FILE_ATTRIBUTE_SYSTEM,
        # 再解析ポイントと、FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS（stat にない）
        "onedrive": stat.FILE_ATTRIBUTE_REPARSE_POINT | 0x400000,
    }
    monkeypatch.setattr(folders, "_attributes", lambda e: attributes.get(e.name, 0))
    monkeypatch.setattr(folders, "_junction", lambda e: e.name == "junction")
    got = folders.collect([root], depth=None)
    assert rel(root, got) == ["1.pdf", "onedrive/1.pdf", "real/1.pdf", "real/2.pdf"]
    for name in [".git", "hidden", "junction"]:
        assert rel(root, folders.collect([root / name])) == [f"{name}/1.pdf"]
    assert rel(root, folders.collect([root / "link"])) == ["link/1.pdf", "link/2.pdf"]


def test_unreadable_folders_are_skipped_with_reason(tmp_path, monkeypatch):
    """読めないフォルダは、そのフォルダだけを除いて理由を示し、ほかは開く。サブフォルダは開いたフォルダからの
    相対パス、選んだフォルダは名前で示す"""
    touch(tmp_path, "in/1.pdf", "in/A/locked/1.pdf", "in/A/2.pdf", "locked/1.pdf")
    real_scandir = os.scandir

    def scandir(path):
        if Path(path).name == "locked":
            raise PermissionError(13, "Permission denied")
        return real_scandir(path)

    monkeypatch.setattr(folders.os, "scandir", scandir)
    got = folders.collect([tmp_path / "in", tmp_path / "locked"], depth=None)
    assert rel(tmp_path, got) == ["in/1.pdf", "in/A/2.pdf"]
    assert got.errors == [
        f"フォルダを読めません（PermissionError）: {shown('in', 'A', 'locked')}",
        "フォルダを読めません（PermissionError）: locked",
    ]
    got = folders.collect([tmp_path / "in"], depth=None)
    assert got.errors == [
        f"フォルダを読めません（PermissionError）: {shown('A', 'locked')}"
    ]


def test_same_files_are_opened_once(tmp_path):
    """同じファイルを重ねて選んだとき（同じフォルダ、フォルダとその中のファイル、フォルダとそのサブフォルダ、リンクの
    ファイルとその先）は、初めに出た1つだけを開く"""
    touch(tmp_path, "in/1.pdf", "in/A/2.pdf")
    (tmp_path / "in" / "A" / "3.pdf").symlink_to(tmp_path / "in" / "1.pdf")
    folder, sub = tmp_path / "in", tmp_path / "in" / "A"
    got = folders.collect([sub, folder, folder, folder / "1.pdf"], depth=None)
    assert rel(tmp_path, got) == ["in/A/2.pdf", "in/A/3.pdf"]
    got = folders.collect([folder / "A" / ".." / "1.pdf", folder], depth=None)
    assert rel(tmp_path, got) == ["in/A/../1.pdf", "in/A/2.pdf"]


def test_paths_of_two_or_more_folders_start_with_folder_name(tmp_path):
    """2つ以上のフォルダを開いたときは、除いた前の出力と読めないフォルダの相対パスの先頭に、開いたフォルダの名前を
    付ける。同じ名前のフォルダ（大文字と小文字を区別しない）は、2つ目から「_2」を付ける"""
    touch(
        tmp_path,
        "a/2026/x_redacted.pdf",
        "b/2026/A/x_redacted.pdf",
        "c/2026/x_redacted.pdf",
        "c/2026/1.pdf",
    )
    folders_ = [
        tmp_path / "a" / "2026",
        tmp_path / "b" / "2026",
        tmp_path / "c" / "2026",
    ]
    got = folders.collect(folders_, depth=None)
    assert got.skipped == [
        shown("2026", "x_redacted.pdf"),
        shown("2026_2", "A", "x_redacted.pdf"),
        shown("2026_3", "x_redacted.pdf"),
    ]
    # ファイルと一緒に選んだ1つのフォルダには付けない
    got = folders.collect([tmp_path / "c" / "2026" / "1.pdf", tmp_path / "a" / "2026"])
    assert got.skipped == ["x_redacted.pdf"]
    assert folders.folder_names(
        [Path("a/2026"), Path("b/2026"), Path("c/X"), Path("d/x"), Path("e/x_2")]
    ) == ["2026", "2026_2", "X", "x_2", "x_2_2"]
    assert folders.root_label(PureWindowsPath("D:\\")) == "D"
    assert folders.root_label(PureWindowsPath("\\\\server\\share\\")) == "share"


def test_found_files_carry_folder_for_labels(tmp_path):
    """開くファイルには、開いたフォルダからのフォルダの相対パスを持たせる（画面に出す相対パスと、出力先のサブフォルダ
    に使う）。2つ以上のフォルダを開いたときは、先頭にフォルダの名前を付ける。直接選んだファイルは名前だけ"""
    touch(tmp_path, "a/2026/1.pdf", "a/2026/A/B/2.pdf", "b/2026/3.pdf", "4.pdf")
    one = folders.collect([tmp_path / "a" / "2026", tmp_path / "4.pdf"], depth=None)
    assert [f.label for f in one.files] == ["1.pdf", shown("A", "B", "2.pdf"), "4.pdf"]
    assert [f.folder for f in one.files] == [PurePath(), PurePath("A", "B"), PurePath()]
    two = folders.collect(
        [tmp_path / "a" / "2026", tmp_path / "b" / "2026"], depth=None
    )
    assert [f.label for f in two.files] == [
        shown("2026", "1.pdf"),
        shown("2026", "A", "B", "2.pdf"),
        shown("2026_2", "3.pdf"),
    ]


def test_selection_knows_subfolders_from_first_read(tmp_path, monkeypatch):
    """作るときに選んだフォルダの直下だけを読み、たどるサブフォルダがあるかを知る（たどらないフォルダは数えない）"""
    touch(tmp_path, "plain/1.pdf", "hidden/.git/1.pdf", "deep/A/1.pdf")
    read = []
    real_scan = folders._scan
    monkeypatch.setattr(
        folders, "_scan", lambda f: read.append(f.path.name) or real_scan(f)
    )
    assert not folders.Selection([tmp_path / "plain"]).has_subfolders
    assert not folders.Selection([tmp_path / "hidden"]).has_subfolders
    assert not folders.Selection([tmp_path / "plain" / "1.pdf"]).has_subfolders
    assert folders.Selection([tmp_path / "plain", tmp_path / "deep"]).has_subfolders
    assert read == ["plain", "hidden", "plain", "deep"]


def test_counting_reports_each_depth_and_stops_at_limits(tmp_path, monkeypatch):
    """浅い深さから数え、深さを1つたどり終えるたびに数を入れる。件数か時間の上限を超えたら止め、
    数え終わっていない深さは、それまでに数えた数になる。開くときは、数えたときにたどった分を読み直さない"""
    touch(
        tmp_path,
        "1.pdf",
        "A/1.pdf",
        "A/2.pdf",
        "B/1.pdf",
        "A/C/1.pdf",
        "A/C/D/1.pdf",
        "A/C/D/2.pdf",
    )
    selection = folders.Selection([tmp_path])
    assert selection.counts == folders.Counts((1,), 0, False, False)
    selection.count()
    counts = selection.counts
    assert counts == folders.Counts((1, 3, 1, 2), 3, True, False)
    assert [counts.count(d) for d in (0, 1, 2, 3, None)] == [1, 4, 5, 7, 7]
    assert all(counts.exact(d) for d in (0, 1, 5, None))
    read = []
    real_scan = folders._scan
    monkeypatch.setattr(folders, "_scan", lambda f: read.append(f) or real_scan(f))
    assert selection.walk_to(None)
    assert read == [] and len(selection.inputs(None).files) == 7

    # 件数の上限（4件）：深さ1を数え終えたところで止まる
    selection = folders.Selection([tmp_path])
    selection.count(limit=4)
    counts = selection.counts
    assert (counts.done_depth, counts.complete, counts.stopped) == (1, False, True)
    assert counts.exact(1) and not counts.exact(2) and not counts.exact(None)
    assert counts.count(None) == 4
    found = []
    assert selection.walk_to(2, progress=found.append)
    assert (
        found == [5] and selection.walk.queue[0].depth == 3
    )  # 深さ2までだけ続きをたどる
    assert [
        f.path.relative_to(tmp_path).as_posix() for f in selection.inputs(2).files
    ] == [
        "1.pdf",
        "A/1.pdf",
        "A/2.pdf",
        "A/C/1.pdf",
        "B/1.pdf",
    ]

    # 時間の上限：サブフォルダの2つ目（B）を読んだところで、時間を超えた
    ticks = iter([0.0, 1.0, 20.0])
    selection = folders.Selection([tmp_path])
    selection.count(seconds=10, clock=lambda: next(ticks))
    assert selection.counts == folders.Counts((1, 3), 1, False, True)


def test_walk_to_can_be_cancelled(tmp_path):
    """続きをたどる途中で取り消されたら、そこで止めて偽を返す"""
    touch(tmp_path, "A/1.pdf", "B/1.pdf", "A/C/1.pdf")
    selection = folders.Selection([tmp_path])
    calls = []
    assert not selection.walk_to(
        None, cancelled=lambda: calls.append(1) or len(calls) > 1
    )
    assert len(selection.walk.queue) == 2  # A だけを読んだ（B と A/C が残る）


def test_counting_in_thread_stops_when_asked(tmp_path):
    """裏のスレッドで数え、止めるよう頼むと、読んでいる途中のフォルダを読み終わってから止まる"""
    touch(tmp_path, *[f"{i}/{j}/1.pdf" for i in range(30) for j in range(30)])
    selection = folders.Selection([tmp_path])
    selection.start_counting()
    selection.stop_counting()
    assert not selection._thread.is_alive()
    assert selection.walk_to(None) and len(selection.inputs(None).files) == 900
