# Copyright (C) 2026 Hideki Ohmura
# SPDX-License-Identifier: AGPL-3.0-only
"""様式のデータ：様式のフォルダの様式の読み込み、様式のフォルダの決め方

1つの様式は、型の画像、墨消しする範囲、題名の語の組である。様式は、様式のフォルダに .json と型の画像で置く。
ツール本体に組み込みの様式はない。

画面のプロセスが、ファイルを開くときに読み、使う様式の組を解析の依頼に入れる。画面のプロセスが
起動してすぐに使うため、重いライブラリ（OpenCV など）は読み込まない。型の画像の大きさは、PNG の先頭の
情報から読む。様式の保存・削除の操作と、その記録は formsave.py にある。画面に出す文
（読めない理由など）は、作るときの画面の言語の文にする（i18n.tr）。
"""

import json
import math
import re
import struct
import unicodedata
import zlib
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import NamedTuple

from . import datadir, i18n  # どちらも標準ライブラリだけを読み込む
from .i18n import tr

# 様式の .json のフォーマットの番号（このツールが扱えるもの）。番号を上げる決まり：
# 項目を足すとき・項目の意味を変えるときは上げる。足した項目を知らなくても墨消しの結果が変わらない変更なら、
# USABLE_FROM_FORMAT は前のままにする（古いバージョンでも墨消しに使えるが、編集はできない）。知らないと墨消しの
# 結果が変わる変更なら、USABLE_FROM_FORMAT も上げる（古いバージョンでは墨消しにも使わない）
# 2：墨消しする範囲に、印刷された枠の線がある組（frame）を足した番号。フォーマット1までのバージョンは frame を
# 知らないが、そのバージョンの墨消しの仕方のまま墨消しに使えるので、USABLE_FROM_FORMAT は1のまま
# 3：見本の罫線（lines）を足した番号。罫線はバリエーションの欄を探すのにだけ使い、見本どおりの用紙の墨消しは
# 変わらないので、USABLE_FROM_FORMAT は1のまま
# 4：罫線の並びでの判別に使うかと、罫線の一致度の下限（line_match）を足した番号。知らないバージョンは、その
# バージョンの条件でバリエーションを判別する。見本どおりの用紙の墨消しは変わらないので、USABLE_FROM_FORMAT は1のまま
FORMAT = 4
# 保存する様式を墨消しに使うのに、ツールが扱えないといけないフォーマットの番号（.json の usable_from_format）
USABLE_FROM_FORMAT = 1
NEWER_FORM = "新しいバージョンのツールで保存された様式です。編集するには、ツールを新しいバージョンに更新してください。"
# 罫線の一致度の下限（FormSpec.line_match_min）の、新しく登録する様式の値と、国税庁の公開様式から作った様式の値。
# 下限は様式ごとに違う：下の端は、表の作りが似た別の帳票がどこまで一致するか、上の端は、同じ帳票の
# バリエーションがどこまで下がるかで決まる。扶養控除等申告書は、公的年金等の受給者の扶養親族等申告書が0.62まで一致する。
# 基・配・特・所の申告書は、見本と作りの違う用紙が0.60まで下がる。line_match のない .json（フォーマット3以下）を
# 読んだときも、この値にする
LINE_MATCH_MIN = 0.60
# 下限を決める刻みと上限（範囲を描く画面のスライダーと、新しく登録する様式の下限の初めの値）
LINE_MATCH_STEP = 0.05
LINE_MATCH_MAX = 0.95
BUNDLED_LINE_MATCH_MIN = {
    "nta-fuyou-r9": 0.65,
    "nta-hoken-r8": 0.45,
    "nta-kihai-r8": 0.45,
}
# 国税庁の公開様式から作った3様式（tools/make_templates.py が作る）の置き場所（ツール本体の中）。導入のときに
# 様式のフォルダに入れるためのもので、起動と解析では読まない
BUNDLED_DIR = Path(__file__).resolve().parent / "bundled_forms"
# 型の画像は作業画像（200dpi）の半分の解像度で持ち、位置合わせも半分で行う。
# 見本に既知の変形をかけて試すと、ずれは最大1.86画素（元の解像度では1.14画素）だった
TEMPLATE_SCALE = 0.5
# 型の画像の縦横の上限（画素）。共有フォルダの中の画像を読むため、大きすぎる画像は読めないものとして扱う
MAX_IMAGE_SIDE = 8000
# 型の画像のファイルの大きさの上限（見本のスキャンから登録した見本の画像は約0.5MB）と、様式の .json の大きさの上限
# （.json は数KB）
MAX_IMAGE_BYTES = 16 * 1024**2
MAX_SPEC_BYTES = 1024**2
# 見本の罫線（lines）の本数の上限（横・縦それぞれ）と、太さの上限（画素）。配布用パッケージの3様式は横59〜110本・
# 縦67〜117本、太さは9以下。共有フォルダの中の .json を読むため、外れたら読めない様式とする
MAX_LINES = 5000
MAX_LINE_WIDTH = 32
SIZE_TOLERANCE = 2  # 型の画像の大きさと、様式の情報（size）の半分との差として許す画素
# どの様式とも位置合わせできなかったページの様式の名前。様式の名前には使えない
UNKNOWN = "様式不明"
RESERVED_NAMES = (UNKNOWN,)
# 様式のフォルダの中で、様式の情報として読むファイルの名前（ほかの .json は読まない）。u- は画面から登録した様式、
# nta- は国税庁の公開様式から作った様式（nta-<種類>-r<令和の年>）。年分のない nta- を読まないのは、版の付いた
# ファイル名（<識別子>-r<版>）で、識別子の後ろを版と読むため（nta-fuyou の版を数えるときに nta-fuyou-r9 を版9と
# 読まないように）
SPEC_FILE = re.compile(r"(u-[0-9A-Za-z]{1,64}|nta-[a-z]{1,16}-r[0-9]{1,3})\.json")
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
# 題名と題名の語を比べる前に除く文字（空白とかっこ）
TITLE_DROP = re.compile(r"[\s()（）「」]")

# 様式のフォルダを決めたもの（画面に出す文は formstext.ORIGIN_TEXTS）
ORIGIN_USER = "user"
ORIGIN_DEFAULT = "default"
# 既定の場所：%LOCALAPPDATA%\SumikeshiStudio\forms（新しいバージョンに更新しても残る）
DEFAULT_FOLDER = "forms"


def display_name(name: str) -> str:
    """画面に出す、判別した様式の名前（様式不明は画面の言語の文。様式の名前はそのまま）"""
    return tr(UNKNOWN) if name == UNKNOWN else name


def title_key(text: str) -> str:
    """題名と題名の語を比べるときの形：NFKC で正規化し、空白とかっこを除く"""
    return TITLE_DROP.sub("", unicodedata.normalize("NFKC", text))


class FormError(Exception):
    """様式の型の画像を読めない（解析の途中で、様式のフォルダにつながらなくなったときなど）"""


class Line(NamedTuple):
    """見本とページの罫線1本（rulings.read が読み、様式の .json の lines に持つ）。作業画像の画素で、横の線は pos が y、
    a・b が左右の端。縦の線は pos が x、a・b が上下の端"""

    pos: float
    a: int
    b: int
    t: int  # 太さ

    @property
    def length(self) -> int:
        return self.b - self.a


# 墨消しする範囲の、印刷された枠の線の組
TOP_BOTTOM = "top_bottom"  # 上下の線
LEFT_RIGHT = "left_right"  # 左右の線
FRAME_PAIRS = (TOP_BOTTOM, LEFT_RIGHT)


@dataclass(frozen=True)
class Field:
    name: str  # 範囲の名前（「本人の個人番号」など）
    x: float  # 作業画像の上の左上・幅・高さ
    y: float
    w: float
    h: float
    # 印刷された枠の線がある組（FRAME_PAIRS の部分。空なら枠なし）。記録のない様式（format 1）では None。
    # 解析では、この組の線だけを探して墨消しを線に合わせる（frame_pairs）
    frame: tuple[str, ...] | None = None
    # 解析で、ページの上の墨消しの範囲を長方形でない形にするときの角（作業画像の上の (x, y) を時計回りに。写真で、
    # 探した組の線がすべて見つかった欄の、線の中心を結んだ四隅）。様式の範囲と、長方形の範囲では None。保存しない
    corners: tuple[tuple[float, float], ...] | None = None


@dataclass(frozen=True)
class FormSpec:
    """1つの様式。解析の依頼に入れて、解析のプロセスに渡す"""

    id: str
    name: str
    image: str  # 型の画像のファイル名
    size: tuple[int, int]  # 作業画像の幅・高さ（型の画像は、その TEMPLATE_SCALE 倍）
    fields: tuple[Field, ...]  # 墨消しする範囲
    erase: tuple[Field, ...] = ()  # 型の画像から消した範囲（出力の墨消しには使わない）
    title_words: tuple[str, ...] = ()
    title_excludes: tuple[str, ...] = ()
    revision: int = 0
    updated_at: str = ""
    updated_by: str = ""
    format: int = FORMAT  # .json のフォーマットの番号（このツールが扱えるものより大きければ、編集できない）
    # 保存したツールのバージョン（例：0.1.0 (built 2026-11-07 04:58)）
    saved_with: str = ""
    folder: str = ""  # 様式のフォルダ（型の画像がある場所）
    # 見本の罫線（横の線, 縦の線。rulings.read）。塗る前の作業画像から読めた様式だけが持つ（登録と、新しい用紙への
    # 置き換えで保存したとき）。持たなければ None
    lines: tuple[tuple[Line, ...], tuple[Line, ...]] | None = None
    # バリエーションを、罫線の並びで判別するのに、この様式を使うか（表の作りが同じ別の帳票がある様式は、下限を
    # どこに置いても分けられないので、使わないにする）
    line_match_use: bool = True
    # 罫線の一致度の下限。見本の側の割合とページの側の割合が、どちらもこの値以上のときに、この様式の
    # バリエーションと判別する（variants.decides）
    line_match_min: float = LINE_MATCH_MIN

    @property
    def editable(self) -> bool:
        """このツールで編集できる（新しいバージョンのツールで保存された様式は、墨消しに使えても編集しない。
        古いバージョンの判断で、新しいバージョンで足した項目を消したり、新しい様式を消したりしないため）"""
        return self.format <= FORMAT

    def image_path(self) -> Path:
        return Path(self.folder) / self.image

    def log_label(self) -> str:
        """作業ログの「Forms used」に書く名前（様式の名前は日本語のまま。後ろに付ける「(revision N)」は英語）"""
        return f"{self.name} (revision {self.revision})"

    def detail_label(self) -> str:
        """画面下の「帳票：」に出す名前。版と、保存の記録があれば保存した日・保存した人も出す（配布用パッケージのファイルを
        そのままコピーした様式には、保存の記録がない）"""
        if not (self.updated_at or self.updated_by):
            return tr(
                "{name}（第{revision}版）", name=self.name, revision=self.revision
            )
        return tr(
            "{name}（第{revision}版、{date} {user}）",
            name=self.name,
            revision=self.revision,
            date=self.updated_at[:10],
            user=self.updated_by,
        )


@dataclass(frozen=True)
class Problem:
    """読めない様式のファイル"""

    file: str  # ファイル名
    name: str  # 様式の名前（.json を読めなければ空）
    reason: str
    # 新しいバージョンのツールで保存された様式（このツールでは削除もしない）
    newer: bool = False

    @property
    def form_id(self) -> str:
        return Path(self.file).stem

    def text(self) -> str:
        return tr(
            "{name}（{file}）：{reason}",
            name=self.name or self.file,
            file=self.file,
            reason=self.reason,
        )


@dataclass(frozen=True)
class FolderChoice:
    """様式のフォルダの決め方の結果"""

    path: Path
    origin: str = ""  # ORIGIN_USER・ORIGIN_DEFAULT


@dataclass
class Loaded:
    """ファイルを開くときに読んだ様式"""

    folder: Path | None = None
    origin: str = ""
    # 読めた様式（この端末で使わない様式も含む）。判別に使う順
    forms: list[FormSpec] = field(default_factory=list)
    problems: list[Problem] = field(default_factory=list)
    error: str = ""  # 様式のフォルダ、または配布用パッケージの設定ファイルを読めない
    # この端末で使わない様式の識別子（利用者ごとの設定）
    off: frozenset[str] = frozenset()

    @property
    def active(self) -> tuple[FormSpec, ...]:
        """判別に使う様式（この端末で使わない様式を除く）。この順に位置合わせする"""
        return tuple(f for f in self.forms if f.id not in self.off)

    def off_names(self) -> list[str]:
        """この端末で使わない様式の名前（読めない様式は、名前かファイル名）"""
        names = [f.name for f in self.forms if f.id in self.off]
        return names + [
            p.name or p.file for p in self.problems if p.form_id in self.off
        ]

    def blocking_text(self) -> str:
        """ファイルを開かない理由。開けるなら空

        様式のフォルダが決まっているのに、使う様式をすべて読めないときは開かない。足りない様式で解析すると、
        その様式のページが墨消しされないまま進むため。この端末で使わない様式は、読めなくても開くのを止めない。
        """
        if self.error:
            return self.error
        bad = [p for p in self.problems if p.form_id not in self.off]
        if not bad:
            return ""
        return "\n".join(
            [
                tr("次の様式を読めないため、ファイルを開けません。"),
                *(i18n.bullet(p.text()) for p in bad),
                tr("様式のフォルダ：{folder}", folder=self.folder),
                tr(
                    "起動の画面で「様式を編集」を押し、その様式を削除してください"
                    "（様式のファイルの誤りを修正してもかまいません）。起動の画面で、その様式の"
                    "「使う」のチェックを外しても、ファイルを開けます。"
                ),
            ]
        )

    def summary_text(self) -> str:
        """画面下に出す、様式の数（例：様式：使う4・この端末で使わない5）

        この端末で使わない様式は、名前を並べず数だけを出す。どの様式を使わないかは起動の画面の一覧で見る
        （使わない様式が多いと、名前を並べた文が長くなるため）。
        """
        text = tr("様式：使う{count}", count=len(self.active))
        if off := len(self.off_names()):
            text += tr("・この PC で使わない{count}", count=off)
        return text


# ---------------------------------------------------------------- 様式のフォルダの決め方


def default_folder() -> Path:
    """様式のフォルダの既定の場所（%LOCALAPPDATA%\\SumikeshiStudio\\forms）"""
    return datadir.data_dir() / DEFAULT_FOLDER


def choose_folder(user_value: str) -> FolderChoice:
    """様式のフォルダを決める

    起動の画面で指定したフォルダ（user_value。利用者ごとの設定）があればそれを、なければ既定の場所を使う。
    既定の場所があるので、指定しなくてもすぐに様式を登録できる。配布用パッケージの設定のファイルでは指定しない
    （起動の画面に様式のフォルダの場所が毎回出るので、選び忘れたときに気づけるため）。
    """
    if user_value:
        return FolderChoice(Path(user_value), ORIGIN_USER)
    return FolderChoice(default_folder(), ORIGIN_DEFAULT)


# ---------------------------------------------------------------- 読み込み


def has_forms(folder: Path) -> bool:
    """様式のフォルダに様式があるか：直下に、様式の情報として読むファイル（SPEC_FILE に合うもの）が1つ以上ある。
    読めない .json も数える（読めない様式を上書きしないため）。一覧を取れなければ OSError"""
    return any(SPEC_FILE.fullmatch(p.name) for p in folder.iterdir())


def form_files(folder: Path) -> list[str]:
    """様式のフォルダの直下の、様式の情報として読むファイルの名前（読めない .json も含む）。一覧を取れなければ
    OSError"""
    return sorted(p.name for p in folder.iterdir() if SPEC_FILE.fullmatch(p.name))


def count_forms(folder: Path) -> int:
    """様式のフォルダの様式のファイルの数（has_forms と同じ数え方）。フォルダがない・一覧を取れないときは 0"""
    try:
        return len(form_files(folder))
    except OSError:
        return 0


def overlaps(a: Path, b: Path) -> bool:
    """2つのフォルダの一方が、もう一方の中にある（同じフォルダも含む）"""
    a, b = a.resolve(), b.resolve()
    return a.is_relative_to(b) or b.is_relative_to(a)


def load(choice: FolderChoice, off: Iterable[str] = ()) -> Loaded:
    """様式のフォルダの様式を読む。off は、この端末で使わない様式の識別子

    判別に使う順は、様式の名前の文字の符号の順（同じ名前なら識別子の順）とする。
    """
    loaded = Loaded(choice.path, choice.origin, off=frozenset(off))
    folder = choice.path
    if choice.origin == ORIGIN_DEFAULT:  # 既定の場所は、なければ作る
        try:
            folder.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass  # 作れなければ、下で読めないフォルダとして扱う
    try:
        names = sorted(p.name for p in folder.iterdir() if SPEC_FILE.fullmatch(p.name))
    except OSError:
        loaded.error = tr(
            "様式のフォルダを読めないため、ファイルを開けません。\n"
            "{folder}\n"
            "共有フォルダにつながっているか確かめてください。"
            "様式のフォルダは、起動の画面の「変更」で選び直せます。",
            folder=folder,
        )
        return loaded
    specs: list[FormSpec] = []
    for name in names:
        try:
            spec = read_spec(folder / name)
        except Exception as e:  # noqa: BLE001  1つの様式の失敗で、ほかの様式の読み込みを止めない
            # 入れ子の深い .json（RecursionError）や長い整数（ValueError）も、読めない様式にする
            if isinstance(e, SpecError):
                problem = Problem(name, e.name, str(e), e.newer)
            else:
                problem = Problem(
                    name,
                    "",
                    tr("ファイルを読めません（{error}）", error=type(e).__name__),
                )
            loaded.problems.append(problem)
            continue
        specs.append(spec)
    loaded.forms = sorted(specs, key=lambda s: (s.name, s.id))
    return loaded


def spec_key(spec: FormSpec) -> tuple:
    """様式の版を見分ける鍵（様式のフォルダ・識別子・版・保存の記録）。同じ鍵の様式は、同じファイルから読んだもの"""
    return (spec.folder, spec.id, spec.revision, spec.updated_at, spec.updated_by)


class SpecError(Exception):
    """様式のファイルを読めない。name は、.json から読めた範囲の値"""

    def __init__(self, reason: str, name: str = "", newer: bool = False):
        super().__init__(reason)
        self.name = name
        self.newer = newer  # 新しいバージョンのツールで保存された様式


def read_spec(path: Path) -> FormSpec:
    """様式の .json を読む（型の画像は同じフォルダにある）

    型の画像があり、PNG で、大きさが上限以下で、様式の情報と合うことも確かめる。
    """
    try:
        if path.stat().st_size > MAX_SPEC_BYTES:
            raise SpecError(
                tr(
                    "ファイルが大きすぎます（上限は{limit}MB）",
                    limit=MAX_SPEC_BYTES // 1024**2,
                )
            )
        data = json.loads(path.read_text("utf-8-sig"))
    except OSError as e:
        raise SpecError(
            tr("ファイルを読めません（{error}）", error=type(e).__name__)
        ) from e
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise SpecError(tr("ファイルの中身が様式の形になっていません")) from e
    if not isinstance(data, dict):
        raise SpecError(tr("ファイルの中身が様式の形になっていません"))
    name = data.get("name") if isinstance(data.get("name"), str) else ""

    def fail(reason: str, **values):
        raise SpecError(tr(reason, **values), name)

    # フォーマットの番号。このツールが扱える番号（FORMAT）より大きくても、墨消しに使うのに
    # 要る番号（usable_from_format。なければ format と同じ）が扱える番号以下なら、墨消しに使う（知らない
    # 項目は無視する）が、編集はできない。どちらも大きければ、読めない様式とする
    fmt = data.get("format")
    usable = data.get("usable_from_format", fmt)
    if not all(
        isinstance(v, int) and not isinstance(v, bool) and v >= 1 for v in (fmt, usable)
    ):
        fail("フォーマットの番号（format・usable_from_format）が正しくありません")
    if fmt > FORMAT and usable > FORMAT:
        raise SpecError(
            tr(
                "新しいバージョンのツールで保存された様式です（フォーマットの番号 {format}）。"
                "ツールを新しいバージョンに更新してください。",
                format=fmt,
            ),
            name,
            newer=True,
        )
    if data.get("id") != path.stem:
        fail("識別子（id）がファイル名と合いません")
    if not name.strip():
        fail("名前（name）がありません")
    if has_control(name):
        fail("名前（name）に使えない文字（制御文字）があります")
    if name in RESERVED_NAMES:
        fail("「{name}」は様式の名前に使えません", name=name)
    revision = data.get("revision")
    if not isinstance(revision, int) or isinstance(revision, bool) or revision < 0:
        fail("版（revision）が正しくありません")
    image = data.get("image")
    if (
        not isinstance(image, str)
        or Path(image).name != image
        or image in ("", ".", "..")
    ):
        fail("見本の画像のファイル名（image）が正しくありません")
    size = data.get("size")
    if not (
        isinstance(size, list)
        and len(size) == 2
        and all(isinstance(v, int) and not isinstance(v, bool) and v > 0 for v in size)
    ):
        fail("作業画像の大きさ（size）が正しくありません")
    fields = _fields(data.get("fields"), "fields", fail, size)
    erase = _fields(data.get("erase", []), "erase", fail, size)
    words = _words(data.get("title_words", []), "title_words", fail)
    excludes = _words(data.get("title_excludes", []), "title_excludes", fail)
    texts = [data.get(k, "") for k in ("updated_at", "updated_by")]
    if not all(isinstance(t, str) for t in texts):
        fail("保存の記録（updated_at・updated_by）が正しくありません")
    lines = _lines(data.get("lines"), fail, size)
    use, least = _line_match(data.get("line_match"), path.stem, fail)
    _check_image(path.parent / image, size, fail)
    return FormSpec(
        id=path.stem,
        name=name,
        image=image,
        size=(size[0], size[1]),
        fields=fields,
        erase=erase,
        title_words=words,
        title_excludes=excludes,
        revision=revision,
        updated_at=texts[0],
        updated_by=texts[1],
        format=fmt,
        saved_with=data.get("saved_with", "")
        if isinstance(data.get("saved_with"), str)
        else "",
        folder=str(path.parent),
        lines=lines,
        line_match_use=use,
        line_match_min=least,
    )


def has_control(text: str) -> bool:
    """制御文字（Unicode の分類 Cc）があるか（様式の名前に使わない）"""
    return any(unicodedata.category(c) == "Cc" for c in text)


def _fields(value, key: str, fail, size: list[int]) -> tuple[Field, ...]:
    """範囲の一覧。座標と大きさは有限の数（json.loads は NaN や Infinity も読むため）で、範囲は用紙（size。
    作業画像の幅・高さ）の中にあること（はみ出しは SIZE_TOLERANCE まで。範囲を描く画面は範囲を画像の中に
    収めるが、保存のときの丸めで1画素はみ出すことがある）"""
    if not isinstance(value, list):
        fail("範囲（{key}）が正しくありません", key=key)
    out = []
    width, height = size
    tol = SIZE_TOLERANCE
    for f in value:
        rect = f.get("rect") if isinstance(f, dict) else None
        if not (
            isinstance(f, dict)
            and isinstance(f.get("name"), str)
            and isinstance(rect, list)
            and len(rect) == 4
            and all(
                isinstance(v, int | float)
                and not isinstance(v, bool)
                and math.isfinite(v)
                for v in rect
            )
            and rect[2] > 0
            and rect[3] > 0
        ):
            fail("範囲（{key}）が正しくありません", key=key)
        x, y, w, h = rect
        if x < -tol or y < -tol or x + w > width + tol or y + h > height + tol:
            fail("範囲（{key}）が用紙の外にあります（{name}）", key=key, name=f["name"])
        out.append(Field(f["name"], *rect, _frame(f.get("frame"), fail, key)))
    return tuple(out)


def default_line_match_min(form_id: str) -> float:
    """罫線の一致度の下限の、決めていない様式の値（国税庁の公開様式から作った様式は様式ごとの値）"""
    return BUNDLED_LINE_MATCH_MIN.get(form_id, LINE_MATCH_MIN)


def _line_match(value, form_id: str, fail) -> tuple[bool, float]:
    """line_match（{"use": 使うか, "min": 下限}）。なければ（フォーマット3以下の様式）、使うにして、下限は
    default_line_match_min"""
    if value is None:
        return True, default_line_match_min(form_id)
    use, least = (
        (value.get("use"), value.get("min")) if isinstance(value, dict) else (0, 0)
    )
    if not (
        isinstance(use, bool)
        and isinstance(least, (int, float))
        and not isinstance(least, bool)
        and 0 < least <= 1
    ):
        fail("罫線の一致度の下限（line_match）が正しくありません")
    return use, float(least)


def _lines(value, fail, size: list[int]) -> tuple | None:
    """見本の罫線（lines）。ないときは None。横の線（h）と縦の線（v）の並びで、線は [位置, 端, 端, 太さ]。位置と
    端は用紙（size）の中（はみ出しは SIZE_TOLERANCE まで）、端は小さい順、太さは1以上 MAX_LINE_WIDTH 以下"""
    if value is None:
        return None
    if not (isinstance(value, dict) and set(value) >= {"h", "v"}):
        fail("見本の罫線（lines）が正しくありません")
    width, height = size
    tol = SIZE_TOLERANCE
    out = []
    for key, along, across in (("h", width, height), ("v", height, width)):
        items = value[key]
        if not isinstance(items, list) or len(items) > MAX_LINES:
            fail("見本の罫線（lines）が正しくありません")
        lines = []
        for item in items:
            if not (
                isinstance(item, list)
                and len(item) == 4
                and all(
                    isinstance(v, int | float)
                    and not isinstance(v, bool)
                    and math.isfinite(v)
                    for v in item
                )
                and all(float(v).is_integer() for v in item[1:])
            ):
                fail("見本の罫線（lines）が正しくありません")
            pos, a, b, t = item[0], int(item[1]), int(item[2]), int(item[3])
            if not (
                -tol <= pos <= across + tol
                and -tol <= a < b <= along + tol
                and 1 <= t <= MAX_LINE_WIDTH
            ):
                fail("見本の罫線（lines）が用紙の外にあるか、正しくありません")
            lines.append(Line(float(pos), a, b, t))
        out.append(tuple(lines))
    return tuple(out)


def _frame(value, fail, key: str) -> tuple[str, ...] | None:
    """範囲の frame（枠の線がある組）。ないときは None。知らない組の名前は無視する（新しいバージョンで足したときのため）"""
    if value is None:
        return None
    if not (isinstance(value, list) and all(isinstance(v, str) for v in value)):
        fail("範囲（{key}）の枠の線（frame）が正しくありません", key=key)
    return tuple(p for p in FRAME_PAIRS if p in value)


def frame_pairs(field: Field) -> tuple[str, ...]:
    """解析で線を探して墨消しを合わせる、枠の線の組

    範囲に記録（frame）があれば、それを使う。記録のない様式（format 1）は枠なしとする（利用者の様式で枠のない所を
    範囲にしていると、線を探すと関係のない線に合わせたり、すべてのページに要確認が付いたりするため）。
    """
    return field.frame if field.frame is not None else ()


def _words(value, key: str, fail) -> tuple[str, ...]:
    if not (isinstance(value, list) and all(isinstance(w, str) for w in value)):
        fail("題名の語（{key}）が正しくありません", key=key)
    return tuple(value)


def png_size(head: bytes) -> tuple[int, int] | None:
    """PNG の先頭（24バイト以上）から、幅・高さを読む。PNG でなければ None"""
    if len(head) < 24 or head[:8] != PNG_SIGNATURE or head[12:16] != b"IHDR":
        return None
    return struct.unpack(">II", head[16:24])


# 最後まで確かめた見本の画像：(パス, 大きさ, 更新日時) → 幅・高さ。見本の画像は版ごとに名前が変わり、上書きしない
# ので、大きさと更新日時が同じなら確かめ直さない
_checked_images: dict[tuple[str, int, int], tuple[int, int]] = {}
PNG_CHANNELS = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}  # 色の種類 → 1画素の値の数


class PngError(ValueError):
    """PNG として読めない（理由を持つ）"""


def check_png(data: bytes) -> tuple[int, int]:
    """PNG を最後まで確かめ、幅・高さを返す。読めなければ PngError

    先頭のシグネチャ、最初のかたまり（チャンク）が IHDR、各かたまりの CRC、IEND まであること、画像のデータ（IDAT）の圧縮が
    最後まで展開できること、展開した大きさが IHDR から決まる大きさと合うこと（インターレースでない画像）を確かめる。
    標準ライブラリだけで行う（画面のプロセスに OpenCV を読み込まないため）。展開したデータは持たずに数えるだけにする。
    """
    if data[:8] != PNG_SIGNATURE or data[12:16] != b"IHDR":
        raise PngError(tr("PNG の画像ではありません"))
    width, height, depth, color = struct.unpack(">IIBB", data[16:26])
    if max(width, height) > MAX_IMAGE_SIDE:
        raise PngError(
            tr("大きすぎます（縦横の上限は{limit}画素）", limit=MAX_IMAGE_SIDE)
        )
    interlace = data[28] if len(data) > 28 else 0
    pos, inflate, total, ended = 8, zlib.decompressobj(), 0, False
    try:
        while pos + 12 <= len(data):
            length, kind = struct.unpack(">I4s", data[pos : pos + 8])
            body = data[pos + 8 : pos + 8 + length]
            if len(body) != length or pos + 12 + length > len(data):
                break
            (crc,) = struct.unpack(">I", data[pos + 8 + length : pos + 12 + length])
            if zlib.crc32(kind + body) != crc:
                raise PngError(tr("壊れています（CRC が合いません）"))
            if kind == b"IDAT":
                while body:
                    total += len(inflate.decompress(body, 1 << 20))
                    body = inflate.unconsumed_tail
            elif kind == b"IEND":
                ended = True
                break
            pos += 12 + length
    except zlib.error as e:
        raise PngError(tr("壊れています（画像のデータを展開できません）")) from e
    if not ended or not inflate.eof:
        raise PngError(tr("途中で切れているか、壊れています"))
    if not interlace and color in PNG_CHANNELS:
        row = math.ceil(width * PNG_CHANNELS[color] * depth / 8) + 1
        if total != row * height:
            raise PngError(tr("壊れています（画像のデータの大きさが合いません）"))
    return width, height


def _check_image(path: Path, size: list[int], fail) -> None:
    """見本の画像を確かめる。最後まで読むのは、プロセスの中で画像ごとに1回だけ"""
    try:
        st = path.stat()
    except OSError:
        fail("見本の画像（{file}）を読めません", file=path.name)
    if st.st_size > MAX_IMAGE_BYTES:
        fail(
            "見本の画像が大きすぎます（ファイルの上限は{limit}MB）",
            limit=MAX_IMAGE_BYTES // 1024**2,
        )
    key = (str(path), st.st_size, st.st_mtime_ns)
    wh = _checked_images.get(key)
    if wh is None:
        try:
            data = path.read_bytes()
        except OSError:
            fail("見本の画像（{file}）を読めません", file=path.name)
        try:
            wh = check_png(data)
        except PngError as e:
            # PngError の文は「〜です」の述語（英語では is ...）なので、見本の画像の名前の後につなぐ
            fail("見本の画像（{file}）が{problem}", file=path.name, problem=e)
        _checked_images[key] = wh
    if any(abs(v - s * TEMPLATE_SCALE) > SIZE_TOLERANCE for v, s in zip(wh, size)):
        fail("見本の画像の大きさが、様式の情報（size）と合いません")
