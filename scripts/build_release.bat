@echo off
chcp 65001 > nul
setlocal

:: 버전 설정
set VERSION=1.0.0
set RELEASE_NAME=SimpleCollector_v%VERSION%

echo ============================================================
echo   Simple Collector 릴리즈 빌드
echo   Version: %VERSION%
echo ============================================================
echo.

:: 릴리즈 폴더 생성
set RELEASE_DIR=release\%RELEASE_NAME%
if exist release rmdir /s /q release
mkdir %RELEASE_DIR%

echo [1/5] 소스 코드 복사...
xcopy /E /I /Q src %RELEASE_DIR%\src
xcopy /E /I /Q config %RELEASE_DIR%\config
xcopy /E /I /Q scripts %RELEASE_DIR%\scripts

echo [2/5] 실행 스크립트 복사...
copy run.bat %RELEASE_DIR%\
copy run_demo.bat %RELEASE_DIR%\
copy requirements.txt %RELEASE_DIR%\

echo [3/5] 설치 스크립트 루트로 이동...
move %RELEASE_DIR%\scripts\install.bat %RELEASE_DIR%\install.bat

echo [4/5] 불필요 파일 제거...
:: __pycache__ 제거
for /d /r %RELEASE_DIR% %%d in (__pycache__) do @if exist "%%d" rmdir /s /q "%%d"
:: .pyc 파일 제거
del /s /q %RELEASE_DIR%\*.pyc 2>nul

echo [5/5] ZIP 파일 생성...
cd release
powershell -Command "Compress-Archive -Path '%RELEASE_NAME%' -DestinationPath '%RELEASE_NAME%.zip' -Force"
cd ..

echo.
echo ============================================================
echo   빌드 완료!
echo   출력: release\%RELEASE_NAME%.zip
echo ============================================================
echo.
echo   배포 방법:
echo   1. ZIP 파일을 대상 PC에 복사
echo   2. 압축 해제
echo   3. install.bat 실행
echo   4. config 폴더의 설정 수정
echo   5. run.bat 실행
echo.
pause
