# Copyright (C) 2026 Hideki Ohmura
# SPDX-License-Identifier: AGPL-3.0-only
from .launcher import main

# 解析のプロセス（multiprocessing の spawn）は、このファイルを読み込まない。親のプロセスを -m で起動したときは、
# 子のプロセスに主モジュールの名前（sumikeshi_studio.__main__）が渡るが、Python 3.14 の multiprocessing/spawn.py の
# _fixup_main_from_name は、名前が「.__main__」で終わるモジュールを子のプロセスで実行しないため。下の判定は、
# ほかのスクリプトがこのファイルを読み込んだときに、起動の画面を出さないために置く
if __name__ == "__main__":
    main()
