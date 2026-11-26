# Copyright (C) 2026 Hideki Ohmura
# SPDX-License-Identifier: AGPL-3.0-only
"""入力ファイルのページ画像の読み込みと、向き・傾きの補正"""

import ctypes
import math
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, replace
from functools import lru_cache
from pathlib import Path

import cv2
import numpy as np
import pypdfium2 as pdfium
import pypdfium2.raw as pdfium_c
from PIL import Image, ImageOps

from . import filetypes
from .i18n import tr

WORK_DPI = 200  # 検出と墨消しの画面に使う解像度。検出の調整値はこの解像度に合わせてある
# 記録された解像度から求めた用紙の長辺がこの範囲（インチ。A5〜A3 程度）なら、その解像度を信じる。
# 範囲外（画像を保存するソフトが既定で書き込む 72dpi など）なら、A4 とみなして解像度を求め直す
PLAUSIBLE_LONG_IN = (7.0, 17.0)
A4_LONG_IN = 297 / 25.4
# PDF を描く解像度の上限と下限。高い解像度の写真を小さく貼った PDF は、上限がないと 1,303dpi（A4 で RGB
# 約492MB）になることがある。600dpi なら A4 で約104MB。下限は、解像度を決めた画像（1平方インチを超える画像の
# うち、ページの1インチあたりの画素の数が最も多いもの）がページの面積の PDF_PARTIAL 未満のとき（パソコンで作った
# PDF に粗いロゴなどがあるとき）と、ページに画像のほかに文字か図形があるとき（パソコンで作った PDF に、ページ全体を
# 覆う粗い背景の画像があるとき）に使う。画像だけのページ（スキャン）は、低い解像度でも元のまま描く（拡大しても
# 細かくならず、出力が大きくなるだけのため）
PDF_MAX_DPI = 600
PDF_MIN_DPI = WORK_DPI
PDF_PARTIAL = 0.5
DRAWING_KINDS = (
    pdfium_c.FPDF_PAGEOBJ_TEXT,
    pdfium_c.FPDF_PAGEOBJ_PATH,
    pdfium_c.FPDF_PAGEOBJ_SHADING,
)
# 縦と横の解像度がこの割合を超えて違う画像（ファクスの 204×98dpi など）は、低いほうの向きを拡大して画素を
# 正方形にする
ASPECT_TOLERANCE = 0.02
# 縦と横の解像度の比として使う上限（ファクスの 204×98dpi は約2.1）。超える記録は、比も解像度も使わない（A4 とみなす）。
# 比で引き伸ばすので、数KB のファイルの記録だけで大きな配列を作らないため
ASPECT_MAX = 4.0
# 配列にする画像の画素の数の上限。Pillow が展開の爆弾として開かない数（保存された画素の数で見る）と同じにし、解像度の比で
# 引き伸ばした後の数と、複数ページの TIFF の2ページ目以降（Pillow は開くときに1ページ目だけを見る）にも当てる
MAX_PIXELS = 2 * Image.MAX_IMAGE_PIXELS
# TIFF の画像の種類（NewSubfileType、タグ254）のうち、ページにしないもの：縮小版（1）と透明のマスク（4）。
# 古い SubfileType（タグ255）では、2 が縮小版
TIFF_NEW_SUBFILE_TYPE = 254
TIFF_SUBFILE_TYPE = 255
TIFF_NOT_PAGE = 0b101

# pypdfium2 を使う処理（PDF を開く・ページを画像にする・PDF を作って保存する）を1つずつにする
# ロック。PDFium は1つのプロセスの中で同時に呼べない（pypdfium2 の説明書）。同じスレッドから重ねて取れる
# RLock にする（export が render を呼ぶため）。ロックの中で、OCR などほかのロックは取らない。
# pypdfium2 の文書・ページは、ロックの中で明示的に閉じる（閉じ忘れると、ガベージコレクションのときに
# ロックの外の別のスレッドで閉じられ、PDFium を同時に呼ぶことになる）
PDF_LOCK = threading.RLock()

MAX_SKEW_DEG = 15  # 傾き補正の対象とする角度の上限
SKEW_MIN_DEG = 0.05  # 傾きがこれ未満なら直さない（correct は回さずに返す）
# 傾きは画像をこの倍率に縮め、0.2度刻みで直線を探してから、直線ごとに近くの画素に当てはめて
# 角度を求め直す。縮めて刻みを粗くするのは、直線を探す時間を短くするため（縮めずに0.1度刻みで探すと、時間が
# 約20倍になる）。当てはめは、縮めて粗くした分の角度の誤差を戻すため（当てはめないと、試験画像の傾きとの差が最大0.09度に
# なり、題名の読み取りが変わることがある。当てはめると最大0.014度で、縮めない方法と同じ）
SKEW_SCALE = 0.5
SKEW_FIT_BAND = 2  # 当てはめに使う画素の、直線からの距離（縮めた画像の画素）
# 当てはめに使う画素がこれより少ない直線は、角度の計算に使わない
SKEW_FIT_MIN_PIXELS = 50


# クリップボードから貼り付けた画像の、画面に出す名前
CLIPBOARD_NAME = "クリップボードの画像"


class InputError(Exception):
    """入力ファイルを開けないときのエラー（利用者に表示する文を持つ）"""


def _os_error(path: Path, e: OSError) -> InputError | None:
    """ファイルを開けない OSError のうち、壊れているのではないものの文。ほかは None

    Windows では、ほかのソフトが書き込み中のファイルを開くと PermissionError になる（ファイルは壊れていない）。
    """
    if isinstance(e, PermissionError):
        return InputError(
            tr(
                "ファイルを読めません（ほかのソフトが使っているか、読む権限がありません）: {name}",
                name=path.name,
            )
        )
    if isinstance(e, FileNotFoundError):
        return InputError(tr("ファイルが見つかりません: {name}", name=path.name))
    return None


def file_stamp(path: Path) -> tuple[int, int]:
    """ファイルの大きさと更新日時（ナノ秒）。開いた後に変わったかを、出力のときに照合する"""
    st = path.stat()
    return st.st_size, st.st_mtime_ns


@dataclass(frozen=True)
class SourcePage:
    """入力ファイルの1ページ。画素は必要なときに読み込む"""

    path: Path
    index: int  # ファイルの中のページ番号（0始まり）
    # 元の画像の、実際の用紙に対する解像度。出力もこの解像度で行う。PDF のページは、開いただけでは None（ページの中身を
    # 読まないと決まらない。resolve で決める）
    dpi: float | None
    zoom: float = 1.0  # PDFのページの大きさを実際の用紙の大きさに直す倍率
    # 画像のファイルの、横・縦の拡大の倍率（記録された解像度が縦横で違うとき、画素を正方形にする）。
    # 画像に保存された向き（EXIF の向きを直す前）の横・縦
    stretch: tuple[float, float] = (1.0, 1.0)


@dataclass(frozen=True, eq=False)
class MemoryPage:
    """メモリの中の画像の1ページ（クリップボードから貼り付けた用紙の画像）

    墨消しする前の画像をディスクに書かない決まりのため、ファイルにせず、画素のまま解析のプロセスに渡す。
    様式の登録・置き換えにだけ使い、墨消しして出力はしない。
    """

    rgb: np.ndarray  # RGB の画像
    dpi: float  # 実際の用紙に対する解像度（解像度の情報がないので、長い辺を A4 の長い辺とみなす）
    name: str = CLIPBOARD_NAME  # 画面に出す名前（memory_page は画面の言語の文にする）
    index: int = 0
    zoom: float = 1.0

    @property
    def path(self) -> Path:
        """画面に出す名前（ファイルはない）"""
        return Path(self.name)


def memory_page(rgb: np.ndarray, name: str | None = None) -> MemoryPage:
    """画像（RGB）を1ページにする。解像度は、写真と同じく長い辺を A4 の長い辺とみなして決める。名前を渡さなければ
    「クリップボードの画像」（画面の言語の文）"""
    return MemoryPage(
        np.ascontiguousarray(rgb),
        _physical_dpi(max(rgb.shape[:2]), 0.0),
        tr(CLIPBOARD_NAME) if name is None else name,
    )


def open_pages(path: Path) -> list[SourcePage]:
    """入力ファイルを調べ、ページの一覧を返す"""
    suffix = path.suffix.lower()
    if suffix not in filetypes.SUPPORTED_SUFFIXES:
        raise InputError(tr("この種類のファイルは開けません: {name}", name=path.name))
    if suffix == ".pdf":
        with PDF_LOCK:
            try:
                pdf = _open_pdf(path)
            except OSError as e:
                raise (_os_error(path, e) or _pdf_error(path)) from e
            except Exception as e:
                raise _pdf_error(path) from e
            try:
                if len(pdf) == 0:
                    raise InputError(tr("ページがありません: {name}", name=path.name))
                pages = []
                for i in range(len(pdf)):
                    try:
                        pages.append(_pdf_page(path, pdf, i))
                    except Exception as e:
                        # 開けてもページの大きさを読めない PDF。InputError にして、一緒に選んだほかのファイルは
                        # 開けるようにする
                        raise _page_error(path, i) from e
                return pages
            finally:
                pdf.close()
    # 複数ページとして扱うのは TIFF だけで、ほかの形式は1枚目だけを開く。形式は拡張子ではなく中身で
    # 見分ける（Pillow は中身で形式を決める。開く形式は filetypes.IMAGE_FORMATS に限る）。複数の画像を持つ JPEG（MPO 形式）は、2枚目以降に
    # 小さな補助の画像を持つことがあり、それをページにすると墨消しせずに出力してしまうため。
    # 解像度は画像ごとに求める（TIFF は大きさの違う画像を持つことがある）。
    # TIFF の縮小版（サムネイル）と透明のマスクは、ページにしない（ページにすると、どの様式にも類似しないページ
    # として、墨消しせずに出力してしまうため）。全部がそうなら1枚目だけをページにする
    pages = []
    try:
        with Image.open(path, formats=filetypes.IMAGE_FORMATS) as im:
            frames = getattr(im, "n_frames", 1) if im.format == "TIFF" else 1
            for i in range(frames):
                im.seek(i)
                if frames > 1 and _tiff_not_page(im):
                    continue
                pages.append(_image_page(path, i, im))
            if not pages:
                im.seek(0)
                pages.append(_image_page(path, 0, im))
    except OSError as e:
        raise (
            _os_error(path, e)
            or InputError(tr("画像を開けません: {name}", name=path.name))
        ) from e
    except Exception as e:
        raise InputError(tr("画像を開けません: {name}", name=path.name)) from e
    return pages


def _tiff_not_page(im: Image.Image) -> bool:
    """TIFF の今の画像が、縮小版か透明のマスク（ページにしないもの）か"""
    tags = getattr(im, "tag_v2", {})
    return bool(tags.get(TIFF_NEW_SUBFILE_TYPE, 0) & TIFF_NOT_PAGE) or (
        tags.get(TIFF_SUBFILE_TYPE) == 2
    )


def _image_page(path: Path, index: int, im: Image.Image) -> SourcePage:
    """画像のファイルの1枚をページにする。解像度は画像ごとに求める

    記録された解像度が縦横で ASPECT_TOLERANCE を超えて違えば（ファクスの 204×98dpi など）、低いほうの向きを
    拡大して画素を正方形にし、解像度は高いほうの値にする。比が ASPECT_MAX を超える記録は使わない。
    """
    dpi_info = im.info.get("dpi") or (0, 0)
    x = float(dpi_info[0]) if len(dpi_info) > 0 and dpi_info[0] else 0.0
    y = float(dpi_info[1]) if len(dpi_info) > 1 and dpi_info[1] else x
    w, h = im.size
    stretch = (1.0, 1.0)
    if x > 0 and y > 0 and abs(x / y - 1) > ASPECT_TOLERANCE:
        if max(x, y) / min(x, y) <= ASPECT_MAX:
            stretch = (1.0, x / y) if x > y else (y / x, 1.0)
            x = max(x, y)
        else:
            x = 0.0
    long_px = max(w * stretch[0], h * stretch[1])
    return SourcePage(path, index, _physical_dpi(round(long_px), x), 1.0, stretch)


def render(
    page: SourcePage, dpi: float | None = None, gray: bool | None = False
) -> np.ndarray:
    """ページを画像にして返す。dpi を省くと元の解像度で返す

    gray が False なら RGB、True ならグレー（高さ×幅）の画像。None なら、色のないページ（グレーか白黒2値の画像だけの
    PDF のページ、グレーか白黒2値の画像のファイル）はグレー、ほかは RGB。
    PDFのページの回転の指定（/Rotate）と、画像の EXIF の向きは反映する。
    PDF のページも、画像のファイルと同じく、元の解像度（貼られた画像の解像度）で描いてから縮める。PDFium で
    作業画像の解像度に直接描くと、300dpi の白黒2値の画像で見本との位置合わせがずれて、墨消しが欄を覆わない
    試験画像があったため。
    色のないページは、初めからグレーで描いて縮める（RGB の画像を作らないので、一時的なメモリが約3分の1になる。RGB で
    描いてからグレーにしたときと画素は同じ）。色のあるページは、RGB で描いて縮めてからグレーにする（PDFium のグレーの
    描き方は、OpenCV の換算と1〜2違い、文字や図形のある PDF では大きく違うことがあるため）。
    JPEG を縮めるときは、元の解像度で読まずに、JPEG の仕組みで 1/2・1/4・1/8 に縮めながら読んでから縮める
    （Pillow の draft。読んだ画像が、縮めた後の大きさより小さくならない範囲で）。元の解像度で読んで縮めたときと
    画素が少し違う（600dpi の A4 で、解析の結果の傾きが 0.001度、範囲の位置が最大0.5画素違った）。
    """
    page = resolve(page)
    dpi = dpi or page.dpi
    shrink = dpi / page.dpi
    with pixels(page, gray, shrink) as (img, owned, full):
        if img.shape[:2] != full:
            # 縮めながら読んだときは、元の解像度で読んで縮めたときと同じ大きさにする
            h, w = full
            img = cv2.resize(
                img,
                (round(w * shrink), round(h * shrink)),
                interpolation=cv2.INTER_AREA,
            )
        elif abs(shrink - 1) * page.dpi > 0.5:
            interp = cv2.INTER_AREA if shrink < 1 else cv2.INTER_CUBIC
            img = cv2.resize(img, None, fx=shrink, fy=shrink, interpolation=interp)
        elif not owned:
            img = img.copy()
    if gray and img.ndim == 3:
        img = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
    return np.ascontiguousarray(img)


@contextmanager
def pixels(
    page: SourcePage, gray: bool | None = False, shrink: float = 1.0
) -> Iterator[tuple[np.ndarray, bool, tuple[int, int]]]:
    """ページを元の解像度の画像にして、(画像, 画像を呼んだ側が持ってよいか, 元の解像度の高さ・幅) を渡す。gray は
    render と同じ。shrink が 1 より小さければ（後で縮めるなら）、JPEG は縮めながら読む（画像は元の解像度より小さい）

    持ってよくない画像（PDF のページと、メモリの中の画像）は、with の中だけで読み、書き換えない。PDF のページは、
    PDFium が描いた画像をコピーせずに渡し、with を出ると閉じる（コピーすると、描いた画像とコピーが同時にあるため）。
    """
    page = resolve(page)
    if isinstance(page, MemoryPage):
        yield page.rgb, False, page.rgb.shape[:2]
    elif page.path.suffix.lower() == ".pdf":
        scale = page.dpi * page.zoom / 72
        with _pdf_bitmap(
            page.path, page.index, scale, None if gray is not False else False
        ) as img:
            yield img, False, img.shape[:2]
    else:
        img, full = _image_pixels(page, gray, shrink)
        yield img, True, full


def _image_pixels(
    page: SourcePage, gray: bool | None, shrink: float = 1.0
) -> tuple[np.ndarray, tuple[int, int]]:
    """画像のファイルの1枚を配列にする（EXIF の向きを直し、画素を正方形にする）。配列と、元の解像度の高さ・幅を返す。
    shrink が 1 より小さく JPEG なら、縮めながら読む"""
    with Image.open(page.path, formats=filetypes.IMAGE_FORMATS) as im:
        im.seek(page.index)
        full = im.size
        orientation = im.getexif().get(EXIF_ORIENTATION, 1)
        height, width = _square_pixels_shape(full, page.stretch, orientation)
        if full[0] * full[1] > MAX_PIXELS:
            width, height = full
        if width * height > MAX_PIXELS:
            # 配列を取る前に止める（解析の依頼の失敗になり、そのページは「処理できません」になる）
            raise InputError(
                tr(
                    "画像が大きすぎます（{width}×{height}画素）",
                    width=width,
                    height=height,
                )
            )
        if shrink < 1 and im.format == "JPEG":
            im.draft(
                im.mode, (math.ceil(full[0] * shrink), math.ceil(full[1] * shrink))
            )
        # 向きを直さない画像（ふつうのスキャンと写真）では、exif_transpose のコピーを作らない
        source = im if orientation in (0, 1) else ImageOps.exif_transpose(im)
        colorless = gray is not False and source.mode in GRAY_MODES
        img = _strips(source, colorless)
    img = _square_pixels(img, page.stretch, orientation)
    return img, _square_pixels_shape(full, page.stretch, orientation)


def _square_pixels_shape(
    size: tuple[int, int], stretch: tuple[float, float], orientation: int
) -> tuple[int, int]:
    """元の大きさ（幅・高さ。EXIF の向きを直す前）の画像を、向きを直して画素を正方形にした後の高さ・幅
    （_square_pixels と同じ計算）"""
    w, h = size
    if orientation in (5, 6, 7, 8):
        w, h = h, w
    fx, fy = stretch
    if (fx, fy) == (1.0, 1.0):
        return h, w
    if orientation in (5, 6, 7, 8):
        fx, fy = fy, fx
    return round(h * fy), round(w * fx)


STRIP_ROWS = 256  # 画像を配列に写すときの帯の高さ（画素）


def _strips(im: Image.Image, colorless: bool) -> np.ndarray:
    """Pillow の画像を、帯ごとに配列に写す（colorless ならグレー、ほかは RGB）

    Pillow の画像をそのまま配列にすると、Pillow が全体のバイト列を作るので、Pillow の画像・バイト列・配列が同時に
    ある時間ができる（600dpi の A4 のカラーで1つ約100MB）。帯ごとに写すと、Pillow の画像と配列の2つで済む。
    白黒2値は、8ビットのグレーの画像を作らずに 0・255 にする。
    """
    w, h = im.size
    # 32ビットの整数の画像を8ビットとみなすかは、帯ごとでなく画像全体で決める（_gray_of_int）
    wide = im.mode == "I" and im.getextrema()[1] > 255
    out = np.empty((h, w) if colorless else (h, w, 3), np.uint8)
    for y in range(0, h, STRIP_ROWS):
        part = im.crop((0, y, w, min(h, y + STRIP_ROWS)))
        if colorless and part.mode == "1":
            out[y : y + part.height] = np.multiply(
                np.asarray(part), 255, dtype=np.uint8
            )
        elif colorless:
            out[y : y + part.height] = np.asarray(_to_gray(part, wide))
        else:
            out[y : y + part.height] = np.asarray(_to_rgb(part, wide))
    return out


# グレーのまま読む画像のファイルの、Pillow のモード（白黒2値・8ビットのグレー・16ビットと32ビットの整数のグレー）
GRAY_MODES = frozenset({"1", "L", "I", "I;16", "I;16L", "I;16B", "I;16N"})


def _to_gray(im: Image.Image, wide: bool | None = None) -> Image.Image:
    """グレーか白黒2値の画像のファイルの1枚を、8ビットのグレーにする（_to_rgb で RGB にしてからグレーにしたときと
    同じ値）。wide は _gray_of_int と同じ"""
    if im.mode == "L":
        return im
    if im.mode == "1":
        return im.convert("L")
    return Image.fromarray(_gray_of_int(im, wide))


def _to_rgb(im: Image.Image, wide: bool | None = None) -> Image.Image:
    """画像のファイルの1枚を RGB にする。16ビットのグレー（I;16 など）と32ビットの整数（I）は、上位8ビットのグレーに
    してから変える。Pillow の convert("RGB") は値を255で切り詰めるので、そのまま変えるとほぼ真っ白になるため。
    32ビットの整数でも、値が255以下なら8ビットの値とみなす"""
    if im.mode == "RGB":  # 変えない（convert はコピーを作る）
        return im
    if not im.mode.startswith("I"):
        return im.convert("RGB")
    return Image.fromarray(_gray_of_int(im, wide)).convert("RGB")


def _gray_of_int(im: Image.Image, wide: bool | None = None) -> np.ndarray:
    """16ビットのグレーと32ビットの整数の画像を、8ビットのグレーにする（32ビットでも、値が255以下なら8ビットの値と
    みなす。wide は、値が255を超えるか。None なら im から決める（画像の一部の帯のときは、画像全体で決めて渡す））"""
    a = np.asarray(im)
    if wide is None:
        wide = a.max() > 255
    if im.mode == "I" and not wide:
        return np.clip(a, 0, 255).astype(np.uint8)
    return (np.clip(a, 0, 65535).astype(np.uint32) >> 8).astype(np.uint8)


EXIF_ORIENTATION = 0x0112  # EXIF の向きのタグ。5〜8 は、向きを直すと縦横が入れ替わる


def _square_pixels(
    rgb: np.ndarray, stretch: tuple[float, float], orientation: int
) -> np.ndarray:
    """縦横で解像度の違う画像の画素を正方形にする（stretch は、EXIF の向きを直す前の横・縦の倍率）"""
    fx, fy = stretch
    if (fx, fy) == (1.0, 1.0):
        return rgb
    if orientation in (5, 6, 7, 8):  # 向きを直した画像では、横と縦が入れ替わっている
        fx, fy = fy, fx
    h, w = rgb.shape[:2]
    return cv2.resize(
        rgb, (round(w * fx), round(h * fy)), interpolation=cv2.INTER_CUBIC
    )


def correct(img: np.ndarray, quarter_turns: int, skew_deg: float) -> np.ndarray:
    """反時計回りに quarter_turns×90度回し、さらに skew_deg 度だけ反時計回りに回して傾きを直す（estimate_skew の
    正の角度は右下がりなので、反時計回りに回すと水平になる）

    傾きを直すときは画像を広げ、ページの端が切れないようにする。
    """
    img = np.ascontiguousarray(np.rot90(img, quarter_turns % 4))
    if abs(skew_deg) < SKEW_MIN_DEG:
        return img
    h, w = img.shape[:2]
    m = cv2.getRotationMatrix2D((w / 2, h / 2), skew_deg, 1.0)
    c, s = abs(m[0, 0]), abs(m[0, 1])
    nw, nh = round(float(h * s + w * c)), round(float(h * c + w * s))
    m[0, 2] += (nw - w) / 2
    m[1, 2] += (nh - h) / 2
    border = 255 if img.ndim == 2 else (255, 255, 255)
    return cv2.warpAffine(img, m, (nw, nh), flags=cv2.INTER_LINEAR, borderValue=border)


def _pdf_error(path: Path) -> InputError:
    """pypdfium2 で開けなかった PDF の理由を決める（PDF_LOCK を持って呼ぶ）

    pypdfium2 は、読めたがページがない PDF も PdfiumError にする。そのときの err_code には、前に別の PDF を
    開けなかったときの値が残っていることがある（PDFium の FPDF_GetLastError は、成功しても値を戻さないので、
    パスワード付きの PDF の後に開いた、ページのない PDF を「パスワード付き」と示してしまう）。そのため、
    メモリでもう一度読み、読めたかどうかと、読めなかったときの理由を確かめる。
    """
    try:
        data = path.read_bytes()
    except OSError:
        return InputError(
            tr("PDFを開けません（壊れている可能性があります）: {name}", name=path.name)
        )
    doc = pdfium_c.FPDF_LoadMemDocument64(data, len(data), None)
    if doc:
        pdfium_c.FPDF_CloseDocument(doc)
        return InputError(tr("ページがありません: {name}", name=path.name))
    if pdfium_c.FPDF_GetLastError() == pdfium_c.FPDF_ERR_PASSWORD:
        return InputError(tr("パスワード付きのPDFは開けません: {name}", name=path.name))
    return InputError(
        tr("PDFを開けません（壊れている可能性があります）: {name}", name=path.name)
    )


@contextmanager
def open_pdf(path: Path) -> Iterator[pdfium.PdfDocument]:
    """PDF を pypdfium2 で開き、抜けるときに閉じる（PDF_LOCK を持って使う）"""
    pdf = _open_pdf(path)
    try:
        yield pdf
    finally:
        pdf.close()


def _open_pdf(path: Path) -> pdfium.PdfDocument:
    """PDF を pypdfium2 で開く。使い終わったら close する（PDF_LOCK を持って呼ぶ）

    ファイルは Python で開いて渡す。pypdfium2 にファイルの名前を渡すと、名前を UTF-8 にして PDFium に渡すが、
    Windows で日本語の名前のファイルを開けるかを確かめていないため。ファイルは文書を閉じるときに
    閉じる（autoclose）。
    """
    f = open(path, "rb")  # noqa: SIM115  文書を閉じるときに pypdfium2 が閉じる
    try:
        return pdfium.PdfDocument(f, autoclose=True)
    except BaseException:
        f.close()
        raise


def render_pdf_page(
    path: Path, index: int, scale: float, gray: bool | None = False
) -> np.ndarray:
    """PDF のページを、1ポイントあたり scale 画素の RGB（gray なら グレー）の画像にする。gray が None なら、色のない
    ページ（PageParts.colorless）はグレー、ほかは RGB

    /Rotate と、注釈・入力欄の見た目も描く。入力欄の値は、init_forms をしないと描かれない。
    """
    with _pdf_bitmap(path, index, scale, gray) as img:
        return np.array(img)  # コピーを返す（描いた画像は閉じるため）


@contextmanager
def _pdf_bitmap(
    path: Path, index: int, scale: float, gray: bool | None
) -> Iterator[np.ndarray]:
    """render_pdf_page と同じ画像を、PDFium が描いた画像のままコピーせずに渡す（with の中だけで使う）

    描いたら文書を閉じて PDF_LOCK を放す（with の中の処理のあいだ、ほかのスレッドの PDF の処理を止めないため）。
    描いた画像は文書と別に持たれていて、文書を閉じた後も読める。
    """
    if gray is None:
        gray = _pdf_parts(path, index, file_stamp(path)).colorless
    with PDF_LOCK, open_pdf(path) as pdf:
        pdf.init_forms()
        page = _load_page(path, pdf, index)
        try:
            fmt = pdfium_c.FPDFBitmap_Gray if gray else pdfium_c.FPDFBitmap_BGR
            bitmap = page.render(
                scale=scale, force_bitmap_format=fmt, rev_byteorder=not gray
            )
        finally:
            page.close()
    try:
        yield bitmap.to_numpy()
    finally:
        with PDF_LOCK:
            bitmap.close()


def _pdf_page(path: Path, pdf: pdfium.PdfDocument, index: int) -> SourcePage:
    """PDF の1ページを、ページの中身を読まずに、大きさだけでページにする（解像度は None。resolve で決める）

    開く処理は、画面のプロセスとコマンドラインのプロセスで動く。ページの中身を読むと（FPDF_LoadPage）、自分自身を描く
    部品などを持つ細工した PDF で、PDFium が部品を展開し続けて異常終了し、プロセスごと終わるため。ページの中身を読む
    のは、解析のプロセスだけにする（異常終了しても、そのページが処理できないページになるだけで済む）。
    """
    size = pdfium_c.FS_SIZEF()
    if not pdfium_c.FPDF_GetPageSizeByIndexF(pdf.raw, index, ctypes.byref(size)):
        raise ValueError(f"could not read the size of page {index + 1}")
    return SourcePage(path, index, None, _pdf_zoom(size.width, size.height))


def _page_error(path: Path, index: int) -> InputError:
    """PDF の index のページを読めないときの InputError"""
    return InputError(
        tr("PDF の{page}ページ目を読めません: {name}", page=index + 1, name=path.name)
    )


def _load_page(path: Path, pdf: pdfium.PdfDocument, index: int) -> pdfium.PdfPage:
    """PDF の index のページの中身を読む（解析のプロセスで呼ぶ）。読めなければ InputError（PDFium の英語の文を、
    そのまま画面に出さないため）"""
    try:
        return pdf[index]
    except pdfium.PdfiumError as e:
        raise _page_error(path, index) from e


def _pdf_zoom(width: float, height: float) -> float:
    """PDF のページの大きさ（ポイント）を実際の用紙の大きさに直す倍率。大きさが実際の用紙と合わなければ A4 とみなす"""
    page_long_in = max(width, height) / 72
    if PLAUSIBLE_LONG_IN[0] <= page_long_in <= PLAUSIBLE_LONG_IN[1]:
        return 1.0
    return A4_LONG_IN / page_long_in


def resolve(page):
    """解像度の決まっていない PDF のページ（open_pages の結果）を、解像度を決めたページにする。決まっていればそのまま

    ページの中身（貼られた画像）を読むので、解析のプロセスで呼ぶ（_pdf_page）。描く処理（render・pixels）が呼ぶ。
    """
    if not isinstance(page, SourcePage) or page.dpi is not None:
        return page
    dpi = _pdf_dpi(page.path, page.index, page.zoom, file_stamp(page.path))
    return replace(page, dpi=dpi)


def _pdf_dpi(path: Path, index: int, zoom: float, stamp: tuple[int, int]) -> float:
    """PDF のページを描く解像度（resolve。stamp はファイルの大きさと更新日時で、変わったファイルを読み直すための鍵）"""
    parts = _pdf_parts(path, index, stamp)
    if not parts.px_per_in:
        return float(WORK_DPI)
    dpi = min(parts.px_per_in / zoom, PDF_MAX_DPI)
    page_in2 = (parts.width / 72) * (parts.height / 72)
    if parts.drawing or parts.area_in2 < PDF_PARTIAL * page_in2:
        dpi = max(dpi, PDF_MIN_DPI)
    return dpi


@dataclass(frozen=True)
class PageParts:
    """PDF のページの部品をたどった結果（_page_parts）"""

    width: float  # /Rotate を反映した大きさ（ポイント）
    height: float
    # 貼られた範囲が1平方インチを超える画像のうち、ページの1インチあたりの画素数が最も多いものの、その画素数と、貼られた
    # 範囲の外接矩形の面積（平方インチ）。なければ 0
    px_per_in: float
    area_in2: float
    drawing: bool  # 画像のほかに、文字・図形・シェーディングがある
    # グレーか白黒2値の画像だけでできている（文字・図形・シェーディング・注釈がなく、画像の色空間が DeviceGray か
    # CalGray で、1画素が1ビットか8ビット）。そのページは、グレーで描いても、RGB で描いてからグレーにしたときと画素が同じ
    colorless: bool


@lru_cache(maxsize=256)
def _pdf_parts(path: Path, index: int, stamp: tuple[int, int]) -> PageParts:
    """PDF の index のページの部品をたどった結果（_page_parts）。描く解像度（_pdf_dpi）と、グレーで描くか（_pdf_bitmap）の
    両方に使うので、1回だけたどって覚える（stamp はファイルの大きさと更新日時で、変わったファイルを読み直すための鍵）。
    ページの中身を読むので、解析のプロセスで呼ぶ"""
    with PDF_LOCK, open_pdf(path) as pdf:
        page = _load_page(path, pdf, index)
        try:
            return _page_parts(page)
        finally:
            page.close()


def _page_parts(page: pdfium.PdfPage) -> PageParts:
    """ページの部品（画像・文字・図形・シェーディング・Form XObject）を1回たどって、PageParts を求める（PDF_LOCK を持って
    呼ぶ。page.raw は、page を持っているこの関数の中だけで使う）

    部品は、ラッパーの Python のオブジェクトを作らずに C の関数でたどる（部品の多いページで開く時間を延ばさないため）。
    Form XObject の中の部品の変換行列は Form XObject の中の座標で表されるので、Form XObject の変換行列を掛け合わせて
    ページの座標に直す（直さないと、縮めて貼った Form XObject の中の画像の解像度を小さく見積もる）。画像の範囲は、
    貼られた範囲の外接矩形の面積で測る（小さな画像（ロゴなど）は対象にしない）。インライン画像も対象にする。
    変換行列を読めない部品は、解像度と「文字・図形がある」には数えない（その Form XObject の中も同じ）が、色のない
    ページかは、その中まで調べる。
    """
    width, height = page.get_size()
    gray_spaces = (
        pdfium_c.FPDF_COLORSPACE_DEVICEGRAY,
        pdfium_c.FPDF_COLORSPACE_CALGRAY,
    )
    colorless = pdfium_c.FPDFPage_GetAnnotCount(page.raw) == 0
    found = False  # 色のないページかを決める画像があった
    best, best_area, drawing = 0.0, 0.0, False
    # （親, Form XObject か, ページの座標への変換行列。読めなければ None）
    stack = [(page.raw, False, (1.0, 0.0, 0.0, 1.0, 0.0, 0.0))]
    w, h, m = ctypes.c_uint(), ctypes.c_uint(), pdfium_c.FS_MATRIX()
    meta = pdfium_c.FPDF_IMAGEOBJ_METADATA()
    while stack:
        parent, is_form, ctm = stack.pop()
        if is_form:
            count = pdfium_c.FPDFFormObj_CountObjects(parent)
            get = pdfium_c.FPDFFormObj_GetObject
        else:
            count = pdfium_c.FPDFPage_CountObjects(parent)
            get = pdfium_c.FPDFPage_GetObject
        for i in range(count):
            obj = get(parent, i)
            kind = pdfium_c.FPDFPageObj_GetType(obj)
            if kind not in (pdfium_c.FPDF_PAGEOBJ_IMAGE, pdfium_c.FPDF_PAGEOBJ_FORM):
                colorless = False
                if kind in DRAWING_KINDS and ctm is not None:
                    drawing = True
                continue
            full = None
            if ctm is not None and pdfium_c.FPDFPageObj_GetMatrix(obj, m):
                full = _mul((m.a, m.b, m.c, m.d, m.e, m.f), ctm)
            if kind == pdfium_c.FPDF_PAGEOBJ_FORM:
                stack.append((obj, True, full))
                continue
            if colorless:
                if (
                    pdfium_c.FPDFImageObj_GetImageMetadata(
                        obj, page.raw, ctypes.byref(meta)
                    )
                    and meta.colorspace in gray_spaces
                    and meta.bits_per_pixel in (1, 8)
                ):
                    found = True
                else:
                    colorless = False
            if full is not None and pdfium_c.FPDFImageObj_GetImagePixelSize(obj, w, h):
                a, b, c, d = full[:4]  # 画像は1×1の正方形を変換行列で貼る
                bw = abs(a) + abs(c)  # 外接矩形の幅・高さ（ポイント）
                bh = abs(b) + abs(d)
                area_in2 = (bw / 72) * (bh / 72)
                if area_in2 > 1:
                    ppi = ((w.value * h.value) / area_in2) ** 0.5
                    if ppi > best:
                        best, best_area = ppi, area_in2
    return PageParts(width, height, best, best_area, drawing, colorless and found)


def _mul(m: tuple, n: tuple) -> tuple:
    """PDF の変換行列の積（点に m を当ててから n を当てる）。行列は (a, b, c, d, e, f)"""
    a, b, c, d, e, f = m
    A, B, C, D, E, F = n
    return (
        a * A + b * C,
        a * B + b * D,
        c * A + d * C,
        c * B + d * D,
        e * A + f * C + E,
        e * B + f * D + F,
    )


def _physical_dpi(long_px: int, meta_dpi: float) -> float:
    """画像の実際の用紙に対する解像度。記録された値がありえないときは A4 とみなす"""
    if meta_dpi >= 50:
        long_in = long_px / meta_dpi
        if PLAUSIBLE_LONG_IN[0] <= long_in <= PLAUSIBLE_LONG_IN[1]:
            return meta_dpi
    return long_px / A4_LONG_IN


def estimate_skew(gray: np.ndarray) -> float:
    """長い直線の角度の中央値から傾き（度）を求める。画像の上で直線が右下がり（時計回りに傾いている）なら正。
    求められなければ 0"""
    small = cv2.resize(
        gray, None, fx=SKEW_SCALE, fy=SKEW_SCALE, interpolation=cv2.INTER_AREA
    )
    bw = cv2.adaptiveThreshold(
        ~small, 255, cv2.ADAPTIVE_THRESH_MEAN_C, cv2.THRESH_BINARY, 15, -10
    )
    lines = cv2.HoughLinesP(
        bw,
        1,
        np.pi / 900,
        round(300 * SKEW_SCALE),
        minLineLength=round(600 * SKEW_SCALE),
        maxLineGap=10,
    )
    if lines is None:
        return 0.0
    ys, xs = np.nonzero(bw)  # 行の順に並ぶ（ys は小さい順）
    angles = []
    for x1, y1, x2, y2 in lines.reshape(-1, 4):
        if abs(np.degrees(np.arctan2(y2 - y1, x2 - x1))) >= MAX_SKEW_DEG:
            continue
        # 見つけた直線の近くの画素に直線を当てはめ、角度を求め直す。直線から SKEW_FIT_BAND 以内の画素は、直線の
        # 両端の行の上下 SKEW_FIT_BAND 行の中にしかないので、先にその行の範囲（上下に1行の余裕を足す）を切り出す。
        # 画素の並びは変わらないので、当てはめに渡す画素は全部の画素から選ぶときと同じ
        lo = np.searchsorted(ys, min(y1, y2) - SKEW_FIT_BAND - 1, "left")
        hi = np.searchsorted(ys, max(y1, y2) + SKEW_FIT_BAND + 1, "right")
        band_x, band_y = xs[lo:hi], ys[lo:hi]
        sel = (band_x >= min(x1, x2)) & (band_x <= max(x1, x2))
        px, py = band_x[sel], band_y[sel]
        near = np.abs(py - (y1 + (px - x1) * (y2 - y1) / (x2 - x1))) <= SKEW_FIT_BAND
        if near.sum() >= SKEW_FIT_MIN_PIXELS:
            slope = np.polyfit(px[near], py[near], 1)[0]
            angles.append(np.degrees(np.arctan(slope)))
    return float(np.median(angles)) if angles else 0.0
