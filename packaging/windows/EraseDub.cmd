@echo off
rem Fallback launcher for the portable bundle (same behaviour as EraseDub.exe).
setlocal
set "ROOT=%~dp0"
set "PYTHONHOME="
set "PYTHONPATH="
set "PYTHONNOUSERSITE=1"
set "PATH=%ROOT%ffmpeg\bin;%PATH%"
if not defined HF_HOME set "HF_HOME=%ROOT%models\huggingface"
if not defined TORCH_HOME set "TORCH_HOME=%ROOT%models\torch"
if not defined NLTK_DATA set "NLTK_DATA=%ROOT%models\nltk_data"
if "%~1"=="" (
  "%ROOT%python\python.exe" -m erasedub webui --open
) else (
  "%ROOT%python\python.exe" -m erasedub %*
)
exit /b %ERRORLEVEL%
