import pandas as pd
import numpy as np
import os
from datetime import datetime, timedelta

def generate_traffic_proxy():
    """
    Generates hourly traffic volume proxy based on Bangalore patterns.
    - Morning Peak: 10 AM (Office/Market)
    - Evening Peak: 7 PM (Return)
    - Scale: 0.6x (Nagpur vs Bangalore density)
    """
    print("Generating Traffic Proxy (Bangalore Profile)...")
    dates = pd.date_range(start='2023-01-01', end='2023-12-31 23:00:00', freq='H')
    
    traffic_data = []
    
    # Base pattern (Bangalore typical)
    hourly_weights = {
        0: 0.1, 1: 0.05, 2: 0.05, 3: 0.05, 4: 0.1, 5: 0.3,
        6: 0.8, 7: 1.2, 8: 1.8, 9: 2.2, 10: 2.5, 11: 2.0,  # 10am Peak
        12: 1.8, 13: 1.6, 14: 1.7, 15: 1.9, 16: 2.2,
        17: 2.6, 18: 2.8, 19: 3.0, 20: 2.4, 21: 1.8, # 7pm Peak
        22: 1.2, 23: 0.8
    }
    
    base_volume = 1200 # vehicles per hour
    nagpur_factor = 0.6
    
    for dt in dates:
        hour = dt.hour
        is_weekend = dt.weekday() >= 5
        
        # Base demand
        demand = base_volume * hourly_weights.get(hour, 1.0)
        
        # Weekend reduction
        if is_weekend:
            demand *= 0.7
            
        # Add random noise (stochasticity)
        noise = np.random.normal(1.0, 0.1) # 10% variation
        
        final_volume = int(demand * nagpur_factor * noise)
        
        traffic_data.append({
            'timestamp': dt,
            'road_id': 'generic_urban_arterial',
            'volume_count': final_volume,
            'occupancy_percent': min(100, (final_volume / (2000 * nagpur_factor)) * 100)
        })
        
    df = pd.DataFrame(traffic_data)
    os.makedirs('data/processed/traffic', exist_ok=True)
    output_path = 'data/processed/traffic/nagpur_traffic_proxy.parquet'
    df.to_parquet(output_path)
    print(f"Saved Traffic Proxy: {output_path} ({len(df)} rows)")

def generate_market_proxy():
    """
    Generates daily market price proxy based on Agmarknet seasonal trends.
    - Peak Season (Dec-Feb): Low Prices
    - Off Season (Summer): High Prices
    """
    print("Generating Market Proxy (Agmarknet Seasonal Trend)...")
    dates = pd.date_range(start='2023-01-01', end='2023-12-31', freq='D')
    
    market_data = []
    
    for dt in dates:
        month = dt.month
        
        # Seasonal Price Curve (Rs/Quintal)
        if month in [12, 1, 2]: # Peak Season (Winter)
            base_price = 2500 
        elif month in [3, 4, 10, 11]: # Shoulder
            base_price = 4500
        else: # Off Season (Summer/Monsoon)
            base_price = 8000
            
        # Daily fluctuation
        price = int(np.random.normal(base_price, 200))
        arrival_tonnes = int(500000 / price * 50) # Inverse relationship approx
        
        market_data.append({
            'date': dt,
            'commodity': 'Orange',
            'market': 'Nagpur_Mandi',
            'modal_price_rs_quintal': price,
            'arrival_tonnes': arrival_tonnes
        })
        
    df = pd.DataFrame(market_data)
    os.makedirs('data/processed/market', exist_ok=True)
    output_path = 'data/processed/market/nagpur_market_proxy.parquet'
    df.to_parquet(output_path)
    print(f"Saved Market Proxy: {output_path} ({len(df)} rows)")

def generate_spoilage_proxy():
    """
    Generates shelf-life training dataset based on Arrhenius Logic.
    Mimics 'IoT Fruit Spoilage Dataset'.
    """
    print("Generating Spoilage Proxy (Arrhenius Logic)...")
    
    # Generate random conditions
    n_samples = 10000
    temps = np.random.uniform(5, 45, n_samples) # 5C to 45C
    humidities = np.random.uniform(30, 95, n_samples) # 30% to 95%
    days = np.random.uniform(1, 20, n_samples) # Storage duration
    
    spoilage_data = []
    
    for t, h, d in zip(temps, humidities, days):
        # Physics model: Rate increases 2x every 10C
        # Rate = base * 2^((T-20)/10)
        decay_rate = 0.1 * (2 ** ((t - 20) / 10))
        
        # Humidity penalty (high humidity = faster fungal growth)
        if h > 85: decay_rate *= 1.5
        
        total_decay = decay_rate * d
        
        # Classify
        quality_score = max(0, 100 - (total_decay * 10))
        is_spoiled = 1 if quality_score < 60 else 0
        quality_label = 'Bad' if is_spoiled else 'Good'
        
        spoilage_data.append({
            'temperature_c': round(t, 1),
            'humidity_percent': round(h, 1),
            'storage_days': round(d, 1),
            'quality_score': round(quality_score, 1),
            'quality_label': quality_label
        })
        
    df = pd.DataFrame(spoilage_data)
    os.makedirs('data/processed/quality', exist_ok=True)
    output_path = 'data/processed/quality/fruit_spoilage_proxy.parquet'
    df.to_parquet(output_path)
    print(f"Saved Spoilage Proxy: {output_path} ({len(df)} rows)")

if __name__ == "__main__":
    generate_traffic_proxy()
    generate_market_proxy()
    generate_spoilage_proxy()
    print("All Proxy Datasets Generated Successfully!")
