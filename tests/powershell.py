# Copyright (C) 2026 Hideki Ohmura
# SPDX-License-Identifier: AGPL-3.0-only
"""PowerShell のスクリプト（.ps1）の文を調べる補助（tests/test_install_scripts.py などが使う）

- code_only：コメントを除き、文字列を空にした文（かっこの対応と、使うコマンド・演算子を調べるため）
- parse_errors：PowerShell 7（pwsh）の構文の解析（[System.Management.Automation.Language.Parser]::ParseFile）の誤り。
  pwsh がなければ使えない（PWSH が None。テストは PWSH_MISSING を理由に飛ばす）
- ps7_syntax：Windows に標準で入っている Windows PowerShell 5.1 にない、PowerShell 7 で足された演算子（文字列とコメントの外）
- run_json：pwsh でスクリプトの文を動かし、出力の JSON を読む（関数を、作った値で動かして確かめるため）
- shadowed：関数が、スクリプト全体の変数を、同じ名前の引数か代入で隠している所（pwsh の構文の解析で調べる）
"""

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

PWSH = shutil.which("pwsh")
PWSH_MISSING = "pwsh（PowerShell 7）がない"

# スクリプトごとの構文の誤り（行:桁 誤りの種類 文）を JSON で出す
PARSE = """\
$result = [ordered]@{}
foreach ($path in $args) {
    $tokens = $null
    $errors = $null
    [void][System.Management.Automation.Language.Parser]::ParseFile($path, [ref]$tokens, [ref]$errors)
    $result[$path] = @($errors | ForEach-Object {
        "{0}:{1} {2} {3}" -f $_.Extent.StartLineNumber, $_.Extent.StartColumnNumber, $_.ErrorId, $_.Message
    })
}
$result | ConvertTo-Json -Depth 3 -Compress
"""

# Windows PowerShell 5.1 にない演算子（PowerShell 7 で足されたもの）。code_only の後の文で探す。「?」だけの語は、三項の演算子
# のほか Where-Object の別名でもあるが、別名も使わない決まりにする（読み手が演算子と見分けられるように）
PS7_SYNTAX = {
    "&&（パイプラインを成功したときだけつなぐ）": re.compile(r"&&"),
    "||（パイプラインを失敗したときだけつなぐ）": re.compile(r"\|\|"),
    "??・??=（null のときの値）": re.compile(r"\?\?"),
    "?.・?[（null でないときだけのアクセス）": re.compile(r"\?[.\[]"),
    "? :（三項の演算子）": re.compile(r"(?<![\w$?])\?(?![\w?.\[])"),
}


def code_only(text: str) -> str:
    """スクリプトからコメントを除き、文字列を空の文字列（'' か ""）に置き換えた文。括弧の対応と、使うコマンドを調べるため"""
    out = []
    i = 0
    while i < len(text):
        c = text[i]
        if c == "#":
            end = text.find("\n", i)
            i = len(text) if end < 0 else end
        elif c == "'":
            j = i + 1
            while True:
                k = text.index("'", j)
                if text.startswith("''", k):
                    j = k + 2
                    continue
                break
            out.append("''")
            i = k + 1
        elif c == '"':
            j = i + 1
            while text[j] != '"':
                j += 2 if text[j] == "`" else 1
            out.append('""')
            i = j + 1
        else:
            out.append(c)
            i += 1
    return "".join(out)


def ps7_syntax(text: str) -> list[str]:
    """文字列とコメントの外にある、Windows PowerShell 5.1 にない演算子の一覧（行の番号と演算子の名前）"""
    found = []
    for n, line in enumerate(code_only(text).split("\n"), 1):
        for name, pattern in PS7_SYNTAX.items():
            if pattern.search(line):
                found.append(f"{n}行：{name}")
    return found


def parse_errors(paths: list[Path], work: Path) -> dict[str, list[str]]:
    """pwsh で paths のスクリプトを構文の解析にかけ、スクリプト（渡したパスの文字列）ごとの誤りの一覧を返す。work は、
    解析のスクリプトと pwsh の設定・キャッシュを置く空のフォルダ（利用者のフォルダに書かせないため）"""
    return run_json(PARSE, work, [str(p) for p in paths])


# 関数が、スクリプト全体の変数を同じ名前の引数か代入で隠している所を JSON で出す。PowerShell の変数は、呼んだ関数の変数が
# 呼ばれた関数からも見え（動的な見え方）、名前の大文字・小文字を区別しないので、関数の中で $script: を付けずに読む
# スクリプト全体の変数（たとえば Say の $Log）と同じ名前を、別の関数が引数か代入で使うと、その関数から呼ばれたときに、
# 読む側は別の値を読む。引数のファイルは、同じスクリプトとして読む（ドットで読み込むファイルも並べる）
SHADOWED = """\
function Get-Owner($node) {
    $p = $node.Parent
    while ($p -and -not ($p -is [System.Management.Automation.Language.FunctionDefinitionAst])) { $p = $p.Parent }
    return $p
}
function Get-Target($left) {
    while ($left -is [System.Management.Automation.Language.ConvertExpressionAst]) { $left = $left.Child }
    if ($left -is [System.Management.Automation.Language.VariableExpressionAst]) { return $left }
    return $null
}
function Get-Name($v) {
    $p = $v.VariablePath
    if ($p.IsDriveQualified -or $p.IsGlobal -or $p.IsScript) { return $null }
    return $p.UserPath.ToLower()
}
$top = @{}
$funcs = @()
foreach ($file in $args) {
    $tokens = $null
    $errors = $null
    $ast = [System.Management.Automation.Language.Parser]::ParseFile($file, [ref]$tokens, [ref]$errors)
    if ($ast.ParamBlock) { foreach ($p in $ast.ParamBlock.Parameters) { $top[$p.Name.VariablePath.UserPath.ToLower()] = $true } }
    foreach ($a in $ast.FindAll({ $args[0] -is [System.Management.Automation.Language.AssignmentStatementAst] }, $true)) {
        if (Get-Owner $a) { continue }
        $t = Get-Target $a.Left
        if ($t) { $top[($t.VariablePath.UserPath.ToLower() -replace '^script:', '')] = $true }
    }
    $kind = [System.Management.Automation.Language.FunctionDefinitionAst]
    foreach ($f in $ast.FindAll({ $args[0] -is [System.Management.Automation.Language.FunctionDefinitionAst] }, $true)) {
        $locals = @{}
        $params = @()
        if ($f.Parameters) { $params += $f.Parameters }
        if ($f.Body.ParamBlock) { $params += $f.Body.ParamBlock.Parameters }
        foreach ($p in $params) { $locals[$p.Name.VariablePath.UserPath.ToLower()] = "引数" }
        $nodes = $f.Body.FindAll({
            $args[0] -is [System.Management.Automation.Language.AssignmentStatementAst] -or
            $args[0] -is [System.Management.Automation.Language.ForEachStatementAst]
        }, $true)
        foreach ($n in $nodes) {
            if ((Get-Owner $n) -ne $f) { continue }
            $t = if ($n -is [System.Management.Automation.Language.ForEachStatementAst]) { $n.Variable } else { Get-Target $n.Left }
            if ($t -and ($name = Get-Name $t)) { $locals[$name] = "代入" }
        }
        $reads = @{}
        foreach ($v in $f.Body.FindAll({ $args[0] -is [System.Management.Automation.Language.VariableExpressionAst] }, $true)) {
            if ((Get-Owner $v) -ne $f) { continue }
            $name = Get-Name $v
            if ($name -and -not $locals.ContainsKey($name)) { $reads[$name] = $true }
        }
        $funcs += [pscustomobject]@{ File = (Split-Path $file -Leaf); Name = $f.Name; Locals = $locals; Reads = $reads }
    }
}
$readers = @{}
foreach ($f in $funcs) {
    foreach ($k in $f.Reads.Keys) {
        if ($top.ContainsKey($k)) { $readers[$k] = @($readers[$k]) + @($f.Name) | Where-Object { $_ } | Select-Object -Unique }
    }
}
$found = @()
foreach ($f in $funcs) {
    foreach ($k in $f.Locals.Keys) {
        if ($readers.ContainsKey($k)) {
            $found += ("{0} の {1}：`${2}（{3}。読む関数：{4}）" -f $f.File, $f.Name, $k, $f.Locals[$k], ($readers[$k] -join "・"))
        }
    }
}
ConvertTo-Json @($found) -Compress
"""


def shadowed(paths: list[Path], work: Path) -> list[str]:
    """関数が、スクリプト全体の変数（ほかの関数が $script: を付けずに読むもの）を、同じ名前の引数か代入で隠している所。
    paths は、同じスクリプトとして読むファイル（ドットで読み込むファイルも並べる）"""
    return sorted(run_json(SHADOWED, work, [str(p) for p in paths]))


def run_json(text: str, work: Path, args: list[str] | None = None):
    """pwsh で text のスクリプトを args を付けて動かし、標準出力の JSON を読んで返す。work は、スクリプトと pwsh の設定・
    キャッシュを置くフォルダ（利用者のフォルダに書かせないため）"""
    work.mkdir(parents=True, exist_ok=True)
    script = work / "script.ps1"
    script.write_text(text, "utf-8")
    env = {
        **os.environ,
        "POWERSHELL_TELEMETRY_OPTOUT": "1",
        "POWERSHELL_UPDATECHECK": "Off",
        "HOME": str(work),
        "XDG_CONFIG_HOME": str(work / "config"),
        "XDG_CACHE_HOME": str(work / "cache"),
        "XDG_DATA_HOME": str(work / "data"),
    }
    out = subprocess.run(
        [PWSH, "-NoLogo", "-NoProfile", "-NonInteractive", "-File", str(script)]
        + (args or []),
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
        timeout=180,
        check=False,
    )
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)
