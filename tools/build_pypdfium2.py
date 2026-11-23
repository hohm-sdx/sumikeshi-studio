# Copyright (C) 2026 Hideki Ohmura
# SPDX-License-Identifier: AGPL-3.0-only
"""pdfium-binaries の決めたバージョンの PDFium で、pypdfium2 の配布物（wheel）を作る

PyPI の pypdfium2 5.14.0 の PDFium（156.0.8076）には、脆弱性の修正5件が入っていない。5件が入っている
pdfium-binaries の 8138 の PDFium で、pypdfium2 5.14.0 のソースの配布物から配布物を作る（pypdfium2 の作る手順の
PDFIUM_PLATFORM=<作り先>:<バージョン>。pypdfium2 の README の「$PDFIUM_PLATFORM defines which binary to include on setup」）。

- 使うもの（pypdfium2 のソースの配布物と、pdfium-binaries の tgz と証明書）は、ここに書いた SHA-256 で照合する。
- pdfium-binaries の tgz は、GitHub の証明書（attestation）で検証する。gh（GitHub CLI）が要る。なければ止める
  （pypdfium2 の作る手順は、gh がないと警告だけで検証を飛ばすため）。
- 許諾文は、pdfium-binaries の tgz のもの（そのバージョンの Windows・Linux 用の作りの部品の文）を入れる。pypdfium2 の
  setupsrc/emplace.py で先に tgz を取得・展開してから、USE_TARBALL_LICENSES=1 で作る（先に展開しないと、作る手順が
  許諾文を探す時点で文がなく、PDFium の部品の文がひとつも入らなかった）。
- pypdfium2 の作る手順は tgz を自分で取得し直すので、作った配布物の中の PDFium の部品と許諾文が、照合した tgz の
  中のものと同じかを確かめる。
- 作った配布物は、作るたびに中の時刻が変わり、SHA-256 も変わる。一度作ったものを wheels/ に置き、pyproject.toml の
  path の source と uv.lock の SHA-256 で固定する。

使い方（リポジトリの直下で。インターネットへの接続と、gh と、C のプリプロセッサ（cpp）が要る）:
    uv run python tools/build_pypdfium2.py <作業用のフォルダ> windows_x64 linux_x64
"""

import hashlib
import io
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath

SDIST_URL = (
    "https://files.pythonhosted.org/packages/95/d0/"
    "c81d3a7c2a9af37b817ace1de0acd40cf44d15f12407c5e86b3668364a5c/pypdfium2-5.14.0.tar.gz"
)
SDIST_SHA256 = "c5f009b3157f10e97dceb55963f5910eff92feb00587ba10a76f12b87ce1a4b6"
PDFIUM_VERSION = 8138
RELEASE_URL = f"https://github.com/bblanchon/pdfium-binaries/releases/download/chromium/{PDFIUM_VERSION}/"
ATTESTATION = (
    "pdfium-attestation.json",
    "7526f4b934ab498d6206ac7f3d78dd4ce8e0f7d3175227ab3f4ecc255d950ad7",
)
# 作り先ごとの tgz の名前と SHA-256（GitHub のリリースに載っている値）、tgz の中の部品、配布物の中の部品
PLATFORMS = {
    "windows_x64": (
        "pdfium-win-x64.tgz",
        "8d48973e3eb472dda0f5f2435e595ac043f9f2219dd71883392f549445afa8a5",
        "bin/pdfium.dll",
        "pypdfium2_raw/pdfium.dll",
    ),
    "linux_x64": (
        "pdfium-linux-x64.tgz",
        "d6b1e28950a0392590d79bbdbb7eb19686872b688f8a36f170acb19e41473113",
        "lib/libpdfium.so",
        "pypdfium2_raw/libpdfium.so",
    ),
}


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def fetch(url: str, dest: Path, expected: str) -> Path:
    """取得して SHA-256 を照合する（取得済みで合っていれば、取得し直さない）"""
    if not (dest.exists() and sha256(dest.read_bytes()) == expected):
        print(f"取得：{url}")
        with urllib.request.urlopen(url) as r:
            dest.write_bytes(r.read())
    actual = sha256(dest.read_bytes())
    if actual != expected:
        sys.exit(f"SHA-256 が合いません：{dest.name}（{actual}）")
    return dest


def verify_attestation(tgz: Path, bundle: Path, work: Path) -> None:
    """pdfium-binaries の tgz を、GitHub の証明書で検証する（pypdfium2 の作る手順と同じ指定）"""
    gh = shutil.which("gh")
    if not gh:
        sys.exit(
            "gh（GitHub CLI）が見つかりません。pdfium-binaries の tgz を証明書で検証できないので止めます"
        )
    root = work / "trusted_root.jsonl"
    root.write_bytes(
        subprocess.run(
            [gh, "attestation", "trusted-root"], check=True, capture_output=True
        ).stdout
    )
    r = subprocess.run(
        [gh, "attestation", "verify", str(tgz), "-R", "bblanchon/pdfium-binaries",
         "-b", str(bundle), "--custom-trusted-root", str(root)],
        check=False, capture_output=True, text=True,
    )  # fmt: skip
    if r.returncode != 0:
        sys.exit(f"証明書の検証が通りません：{tgz.name}\n{r.stdout}{r.stderr}")
    print(f"証明書の検証が通りました：{tgz.name}")


def tgz_licenses(tgz: Path) -> dict[str, bytes]:
    """tgz の許諾文（licenses/ の文と、pdfium-binaries の LICENSE）。名前は pypdfium2 が配布物に入れるときの名前"""
    with tarfile.open(tgz) as t:
        found = {
            PurePosixPath(m.name).name: t.extractfile(m).read()
            for m in t.getmembers()
            if m.isfile() and m.name.startswith("licenses/")
        }
        found["pdfium-binaries.txt"] = t.extractfile("LICENSE").read()
    return found


def build(platform: str, sdist: Path, out: Path) -> Path:
    """pypdfium2 の作る手順で、作り先の配布物を作る（ソースの配布物は作り先ごとに新しく展開する）"""
    target = f"{platform}:{PDFIUM_VERSION}"
    with tempfile.TemporaryDirectory() as tmp:
        with tarfile.open(sdist) as t:
            t.extractall(tmp, filter="data")
        src = next(Path(tmp).glob("pypdfium2-*"))
        # 先に tgz を取得・検証・展開し、部品と許諾文を data/<作り先>/ に置く。setupsrc の中の部品を読み込むので、
        # -I は付けない（付けると、スクリプトのフォルダが読み込む場所に入らない）
        subprocess.run(
            [sys.executable, str(src / "setupsrc" / "emplace.py"), target],
            check=True, cwd=src,
        )  # fmt: skip
        env = dict(os.environ, PDFIUM_PLATFORM=target, USE_TARBALL_LICENSES="1")
        before = set(out.glob("*.whl"))
        subprocess.run(
            ["uv", "build", "--wheel", "-q", "--out-dir", str(out), str(src)],
            check=True, env=env,
        )  # fmt: skip
    made = sorted(set(out.glob("*.whl")) - before)
    if len(made) != 1:
        sys.exit(f"配布物ができませんでした（{platform}）")
    return made[0]


def check_wheel(
    wheel: Path, platform: str, lib: bytes, licenses: dict[str, bytes]
) -> None:
    """作った配布物の中の PDFium の部品と許諾文が、照合した tgz のものと同じかを確かめる"""
    member = PLATFORMS[platform][3]
    z = zipfile.ZipFile(io.BytesIO(wheel.read_bytes()))
    if z.read(member) != lib:
        sys.exit(
            f"配布物の中の PDFium の部品が、照合した tgz のものと違います：{wheel.name}"
        )
    prefix = f"/licenses/data/{platform}/BUILD_LICENSES/"
    inside = {
        n.split(prefix, 1)[1]: z.read(n)
        for n in z.namelist()
        if prefix in n and not n.endswith("/")
    }
    if inside != licenses:
        sys.exit(
            f"配布物の中の許諾文が、照合した tgz のものと違います：{wheel.name}"
            f"（配布物 {sorted(inside)}、tgz {sorted(licenses)}）"
        )


def main(argv: list[str]) -> None:
    if len(argv) < 2 or any(p not in PLATFORMS for p in argv[1:]):
        sys.exit(__doc__)
    work = Path(argv[0]).resolve()
    out = work / "wheels"
    out.mkdir(parents=True, exist_ok=True)
    sdist = fetch(SDIST_URL, work / Path(SDIST_URL).name, SDIST_SHA256)
    bundle = fetch(RELEASE_URL + ATTESTATION[0], work / ATTESTATION[0], ATTESTATION[1])
    for platform in argv[1:]:
        name, expected, member, wheel_member = PLATFORMS[platform]
        tgz = fetch(RELEASE_URL + name, work / name, expected)
        verify_attestation(tgz, bundle, work)
        with tarfile.open(tgz) as t:
            lib = t.extractfile(member).read()
        licenses = tgz_licenses(tgz)
        wheel = build(platform, sdist, out)
        check_wheel(wheel, platform, lib, licenses)
        print(f"{wheel}\n  配布物の SHA-256：{sha256(wheel.read_bytes())}\n"
              f"  中の {wheel_member} の SHA-256：{sha256(lib)}（{name} の {member} と同じ）\n"
              f"  許諾文：{len(licenses)}個（{name} の licenses/ と LICENSE と同じ）")  # fmt: skip


if __name__ == "__main__":
    main(sys.argv[1:])
