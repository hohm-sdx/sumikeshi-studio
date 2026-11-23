@echo off
rem Copyright (C) 2026 Hideki Ohmura
rem SPDX-License-Identifier: AGPL-3.0-only
rem Install or update Sumikeshi Studio (see scripts\install.ps1). No network access.
rem The goto ends this batch file before PowerShell moves its folder, so that cmd does not read the file again
rem afterwards (which prints "The system cannot find the path specified." and makes the exit code 1).
rem PowerShell is the last command on the line, so its exit code becomes the exit code of this batch file.
cd /d "%TEMP%" & (goto) 2>nul & powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\install.ps1" -Source "%~dp0." %*
