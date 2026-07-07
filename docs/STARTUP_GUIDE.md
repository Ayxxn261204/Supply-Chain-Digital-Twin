# Digital Twin Supply Chain - Complete Startup Guide

## Quick Start (TL;DR)

```powershell
# 1. Start InfluxDB (in any terminal)
docker start influxdb

# 2. Start Backend API (Terminal 1)
cd C:\Users\aryap\OneDrive\Desktop\Arya_College\FYP_Dynamic\FYP\api
python -m uvicorn main:app --reload --port 8000

# 3. Start Frontend (Terminal 2)
cd C:\Users\aryap\OneDrive\Desktop\Arya_College\FYP_Dynamic\FYP\dashboard_v2
npm run dev

# 4. Run Simulation (Terminal 3)
cd C:\Users\aryap\OneDrive\Desktop\Arya_College\FYP_Dynamic\FYP
python main.py --duration-days 7 --speed 10 --time-step 1 --start-date 2024-07-15

# 5. Open Dashboard: http://localhost:5173
```

---

## Detailed Step-by-Step Guide

### Prerequisites Check

**Before starting, verify you have**:
```powershell
# Check Python
python --version
# Should show: Python 3.10 or higher

# Check Node.js
node --version
# Should show: v18 or higher

# Check npm
npm --version

# Check Docker
docker --version

# Check if in correct directory
pwd
# Should show: C:\Users\aryap\OneDrive\Desktop\Arya_College\FYP_Dynamic\FYP
```

**Environment File**:
Ensure `.env` file exists in `FYP/` directory with:
```env
INFLUX_URL=http://localhost:8086
INFLUX_TOKEN=my-super-secret-auth-token
INFLUX_ORG=digital-twin
INFLUX_BUCKET=supply-chain
```

---

## Component Startup Order (IMPORTANT!)

**Start components in this order**:
1. **InfluxDB** (Database - must be first)
2. **Backend API** (Depends on InfluxDB)
3. **Frontend** (Depends on API)
4. **Simulation** (Writes to InfluxDB, last)

**Why this order?**
- API needs InfluxDB running to connect
- Frontend needs API to fetch data
- Simulation writes data that others consume

---

## Step 1: Start InfluxDB Database

### First Time Setup (Only Once)

```powershell
# Create and run InfluxDB container
docker run -d `
  --name influxdb `
  -p 8086:8086 `
  -v influxdb-data:/var/lib/influxdb2 `
  -e DOCKER_INFLUXDB_INIT_MODE=setup `
  -e DOCKER_INFLUXDB_INIT_USERNAME=admin `
  -e DOCKER_INFLUXDB_INIT_PASSWORD=adminpassword `
  -e DOCKER_INFLUXDB_INIT_ORG=digital-twin `
  -e DOCKER_INFLUXDB_INIT_BUCKET=supply-chain `
  -e DOCKER_INFLUXDB_INIT_ADMIN_TOKEN=my-super-secret-auth-token `
  influxdb:2.7
```

### Normal Startup (Every Time)

```powershell
# Start existing container
docker start influxdb

# Verify it's running
docker ps
# Should show: influxdb container with status "Up"
```

### Verification

```powershell
# Test InfluxDB is accessible
Invoke-WebRequest -Uri "http://localhost:8086/health" -UseBasicParsing
# Should return: "pass"
```

**If container doesn't exist**:
Run the "First Time Setup" commands above.

**If port 8086 is in use**:
```powershell
# Find what's using the port
Get-NetTCPConnection -LocalPort 8086
# Stop the conflicting process or use different port
```

---

## Step 2: Start Backend API Server

### Open Terminal 1 (PowerShell)

```powershell
# Navigate to API directory
cd C:\Users\aryap\OneDrive\Desktop\Arya_College\FYP_Dynamic\FYP\api

# Start FastAPI server
python -m uvicorn main:app --reload --port 8000
```

**What you'll see**:
```
INFO:     Uvicorn running on http://127.0.0.1:8000
INFO:     Will watch for changes in these directories...
INFO:     Application startup complete.
2025-12-29 XX:XX:XX - [STARTUP] Starting FastAPI Dashboard API...
2025-12-29 XX:XX:XX - [OK] Connected to InfluxDB at http://localhost:8086
```

### Verification

**In a new terminal**:
```powershell
# Test API health
Invoke-WebRequest -Uri "http://localhost:8000/health" -UseBasicParsing | Select-Object -ExpandProperty Content
```

**Expected Response**:
```json
{"status":"healthy","influxdb":"connected","url":"http://localhost:8086","org":"digital-twin","bucket":"supply-chain"}
```

**Common Issues**:
- **ModuleNotFoundError**: Make sure you're in the `api/` directory
- **InfluxDB connection failed**: Start InfluxDB first (Step 1)
- **Port 8000 in use**: Change port or kill existing process

---

## Step 3: Start Frontend Dashboard

### Open Terminal 2 (PowerShell)

```powershell
# Navigate to dashboard directory
cd C:\Users\aryap\OneDrive\Desktop\Arya_College\FYP_Dynamic\FYP\dashboard_v2

# Start Vite dev server
npm run dev
```

**What you'll see**:
```
  VITE v7.3.0  ready in XXX ms

  ➜  Local:   http://localhost:5173/
  ➜  Network: use --host to expose
  ➜  press h + enter to show help
```

### Verification

**Open browser** to: `http://localhost:5173`

**You should see**:
- Dashboard loads without errors
- Simulation selector (may be empty initially)
- Map displaying Nagpur region
- KPI cards (showing zeros until simulation runs)

**Common Issues**:
- **Dependencies missing**: Run `npm install` first
- **Port 5173 in use**: Vite will auto-increment to 5174, 5175, etc.
- **API connection errors**: Make sure backend (Step 2) is running

---

## Step 4: Run Simulation

### Open Terminal 3 (PowerShell)

```powershell
# Navigate to project root
cd C:\Users\aryap\OneDrive\Desktop\Arya_College\FYP_Dynamic\FYP

# Run simulation with your desired parameters
python main.py --duration-days 7 --speed 10 --time-step 1 --start-date 2024-07-15
```

### Command Options

**Available Parameters**:
```powershell
python main.py [options]

Options:
  --duration-days DAYS      # Simulation duration (default: 7)
  --speed MULTIPLIER        # Speed multiplier (default: 1, max: 100)
  --time-step MINUTES       # Timestep in minutes (default: 1)
  --start-date YYYY-MM-DD   # Start date (default: 2024-01-01)
  --seed NUMBER             # Random seed for reproducibility
  --config PATH             # Custom config file path
```

**Example Configurations**:

```powershell
# Quick test (1 day, fast)
python main.py --duration-days 1 --speed 50 --time-step 5

# Full week (monsoon season)
python main.py --duration-days 7 --speed 10 --time-step 1 --start-date 2024-07-15

# Month-long simulation (slower)
python main.py --duration-days 30 --speed 5 --time-step 1

# Reproducible simulation (same seed)
python main.py --duration-days 7 --speed 10 --seed 42
```

### What You'll See

**Console Output**:
```
============================================================
   Digital Twin Supply Chain Simulation
============================================================
Configuration:
   Duration: 7 days
   Speed: 10x
   Timestep: 1 minutes
   Start Date: 2024-07-15

[Initialization]
   Loading road network...
   Indexing neighbors: 201619/201619 segments (100.0%)
   [OK] Road network loaded (201619 segments, 77879 nodes)

   [OK] Generated 1 warehouses dynamically
   [OK] Generated 3 retailers dynamically
   [OK] Created 4 trucks

[Simulation Running]
   Day 1, 00:00 - Warehouse WH_00: 5000.0 kg, 4 trucks available
   Day 1, 01:00 - Retailer RT_01: Placed order for 800 kg
   ...
```

### Verification

**Dashboard should update**:
1. Simulation appears in selector dropdown
2. KPI cards show non-zero values
3. Map shows warehouse, retailers, trucks moving
4. Analytics charts start populating

**Check Data in InfluxDB**:
```powershell
# List simulations
Invoke-WebRequest -Uri "http://localhost:8000/api/simulations" -UseBasicParsing
```

---

## Complete System Status Check

**Run this to verify everything is running**:

```powershell
# Check all containers
docker ps
# Should show: influxdb running

# Check all Node processes
Get-Process -Name "node" -ErrorAction SilentlyContinue

# Check all Python processes
Get-Process -Name "python" -ErrorAction SilentlyContinue

# Test API
Invoke-WebRequest -Uri "http://localhost:8000/health" -UseBasicParsing

# Test Frontend (open in browser)
Start-Process "http://localhost:5173"
```

---

## Stopping Everything (Clean Shutdown)

### Stop in Reverse Order

**1. Stop Simulation** (Terminal 3):
```
Press Ctrl+C
```

**2. Stop Frontend** (Terminal 2):
```
Press Ctrl+C
```

**3. Stop Backend API** (Terminal 1):
```
Press Ctrl+C
```

**4. Stop InfluxDB** (Optional - can keep running):
```powershell
docker stop influxdb
```

### Force Kill Everything

**If components won't stop**:
```powershell
# Kill all Python processes
Get-Process -Name "python" | Stop-Process -Force

# Kill all Node processes  
Get-Process -Name "node" | Stop-Process -Force

# Stop InfluxDB
docker stop influxdb
```

---

## Troubleshooting Guide

### Issue: "ModuleNotFoundError" when starting API

**Solution**:
```powershell
# Make sure you're in the api/ directory
cd C:\Users\aryap\OneDrive\Desktop\Arya_College\FYP_Dynamic\FYP\api

# Install dependencies if missing
pip install -r requirements.txt
```

### Issue: "InfluxDB connection failed"

**Solution**:
```powershell
# Check if InfluxDB is running
docker ps
# If not running:
docker start influxdb

# Verify InfluxDB is accessible
Invoke-WebRequest -Uri "http://localhost:8086/health" -UseBasicParsing
```

### Issue: "Port already in use"

**Solution**:
```powershell
# For API (port 8000)
Get-NetTCPConnection -LocalPort 8000 | Select-Object OwningProcess
Stop-Process -Id <PID> -Force

# For Frontend (port 5173)
Get-NetTCPConnection -LocalPort 5173 | Select-Object OwningProcess
Stop-Process -Id <PID> -Force
```

### Issue: Dashboard shows "No simulations found"

**Checklist**:
1. Is InfluxDB running? `docker ps`
2. Is API connected? Check `http://localhost:8000/health`
3. Has simulation started writing data? Check console output
4. Wait 30 seconds for initial data to propagate

### Issue: Simulation crashes immediately

**Check**:
```powershell
# Verify .env file exists
Get-Content .env

# Check Python environment
python --version
pip list | Select-String "influxdb"

# Check simulation config
Get-Content config\simulation_config.yaml
```

---

## Production Deployment (Optional)

### For Production Server

**1. Build Frontend**:
```powershell
cd dashboard_v2
npm run build
# Output in: dashboard_v2/dist/
```

**2. Run API in production mode**:
```powershell
cd api
python -m uvicorn main:app --host 0.0.0.0 --port 8000 --workers 4
```

**3. Serve frontend with nginx/IIS**:
Point web server to `dashboard_v2/dist/` directory

**4. Use long-running InfluxDB**:
```powershell
# InfluxDB with persistent storage
docker run -d `
  --name influxdb `
  -p 8086:8086 `
  -v C:\influxdb-data:/var/lib/influxdb2 `
  -e DOCKER_INFLUXDB_INIT_MODE=setup `
  -e DOCKER_INFLUXDB_INIT_USERNAME=admin `
  -e DOCKER_INFLUXDB_INIT_PASSWORD=<SECURE_PASSWORD> `
  -e DOCKER_INFLUXDB_INIT_ORG=digital-twin `
  -e DOCKER_INFLUXDB_INIT_BUCKET=supply-chain `
  -e DOCKER_INFLUXDB_INIT_ADMIN_TOKEN=<SECURE_TOKEN> `
  --restart unless-stopped `
  influxdb:2.7
```

---

## Useful Commands Reference

### Docker
```powershell
docker ps                    # List running containers
docker ps -a                 # List all containers
docker start influxdb        # Start InfluxDB
docker stop influxdb         # Stop InfluxDB
docker restart influxdb      # Restart InfluxDB
docker logs influxdb         # View logs
```

### Process Management
```powershell
Get-Process -Name "python"   # List Python processes
Get-Process -Name "node"     # List Node processes
Stop-Process -Id <PID>       # Kill specific process
```

### Network
```powershell
Get-NetTCPConnection -LocalPort 8000  # Check port 8000
Get-NetTCPConnection -LocalPort 5173  # Check port 5173
Get-NetTCPConnection -LocalPort 8086  # Check port 8086
```

---

## Summary Checklist

**Every time you start the system**:

- [ ] 1. Start InfluxDB: `docker start influxdb`
- [ ] 2. Verify InfluxDB: `http://localhost:8086/health`
- [ ] 3. Start API (in `api/`): `python -m uvicorn main:app --reload --port 8000`
- [ ] 4. Verify API: `http://localhost:8000/health`
- [ ] 5. Start Frontend (in `dashboard_v2/`): `npm run dev`
- [ ] 6. Verify Frontend: Open `http://localhost:5173`
- [ ] 7. Run Simulation: `python main.py [options]`
- [ ] 8. Verify Dashboard shows data

**Expected Timeline**:
- InfluxDB: ~2 seconds to start
- API: ~3 seconds to start
- Frontend: ~5-10 seconds to start
- Simulation: Runs for configured duration

**System Ready When**:
- Dashboard loads without errors
- KPI cards show data (after simulation starts)
- Map displays entities
- Analytics charts populate

---

## Quick Tips

1. **Keep terminals open**: Each component needs its own terminal
2. **Watch the logs**: Console output shows what's happening
3. **Start in order**: Always InfluxDB → API → Frontend → Simulation
4. **Check health endpoints**: Use health checks to verify status
5. **Wait for data**: Dashboard needs 30-60 seconds to populate initially

**You're now ready to run the system independently!** 🚀
