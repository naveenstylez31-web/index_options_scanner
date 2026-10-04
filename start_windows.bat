@echo off
REM Windows: Task Scheduler > Create Task > Trigger "At log on" (or daily 08:00)
REM Action: this .bat file. Settings: "If the task is already running, do not start a new instance".
cd /d %~dp0..
call .venv\Scripts\activate.bat
python -m scanner run
