@echo off
setlocal

:: Ask user for password
set /p APP_PASS=Enter the password for remote-pc-control: 

:: Check if repo folder exists
if not exist "remote-pc-control" (
    echo Downloading repo ZIP...
    powershell -Command "Invoke-WebRequest -Uri https://github.com/g00glesucksdude-oss/remote-pc-control/archive/refs/heads/main.zip -OutFile repo.zip"
    
    echo Extracting files...
    powershell -Command "Expand-Archive -Path repo.zip -DestinationPath ."
    
    ren remote-pc-control-main remote-pc-control
    del repo.zip
) else (
    echo Repo already present.
)

cd remote-pc-control

:: If requirements.txt exists, install dependencies
if exist requirements.txt (
    echo Installing dependencies...
    pip install -r requirements.txt
) else (
    echo No requirements.txt found, skipping dependency install.
)

:: Patch PASSWORD_HASH line in latest-stable-version.py
powershell -Command "(Get-Content latest-stable-version.py) | ForEach-Object { if ($_.TrimStart().StartsWith('PASSWORD_HASH')) { 'PASSWORD_HASH = generate_password_hash(os.environ.get(\"REMOTE_PASS\", \"%APP_PASS%\"))' } else { $_ } } | Set-Content latest-stable-version.py"

:: Check if ngrok is installed
where ngrok >nul 2>nul
if %errorlevel% neq 0 (
    echo ngrok not found. Installing via winget...
    winget install ngrok.ngrok -e --id ngrok.ngrok
) else (
    echo ngrok already installed.
)

:: Add ngrok auth token (safe to repeat)
ngrok config add-authtoken 38vELdOEyKM53InhLtJdlfvGeao_3EqikqDLeeWYWtEPCpjRZ

:: Run Flask app in background
start python latest-stable-version.py

:: Forward Flask app (default port 5000)
echo Starting ngrok tunnel...
ngrok http 5000

endlocal
