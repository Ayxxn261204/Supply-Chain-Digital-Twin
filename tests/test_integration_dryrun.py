import sys
import os
import logging
from datetime import datetime, timedelta

# Setup paths
sys.path.append(os.getcwd())

# Configuration logging
logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
logger = logging.getLogger("IntegrationVerifier")

def verify_integration():
    print(f"\n{'='*50}")
    print("🧪 STARTING INTEGRATION VERIFICATION")
    print(f"{'='*50}")

    errors = []

    # 1. Verify Traffic Loader
    try:
        print("\n[1/3] Testing Traffic Loader...")
        from src.data.loaders import TrafficLoader
        loader = TrafficLoader()
        
        # Test Query
        test_time = datetime(2023, 1, 15, 9, 30) # 9:30 AM (Rush Hour)
        data = loader.get_traffic_at_time(test_time)
        print(f"   ✅ Query 9:30 AM: {data}")
        
        if data['volume_count'] < 100:
             errors.append("Traffic volume unreasonably low for 9:30 AM")
             
    except Exception as e:
        errors.append(f"Traffic Loader Failed: {e}")
        print(f"   ❌ FAILED: {e}")

    # 2. Verify Market Loader
    try:
        print("\n[2/3] Testing Market Loader...")
        from src.data.loaders import MarketLoader
        loader = MarketLoader()
        
        # Test Query
        test_date = datetime(2023, 5, 20) # May (Peak Summer)
        data = loader.get_market_data(test_date)
        print(f"   ✅ Query May 20: {data}")
        
        if data['price'] <= 0:
             errors.append("Market price is zero or negative")

    except Exception as e:
        errors.append(f"Market Loader Failed: {e}")
        print(f"   ❌ FAILED: {e}")

    # 3. Verify Spoilage Loader
    try:
        print("\n[3/3] Testing Spoilage Loader...")
        from src.data.loaders import SpoilageLoader
        loader = SpoilageLoader()
        
        # Test Query: High Temp (35C) High Humidity (80%) -> Fast Decay
        rate_fast = loader.get_quality_decay_rate(35.0, 80.0)
        
        # Test Query: Low Temp (4C) -> Slow Decay
        rate_slow = loader.get_quality_decay_rate(4.0, 90.0)
        
        print(f"   ✅ 35C/80% Rate: {rate_fast:.4f} %/hr")
        print(f"   ✅ 4C/90% Rate:  {rate_slow:.4f} %/hr")
        
        if rate_fast <= rate_slow:
            errors.append("Spoilage logic flaw: 35C should decay faster than 4C")
            
    except Exception as e:
        errors.append(f"Spoilage Loader Failed: {e}")
        print(f"   ❌ FAILED: {e}")

    # 4. Check Config File for Keys
    print("\n[4/4] Checking Config YAML...")
    try:
        with open("config/simulation_config.yaml", "r") as f:
            content = f.read()
            if "market:" not in content:
                print("   ⚠️  Warning: 'market' section missing in yaml (Sim will use defaults)")
            if "dataset_path" not in content:
                errors.append("Config missing dataset_path entries")
            print("   ✅ Config check passed (basic existence)")
    except Exception as e:
        errors.append(f"Config Check Failed: {e}")

    print(f"\n{'='*50}")
    if not errors:
        print("🎉 SUCCESS: All Systems Data-Ready!")
    else:
        print("⚠️  VERIFICATION COMPLETED WITH ERRORS:")
        for err in errors:
            print(f"   - {err}")
    print(f"{'='*50}\n")

if __name__ == "__main__":
    verify_integration()
