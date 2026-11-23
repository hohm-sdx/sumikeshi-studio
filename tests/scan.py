# Copyright (C) 2026 Hideki Ohmura
# SPDX-License-Identifier: AGPL-3.0-only
"""ページの画像（tests/data/）から、スキャンした書類に見立てた画像だけのPDFと、スマートフォンで撮った写真に
見立てた画像を作る（テスト用）

傾き・ずれ・ぼかし・ノイズを加える。ページの画像は、国税庁の公開様式（samples/）と架空の控除証明書を、
PyMuPDF で描いて書き出したもの（tests/data/README.md）。記入した番号・氏名・会社名は架空のもの。
罫線を読む処理のテストに使う、線を引いた表の画像（table）と、位置合わせの手がかりの少ない用紙の画像（simple_form）も作る。
"""

import ctypes
import io
import zlib
from pathlib import Path

import cv2
import numpy as np
import pypdfium2 as pdfium
import pypdfium2.raw as pdfium_c
from PIL import Image

SAMPLES = Path(__file__).resolve().parents[1] / "samples"
# 実物のスキャナーで取り込んだ見本（記入は架空）。リポジトリにないことがある（conftest.needs_sample_scan）
SAMPLE_SCAN = SAMPLES / "fictional_entries" / "sample_カラー原色.pdf"
DATA = Path(__file__).resolve().parent / "data"
DUMMY = "123412341234"  # 架空の個人番号（tests/data/ の記入ありの画像に書いたもの）
PHOTO_LONG, PHOTO_SHORT = 4032, 3024  # 1,200万画素の写真
GRAY_DPI = 200  # グレーの試験画像の解像度
BILEVEL_DPI = 300  # 白黒2値の試験画像の解像度
JPEG_DPI = 232  # カラーの JPEG の試験画像の解像度（見本のスキャンと同じ）
JPEG_PAPER = (
    1.0,
    0.97,
    0.9,
)  # カラーの JPEG の紙の地の色（赤・緑・青の倍率。薄い黄色）


def table(rows: list[int], cols: list[int], height: int = 1200) -> np.ndarray:
    """rows の高さに横の線、cols の位置に縦の線を引き、ますの中に文字に見立てた画を書いた表（幅1600画素のグレー）"""
    g = np.full((height, 1600), 255, np.uint8)
    for y in rows:
        cv2.line(g, (cols[0], y), (cols[-1], y), 0, 2)
    for x in cols:
        cv2.line(g, (x, rows[0]), (x, rows[-1]), 0, 2)
    for y in rows[:-1]:
        for x in cols[:-1]:
            cv2.putText(g, "AB", (x + 12, y + 30), cv2.FONT_HERSHEY_SIMPLEX, 0.9, 0, 2)
    return g


SIMPLE_FIELD = (142, 642, 1370, 104)  # simple_form の表の1行目（墨消しする範囲に使う）


def simple_form() -> np.ndarray:
    """5行の表の線と、英字1語の題名だけの用紙（A4 縦・200dpi）。位置合わせの手がかりが少ない"""
    g = np.full((2339, 1653), 255, np.uint8)
    cv2.rectangle(g, (138, 638), (1516, 1168), 0, 2)
    for y in (750, 860, 972, 1082):
        cv2.line(g, (138, y), (1516, y), 0, 1)
    cv2.putText(g, "Certificate", (160, 190), cv2.FONT_HERSHEY_SIMPLEX, 1.6, 0, 3)
    return g


def data_image(file: str) -> np.ndarray:
    """tests/data/ の PNG を読む（グレーは2次元、カラーは RGB の順の3次元）"""
    # 名前に日本語があっても読めるように、OpenCV にファイルの名前を渡さない
    img = cv2.imdecode(np.fromfile(DATA / file, np.uint8), cv2.IMREAD_UNCHANGED)
    if img is None:
        raise FileNotFoundError(DATA / file)
    return cv2.cvtColor(img, cv2.COLOR_BGR2RGB) if img.ndim == 3 else img


def _page(form: str, dpi: int, rgb: bool, filled: bool = False) -> np.ndarray:
    """samples/<form>.pdf の1ページ目を描いた画像（tests/data/ から読む）"""
    color = "カラー" if rgb else "グレー"
    if filled:
        # 記入ありの画像は、扶養控除等申告書 令和9年分の 200dpi のグレーだけを書き出した
        assert (form, dpi, rgb) == ("2027bun_01", GRAY_DPI, False), (form, dpi, rgb)
        return data_image(f"架空記入_{form}_{dpi}dpi_{color}.png")
    return data_image(f"公開様式_{form}_{dpi}dpi_{color}.png")


def make_scan(
    form: str,
    out: Path,
    angle: float = 0.8,
    filled: bool = False,
    quarter_turns: int = 0,
    kind: str = "gray",
) -> Path:
    """samples/<form>.pdf の1ページ目から試験用のPDFを作る

    filled は扶養控除等申告書 令和9年分（2027bun_01）にだけ使える。本人・配偶者・扶養親族の個人番号欄6か所に
    架空の番号（DUMMY）を、支払者の欄に架空の法人番号を書いたもの。
    quarter_turns を指定すると、反時計回りにその回数×90度回した画像にする。
    kind は画像の種類。"gray" は 200dpi のグレー（Flate で圧縮）、"bilevel" は 300dpi の白黒2値（CCITT G4）、
    "jpeg" は 232dpi のカラーの JPEG を貼った PDF にする。
    """
    if kind == "bilevel":
        page = _page(form, BILEVEL_DPI, False, filled)
        return _scan_bilevel(page, out, angle, quarter_turns)
    if kind == "jpeg":
        return _scan_jpeg(
            _page(form, JPEG_DPI, True, filled), out, angle, quarter_turns
        )
    assert kind == "gray", kind
    return _scan(_page(form, GRAY_DPI, False, filled), out, angle, quarter_turns)


# 保険会社などが発行する控除証明書の題名（個人番号欄はない。申告書と判別してはならない）
CERTIFICATES = {
    "生命保険": "令和8年分　生命保険料控除証明書",
    "地震保険": "令和8年分　地震保険料控除証明書",
    "国民年金": "令和8年分　社会保険料（国民年金保険料）控除証明書",
}


def make_certificate_scan(kind: str, out: Path, angle: float = 0.8) -> Path:
    """架空の控除証明書（A4縦）の試験用のPDFを作る。題名は CERTIFICATES[kind]"""
    return _scan(data_image(f"架空_控除証明書_{kind}_200dpi_グレー.png"), out, angle, 0)


def _scan(img: np.ndarray, out: Path, angle: float, quarter_turns: int) -> Path:
    """200dpi のグレーのページの画像を、傾け・ずらし・ぼかし・ノイズを加えた画像だけのPDFにする"""
    h, w = img.shape
    m, (nw, nh) = _tilt(w, h, angle)
    img = cv2.warpAffine(img, m, (nw, nh), borderValue=255)
    img = cv2.GaussianBlur(img, (3, 3), 0)
    noise = np.random.default_rng(0).normal(0, 8, img.shape)
    img = np.clip(img.astype(float) + noise, 0, 255).astype(np.uint8)
    img = np.ascontiguousarray(np.rot90(img, quarter_turns))
    entries = "/ColorSpace /DeviceGray /BitsPerComponent 8 /Filter /FlateDecode"
    # 圧縮の強さは 1 にする（テストの時間を短くするため）。強さ 6 では1枚の圧縮に約0.5秒かかり、ふだんの
    # テストで作る55枚の合計が約32秒だった（強さ 1 では約0.1秒・合計約4秒）。ファイルは1割ほど大きくなるが、
    # 展開した画素は同じなので、解析の結果は変わらない
    data = zlib.compress(img.tobytes(), 1)
    h, w = img.shape
    # 画像を貼る大きさは、ページの大きさ以上で最も小さい float32 の値にする。PDFium が描く画像の大きさと画素が、
    # その値で変わるため（ページの大きさと同じ値にすると、描いた画像の幅か高さが1画素大きくなる）
    size = (_f32_ceil(w * 72 / GRAY_DPI), _f32_ceil(h * 72 / GRAY_DPI))
    return _image_pdf(out, w, h, GRAY_DPI, entries, data, size)


def _f32_ceil(x: float) -> str:
    """x 以上で最も小さい float32 の値を、その値に読み戻せる文字列で返す"""
    v = np.float32(x)
    if float(v) < x:
        v = np.nextafter(v, np.float32(np.inf))
    return np.format_float_positional(v, unique=True, trim="-")


def _scan_bilevel(
    img: np.ndarray, out: Path, angle: float, quarter_turns: int, threshold: int = 128
) -> Path:
    """300dpi のグレーのページの画像を白黒2値にし、CCITT G4 で圧縮したまま貼った画像だけのPDFにする

    スキャナーの白黒の読み取りに見立てる。傾けてぼかした画像を、濃さ threshold 未満を黒にして2値にする。
    128 では公開様式の細い枠の線がほとんど消え、160 では8割以上が残る。
    """
    h, w = img.shape
    m, (nw, nh) = _tilt(w, h, angle)
    img = cv2.warpAffine(img, m, (nw, nh), borderValue=255)
    img = cv2.GaussianBlur(img, (3, 3), 0)
    img = np.ascontiguousarray(np.rot90(img, quarter_turns))
    # G4 は1のビットを黒の連なりとして書くので、文字を1にする。PDF の /BlackIs1 は既定の false のまま
    # （黒の連なりを黒として描く）で、スキャナーが作る PDF と同じ形になる
    ink = Image.fromarray(np.where(img < threshold, 255, 0).astype(np.uint8)).convert(
        "1"
    )
    buf = io.BytesIO()
    # 1つの帯（strip）にまとめ、その中身をそのまま PDF の画像にする
    ink.save(buf, "TIFF", compression="group4", tiffinfo={278: ink.height})
    with Image.open(buf) as tif:
        (offset,), (length,) = tif.tag_v2[273], tif.tag_v2[279]
    data = buf.getvalue()[offset : offset + length]
    entries = (
        "/ColorSpace /DeviceGray /BitsPerComponent 1 /Filter /CCITTFaxDecode "
        f"/DecodeParms << /K -1 /Columns {ink.width} /Rows {ink.height} >>"
    )
    return _image_pdf(out, ink.width, ink.height, BILEVEL_DPI, entries, data)


def _scan_jpeg(rgb: np.ndarray, out: Path, angle: float, quarter_turns: int) -> Path:
    """232dpi のカラーのページの画像の紙の地に色を付け、JPEG のまま貼った画像だけのPDFにする"""
    img = rgb.astype(float) * np.array(JPEG_PAPER)
    h, w = img.shape[:2]
    m, (nw, nh) = _tilt(w, h, angle)
    paper = tuple(255 * c for c in JPEG_PAPER)
    img = cv2.warpAffine(img, m, (nw, nh), borderValue=paper)
    img = cv2.GaussianBlur(img, (3, 3), 0)
    noise = np.random.default_rng(0).normal(0, 6, img.shape)
    img = np.clip(img + noise, 0, 255).astype(np.uint8)
    img = np.ascontiguousarray(np.rot90(img, quarter_turns))
    ok, buf = cv2.imencode(
        ".jpg", cv2.cvtColor(img, cv2.COLOR_RGB2BGR), [cv2.IMWRITE_JPEG_QUALITY, 85]
    )
    assert ok
    entries = "/ColorSpace /DeviceRGB /BitsPerComponent 8 /Filter /DCTDecode"
    return _image_pdf(out, img.shape[1], img.shape[0], JPEG_DPI, entries, buf.tobytes())


def _image_pdf(
    out: Path,
    w: int,
    h: int,
    dpi: int,
    entries: str,
    data: bytes,
    size: tuple[str, str] | None = None,
) -> Path:
    """画像1枚（幅 w・高さ h の画素、dpi の解像度）だけを、圧縮されたまま貼った1ページのPDFを書く

    size は、画像を貼る大きさ（ポイント）を書く文字列。省くと、ページの大きさと同じ値を書く。
    """
    pw, ph = w * 72 / dpi, h * 72 / dpi
    sw, sh = size or (f"{pw:.3f}", f"{ph:.3f}")
    content = f"q {sw} 0 0 {sh} 0 0 cm /Im1 Do Q".encode()
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {pw:.3f} {ph:.3f}] "
        "/Resources << /XObject << /Im1 5 0 R >> >> /Contents 4 0 R >>".encode(),
        b"<< /Length %d >>\nstream\n" % len(content) + content + b"\nendstream",
        f"<< /Type /XObject /Subtype /Image /Width {w} /Height {h} {entries} "
        f"/Length {len(data)} >>\nstream\n".encode()
        + data
        + b"\nendstream",
    ]
    buf = io.BytesIO()
    buf.write(b"%PDF-1.4\n")
    offsets = []
    for i, obj in enumerate(objs, 1):
        offsets.append(buf.tell())
        buf.write(b"%d 0 obj\n" % i + obj + b"\nendobj\n")
    xref = buf.tell()
    buf.write(b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1))
    for off in offsets:
        buf.write(b"%010d 00000 n \n" % off)
    buf.write(
        b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n"
        % (len(objs) + 1, xref)
    )
    out.write_bytes(buf.getvalue())
    return out


def _tilt(w: int, h: int, angle: float) -> tuple[np.ndarray, tuple[int, int]]:
    """_scan がページの画像（幅 w・高さ h）を傾けてずらす変換（2×3）と、傾けた後の画像の幅・高さ"""
    m = cv2.getRotationMatrix2D((w / 2, h / 2), angle, 1.0)
    m[0, 2] += 14
    m[1, 2] += 9
    c, s = abs(m[0, 0]), abs(m[0, 1])
    nw, nh = int(h * s + w * c) + 40, int(h * c + w * s) + 40
    m[0, 2] += (nw - w) / 2
    m[1, 2] += (nh - h) / 2
    return m, (nw, nh)


def scan_transform(
    shape: tuple[int, int], angle: float, quarter_turns: int
) -> tuple[np.ndarray, tuple[int, int]]:
    """make_scan が、ページの画像（shape は高さ・幅）を試験画像にするときの変換（3×3）と、試験画像の高さ・幅を
    返す。試験画像は、傾けてずらしてから、反時計回りに quarter_turns×90度回す。ページの画像の解像度は、試験画像と
    同じもの（グレーは 200dpi、白黒2値は 300dpi、カラーの JPEG は 232dpi）"""
    h, w = shape
    m, (cw, ch) = _tilt(w, h, angle)
    M = np.vstack([m, [0.0, 0.0, 1.0]])
    for _ in range(quarter_turns % 4):  # np.rot90 と同じく反時計回りに90度
        M = np.array([[0.0, 1.0, 0.0], [-1.0, 0.0, cw], [0.0, 0.0, 1.0]]) @ M
        cw, ch = ch, cw
    return M, (ch, cw)


def page_image(form: str, filled: bool = False, bilevel: bool = False) -> np.ndarray:
    """samples/<form>.pdf の1ページ目を描いた、200dpi のグレーの画像（傾けない。tests/data/ から読む）

    filled なら、扶養控除等申告書 令和9年分（2027bun_01）に架空の番号を書いたもの。
    bilevel なら、300dpi の白黒2値の画像（スキャナーの白黒の読み取りに見立てる）を 200dpi に縮めたものにする。
    縮めると、作業画像と同じく文字の縁は灰色になる。
    """
    if not bilevel:
        return _page(form, GRAY_DPI, False, filled)
    img = _page(form, BILEVEL_DPI, False, filled)
    img = np.where(img < 128, 0, 255).astype(np.uint8)
    h, w = img.shape
    size = (round(w * GRAY_DPI / BILEVEL_DPI), round(h * GRAY_DPI / BILEVEL_DPI))
    return cv2.resize(img, size, interpolation=cv2.INTER_AREA)


# Helvetica の数字の高さと幅（文字の大きさに対する割合）
CAP, DIGIT_WIDTH = 0.72, 0.556
MM = GRAY_DPI / 25.4  # 1mm の画素（200dpi）


def _digits(b, place: str):
    """欄 b に書く数字の (左端, 下端（ページの上からの距離）, 文字の大きさ, 横の倍率, 文字)（ポイント）

    b は枠の線の中心に合わせた欄なので、線の内側の縁は、b の辺から1画素内側とみなす。
    """
    k = 72 / GRAY_DPI  # 200dpi の画像の画素 → ポイント
    cell = b.w / len(DUMMY)
    top, bottom = b.y + 1, b.y + b.h - 1  # 線の内側の縁
    out = []
    for i, digit in enumerate(DUMMY):
        center = (b.x + (i + 0.5) * cell) * k
        if place in ("center", "left", "right"):
            size = min(12.0, b.h * k * 0.7)
            base = (b.y + b.h / 2) * k + size * CAP / 2
            sx = 1.0
            half = size * DIGIT_WIDTH / 2
            if place == "left":  # ます目の左の縁（線の内側の縁）から 0.2ポイント
                center = (b.x + 1 + i * cell) * k + 0.2 + half
            elif place == "right":
                center = (b.x - 1 + (i + 1) * cell) * k - 0.2 - half
        else:
            if place == "touch":
                lo, hi = top, bottom
            elif place.startswith("over"):
                over = float(place[4:]) * MM
                lo, hi = top - over, bottom + over
            elif place == "below":
                lo = b.y + b.h + 1  # 線の外側の縁
                hi = lo + 3 * MM
            else:
                raise ValueError(place)
            size = (hi - lo) * k / CAP
            base = hi * k
            sx = min(1.0, 0.8 * cell * k / (size * DIGIT_WIDTH))
        out.append((center - size * DIGIT_WIDTH * sx / 2, base, size, sx, digit))
    return out


def written_page(form: str, dpi: int, boxes=(), place: str = "center") -> np.ndarray:
    """samples/<form>.pdf の1ページ目を pypdfium2 で描いた、グレーの画像（傾けない）

    boxes（傾けない 200dpi の画像の座標の欄。conftest.py の reference の墨消しする範囲。印刷された枠の線の中心に
    合わせたもの）のそれぞれに、架空の番号（DUMMY）を、欄を12に分けたます目に1桁ずつ書く。文字は PDFium の
    標準のフォント（Helvetica）。数字だけを書いた画像は tests/data/ に入れず、テストのときに作る
    （tests/data/README.md）。boxes を省くと、同じ描き方で記入のない画像になる。

    place は数字の置き方。
    - center：ます目の中央に、欄の高さの7割（12ポイントまで）
    - left・right：center と同じ大きさで、ます目の左・右に寄せる（先頭と最後の桁は、線の内側の縁に接する）
    - touch：枠の内側の高さいっぱい（上下の線の内側の縁に接する）
    - over1・over2：上下の線の内側の縁を 1mm・2mm 越える
    - below：下の線の外側の縁に接して、高さ3mm（下の段の生年月日などの記入に見立てる）
    touch・over・below は、ます目の幅の8割に収まるよう横に縮める。
    """
    pdf = pdfium.PdfDocument(str(SAMPLES / f"{form}.pdf"))
    page = pdf[0]
    try:
        if boxes:
            _, page_h = page.get_size()
            font = pdfium_c.FPDFText_LoadStandardFont(pdf.raw, b"Helvetica")
            for b in boxes:
                for x, bottom, size, sx, digit in _digits(b, place):
                    obj = pdfium_c.FPDFPageObj_CreateTextObj(
                        pdf.raw, font, ctypes.c_float(size)
                    )
                    pdfium_c.FPDFText_SetText(obj, (ctypes.c_ushort * 2)(ord(digit), 0))
                    pdfium_c.FPDFPageObj_Transform(obj, sx, 0, 0, 1, x, page_h - bottom)
                    pdfium_c.FPDFPage_InsertObject(page.raw, obj)
            pdfium_c.FPDFPage_GenerateContent(page.raw)
            pdfium_c.FPDFFont_Close(font)
        bitmap = page.render(
            scale=dpi / 72, force_bitmap_format=pdfium_c.FPDFBitmap_Gray
        )
        try:
            return bitmap.to_numpy().squeeze().copy()
        finally:
            bitmap.close()
    finally:
        page.close()
        pdf.close()


def bent(img: np.ndarray, top: float, right: float) -> np.ndarray:
    """端が浮いて曲がった用紙に見立てて、画像の中身をなめらかにずらす（写真の試験画像に使う）

    top：上から4割の帯で、上の端ほど中身を上に top 画素、左に 0.3 × top 画素までずらす（上の端が浮いた形。写真で測った
    ずれ方をまねた）。right：右から4分の1の帯で、右の端ほど中身を右に right 画素までずらす（右の端が浮いた形）。
    ずれは、帯の内側の端からの距離の2乗で大きくなる。ずらした分が切れないよう、上と右に白い余白を足す。
    """
    h, w = img.shape
    pt, pr = int(top) + 2, int(right) + 2
    canvas = np.full((h + pt, w + pr), 255, np.uint8)
    canvas[pt:, :w] = img
    yy, xx = np.mgrid[0 : h + pt, 0 : w + pr].astype(np.float32)
    t = np.clip((0.4 * h - (yy - pt)) / (0.4 * h), 0, 1) ** 2
    u = np.clip((xx - 0.75 * w) / (0.25 * w), 0, 1) ** 2
    dy = -top * t
    dx = -0.3 * top * t + right * u
    return cv2.remap(canvas, xx - dx, yy - dy, cv2.INTER_LINEAR, borderValue=255)


def make_photo(
    sheets: list[tuple],
    out: Path,
    portrait: bool = False,
    desk: tuple = (70, 100, 140),
    seed: int = 0,
) -> list[np.ndarray]:
    """紙を机の上に置いて撮った写真に見立てた JPEG（1,200万画素、72dpi と記録）を作る

    sheets は、紙ごとの (画像, 中心x, 中心y, 長辺の画素数, 回転の角度, 台形のゆがみ) の並び。
    台形のゆがみは、紙の上の辺を下の辺よりこの割合だけ短くする（斜めから撮ったときのゆがみ）。
    desk は机の色（BGR）。写真には、木目のようなむら・明るさのむら・ぼかし・ノイズを加える。
    紙ごとに、紙の画像の座標から写真の座標への射影変換を返す。
    """
    W, H = (PHOTO_SHORT, PHOTO_LONG) if portrait else (PHOTO_LONG, PHOTO_SHORT)
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
    grain = 12 * np.sin(xx / 37 + 3 * np.sin(yy / 211)) + rng.normal(0, 6, (H, W))
    img = np.stack([c + grain for c in desk], axis=2).astype(np.float32)
    transforms = []
    for paper, cx, cy, long_px, angle, keystone in sheets:
        ph, pw = paper.shape
        s = long_px / max(ph, pw)
        hw, hh, k = pw * s / 2, ph * s / 2, keystone / 2
        dst = np.float32(
            [[-hw * (1 - k), -hh], [hw * (1 - k), -hh], [hw, hh], [-hw, hh]]
        )
        a = np.deg2rad(angle)
        rot = np.float32([[np.cos(a), -np.sin(a)], [np.sin(a), np.cos(a)]])
        dst = dst @ rot.T + np.float32([cx, cy])
        src = np.float32([[0, 0], [pw, 0], [pw, ph], [0, ph]])
        M = cv2.getPerspectiveTransform(src, dst)
        tint = np.float32([0.93, 0.96, 0.98])  # 紙の地の色
        color = cv2.cvtColor(paper, cv2.COLOR_GRAY2BGR).astype(np.float32) * tint
        warped = cv2.warpPerspective(color, M, (W, H))
        mask = cv2.warpPerspective(np.ones((ph, pw), np.float32), M, (W, H))[..., None]
        img = img * (1 - mask) + warped * mask
        transforms.append(M)
    img *= (1.0 - 0.25 * (xx / W + yy / H) / 2)[..., None]  # 左上から右下へ暗くなる
    img = cv2.GaussianBlur(img, (0, 0), 1.0) + rng.normal(0, 3, img.shape)
    img = np.clip(img, 0, 255).astype(np.uint8)
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 85])
    assert ok
    # OpenCV は解像度を書かないため、スマートフォンと同じく 72dpi を JFIF に書き込む
    data = bytearray(buf.tobytes())
    assert data[6:11] == b"JFIF\0"
    data[13:18] = bytes([1, 0, 72, 0, 72])  # 単位（1：インチ）、横・縦の解像度
    out.write_bytes(bytes(data))
    return transforms
