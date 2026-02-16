@echo off
setlocal

REM ============================================================
REM NAS-system-REVAMP Installer & Launcher (ZIP-based)
REM ============================================================

:: Ask user for password (optional, can be used later in server.py)
set /p APP_PASS=Enter the password for NAS-system-REVAMP: 

:: Check if repo folder exists
if not exist "NAS-system-REVAMP" (
    echo Downloading repo ZIP...
    powershell -Command "Invoke-WebRequest -Uri https://github.com/g00glesucksdude-oss/NAS-system-REVAMP-/archive/refs/heads/main.zip -OutFile repo.zip"
    
    echo Extracting files...
    powershell -Command "Expand-Archive -Path repo.zip -DestinationPath ."
    
    ren NAS-system-REVAMP--main NAS-system-REVAMP
    del repo.zip
) else (
    echo Repo already present.
)

cd NAS-system-REVAMP

:: If requirements.txt exists, install dependencies
if exist requirements.txt (
    echo Installing dependencies...
    pip install -r requirements.txt
) else (
    echo No requirements.txt found, skipping dependency install.
)

:: Run launcher (this will show animation and start server.py)
echo Starting launcher...
python launcher.py

endlocal
