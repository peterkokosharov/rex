@echo off
REM SPDX-License-Identifier: MIT
REM Author: Peter Kokosharov <peter.kokosharov@gmail.com>
REM AI models: claude-sonnet-5-5
REM ai-rex from a git checkout (no install). Uses "py -3" if present, else "python".
setlocal
set "PYTHONPATH=%~dp0;%PYTHONPATH%"
set "PY=python"
where py >nul 2>nul && set "PY=py -3"
%PY% -P -m runtime %*
exit /b %ERRORLEVEL%
