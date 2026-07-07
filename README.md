# 🚚 Digital Twin of Supply Chain for Perishable Items

Simulates a city-wide delivery network for perishable goods using three AI agents — one picks the best route, one handles road emergencies, and one predicts travel times. Tested over 365 days, it cuts stockouts by 52.9%, fulfills 4.7% more orders, and delivers 474,708 kg more cargo than a standard routing algorithm.

---

## 📌 Overview

Traditional route planning algorithms like A* use a fixed cost function and cannot adapt to real-time events like road accidents, extreme weather, or sudden demand spikes. This project addresses that limitation by designing and implementing a **Supply Chain Digital Twin** for the city of Nagpur, validated over a **365-day simulation**.

The system models a fleet of delivery trucks operating across a realistic road network, subject to dynamic weather, traffic, and accident events — serving as a safe, repeatable environment for training and validating AI models.

---

## 🏗️ System Architecture

The routing intelligence is organized into a **three-tier AI hierarchy**:

```
┌─────────────────────────────────────────┐
│     Tier 1: Central PPO Brain           │
│        (Macro Routing Strategy)         │
└────────────────┬────────────────────────┘
                 │
        ┌────────┴────────┐
        ▼                 ▼
┌───────────────┐  ┌──────────────────────┐
│  Tier 2:      │  │  Tier 3:             │
│  Edge Q-Agent │  │  ETA Forecaster      │
│  (Emergency   │  │  (Ridge Regression)  │
│  Rerouting)   │  │                      │
└───────┬───────┘  └──────────┬───────────┘
        │                     │
        └──────────┬──────────┘
                   ▼
     ┌─────────────────────────┐
     │   Simulation Engine     │
     │   (Digital Twin)        │
     └─────────────────────────┘
```

### Tier 1 — Central PPO Brain (Macro Routing)
- Trained for **50,000 steps** using Proximal Policy Optimization (PPO)
- Observes a **25-dimensional state vector** (fuel level, cargo RSL, traffic density, time of day, zone, etc.)
- Selects one of three routing strategies per dispatch cycle:
  - `0` → Fuel-Priority
  - `1` → RSL-Priority (cargo freshness)
  - `2` → Speed-Priority
- Neural network: MLP with two hidden layers (64 neurons each, ReLU activation)

### Tier 2 — Edge Q-Learning Emergency Agent (Micro Rerouting)
- A separate tabular Q-Learning agent embedded in each truck
- Handles real-time emergency rerouting when accidents or weather block the planned route
- State space: hazard severity (low/med/high) × alternate routes available → 9-state Q-table
- Updates online after every emergency event using the Bellman equation

### Tier 3 — ETA Forecaster (Online Ridge Regression)
- Predicts travel time for each road segment using an **8-dimensional feature vector**:
  - Sin/Cos encoding of time of day and day of week (circular)
  - Weather severity, traffic density, road type, geographic zone
- Updates incrementally via `partial_fit()` after every segment traversal
- Feeds predictions into the A* edge cost function, replacing static estimates

---

## 🗺️ Nagpur Road Network

The city is modeled as a weighted directed graph using **NetworkX**, derived from OpenStreetMap data. Road segments are classified into four zones:

| Zone | Description |
|------|-------------|
| HIGHWAY | Ring roads and national highways — fast, stable traffic |
| RESIDENTIAL | Inner-city streets — moderate congestion, peak hours |
| OFFICE | Commercial corridors — sharp weekday rush-hour peaks |
| SHOPPING | Market districts — midday and weekend peaks |

---

## ⚙️ Key Engineering Contributions

### 5:1 Reward Normalization
Without careful calibration, the PPO agent defaulted entirely to fuel-conservative behavior. A physical analysis revealed that fuel consumption (~10–15% per trip) dominated cargo freshness decay (~1–2% per trip) by roughly 10:1. Setting the freshness penalty weight to `50.0` and the fuel penalty to `10.0` equalized their contributions, enabling true multi-objective strategy switching.

### Anti-Thrashing GPS Lock
During testing, the fleet delivery rate collapsed to **49.8%** because the PPO brain broadcast a new strategy signal every 15 minutes, causing trucks to constantly recalculate routes instead of completing deliveries. A simple state machine fix — only recalculate when the strategy actually changes — recovered the service level to **99.9%**.

---

## 📊 Results (365-Day Benchmark)

| Metric | Baseline (A*) | RL Agent | Delta |
|--------|--------------|----------|-------|
| Service Level | 99.1% | 99.9% | +0.8% ✅ |
| Orders Fulfilled | 6,855 | 7,178 | +323 ✅ |
| Cargo Delivered (kg) | 16,270,753 | 16,745,461 | +474,708 ✅ |
| Retailer Stockouts | 70,898 | 33,401 | −52.9% ✅ |
| Road Accidents | 2,212 | 2,077 | −135 ✅ |
| Avg Cargo RSL at Delivery | 99.7% | 99.7% | Maintained ✅ |
| Avg Fleet Fuel Level | 78.7% | 78.7% | Maintained ✅ |
| Warehouse Restocks | 2,938 | 3,048 | +110 ✅ |

> **Pareto-optimal result**: 6 of 8 metrics improved, none regressed.

---

## 🛠️ Tech Stack

| Library | Version | Purpose |
|---------|---------|---------|
| Python | 3.10 | Primary language |
| Stable-Baselines3 | 2.x | PPO implementation and training |
| Gymnasium | 0.29 | RL environment interface |
| NetworkX | 3.x | Road graph construction and A* routing |
| Scikit-learn | 1.4 | Ridge Regression ETA Forecaster |
| NumPy | 1.26 | State vector construction |
| Pandas | 2.x | Telemetry and event log analysis |
| Matplotlib | 3.8 | Benchmark charts |

---

## 🚀 Getting Started

### Prerequisites
```bash
pip install stable-baselines3 gymnasium networkx scikit-learn numpy pandas matplotlib pyyaml
```

### Train the PPO Agent
```bash
python train.py --steps 50000
```

### Run the 365-Day Benchmark
```bash
# Baseline (A* only)
python simulate.py --mode baseline --seed 42

# RL-enabled (full three-tier AI)
python simulate.py --mode rl --seed 42
```

### Configuration
Edit `simulation_config.yaml` to adjust fleet size, warehouse location, retailer nodes, and weather parameters.

---

## 📁 Project Structure

```
├── agents/
│   ├── ppo_brain.py          # Central PPO Brain
│   ├── edge_q_agent.py       # Per-truck Q-Learning agent
│   └── eta_forecaster.py     # Online Ridge Regression model
├── simulation/
│   ├── engine.py             # Discrete-event simulation loop
│   ├── road_network.py       # Nagpur city graph (NetworkX)
│   └── events.py             # Weather, accident, traffic events
├── data/
│   └── logs/telemetry/       # Simulation output CSVs
├── models/
│   └── rl/                   # Saved PPO checkpoints
├── train.py                  # PPO training script
├── simulate.py               # Benchmark runner
└── simulation_config.yaml    # Configuration file
```

---

## 👥 Authors

- **Ayaan Khan** (BT22CSE001)
- **Arya Patil** (BT22CSE003)
- **Nihaal Badam** (BT22CSE004)
- **Swastik Ankulge** (BT22CSE013)

Under the guidance of **Dr. R.B. Keskar**, Associate Professor, CSE, VNIT Nagpur
and co-guidance of **Dr. Souvik Barat**, Principal Scientist, TCS R&D, Pune.

---

## 📄 License

This project was developed as a final year B.Tech project at the Department of Computer Science and Engineering, Visvesvaraya National Institute of Technology (VNIT), Nagpur, 2026.
