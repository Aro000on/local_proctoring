@echo off
chcp 65001 >nul
setlocal EnableDelayedExpansion
cd /d "%~dp0"

echo ============================================================
echo   Локальный прокторинг - запуск
echo ============================================================
echo.

REM ------------------------------------------------------------
REM 1. Поиск Python 3.10-3.12
REM ------------------------------------------------------------
set "PY="
for %%v in (3.11 3.10 3.12) do (
    if not defined PY (
        py -%%v --version >nul 2>nul
        if !errorlevel!==0 set "PY=py -%%v"
    )
)
if not defined PY (
    python --version >nul 2>nul
    if !errorlevel!==0 set "PY=python"
)
if not defined PY (
    echo [ОШИБКА] Python 3.10-3.12 не найден.
    echo.
    echo Установите Python 3.11 x64: https://www.python.org/downloads/release/python-3119/
    echo При установке отметьте галочку "Add python.exe to PATH".
    echo.
    pause
    exit /b 1
)

for /f "tokens=2" %%v in ('%PY% --version 2^>^&1') do set "PYVER=%%v"
echo [1/7] Python: %PYVER%  (%PY%)

REM ------------------------------------------------------------
REM 2. Проверка VC++ Redistributable REM ------------------------------------------------------------
if not exist "%SystemRoot%\System32\vcruntime140_1.dll" (
    echo.
    echo [ОШИБКА] Не найден vcruntime140_1.dll ^(Microsoft Visual C++ Redistributable x64^).
    echo.
    echo Без него mediapipe НЕ запустится.
    echo Скачайте и установите: https://aka.ms/vs/17/release/vc_redist.x64.exe
    echo После установки ПЕРЕЗАГРУЗИТЕ компьютер и повторите запуск.
    echo.
    pause
    exit /b 1
)
echo [2/7] VC++ runtime: OK

REM ------------------------------------------------------------
REM 3. Создание .venv 
REM ------------------------------------------------------------
if not exist ".venv\Scripts\python.exe" (
    echo [3/7] Создание .venv ...
    %PY% -m venv .venv
    if !errorlevel! neq 0 (
        echo [ОШИБКА] Не удалось создать .venv
        pause
        exit /b 1
    )
) else (
    echo [3/7] .venv уже есть
)
call ".venv\Scripts\activate.bat"

for /f "delims=" %%p in ('where python 2^>nul') do (
    echo       %%p | findstr /I "\.venv\Scripts" >nul && set "IN_VENV=1"
)
if not defined IN_VENV (
    echo [ОШИБКА] Не удалось активировать .venv. Смотрите вывод выше.
    pause
    exit /b 1
)

REM ------------------------------------------------------------
REM 4. Зависимости
REM ------------------------------------------------------------
if not exist ".venv\.deps_installed" (
    echo [4/7] Обновление pip ...
    python -m pip install --upgrade pip wheel setuptools --quiet
    if !errorlevel! neq 0 (
        echo [ОШИБКА] Не удалось обновить pip
        pause
        exit /b 1
    )

    echo [4/7] Установка зависимостей ^(может занять 5-15 минут^) ...
    pip install -r requirements.txt
    if !errorlevel! neq 0 (
        echo.
        echo [ОШИБКА] pip install упал. Смотрите вывод выше.
        pause
        exit /b 1
    )

    echo [4/7] Проверка ключевых импортов ...
    python -c "import numpy, google.protobuf, cv2, mediapipe; print('  numpy', numpy.__version__, '| protobuf', google.protobuf.__version__, '| cv2', cv2.__version__, '| mp', mediapipe.__version__)"
    if !errorlevel! neq 0 (
        echo [ОШИБКА] Импорты не работают. Удалите .venv\.deps_installed и повторите.
        pause
        exit /b 1
    )

    echo done> ".venv\.deps_installed"
) else (
    echo [4/7] Зависимости уже установлены ^(флаг .venv\.deps_installed^)
)

REM ------------------------------------------------------------
REM 5. Веса YOLO
REM ------------------------------------------------------------
if not exist "models\yolov8n.pt" (
    echo [5/7] Скачивание весов YOLOv8n...
    if not exist "models" mkdir models
    python download_models.py
    if !errorlevel! neq 0 (
        echo.
        echo [ОШИБКА] Не удалось скачать веса YOLO.
        echo Проверьте интернет и повторите запуск.
        pause
        exit /b 1
    )
) else (
    echo [5/7] Веса YOLO уже на месте
)

REM ------------------------------------------------------------
REM 6. Быстрая проверка mediapipe FaceMesh
REM ------------------------------------------------------------
echo [6/7] Проверка MediaPipe FaceMesh ...
python -c "import mediapipe as mp; fm = mp.solutions.face_mesh.FaceMesh(); fm.close(); print('  FaceMesh OK')" 2>nul
if !errorlevel! neq 0 (
    echo.
    echo [ОШИБКА] MediaPipe FaceMesh не запускается.
    echo Попробуйте:
    echo   pip install --force-reinstall --no-deps mediapipe==0.10.21
    echo   pip install numpy==1.26.4 protobuf==4.25.3
    echo.
    pause
    exit /b 1
)

REM ------------------------------------------------------------
REM 7. Запуск
REM ------------------------------------------------------------
echo [7/7] Запуск main.py ...
echo ============================================================
echo.

python main.py %*
set "RC=!errorlevel!"

echo.
echo ============================================================
if !RC! equ 0 (
    echo   Приложение завершено.
) else (
    echo   [ОШИБКА] Приложение завершилось с кодом !RC!.
)
echo ============================================================
pause

endlocal