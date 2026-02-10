@echo off
chcp 65001 > nul
echo ============================================================
echo   Simple Collector 설치 스크립트
echo ============================================================
echo.

:: Python 확인
python --version > nul 2>&1
if errorlevel 1 (
    echo [오류] Python이 설치되어 있지 않습니다.
    echo        https://www.python.org/downloads/ 에서 Python 3.10+ 설치 후 다시 실행하세요.
    pause
    exit /b 1
)

echo [1/3] Python 버전 확인...
python --version

echo.
echo [2/3] 필요 패키지 설치 중...
pip install -r requirements.txt --quiet

if errorlevel 1 (
    echo [오류] 패키지 설치 실패
    pause
    exit /b 1
)

echo.
echo [3/3] 설치 완료!
echo.
echo ============================================================
echo   사용법:
echo ============================================================
echo.
echo   1. config 폴더의 설정 파일 수정:
echo      - collector_mc_r.yaml : PLC 주소, MQTT 설정
echo      - tags_mc_r.csv       : 수집할 태그 목록
echo.
echo   2. 실행:
echo      run.bat
echo.
echo   3. 데모 모드 (PLC 없이 테스트):
echo      run_demo.bat
echo.
echo ============================================================
pause
