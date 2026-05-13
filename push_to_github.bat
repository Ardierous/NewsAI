@echo off
setlocal enabledelayedexpansion

cd /d "%~dp0"

REM If launched in auto-close mode (cmd /c), relaunch in persistent window (cmd /k).
REM This prevents instant PowerShell/CMD window closing after push.
if /I "%~1"=="--stay-open" (
  shift
) else (
  echo %cmdcmdline% | findstr /I /C:" /c " >nul
  if not errorlevel 1 (
    start "News Push" cmd /k ""%~f0" --stay-open %*"
    exit /b 0
  )
)

REM Usage:
REM   push_to_github.bat "commit message"
REM   push_to_github.bat --dry-run
REM   push_to_github.bat --max-len 120
REM   push_to_github.bat --max-len 120 --dry-run
REM If message omitted, message is auto-generated from staged changes.
REM Commit message is clipped to MAX_MSG_LEN symbols.
REM Push protocol is appended to docs\push_protocol.log

for /f "tokens=1-2 delims==" %%A in ('wmic os get localdatetime /value ^| find "="') do set _dt=%%B
set ts=%_dt:~0,4%-%_dt:~4,2%-%_dt:~6,2%_%_dt:~8,2%-%_dt:~10,2%-%_dt:~12,2%
set MAX_MSG_LEN=120
set NO_CYRILLIC=1
set DRY_RUN=0

set msg=
:parse_args
if "%~1"=="" goto args_done
if /I "%~1"=="--dry-run" (
  set DRY_RUN=1
  shift
  goto parse_args
)
if /I "%~1"=="--max-len" (
  if "%~2"=="" goto bad_args
  set MAX_MSG_LEN=%~2
  shift
  shift
  goto parse_args
)
set msg=%*
goto args_done

:bad_args
echo ERROR: --max-len requires a numeric value.
exit /b 2

:args_done

echo === Git status (before) ===
git status --short
if errorlevel 1 goto :err

if "%msg%"=="" (
  for /f "usebackq delims=" %%M in (`powershell -NoProfile -Command ^
    "$max=%MAX_MSG_LEN%;" ^
    "$map=@{'а'='a';'б'='b';'в'='v';'г'='g';'д'='d';'е'='e';'ё'='e';'ж'='zh';'з'='z';'и'='i';'й'='y';'к'='k';'л'='l';'м'='m';'н'='n';'о'='o';'п'='p';'р'='r';'с'='s';'т'='t';'у'='u';'ф'='f';'х'='h';'ц'='ts';'ч'='ch';'ш'='sh';'щ'='sch';'ъ'='';'ы'='y';'ь'='';'э'='e';'ю'='yu';'я'='ya'};" ^
    "function T([string]$s){$r='';foreach($c in $s.ToCharArray()){ $k=([string]$c).ToLowerInvariant(); if($map.ContainsKey($k)){$r+=$map[$k]}else{$r+=$c}};$r};" ^
    "function Clip([string]$s,[int]$n){ if($s.Length -le $n){return $s}; $x=$s.Substring(0,$n); $i=$x.LastIndexOf(' '); if($i -gt 20){ return $x.Substring(0,$i).TrimEnd(' ',';','.',',') }; return $x };" ^
    "$files=@(git status --porcelain | ForEach-Object { if($_.Length -ge 4){ $_.Substring(3).Trim().ToLowerInvariant() } } | Where-Object { $_ -ne '' } | Select-Object -Unique);" ^
    "$score=@{}; function Add([string]$k,[int]$v){ if($score.ContainsKey($k)){$score[$k]+=$v}else{$score[$k]=$v} };" ^
    "foreach($f in $files){" ^
      "if($f -match '^backend/app/services/digest_service\.py$'){ Add 'refine digest pipeline and validations' 7 }" ^
      "if($f -match '^backend/app/(api/routes_digests|schemas|models)\.py$'){ Add 'update backend api and data models' 6 }" ^
      "if($f -match '^backend/app/crew/(agents|workflow)\.py$'){ Add 'improve crewai orchestration logic' 6 }" ^
      "if($f -match '^backend/app/(proxyapi_client|main|logging_config)\.py$'){ Add 'enhance proxyapi integration and observability' 5 }" ^
      "if($f -match '^frontend/components/(digestwizard|dashboard)\.tsx$'){ Add 'improve digest wizard ui flow' 5 }" ^
      "if($f -match '^frontend/app/digests/\[id\]/page\.tsx$'){ Add 'adjust digest route behavior' 3 }" ^
      "if($f -match '^main\.py$'){ Add 'update local launcher behavior' 3 }" ^
      "if($f -match '^push_to_github\.bat$'){ Add 'improve git push automation' 2 }" ^
      "if($f -match '(^|/)(readme\.md|\.env\.example|\.gitignore)$'){ Add 'refresh docs and environment setup' 2 }" ^
    "}" ^
    "$corePresent = $score.Keys | Where-Object { $score[$_] -ge 5 };" ^
    "$themes = @($score.GetEnumerator() | Sort-Object -Property Value -Descending | ForEach-Object { $_.Key });" ^
    "if($corePresent.Count -gt 0){ $themes = $themes | Where-Object { $_ -ne 'improve git push automation' -and $_ -ne 'refresh docs and environment setup' } }" ^
    "$themes = @($themes | Select-Object -First 3);" ^
    "if($themes.Count -eq 0){ $themes = @('maintenance updates') }" ^
    "$msg='update: ' + ($themes -join '; ');" ^
    "$msg=T $msg; $msg=Clip $msg $max; Write-Output $msg"` ) do set msg=%%M
  if "!msg!"=="" set msg=update %ts%
)

if "%NO_CYRILLIC%"=="1" (
  for /f "usebackq delims=" %%M in (`powershell -NoProfile -Command ^
    "$s='%msg%';" ^
    "$map=@{'а'='a';'б'='b';'в'='v';'г'='g';'д'='d';'е'='e';'ё'='e';'ж'='zh';'з'='z';'и'='i';'й'='y';'к'='k';'л'='l';'м'='m';'н'='n';'о'='o';'п'='p';'р'='r';'с'='s';'т'='t';'у'='u';'ф'='f';'х'='h';'ц'='ts';'ч'='ch';'ш'='sh';'щ'='sch';'ъ'='';'ы'='y';'ь'='';'э'='e';'ю'='yu';'я'='ya'};" ^
    "function Clip([string]$x,[int]$n){ if($x.Length -le $n){return $x}; $y=$x.Substring(0,$n); $i=$y.LastIndexOf(' '); if($i -gt 20){ return $y.Substring(0,$i).TrimEnd(' ',';','.',',') }; return $y };" ^
    "$r=''; foreach($c in $s.ToCharArray()){ $k=([string]$c).ToLowerInvariant(); if($map.ContainsKey($k)){$r+=$map[$k]}else{$r+=$c}}; $r=Clip $r %MAX_MSG_LEN%; Write-Output $r"` ) do set msg=%%M
)

echo.
echo === Commit message ===
echo %msg%

if "%DRY_RUN%"=="0" (
  echo.
  set /p _ans=Confirm commit/push? [Y]es/[E]dit/[N]o:
  if /I "!_ans!"=="N" (
    echo Cancelled by user.
    exit /b 0
  )
  if /I "!_ans!"=="E" (
    set /p msg=Enter commit message:
    if "%NO_CYRILLIC%"=="1" (
      for /f "usebackq delims=" %%M in (`powershell -NoProfile -Command ^
        "$s='%msg%';" ^
        "$map=@{'а'='a';'б'='b';'в'='v';'г'='g';'д'='d';'е'='e';'ё'='e';'ж'='zh';'з'='z';'и'='i';'й'='y';'к'='k';'л'='l';'м'='m';'н'='n';'о'='o';'п'='p';'р'='r';'с'='s';'т'='t';'у'='u';'ф'='f';'х'='h';'ц'='ts';'ч'='ch';'ш'='sh';'щ'='sch';'ъ'='';'ы'='y';'ь'='';'э'='e';'ю'='yu';'я'='ya'};" ^
        "function Clip([string]$x,[int]$n){ if($x.Length -le $n){return $x}; $y=$x.Substring(0,$n); $i=$y.LastIndexOf(' '); if($i -gt 20){ return $y.Substring(0,$i).TrimEnd(' ',';','.',',') }; return $y };" ^
        "$r=''; foreach($c in $s.ToCharArray()){ $k=([string]$c).ToLowerInvariant(); if($map.ContainsKey($k)){$r+=$map[$k]}else{$r+=$c}}; $r=Clip $r %MAX_MSG_LEN%; Write-Output $r"` ) do set msg=%%M
    )
  )
)

if "%DRY_RUN%"=="1" (
  echo.
  echo === DRY RUN ===
  echo No staging/commit/push performed.
  echo.
  pause
  exit /b 0
)

echo.
echo === Stage changes ===
git add -A
if errorlevel 1 goto :err

if not exist docs mkdir docs >nul 2>nul
powershell -NoProfile -Command "$line='[%ts%] %msg%'; Add-Content -LiteralPath 'docs\push_protocol.log' -Value $line -Encoding UTF8"
if errorlevel 1 goto :err

git add docs\push_protocol.log
if errorlevel 1 goto :err

echo.
echo === Commit ===
git commit -m "%msg%"
if errorlevel 1 (
  echo No changes to commit or commit failed.
)

echo.
echo === Push ===
git push
if errorlevel 1 goto :err

echo.
echo DONE.
echo.
pause
exit /b 0

:err
echo.
echo ERROR: command failed.
echo.
pause
exit /b 1
