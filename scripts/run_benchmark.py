"""
Sequential benchmark runner.
Calls main.py twice as independent processes - identical to running manually.
  Run 1: Baseline (RL disabled)
  Run 2: RL Enabled

Results are saved automatically by the engine to data/logs/telemetry/
Run analyze_longterm_sim.py on each telemetry file afterward to compare.
"""
import subprocess
import sys
import time
import yaml
import copy
from pathlib import Path

CONFIG_PATH = Path("config/simulation_config.yaml")
ROOT = Path(__file__).resolve().parent.parent

def load_yaml():
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)

def save_yaml(data):
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        yaml.dump(data, f, default_flow_style=False, allow_unicode=True)

def run_phase(label, rl_enabled):
    print(f"\n{'='*60}")
    print(f"  PHASE: {label}")
    print(f"  RL routing: {'ENABLED' if rl_enabled else 'DISABLED'}")
    print(f"  Duration: 365 days | Seed: 42 | Start: 2023-01-01")
    print(f"{'='*60}\n")

    # Patch config for this run
    cfg = load_yaml()
    cfg.setdefault("ai", {}).setdefault("optimization", {})["enabled"] = rl_enabled
    if rl_enabled:
        cfg["ai"]["optimization"]["inference_only"] = True  # Don't train during benchmark
    save_yaml(cfg)

    t0 = time.time()
    result = subprocess.run(
        [
            sys.executable, "main.py",
            "--headless",
            "--duration-days", "365",
            "--seed", "42",
            "--start-date", "2023-01-01",
        ],
        cwd=str(ROOT),
    )
    elapsed = time.time() - t0
    hours = int(elapsed // 3600)
    mins = int((elapsed % 3600) // 60)
    print(f"\n  {label} finished in {hours}h {mins}m (exit code: {result.returncode})")
    return result.returncode

def main():
    print("\nBENCHMARK RUNNER STARTING")
    print("Results saved to data/logs/telemetry/ - run analyze_longterm_sim.py to compare\n")

    # Run 1: Baseline
    rc1 = run_phase("BASELINE (Heuristic A*)", rl_enabled=False)

    # Run 2: RL Enabled
    rc2 = run_phase("RL-ENABLED (PPO)", rl_enabled=True)

    # Restore config to RL-enabled (default state)
    cfg = load_yaml()
    cfg["ai"]["optimization"]["enabled"] = True
    cfg["ai"]["optimization"]["inference_only"] = False
    save_yaml(cfg)

    print(f"\n{'='*60}")
    print("  BOTH PHASES COMPLETE")
    print(f"  Phase 1 exit: {rc1} | Phase 2 exit: {rc2}")
    print("  Now run: python scripts/analyze_longterm_sim.py")
    print(f"{'='*60}\n")

if __name__ == "__main__":
    main()
