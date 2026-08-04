@echo off
call "C:\Users\%USERNAME%\anaconda3\Scripts\activate.bat" camera
set PATH=%PATH%;C:\Users\%USERNAME%\Documents\Hamamatsu_DCAMSDK4_v25056964\dcamsdk4\lib\win64
cd /d "D:\DCAM"
python main.py
if errorlevel 1 (
    echo.
    echo [ERROR] Application exited with an error.
    pause
)
