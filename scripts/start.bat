@echo off
REM Quick start script for Digital Twin data pipeline (Windows)

echo ==========================================
echo Digital Twin - Quick Start
echo ==========================================
echo.

REM Check if Docker is running
docker info >nul 2>&1
if errorlevel 1 (
    echo X Docker is not running. Please start Docker first.
    exit /b 1
)

echo √ Docker is running
echo.

REM Start services
echo Starting Docker services...
docker-compose up -d

echo.
echo Waiting for services to become healthy (60 seconds)...
timeout /t 60 /nobreak >nul

echo.
echo Service Status:
docker-compose ps

echo.
echo ==========================================
echo √ Setup Complete!
echo ==========================================
echo.
echo Next steps:
echo 1. Run test publisher:
echo    python src/data/test_publisher.py --duration 30
echo.
echo 2. Access InfluxDB UI:
echo    http://localhost:8086
echo    Username: admin
echo    Password: adminpassword123
echo.
echo 3. View logs:
echo    docker-compose logs -f
echo.
echo 4. Stop services:
echo    docker-compose down
echo.
pause
