# Copyright (C) 2026 Hideki Ohmura
# SPDX-License-Identifier: AGPL-3.0-only
"""選ばれたファイルとフォルダから、開くファイルの一覧を作る（フォルダをたどる深さ・除くもの・並べる順）

墨消しの画面が起動してすぐに使うので、重いライブラリを読み込まない。
"""

import os
import re
import stat
import threading
import time
from collections import deque
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass, field
from pathlib import Path, PurePath

from . import filetypes
from .i18n import tr

# 出力したPDFの名前の末尾
OUTPUT_MARK = "_redacted"
# 墨消し済みのPDF とみなす名前（拡張子を除く）の末尾。同じ名前を避けて付けた「_redacted_2」なども含む
_OUTPUT_END = re.compile(re.escape(OUTPUT_MARK) + r"(?:_[0-9]+)?$")
# たどらないフォルダの、Windows の属性（隠し・システム）。再解析ポイントの属性では判定しない。OneDrive の
# クラウドのプレースホルダーのフォルダもこの属性を持つことがあり、除いてしまうため
_HIDDEN_ATTRIBUTES = stat.FILE_ATTRIBUTE_HIDDEN | stat.FILE_ATTRIBUTE_SYSTEM
# 除いた墨消し済みのPDFを、マウスを重ねたときの説明に並べる数
SKIPPED_SHOWN = 10


def is_output(name: str) -> bool:
    """墨消し済みのPDF の名前か（拡張子を除いた名前の末尾が「_redacted」か「_redacted_<数字>」）"""
    return _OUTPUT_END.search(PurePath(name).stem) is not None


def _natural_key(name: str):
    """名前の中の数字を数として比べる（「2」を「10」より前にする）。大文字と小文字は区別しない"""
    return [
        (0, int(s), "") if s.isdigit() else (1, 0, s.casefold())
        for s in re.split(r"(\d+)", name)
    ]


def _attributes(entry: os.DirEntry) -> int:
    """Windows のファイルの属性（ほかの OS では 0）。Windows ではフォルダの一覧を読んだときの値を使うので、
    項目ごとにファイルを開かない"""
    try:
        return getattr(entry.stat(follow_symlinks=False), "st_file_attributes", 0)
    except OSError:
        return 0


def _junction(entry: os.DirEntry) -> bool:
    """ジャンクションか（Windows のほかでは、いつも偽）"""
    return entry.is_junction()


@dataclass(eq=False)
class Folder:
    """たどったフォルダ1つ"""

    path: Path
    rel: PurePath  # 開いたフォルダからの相対パス（開いたフォルダは空）
    depth: int  # 開いたフォルダを 0 とした深さ
    files: list[str] = field(default_factory=list)  # 開くファイルの名前（並べる順）
    linked: set[str] = field(default_factory=set)  # files のうち、リンクのファイル
    skipped: list[str] = field(
        default_factory=list
    )  # 除いた墨消し済みのPDFの名前（並べる順）
    # たどるサブフォルダ（並べる順）
    subfolders: list[Folder] = field(default_factory=list)
    error: str = ""  # 読めなかったときの理由（例外の名前）
    scanned: bool = False


def _scan(folder: Folder) -> None:
    """フォルダの直下を読み、開くファイル・除いた墨消し済みのPDF・たどるサブフォルダを決める。どれもリンクの先を
    たどらずに判定する"""
    files: list[str] = []
    subfolders: list[str] = []
    try:
        with os.scandir(folder.path) as entries:
            for entry in entries:
                try:
                    if _junction(entry):
                        continue
                    if entry.is_symlink():
                        # リンクのフォルダはたどらない。リンクのファイルは開く
                        if entry.is_file():
                            files.append(entry.name)
                            folder.linked.add(entry.name)
                        continue
                    if entry.is_dir(follow_symlinks=False):
                        if not entry.name.startswith(".") and not (
                            _attributes(entry) & _HIDDEN_ATTRIBUTES
                        ):
                            subfolders.append(entry.name)
                    elif entry.is_file(follow_symlinks=False):
                        files.append(entry.name)
                except OSError:
                    continue  # 読んでいるあいだに削除された項目など
    except OSError as e:
        folder.error = type(e).__name__
    supported = [
        n for n in files if PurePath(n).suffix.lower() in filetypes.SUPPORTED_SUFFIXES
    ]
    folder.files = sorted((n for n in supported if not is_output(n)), key=_natural_key)
    folder.skipped = sorted((n for n in supported if is_output(n)), key=_natural_key)
    folder.linked &= set(folder.files)
    folder.subfolders = [
        Folder(folder.path / n, folder.rel / n, folder.depth + 1)
        for n in sorted(subfolders, key=_natural_key)
    ]
    folder.scanned = True


def _within(folder: Folder, depth: int | None) -> bool:
    """depth（None はすべて）までに入るか"""
    return depth is None or folder.depth <= depth


class Walk:
    """選ばれたフォルダを、浅い深さから順にたどる（幅優先）"""

    def __init__(self, roots: Iterable[Path]):
        self.roots = [Folder(p, PurePath(), 0) for p in roots]
        self.queue: deque[Folder] = deque(self.roots)
        self.found: list[int] = []  # たどったフォルダの、深さごとの開くファイルの数

    def step(self) -> Folder | None:
        """次のフォルダを1つたどる。たどるフォルダが残っていなければ None"""
        if not self.queue:
            return None
        folder = self.queue.popleft()
        _scan(folder)
        self.queue.extend(folder.subfolders)
        while len(self.found) <= folder.depth:
            self.found.append(0)
        self.found[folder.depth] += len(folder.files)
        return folder

    def count(self, depth: int | None) -> int:
        """たどったフォルダのうち、depth（None はすべて）までの開くファイルの数"""
        return sum(self.found if depth is None else self.found[: depth + 1])

    def done(self, depth: int | None) -> bool:
        """depth（None はすべて）までのフォルダを、たどり終えたか"""
        return not self.queue or (depth is not None and self.queue[0].depth > depth)

    def run(self, depth: int | None) -> None:
        """depth（None はすべて）までのフォルダをたどる"""
        while not self.done(depth):
            self.step()


def _ordered(root: Folder, depth: int | None) -> Iterator[Folder]:
    """depth までのフォルダを、並べる順に返す（フォルダの直下のファイル、続けてサブフォルダを名前の順に
    1つずつ。深いフォルダでも呼び出しが深くならないよう、再帰しない）"""
    stack = [root]
    while stack:
        folder = stack.pop()
        if not folder.scanned or not _within(folder, depth):
            continue
        yield folder
        stack.extend(reversed(folder.subfolders))


def root_label(path: Path) -> str:
    """開いたフォルダの名前（ドライブのルートなど、名前のないフォルダはドライブの名前）"""
    if path.name:
        return path.name
    parts = [p for p in re.split(r"[\\/:]+", path.drive) if p]
    return parts[-1] if parts else "root"


def folder_names(folders: list[Path]) -> list[str]:
    """2つ以上のフォルダを開いたときの、フォルダごとの名前。同じ名前（大文字と小文字を区別しない）は、2つ目から
    「_2」「_3」…を付ける"""
    names: list[str] = []
    used: set[str] = set()
    for p in folders:
        base = root_label(p)
        name, n = base, 1
        while name.casefold() in used:
            n += 1
            name = f"{base}_{n}"
        used.add(name.casefold())
        names.append(name)
    return names


@dataclass(frozen=True)
class Found:
    """開くファイル1つ"""

    path: Path
    # 開いたフォルダからの、ファイルのあるフォルダの相対パス（2つ以上のフォルダを開いたときは、先頭にフォルダの名前。
    # 開いたフォルダの直下のファイルと、直接選んだファイルは空）。画面に出す相対パスと、出力先のサブフォルダに使う
    folder: PurePath = field(default_factory=PurePath)

    @property
    def label(self) -> str:
        """画面に出す相対パス（直下のファイルと直接選んだファイルは名前だけ）"""
        return str(self.folder / self.path.name)


@dataclass
class Inputs:
    """開くファイルの一覧"""

    files: list[Found] = field(default_factory=list)
    # 開けなかったフォルダの理由（画面に出す文）
    errors: list[str] = field(default_factory=list)
    # 除いた墨消し済みのPDFの、開いたフォルダからの相対パス（2つ以上のフォルダを開いたときは、先頭にフォルダの名前）
    skipped: list[str] = field(default_factory=list)


# 深さを選ぶダイアログのために数える時間（秒）と件数の上限。値は Windows の PC で大きなフォルダを測ってから決める
COUNT_SECONDS = 10.0
COUNT_FILES = 10_000


@dataclass(frozen=True)
class Counts:
    """深さごとのファイルの数（深さを選ぶダイアログに出す）"""

    by_depth: tuple[int, ...] = ()  # たどったフォルダの、深さごとのファイルの数
    done_depth: int = -1  # この深さまでのフォルダは、すべてたどった
    complete: bool = False  # すべての深さをたどった
    stopped: bool = False  # 上限に達して、数えるのを止めた

    def count(self, depth: int | None) -> int:
        """depth（None はすべて）までのファイルの数（数え終わっていなければ、それまでに数えた数）"""
        return sum(self.by_depth if depth is None else self.by_depth[: depth + 1])

    def exact(self, depth: int | None) -> bool:
        """depth までを数え終えたか"""
        return self.complete or (depth is not None and depth <= self.done_depth)


class Selection:
    """選ばれたファイルとフォルダ。フォルダは、浅い深さから順にたどる

    作るときに、選ばれたフォルダの直下を読む（サブフォルダがあるかを知るため）。深さを選ぶダイアログのあいだは、
    裏のスレッドで続きをたどって数え（start_counting）、開くときは、数えたときにたどった分を使い、足りない深さだけを
    続けてたどる（walk_to）。
    """

    def __init__(self, paths: Iterable[Path]):
        self.paths = list(paths)
        # 選ばれたフォルダ → 実体のパス。同じファイルかは、実体のパスに相対パスをつないで比べる（フォルダのリンクを
        # たどらないので、ファイルごとに実体のパスを求めなくてよい。ネットワークのドライブでは1件ごとに時間がかかる）
        self.keys: dict[Path, Path] = {}
        for p in self.paths:
            if p.is_dir() and p not in self.keys:
                self.keys[p] = p.resolve()
        # 実体のパス → 選ばれたフォルダ（重ねて選ばれたものは1つ）
        roots: dict[Path, Path] = {}
        for p, key in self.keys.items():
            roots.setdefault(key, p)
        self.walk = Walk(roots.values())
        self.found = dict(zip(roots, self.walk.roots))  # 実体のパス → たどったフォルダ
        self.names = dict(zip(roots, folder_names(list(roots.values()))))
        self.walk.run(0)
        self.counts = self._counts(False)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def has_subfolders(self) -> bool:
        """選ばれたフォルダに、たどるサブフォルダがある"""
        return any(f.subfolders for f in self.walk.roots)

    def _counts(self, stopped: bool) -> Counts:
        walk = self.walk
        return Counts(
            tuple(walk.found),
            walk.queue[0].depth - 1 if walk.queue else len(walk.found) - 1,
            not walk.queue,
            stopped and bool(walk.queue),
        )

    def count(
        self,
        seconds: float = COUNT_SECONDS,
        limit: int = COUNT_FILES,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """浅い深さから順にたどって数え、結果を counts に入れる（深さを1つたどり終えるたびと、終わったとき）。seconds
        秒か limit 件を超えたら止める。stop_counting で止める"""
        start = clock()
        while not self._stop.is_set() and self.walk.queue:
            depth = self.walk.queue[0].depth
            self.walk.step()
            over = sum(self.walk.found) >= limit or clock() - start >= seconds
            if over or self.walk.done(depth):
                self.counts = self._counts(over)
            if over:
                return

    def start_counting(self) -> None:
        """裏のスレッドで数える（count）"""
        self._thread = threading.Thread(target=self.count, daemon=True)
        self._thread.start()

    def stop_counting(self, wait: bool = True) -> None:
        """数えるのを止める。wait なら、たどっている途中のフォルダを読み終わるまで待つ"""
        self._stop.set()
        if wait and self._thread is not None:
            self._thread.join()

    def walk_to(
        self,
        depth: int | None,
        cancelled: Callable[[], bool] = lambda: False,
        progress: Callable[[int], None] = lambda found: None,
    ) -> bool:
        """数えるのを止め、depth（None はすべて）までのフォルダをたどる。progress には、それまでに見つけた depth
        までのファイルの数を渡す。取り消されたら偽"""
        self.stop_counting()
        while not self.walk.done(depth):
            if cancelled():
                return False
            self.walk.step()
            progress(self.walk.count(depth))
        return True

    def inputs(self, depth: int | None) -> Inputs:
        """depth（None はすべて）までの開くファイルの一覧（walk_to でたどった後に呼ぶ）"""
        multiple = len(self.found) > 1
        out = Inputs()
        seen: set[Path] = set()
        done: set[Path] = set()

        def add(found: Found, key: Path) -> None:
            if key not in seen:
                seen.add(key)
                out.files.append(found)

        for p in self.paths:
            if p not in self.keys:
                add(Found(p), p.resolve())
                continue
            key = self.keys[p]
            if key in done:
                continue
            done.add(key)
            for folder in _ordered(self.found[key], depth):
                shown = (
                    PurePath(self.names[key]) / folder.rel if multiple else folder.rel
                )
                if folder.error:
                    out.errors.append(
                        tr(
                            "フォルダを読めません（{error}）: {name}",
                            error=folder.error,
                            name=str(shown) if folder.depth else p.name,
                        )
                    )
                base = key / folder.rel
                for name in folder.files:
                    path = folder.path / name
                    add(
                        Found(path, shown),
                        path.resolve() if name in folder.linked else base / name,
                    )
                out.skipped += [str(shown / name) for name in folder.skipped]
        return out


def collect(paths: Iterable[Path], depth: int | None = 0) -> Inputs:
    """選ばれたファイルとフォルダから、開くファイルの一覧を作る

    フォルダは depth（0 は直下だけ、None はすべて）の深さまでの PDF・画像に展開する。並べる順は、フォルダの直下の
    ファイルを名前の順（数字は数の大きさの順）に、続けてサブフォルダを名前の順に1つずつ、その中を同じ決まりで並べる。
    フォルダの中の、墨消し済みのPDF（is_output）は除き、相対パスを skipped に足す。名前が「.」で始まる
    フォルダ・隠しかシステムの属性のフォルダ・リンクのフォルダ（シンボリックリンク・ジャンクション）は、たどらない。
    選ばれたフォルダは、そのどれでも開く。読めないフォルダは除き、理由を errors に足す。同じファイルが重ねて
    選ばれたときは1つにする。直接選ばれたファイルは、種類や名前を問わず残す（開けない種類は開くときに理由を示す）。
    """
    selection = Selection(paths)
    selection.walk_to(depth)
    return selection.inputs(depth)
