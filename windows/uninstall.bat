@echo off
rem Copyright (C) 2026 Hideki Ohmura
rem SPDX-License-Identifier: AGPL-3.0-only
rem Uninstall Sumikeshi Studio (see scripts\uninstall.ps1). No network access.
setlocal
rem Use Windows PowerShell in its fixed place. If it is not there, look for powershell.exe only in the folders of PATH
rem (not in the current folder, so that a powershell.exe placed there does not run). If neither is found, stop.
set "SUMIKESHI_POWERSHELL=%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe"
if not exist "%SUMIKESHI_POWERSHELL%" for %%P in (powershell.exe) do set "SUMIKESHI_POWERSHELL=%%~$PATH:P"
if not defined SUMIKESHI_POWERSHELL goto missing
rem The goto ends this batch file before PowerShell renames and deletes its folder, so that cmd does not read the file
rem again afterwards (which prints "The system cannot find the path specified." and makes the exit code 1).
rem PowerShell is the last command on the line, so its exit code becomes the exit code of this batch file.
rem The cd comes after the goto: setlocal also saves the current folder, and ending the batch file restores it.
(goto) 2>nul & cd /d "%TEMP%" & "%SUMIKESHI_POWERSHELL%" -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\uninstall.ps1" %*
:missing
echo Stopped because Windows PowerShell was not found.
echo Windows PowerShell Ç™å©Ç¬Ç©ÇÁÇ»Ç¢ÇΩÇﬂÅAíÜé~ÇµÇ‹ÇµÇΩÅB
pause
exit /b 1
