"""
analyze_longterm_sim.py
========================
Parses the most recent headless simulation run (telemetry + events CSVs)
and produces a health report + presentation-ready matplotlib charts.

Usage:
    python scripts/analyze_longterm_sim.py

Outputs:
    - Console: summary report of all key metrics
    - data/analysis/*.png: PNG charts for FYP presentation
"""

import os
import json
import glob
import pandas as pd
import matplotlib
matplotlib.use("Agg")  # non-interactive backend — safe for headless/script runs
import matplotlib.pyplot as plt
import matplotlib.ticker as mtick
from collections import Counter

# ─────────────────────────────────────────────
# 1.  Locate the latest run files
# ─────────────────────────────────────────────
TELEMETRY_DIR = os.path.join("data", "logs", "telemetry")
OUTPUT_DIR    = os.path.join("data", "analysis")
os.makedirs(OUTPUT_DIR, exist_ok=True)

telemetry_files = sorted(glob.glob(os.path.join(TELEMETRY_DIR, "telemetry_sim-*.csv")))
events_files    = sorted(glob.glob(os.path.join(TELEMETRY_DIR, "events_sim-*.csv")))

if not telemetry_files or not events_files:
    print("[ERROR] No simulation output files found in", TELEMETRY_DIR)
    raise SystemExit(1)

latest_tel = telemetry_files[-1]
latest_evt = events_files[-1]
print(f"[INFO] Analysing:\n  Telemetry -> {latest_tel}\n  Events    -> {latest_evt}\n")

# ─────────────────────────────────────────────
# 2.  Load data
#     Telemetry cols: timestamp, entity_type, entity_id, status_code, fuel, rsl, lat, lon
#     Events    cols: timestamp, event_type, data (JSON string)
# ─────────────────────────────────────────────
tel  = pd.read_csv(latest_tel)
evts = pd.read_csv(latest_evt)

# Parse JSON payloads once
def safe_json(s):
    try:
        return json.loads(s)
    except Exception:
        return {}

evts["payload"] = evts["data"].apply(safe_json)

# Convenience: simulation duration
SIM_MINUTES = tel["timestamp"].max()
SIM_DAYS    = SIM_MINUTES / 60 / 24

print(f"[INFO] Simulation span : {SIM_DAYS:.1f} simulated days")
print(f"[INFO] Telemetry rows  : {len(tel):,}")
print(f"[INFO] Event rows      : {len(evts):,}")
print()

# ─────────────────────────────────────────────
# 3.  Derived columns
# ─────────────────────────────────────────────
tel["day"]  = (tel["timestamp"] / 60 / 24).astype(int)
evts["day"] = (evts["timestamp"] / 60 / 24).astype(int)

trucks_tel = tel[tel["entity_type"] == "truck"].copy()
wh_tel     = tel[tel["entity_type"] == "warehouse"].copy()

# ─────────────────────────────────────────────
# 4.  Component health checks  (console report)
# ─────────────────────────────────────────────
print("=" * 60)
print("  COMPONENT HEALTH REPORT")
print("=" * 60)

# 4a. Trucks active
truck_ids = trucks_tel["entity_id"].unique()
print(f"  Trucks tracked                : {len(truck_ids)}")

# 4b. Status codes (0=idle/en-route, 1=delivering, 2=crashed/retired)
status_counts = trucks_tel["status_code"].value_counts().to_dict()
print(f"  Truck status snapshot counts  : {status_counts}")

# 4c. Fuel — any truck ever ran critically low (< 5 %)?
low_fuel = trucks_tel[trucks_tel["fuel"] < 5.0]
if low_fuel.empty:
    print(f"  Fuel critical events (< 5 %)  : 0  [OK]")
else:
    print(f"  Fuel critical events (< 5 %)  : {len(low_fuel)} across {low_fuel['entity_id'].nunique()} truck(s)")

# 4d. RSL degradation
avg_rsl_start = trucks_tel[trucks_tel["day"] == 0]["rsl"].mean()
avg_rsl_end   = trucks_tel[trucks_tel["day"] == int(SIM_DAYS) - 1]["rsl"].mean()
print(f"  Avg cargo RSL day 0           : {avg_rsl_start:.1f}%")
print(f"  Avg cargo RSL final day       : {avg_rsl_end:.1f}%")

# 4e. Warehouses
wh_ids = wh_tel["entity_id"].unique()
print(f"  Warehouses tracked            : {list(wh_ids)}")

# 4f. Events breakdown
event_counts = evts["event_type"].value_counts().to_dict()
print()
print("  EVENT BREAKDOWN:")
for k, v in sorted(event_counts.items(), key=lambda x: -x[1]):
    print(f"    {k:<40} {v:>5}")

# 4g. AI / RL components
route_changed = evts[evts["event_type"] == "route_changed"]
wh_reorders   = evts[evts["event_type"] == "warehouse_reorder_triggered"]
wh_restocks   = evts[evts["event_type"] == "warehouse_restock"]
accidents     = evts[evts["event_type"] == "road_accident"]
low_fuel_warn = evts[evts["event_type"] == "truck_low_fuel_warning"]

print()
print(f"  PPO rerouting decisions       : {len(route_changed)}")
print(f"  Warehouse reorders triggered  : {len(wh_reorders)}")
print(f"  Warehouse restocks received   : {len(wh_restocks)}")
print(f"  Road accidents simulated      : {len(accidents)}")
print(f"  Low-fuel warnings (EdgeBrain) : {len(low_fuel_warn)}")
print()

# 4h. Accident severity breakdown
if not accidents.empty:
    severities = [p.get("severity", "unknown") for p in accidents["payload"]]
    print("  Accident severity breakdown   :", Counter(severities))
    print()

# ─────────────────────────────────────────────
# 5.  Chart 1: Avg Cargo RSL over time (spoilage proxy)
#     Only consider trucks actively carrying cargo (rsl < 100).
#     Idle/empty trucks default to rsl=100 and would skew the average.
# ─────────────────────────────────────────────
plt.style.use("dark_background")
COLORS = {"green": "#69db7c", "orange": "#ffa94d", "red": "#ff6b6b",
          "blue": "#74c0fc", "purple": "#da77f2", "yellow": "#ffd43b"}

# Filter: only snapshots where truck is actively carrying cargo
carrying_tel = trucks_tel[trucks_tel["rsl"] < 100.0].copy()
if carrying_tel.empty:
    print("[WARN] No in-transit RSL readings found — skipping RSL chart.")
    daily_rsl = pd.Series(dtype=float)
else:
    daily_rsl = carrying_tel.groupby("day")["rsl"].mean()

if not daily_rsl.empty:
    fig, ax = plt.subplots(figsize=(11, 5))
    ax.plot(daily_rsl.index, daily_rsl.values, color=COLORS["orange"], linewidth=2.5, label="Avg Cargo RSL (%) — in-transit only")
    ax.fill_between(daily_rsl.index, daily_rsl.values, alpha=0.15, color=COLORS["orange"])
    ax.axhline(50, color=COLORS["red"], linestyle="--", linewidth=1, label="50% RSL threshold")
    ax.set_title("Avg Cargo RSL While En-Route (Spoilage Risk) — 180-Day Run", fontsize=14, pad=12)
    ax.set_xlabel("Simulation Day")
    ax.set_ylabel("RSL (%)")
    ax.legend()
    ax.set_ylim(0, 105)
    ax.yaxis.set_major_formatter(mtick.PercentFormatter())
    fig.tight_layout()
    fig.savefig(os.path.join(OUTPUT_DIR, "chart_rsl_over_time.png"), dpi=150)
    plt.close()
    print("[CHART] chart_rsl_over_time.png  [OK]")

# ─────────────────────────────────────────────
# 6.  Chart 2: Avg Truck Fuel over time
# ─────────────────────────────────────────────
daily_fuel = trucks_tel.groupby("day")["fuel"].mean()

fig, ax = plt.subplots(figsize=(11, 5))
ax.plot(daily_fuel.index, daily_fuel.values, color=COLORS["blue"], linewidth=2.5, label="Avg Fleet Fuel (%)")
ax.fill_between(daily_fuel.index, daily_fuel.values, alpha=0.12, color=COLORS["blue"])
ax.axhline(20, color=COLORS["yellow"], linestyle="--", linewidth=1, label="EdgeBrain 20% alert threshold")
ax.set_title("Average Fleet Fuel Level Over 180-Day Run", fontsize=14, pad=12)
ax.set_xlabel("Simulation Day")
ax.set_ylabel("Fuel (%)")
ax.legend()
ax.set_ylim(0, 105)
ax.yaxis.set_major_formatter(mtick.PercentFormatter())
fig.tight_layout()
fig.savefig(os.path.join(OUTPUT_DIR, "chart_fuel_over_time.png"), dpi=150)
plt.close()
print("[CHART] chart_fuel_over_time.png  [OK]")

# ─────────────────────────────────────────────
# 7.  Chart 3: Warehouse Reorders + Restocks per day
# ─────────────────────────────────────────────
daily_reorders = wh_reorders.groupby("day").size().reindex(range(int(SIM_DAYS) + 1), fill_value=0)
daily_restocks = wh_restocks.groupby("day").size().reindex(range(int(SIM_DAYS) + 1), fill_value=0)

fig, ax = plt.subplots(figsize=(11, 5))
ax.bar(daily_reorders.index, daily_reorders.values, color=COLORS["red"],   alpha=0.8, label="Reorders Triggered")
ax.bar(daily_restocks.index, daily_restocks.values, color=COLORS["green"], alpha=0.7, label="Restocks Received", bottom=daily_reorders.values)
ax.set_title("Warehouse Supply Chain Activity Per Day", fontsize=14, pad=12)
ax.set_xlabel("Simulation Day")
ax.set_ylabel("Event Count")
ax.legend()
fig.tight_layout()
fig.savefig(os.path.join(OUTPUT_DIR, "chart_warehouse_activity.png"), dpi=150)
plt.close()
print("[CHART] chart_warehouse_activity.png  [OK]")

# ─────────────────────────────────────────────
# 8.  Chart 4: Road Accidents per day (traffic model health)
# ─────────────────────────────────────────────
daily_accidents = accidents.groupby("day").size().reindex(range(int(SIM_DAYS) + 1), fill_value=0)

fig, ax = plt.subplots(figsize=(11, 5))
ax.bar(daily_accidents.index, daily_accidents.values, color=COLORS["yellow"], alpha=0.85, label="Road Accidents")
ax.set_title("Simulated Road Accidents Per Day (Traffic Model)", fontsize=14, pad=12)
ax.set_xlabel("Simulation Day")
ax.set_ylabel("Accidents")
ax.legend()
fig.tight_layout()
fig.savefig(os.path.join(OUTPUT_DIR, "chart_accidents_per_day.png"), dpi=150)
plt.close()
print("[CHART] chart_accidents_per_day.png  [OK]")

# ─────────────────────────────────────────────
# 9.  Chart 5: PPO Rerouting Activity (AI health)
# ─────────────────────────────────────────────
daily_reroutes = route_changed.groupby("day").size().reindex(range(int(SIM_DAYS) + 1), fill_value=0)

fig, ax = plt.subplots(figsize=(11, 5))
ax.plot(daily_reroutes.index, daily_reroutes.values, color=COLORS["purple"], linewidth=2.5, label="PPO Rerouting Decisions")
ax.fill_between(daily_reroutes.index, daily_reroutes.values, alpha=0.12, color=COLORS["purple"])
ax.set_title("Central Brain (PPO) Rerouting Decisions Per Day", fontsize=14, pad=12)
ax.set_xlabel("Simulation Day")
ax.set_ylabel("Rerouting Actions")
ax.legend()
fig.tight_layout()
fig.savefig(os.path.join(OUTPUT_DIR, "chart_ppo_activity.png"), dpi=150)
plt.close()
print("[CHART] chart_ppo_activity.png  [OK]")

# ─────────────────────────────────────────────
# 10. Chart 6: Accident severity pie (traffic quality)
# ─────────────────────────────────────────────
if not accidents.empty:
    severities = [p.get("severity", "unknown") for p in accidents["payload"]]
    sev_counts = Counter(severities)
    sev_labels = list(sev_counts.keys())
    sev_vals   = list(sev_counts.values())
    sev_colors = [COLORS["yellow"], COLORS["orange"], COLORS["red"], "#adb5bd"][:len(sev_labels)]

    fig, ax = plt.subplots(figsize=(7, 7))
    wedges, texts, autotexts = ax.pie(
        sev_vals, labels=sev_labels, autopct="%1.1f%%",
        colors=sev_colors, startangle=90,
        wedgeprops={"edgecolor": "#1a1b1e"}
    )
    for t in autotexts:
        t.set_color("white")
    ax.set_title("Road Accident Severity Distribution", fontsize=14)
    fig.tight_layout()
    fig.savefig(os.path.join(OUTPUT_DIR, "chart_accident_severity.png"), dpi=150)
    plt.close()
    print("[CHART] chart_accident_severity.png  [OK]")

print()
print("=" * 60)
print("  All charts saved to:", OUTPUT_DIR)
print("=" * 60)
