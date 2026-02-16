@echo off
setlocal enabledelayedexpansion

:: Ask user for the search term
set /p search=Enter the text to search for: 

:: Loop through all folders recursively
for /r %%d in (levelname.txt) do (
    :: Read the content of levelname.txt
    set "match="
    for /f "usebackq delims=" %%a in ("%%d") do (
        set "line=%%a"
        if /i "!line!"=="%search%" (
            set "match=1"
        )
    )
    :: If match found, open the folder
    if defined match (
        echo Match found in: %%~dpd
        start "" "%%~dpd"
    )
)

endlocal
