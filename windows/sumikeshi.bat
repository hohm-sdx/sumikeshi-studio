@echo off
rem Copyright (C) 2026 Hideki Ohmura
rem SPDX-License-Identifier: AGPL-3.0-only
rem Redact files from the command line without the screen (see README)
"%~dp0python\python.exe" -I -X utf8 -m sumikeshi_studio.cli %*
exit /b %ERRORLEVEL%
