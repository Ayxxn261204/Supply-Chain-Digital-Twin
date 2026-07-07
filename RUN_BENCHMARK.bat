@echo off
cd /d "c:\Users\aryap\OneDrive\Desktop\Arya_College\FYP_Dynamic\FYP"
echo ============================================================
echo   BENCHMARK RUNNER - DO NOT CLOSE THIS WINDOW
echo   Baseline phase then RL phase will run automatically.
echo   Results saved to data/logs/telemetry/
echo ============================================================
echo.
python scripts/run_benchmark.py
echo.
echo ============================================================
echo   BOTH PHASES COMPLETE - you can close this window now.
echo ============================================================
pause
