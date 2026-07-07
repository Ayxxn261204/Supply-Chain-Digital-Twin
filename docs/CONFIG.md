# Configuration Documentation

This document describes the configuration system for the Digital Twin simulation.
All hardcoded values have been extracted to central configuration files in the `config/` directory.

## Configuration Files

| File | Purpose |
|------|---------|
| `config/simulation_defaults.yaml` | Physics, model parameters, and default entity states |
| `config/api.yaml` | API server settings (CORS, pagination) |
| `.env` | Environment-specific credentials (Secrets) |

## Simulation Defaults (`config/simulation_defaults.yaml`)

### Weather
- **defaults.humidity**: Default relative humidity % (50.0) if weather model offline.
- **defaults.temperature**: Default temperature °C (25.0).

### Network (Roads)
- **road_quality_penalties**: Cost penalty added to A* routing for different surface types.
  - `dirt`: 10.0 (High avoidance)
  - `primary`: 0.0 (Preferred)

### Entities

#### Orange Batch (Perishable Model)
- **initial_rsl_percent**: Starting shelf life (100.0%)
- **shelf_life_days**: Total shelf life at optimal conditions (14 days)
- **optimal_temperature_celsius**: Ideal storage temp (4.0°C)
- **q10_factor**: Degradation acceleration per 10°C rise (2.0)

#### Driver (Fatigue Model)
- **max_driving_minutes**: Max operational time before break (240 min)
- **break_minutes**: Duration of mandatory break (30 min)
- **initial_fatigue**: Starting fatigue level (0.0)

## API Settings (`config/api.yaml`)

- **cors.allowed_origins**: List of frontend domains allowed to access API.
- **pagination.default_limit**: Default record count for lists.

## Environment Variables (.env)

| Variable | Description |
|----------|-------------|
| `INFLUX_URL` | URL of InfluxDB server |
| `INFLUX_TOKEN` | Auth token for InfluxDB |
| `INFLUX_ORG` | Organization name |
| `INFLUX_BUCKET` | Bucket name |
