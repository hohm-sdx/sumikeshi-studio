# Copyright (C) 2026 Hideki Ohmura
# SPDX-License-Identifier: AGPL-3.0-only
"""墨消ししたPDFの出力

1ページを画像にして墨消しし JPEG にする処理（mask_page）は、解析のプロセスに頼んで並べて行える。
PDF にまとめて保存する処理は、呼んだ側で行う。PDF は1ページずつファイルに書き足す（PdfWriter）。

出力後の検査（墨消しした範囲が黒いか・文字のデータが残っていないか）は、出力のたびには行わない。出力の中身は
コードと uv.lock で固定したライブラリだけで決まるので、同じ検査はテスト（tests/pdfcheck.py）で、コードや
ライブラリを変えたときに行う。
"""

import math
import os
import uuid
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePath

import cv2
import numpy as np

from . import boxes, folders, imaging
from .i18n import tr
from .pipeline import PageResult

# 墨消しする範囲は広げない（印刷された枠からはみ出さないため）
JPEG_QUALITY = 90


@dataclass(frozen=True)
class PageJob:
    """1ページを墨消しするのに要るもの（解析のプロセスに渡すため、解析の作業画像は持たない）"""

    source: imaging.SourcePage
    quarter_turns: int
    skew: float
    work_shape: tuple[int, int]  # 解析の作業画像の高さ・幅
    rects: tuple[
        tuple[float, float, float, float], ...
    ]  # 墨消しする長方形の範囲（作業画像の上の x, y, 幅, 高さ）
    # 墨消しする長方形でない範囲の角（作業画像の上の (x, y) の並び。boxes.Box.corners）
    polygons: tuple[tuple[boxes.Point, ...], ...] = ()

    @classmethod
    def of(cls, source: imaging.SourcePage, res: PageResult | None) -> PageJob:
        """幅か高さが0以下か、有限の数でない範囲（角が3つ未満の範囲を含む）があれば ValueError（黙って塗らないことを
        なくすため）

        res が None（解析が失敗したページ）なら、向きと傾きを直さず、墨消しする範囲のない依頼にする。コマンドラインから
        出力するときに、解析が失敗したページも画像にできれば墨消しせずに入れ、確かめるべきページとして知らせるため
        （画面は、処理できないページのあるファイルを出力しないので、渡さない）。
        """
        if res is None:
            return cls(source, 0, 0.0, (1, 1), ())
        active = [b for b in res.boxes if b.active]
        rects = tuple(b.rect() for b in active if b.corners is None)
        polygons = tuple(b.corners for b in active if b.corners is not None)
        for b in active:
            x, y, w, h = b.rect()
            values = [x, y, w, h, *(v for p in b.corners or () for v in p)]
            if not (
                all(math.isfinite(v) for v in values)
                and w > 0
                and h > 0
                and (b.corners is None or len(b.corners) >= 3)
            ):
                raise ValueError(
                    tr(
                        "墨消しする範囲の大きさが正しくありません（{kind}）",
                        kind=boxes.kind_text(b.kind),
                    )
                )
        return cls(source, res.quarter_turns, res.skew, res.work_shape, rects, polygons)


@dataclass(frozen=True)
class PageImage:
    """墨消しして JPEG にした1ページ"""

    jpeg: bytes
    width: int  # 画素
    height: int
    masked: int  # 墨消しした範囲の数
    components: int = 3  # JPEG の色の数（グレーは1、カラーは3）


# mask_pages(jobs) は、jobs のそれぞれを墨消しした PageImage を、jobs の順に返す
PageMasker = Callable[[list[PageJob]], Iterable[PageImage]]


@dataclass
class ExportReport:
    pages: int = 0
    masked: int = 0  # 墨消しした範囲の数


class ExportError(Exception):
    """出力できなかった理由（画面に出す文を持つ）。入力ファイルが変わっていた、出力先のファイルを置き換えられない、など"""


# 開いた後に入力ファイルが変わっていたとき
CHANGED = (
    "開いた後に、入力ファイルが変わっています（大きさか更新日時が違います）。"
    "開き直して確かめてから出力してください。"
)
REPLACE_FAILED = (
    "出力先の同じ名前のファイルを、ほかのソフトが開いている可能性があります（{error}）"
)
FOLDER_FAILED = "出力先のフォルダを作れませんでした（{error}）: {folder}"


def check_unchanged(path: Path, stamp: tuple[int, int] | None) -> None:
    """入力ファイルが、開いたときと同じ大きさと更新日時か（stamp は imaging.file_stamp の値）。違えば ExportError。
    stamp が None（開いたときに覚えていない）なら確かめない"""
    if stamp is None:
        return
    try:
        now = imaging.file_stamp(path)
    except OSError:
        now = None
    if now != stamp:
        raise ExportError(tr(CHANGED))


def export(
    pages: list[tuple[imaging.SourcePage, PageResult | None]],
    out_path: Path,
    progress: Callable[[int, int, str], None] | None = None,
    mask_pages: PageMasker | None = None,
    before_replace: Callable[[], None] | None = None,
) -> ExportReport:
    """確定した範囲の画素を墨消しし、画像だけのPDFを作り直して保存する

    元のファイルの文字情報（スキャナーのOCRが付けた透明な文字を含む）・注釈・入力欄・しおり・
    添付ファイル・メタデータは引き継がない。出力したPDFは、ページごとに画像1つだけを持つ。
    mask_pages を渡すと、ページを墨消しする処理をそれで行う（解析のプロセスに頼んで並べて行う。workers.Exporter が
    analysis.mask_page を使うものを渡す）。省くと、このスレッドで1ページずつ行う。

    出力先と同じフォルダに一時的な名前（.<出力の名前>.<8桁>.tmp）のファイルを作り、1ページ墨消しするたびに書き足して、
    書き終わってから正式な名前にする（保存の途中で失敗したときに、前の出力を壊さないため）。before_replace は、
    正式な名前にする直前に呼ぶ（入力ファイルが変わっていないかを確かめる。例外を出せば出力しない）。
    途中で失敗したときは、一時的な名前のファイルを消してから例外を出す。
    """
    mask_pages = mask_pages or (lambda jobs: map(mask_page, jobs))
    jobs = [PageJob.of(src, res) for src, res in pages]
    total = len(jobs)
    report = ExportReport(pages=len(jobs))
    tmp = _temporary(out_path, uuid.uuid4().hex[:8])
    try:
        out_path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        raise ExportError(
            tr(FOLDER_FAILED, error=type(e).__name__, folder=out_path.parent)
        ) from e
    try:
        with tmp.open("wb") as f:
            out = PdfWriter(f)
            _notify(progress, 0, total, tr("1ページ目を墨消ししています"))
            for i, (job, image) in enumerate(zip(jobs, mask_pages(jobs))):
                report.masked += image.masked
                out.add_page(image, job.source.dpi)
                _notify(
                    progress,
                    i + 1,
                    total,
                    tr("{number}ページ目を墨消ししました", number=i + 1),
                )
            out.close()
        if before_replace is not None:
            before_replace()
        try:
            os.replace(tmp, out_path)
        except PermissionError as e:
            raise ExportError(
                tr(REPLACE_FAILED, error=f"{type(e).__name__}: {e}")
            ) from e
    except BaseException:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    _notify(progress, total, total, tr("出力しました"))
    return report


class PdfWriter:
    """画像1枚だけのページを並べた PDF を、1ページずつファイルに書き足す

    PDFium の文書に全ページを足してから保存すると、1つの出力のファイルの JPEG をすべてメモリに持つため、自分で書き、
    メモリには1ページ分だけを持つ。JPEG は圧縮し直さずに、画像の XObject（DCTDecode）として入れる。文書の情報
    （作ったソフトや日時）は書かない。相互参照表は従来の形で書く（tests/pdfcheck.py が読む形）。
    オブジェクトの番号は、1 が目録、2 がページの一覧（どちらも最後に書く）、3 からがページごとの画像・中身・ページ。
    """

    def __init__(self, f):
        self.f = f
        self.offsets: dict[int, int] = {}
        self.kids: list[int] = []
        self.number = 3
        f.write(b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n")

    def _obj(self, number: int, body: bytes) -> None:
        self.offsets[number] = self.f.tell()
        self.f.write(b"%d 0 obj\n" % number + body + b"\nendobj\n")

    def add_page(self, image: PageImage, dpi: float) -> None:
        width, height = _num(image.width * 72 / dpi), _num(image.height * 72 / dpi)
        img, content, page = self.number, self.number + 1, self.number + 2
        self.number += 3
        space = b"/DeviceGray" if image.components == 1 else b"/DeviceRGB"
        self._obj(
            img,
            b"<< /Type /XObject /Subtype /Image /Width %d /Height %d /ColorSpace %s "
            b"/BitsPerComponent 8 /Filter /DCTDecode /Length %d >>\nstream\n"
            % (image.width, image.height, space, len(image.jpeg))
            + image.jpeg
            + b"\nendstream",
        )
        ops = b"q %s 0 0 %s 0 0 cm /Im0 Do Q" % (width, height)
        self._obj(
            content, b"<< /Length %d >>\nstream\n" % len(ops) + ops + b"\nendstream"
        )
        self._obj(
            page,
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 %s %s] "
            b"/Resources << /XObject << /Im0 %d 0 R >> >> /Contents %d 0 R >>"
            % (width, height, img, content),
        )
        self.kids.append(page)

    def close(self) -> None:
        kids = b" ".join(b"%d 0 R" % k for k in self.kids)
        self._obj(
            2, b"<< /Type /Pages /Kids [%s] /Count %d >>" % (kids, len(self.kids))
        )
        self._obj(1, b"<< /Type /Catalog /Pages 2 0 R >>")
        xref = self.f.tell()
        size = self.number
        self.f.write(b"xref\n0 %d\n0000000000 65535 f \n" % size)
        for n in range(1, size):
            self.f.write(b"%010d 00000 n \n" % self.offsets[n])
        # /ID は、ファイルを見分けるための数値（毎回の乱数。PDF 2.0 では必ず要る）
        file_id = uuid.uuid4().hex.encode()
        self.f.write(
            b"trailer\n<< /Size %d /Root 1 0 R /ID [<%s> <%s>] >>\nstartxref\n%d\n%%%%EOF\n"
            % (size, file_id, file_id, xref)
        )


def _num(x: float) -> bytes:
    """PDF に書く数（小数点以下4桁まで。末尾の0は書かない）"""
    return (f"{x:.4f}".rstrip("0").rstrip(".") or "0").encode()


def mask_page(job: PageJob) -> PageImage:
    """1ページを元の解像度で画像にし、向きと傾きを直して、墨消しする範囲の画素を黒くし、JPEG にする

    解析のプロセスでも動く（画像はプロセスの間をメモリで受け渡し、ディスクには書かない）。
    """
    # 色のないページはグレーで描く（出力も色のないページはグレーにするので、JPEG は RGB で描いたときと同じ）。
    # 描いた画像は、向きと傾きを直した画像を作ったら手放す（同時に持つ画像を2つまでにする）
    with imaging.pixels(job.source, gray=None) as (view, owned, _full):
        img = imaging.correct(view, job.quarter_turns, job.skew)
        if not owned and (img is view or np.may_share_memory(img, view)):
            img = img.copy()  # 回さず傾きも直さないときは、描いた画像がそのまま返るので、閉じる前にコピーする
        del (
            view
        )  # 描いた画像を、ここで手放す（変数に残すと、JPEG にするまで持ち続けるため）
    sx = img.shape[1] / job.work_shape[1]
    sy = img.shape[0] / job.work_shape[0]
    for rect in job.rects:
        x0, y0, x1, y1 = _scaled(rect, sx, sy, img.shape)
        img[y0:y1, x0:x1] = 0
    for points in job.polygons:
        x0, y0, mask = polygon_pixels(points, sx, sy, img.shape)
        img[y0 : y0 + mask.shape[0], x0 : x0 + mask.shape[1]][mask] = 0
    compact = _compact(img)
    ok, buf = cv2.imencode(".jpg", compact, [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY])
    if not ok:
        raise RuntimeError(tr("画像の圧縮に失敗しました"))
    components = 1 if compact.ndim == 2 else 3
    masked = len(job.rects) + len(job.polygons)
    return PageImage(buf.tobytes(), img.shape[1], img.shape[0], masked, components)


def output_paths(
    inputs: list[Path], folder: Path, subfolders: Sequence[PurePath] = ()
) -> list[Path]:
    """入力ファイルごとの出力先「folder / サブフォルダ / 元の名前_redacted.pdf」を決める

    サブフォルダは、開いたフォルダからのフォルダの相対パス（folders.Found.folder。省くと folder の直下）。
    同じフォルダに同じ名前になる入力があれば（a.pdf と a.jpg など）、2件目から「_2」「_3」を付ける。
    """
    out: list[Path] = []
    used: set[tuple[str, str]] = set()
    for src, sub in zip(inputs, subfolders or [PurePath()] * len(inputs), strict=True):
        where = str(sub).casefold()  # Windows は大文字と小文字を区別しない
        base = f"{src.stem}{folders.OUTPUT_MARK}"
        name, n = f"{base}.pdf", 1
        while (where, name.casefold()) in used:
            n += 1
            name = f"{base}_{n}.pdf"
        used.add((where, name.casefold()))
        out.append(folder / sub / name)
    return out


def _temporary(out_path: Path, mark: str) -> Path:
    """書いている途中の一時的な名前（出力先と同じフォルダの .<出力の名前>.<8桁>.tmp）。名前の長さを変えたら、
    exporttext.TEMPORARY_EXTRA も直す"""
    return out_path.with_name(f".{out_path.name}.{mark}.tmp")


def _scaled(rect, sx, sy, shape):
    """作業画像の上の範囲を、画像の画素の範囲にする（外向きに丸める。テストの検査（tests/pdfcheck.py）も同じ範囲を使う）"""
    x, y, w, h = rect
    x0 = max(math.floor(x * sx), 0)
    y0 = max(math.floor(y * sy), 0)
    x1 = min(math.ceil((x + w) * sx), shape[1])
    y1 = min(math.ceil((y + h) * sy), shape[0])
    return x0, y0, x1, y1


def polygon_pixels(points, sx, sy, shape) -> tuple[int, int, np.ndarray]:
    """作業画像の上の長方形でない範囲の角の並びを、画像の画素にする：(左の画素, 上の画素, 塗る画素の真偽の配列)。
    配列は、範囲を囲む長方形（_scaled と同じく外向きに丸め、画像の中に収めたもの）の大きさ。テストの検査
    （tests/pdfcheck.py）も同じ画素を使う

    角を結んだ形の中と、辺にかかる画素を塗る（辺を幅1画素の線で塗る。長方形の範囲を外向きに丸めるのと同じく、範囲に
    かかる画素を残さないため）。画素の番号 i は、画素の座標 i〜i+1 を占めるものとして、角の座標から 0.5 を引いて塗る
    """
    pts = np.asarray(points, np.float64) * [sx, sy]
    x0 = max(math.floor(pts[:, 0].min()), 0)
    y0 = max(math.floor(pts[:, 1].min()), 0)
    x1 = min(math.ceil(pts[:, 0].max()), shape[1])
    y1 = min(math.ceil(pts[:, 1].max()), shape[0])
    mask = np.zeros((max(y1 - y0, 0), max(x1 - x0, 0)), np.uint8)
    if mask.size:
        shift = 4  # 角の座標を 1/16 画素まで使う
        fixed = np.round((pts - [x0 + 0.5, y0 + 0.5]) * (1 << shift)).astype(np.int32)
        cv2.fillPoly(mask, [fixed], 1, cv2.LINE_8, shift)
        cv2.polylines(mask, [fixed], True, 1, 1, cv2.LINE_8, shift)
    return x0, y0, mask.astype(bool)


def _compact(rgb: np.ndarray) -> np.ndarray:
    """色のないページはグレーにして小さくする。JPEG に書くため BGR の順にする（rgb をその場で並べ替える）。グレーで
    描いたページはそのまま"""
    if rgb.ndim == 2:
        return rgb
    if not _has_color(rgb):
        return cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    return cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR, dst=rgb)


def _has_color(rgb: np.ndarray) -> bool:
    """赤と青の差が24以上の画素があるか（帯ごとに調べる。全体の差の配列を作ると、16ビットで画像の2倍の大きさになる
    ため）"""
    for y in range(0, rgb.shape[0], imaging.STRIP_ROWS):
        part = rgb[y : y + imaging.STRIP_ROWS]
        if np.abs(part[:, :, 0].astype(np.int16) - part[:, :, 2]).max() >= 24:
            return True
    return False


def _notify(progress, done, total, message):
    if progress:
        progress(done, total, message)
