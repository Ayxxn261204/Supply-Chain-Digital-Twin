from __future__ import annotations

import bisect
import logging
from datetime import datetime, timedelta
from typing import Dict, Optional

import numpy as np
import pandas as pd



# --- Merged from market_loader.py ---

logger = logging.getLogger(__name__)

class MarketLoader:
    _instance = None
    _data = None
    
    def __new__(cls, *args, **kwargs):
        if not cls._instance:
            cls._instance = super(MarketLoader, cls).__new__(cls)
        return cls._instance

    def __init__(self, dataset_path: str = "data/processed/market/nagpur_market_proxy.parquet"):
        if MarketLoader._data is None:
            self.dataset_path = dataset_path
            self._load_data()
            
    def _load_data(self):
        try:
            logger.info(f"Loading market dataset from {self.dataset_path}")
            df = pd.read_parquet(self.dataset_path)
            
            # Ensure date is datetime index
            if 'date' in df.columns:
                df['date'] = pd.to_datetime(df['date'])
                df = df.set_index('date')
                df = df.sort_index()
            
            MarketLoader._data = df
            logger.info(f"Successfully loaded {len(df)} market records")
        except Exception as e:
            logger.error(f"Failed to load market dataset: {e}")
            MarketLoader._data = pd.DataFrame()

    def get_market_data(self, date: datetime, config: Optional[Dict] = None) -> Dict:
        """
        Get market price/arrivals for a specific date.
        
        Args:
            date: Target datetime
            config: Optional simulation config to get fallbacks
        """
        # Get fallbacks from config or use legacy defaults
        market_cfg = config.get('market_data', {}) if config else {}
        fallback_price = market_cfg.get('default_price_rs_quintal', 3500)
        fallback_arrivals = market_cfg.get('default_arrival_tonnes', 50)

        if MarketLoader._data is None or MarketLoader._data.empty:
            return {'price': float(fallback_price), 'arrivals': float(fallback_arrivals)}
            
        try:
            target_date = date.replace(hour=0, minute=0, second=0, microsecond=0)
            
            if target_date in MarketLoader._data.index:
                row = MarketLoader._data.loc[target_date]
                if isinstance(row, pd.DataFrame):
                    row = row.iloc[0]
                return {
                    'price': float(row['modal_price_rs_quintal']),
                    'arrivals': float(row['arrival_tonnes'])
                }
            else:
                # Fallback: Nearest date (handles year wrap roughly)
                available_year = MarketLoader._data.index[0].year
                mapped_date = target_date.replace(year=available_year)
                
                idx = MarketLoader._data.index.get_indexer([mapped_date], method='nearest')[0]
                row = MarketLoader._data.iloc[idx]
                return {
                    'price': float(row['modal_price_rs_quintal']),
                    'arrivals': float(row['arrival_tonnes'])
                }
                
        except Exception as e:
            logger.warning(f"Error querying market data: {e}")
            return {'price': float(fallback_price), 'arrivals': float(fallback_arrivals)}


# --- Merged from spoilage_loader.py ---

logger = logging.getLogger(__name__)

class SpoilageLoader:
    _instance = None
    _data = None
    
    def __new__(cls, *args, **kwargs):
        if not cls._instance:
            cls._instance = super(SpoilageLoader, cls).__new__(cls)
        return cls._instance

    def __init__(self, dataset_path: str = "data/processed/quality/fruit_spoilage_proxy.parquet"):
        if SpoilageLoader._data is None:
            self.dataset_path = dataset_path
            self._load_data()
            
    def _load_data(self):
        try:
            logger.info(f"Loading spoilage dataset from {self.dataset_path}")
            df = pd.read_parquet(self.dataset_path)
            # Create a lookup key roughly rounded to nearest integer for fast O(1) access
            # This avoids complex nearest-neighbor search on every tick
            df['key'] = df.apply(lambda row: f"{int(round(row['temperature_c']))}_{int(round(row['humidity_percent']))}", axis=1)
            
            # Group by key and avg quality score to handle collisions 
            self.lookup_table = df.groupby('key')['quality_score'].mean().to_dict()
            
            SpoilageLoader._data = df # Keep raw just in case
            logger.info(f"Successfully loaded {len(df)} spoilage records into lookup table")
        except Exception as e:
            logger.error(f"Failed to load spoilage dataset: {e}")
            self.lookup_table = {}

    def get_quality_decay_rate(self, temp_c: float, humidity: float) -> float:
        """
        Returns estimated quality loss PER HOUR based on lookup.
        """
        if not hasattr(self, 'lookup_table') or not self.lookup_table:
            # Fallback to simple Arrhenius approximation if data missing
            return 0.1 * (2 ** ((temp_c - 20) / 10))
            
        try:
            # Create key
            key = f"{int(round(temp_c))}_{int(round(humidity))}"
            
            # Lookup quality score (0-100) after ~10 days avg in dataset
            # We need to reverse engineer a 'rate' from this static score
            # Score = 100 - (Rate * Days) -> Rate = (100 - Score) / Days
            # Using 10 days as standard normalization from our generator
            
            end_quality = self.lookup_table.get(key, None)
            
            if end_quality is None:
                 # Nearest Neighbor fallback (simple)
                 # If exact 35C_80% not found, try 35C_85% or 34C_80%
                 # For efficiency, just fallback to formula
                 return 0.1 * (2 ** ((temp_c - 20) / 10))
            
            # Calculate implied decay rate per day, then per hour
            total_loss = 100 - end_quality
            loss_per_day = total_loss / 10.0 # Normalized to 10 days
            loss_per_hour = loss_per_day / 24.0
            
            return max(0.01, loss_per_hour)
            
        except Exception as e:
            return 0.05


# --- Merged from traffic_loader.py ---

logger = logging.getLogger(__name__)

class TrafficLoader:
    _instance = None
    _data = None
    
    def __new__(cls, *args, **kwargs):
        if not cls._instance:
            cls._instance = super(TrafficLoader, cls).__new__(cls)
        return cls._instance

    def __init__(self, dataset_path: str = "data/processed/traffic/nagpur_traffic_proxy.parquet"):
        if TrafficLoader._data is None:
            self.dataset_path = dataset_path
            self._load_data()
            
    def _load_data(self):
        try:
            logger.info(f"Loading traffic dataset from {self.dataset_path}")
            df = pd.read_parquet(self.dataset_path)
            
            # Ensure timestamp is datetime index for fast lookup
            if 'timestamp' in df.columns:
                df['timestamp'] = pd.to_datetime(df['timestamp'])
                df = df.set_index('timestamp')
                df = df.sort_index()
                
            TrafficLoader._data = df
            logger.info(f"Successfully loaded {len(df)} traffic records")
        except Exception as e:
            logger.error(f"Failed to load traffic dataset: {e}")
            TrafficLoader._data = pd.DataFrame() # Empty fallback

    def get_traffic_at_time(self, timestamp: datetime) -> Dict:
        """
        Get traffic parameters for a specific datetime.
        Returns closest hourly record.
        """
        if TrafficLoader._data is None or TrafficLoader._data.empty:
            return {'volume_count': 0, 'occupancy_percent': 0}
            
        try:
            # Round to nearest hour
            target_hour = timestamp.replace(minute=0, second=0, microsecond=0)
            if timestamp.minute >= 30:
                target_hour += timedelta(hours=1)
                
            # Direct lookup if exact match exists
            if target_hour in TrafficLoader._data.index:
                row = TrafficLoader._data.loc[target_hour]
                # Handle case where multiple rows might exist for same time (unlikely but safe)
                if isinstance(row, pd.DataFrame):
                    row = row.iloc[0]
                return {
                    'volume_count': int(row['volume_count']),
                    'occupancy_percent': float(row['occupancy_percent'])
                }
            else:
                # If year mismatch (e.g. simulation runs into next year), map to available year
                # For proxy data 2023, we can modulo the year
                available_year = TrafficLoader._data.index[0].year
                mapped_time = target_hour.replace(year=available_year)
                
                # Nearest search
                idx = TrafficLoader._data.index.get_indexer([mapped_time], method='nearest')[0]
                row = TrafficLoader._data.iloc[idx]
                return {
                    'volume_count': int(row['volume_count']),
                    'occupancy_percent': float(row['occupancy_percent'])
                }
                
        except Exception as e:
            logger.warning(f"Error querying traffic data: {e}, returning default")
            return {'volume_count': 500, 'occupancy_percent': 25.0}


# --- Merged from weather_loader.py ---
"""
Weather data loader for simulation.

Provides efficient time-based queries for real weather data.
"""



class WeatherDataLoader:
    """
    Load and query preprocessed weather data for simulation.
    
    Supports efficient time-based queries with interpolation
    for missing timestamps.
    """
    
    def __init__(self, dataset_path: str):
        """
        Initialize loader with preprocessed weather dataset.
        
        Args:
            dataset_path: Path to preprocessed Parquet file
        """
        self.dataset_path = dataset_path
        self.df: Optional[pd.DataFrame] = None
        self.simulation_minutes: Optional[list] = None
        self.loaded = False
        
    def load(self):
        """Load weather dataset from Parquet file."""
        if self.loaded:
            return
        
        print(f"[WEATHER] Loading weather dataset from {self.dataset_path}")
        
        # Load Parquet file
        self.df = pd.read_parquet(self.dataset_path)
        
        # Extract simulation_minutes for fast lookups
        self.simulation_minutes = self.df['simulation_minutes'].tolist()
        
        self.loaded = True
        print(f"[OK] Loaded {len(self.df)} weather records")
        print(f"  - Time range: {self.df['simulation_minutes'].min():.0f} to {self.df['simulation_minutes'].max():.0f} minutes")
        print(f"  - Weather states: {sorted(self.df['weather_state'].unique())}")
    
    def get_weather_at_time(self, current_time: float) -> Dict:
        """
        Get weather data at specific simulation time.
        
        Uses linear interpolation for timestamps between data points.
        
        Args:
            current_time: Simulation time in minutes
            
        Returns:
            Dictionary with weather data
        """
        if not self.loaded:
            self.load()
        
        # Handle time wrapping (if simulation runs beyond 1 year)
        # Assuming 365 days = 525,600 minutes
        max_time = 365 * 24 * 60
        wrapped_time = current_time % max_time
        
        # Find nearest weather record using binary search
        idx = bisect.bisect_left(self.simulation_minutes, wrapped_time)
        
        # Handle edge cases
        if idx == 0:
            row = self.df.iloc[0]
        elif idx >= len(self.df):
            row = self.df.iloc[-1]
        else:
            # Interpolate between two nearest points
            before = self.df.iloc[idx - 1]
            after = self.df.iloc[idx]
            
            # Calculate interpolation weight
            time_before = before['simulation_minutes']
            time_after = after['simulation_minutes']
            weight = (wrapped_time - time_before) / (time_after - time_before)
            
            # Use the closest one for weather_state (discrete)
            # Interpolate temperature and humidity (continuous)
            if weight < 0.5:
                row = before
                temp = before['temperature_celsius']
                humidity = before['humidity_percent']
            else:
                row = after
                temp = after['temperature_celsius']
                humidity = after['humidity_percent']
            
            # Linear interpolation for continuous values
            temp = before['temperature_celsius'] * (1 - weight) + after['temperature_celsius'] * weight
            humidity = before['humidity_percent'] * (1 - weight) + after['humidity_percent'] * weight
            
            return {
                'state': row['weather_state'],
                'temperature': float(temp),
                'humidity': float(humidity),
                'precipitation': float(row['precipitation_mm']),
                'rain': float(row['rain_mm']),
                'month': int(row['month']),
                'hour': int(row['hour'])
            }
        
        # Exact match or edge case
        return {
            'state': row['weather_state'],
            'temperature': float(row['temperature_celsius']),
            'humidity': float(row['humidity_percent']),
            'precipitation': float(row['precipitation_mm']),
            'rain': float(row['rain_mm']),
            'month': int(row['month']),
            'hour': int(row['hour'])
        }
    
    def get_stats(self) -> Dict:
        """
        Get dataset statistics.
        
        Returns:
            Dictionary with statistics
        """
        if not self.loaded:
            self.load()
        
        return {
            'total_records': len(self.df),
            'date_range': {
                'start': str(self.df['time'].min()),
                'end': str(self.df['time'].max())
            },
            'temperature': {
                'min': float(self.df['temperature_celsius'].min()),
                'max': float(self.df['temperature_celsius'].max()),
                'mean': float(self.df['temperature_celsius'].mean())
            },
            'humidity': {
                'min': float(self.df['humidity_percent'].min()),
                'max': float(self.df['humidity_percent'].max()),
                'mean': float(self.df['humidity_percent'].mean())
            },
            'weather_states': self.df['weather_state'].value_counts().to_dict()
        }


# Singleton instance for lazy loading
_weather_loader: Optional[WeatherDataLoader] = None


def get_weather_loader(dataset_path: str = "data/processed/weather/nagpur_2023.parquet") -> WeatherDataLoader:
    """
    Get singleton weather data loader instance.
    
    Args:
        dataset_path: Path to preprocessed weather dataset
        
    Returns:
        WeatherDataLoader instance
    """
    global _weather_loader
    
    if _weather_loader is None:
        _weather_loader = WeatherDataLoader(dataset_path)
    
    return _weather_loader


def main():
    """Test weather data loader."""
    print("\n" + "="*60)
    print("TESTING WEATHER DATA LOADER")
    print("="*60 + "\n")
    
    # Create loader
    loader = WeatherDataLoader("data/processed/weather/nagpur_2023.parquet")
    loader.load()
    
    # Get stats
    print("\nDataset Statistics:")
    stats = loader.get_stats()
    print(f"  Total records: {stats['total_records']}")
    print(f"  Date range: {stats['date_range']['start']} to {stats['date_range']['end']}")
    print(f"  Temperature: {stats['temperature']['min']:.1f}?C to {stats['temperature']['max']:.1f}?C (avg: {stats['temperature']['mean']:.1f}?C)")
    print(f"  Humidity: {stats['humidity']['min']:.1f}% to {stats['humidity']['max']:.1f}% (avg: {stats['humidity']['mean']:.1f}%)")
    print(f"  Weather states distribution:")
    for state, count in sorted(stats['weather_states'].items()):
        print(f"    {state}: {count} records ({count/stats['total_records']*100:.1f}%)")
    
    # Test queries at various times
    print("\nSample Weather Queries:")
    test_times = [0, 60, 720, 1440, 10080]  # 0 min, 1 hour, 12 hours, 1 day, 1 week
    
    for time_min in test_times:
        weather = loader.get_weather_at_time(time_min)
        hours = time_min / 60
        days = time_min / 1440
        print(f"\n  Time: {time_min} min ({hours:.1f} hrs, {days:.1f} days)")
        print(f"    State: {weather['state']}")
        print(f"    Temp: {weather['temperature']:.1f}?C, Humidity: {weather['humidity']:.1f}%")
        print(f"    Month: {weather['month']}, Hour: {weather['hour']}")


if __name__ == '__main__':
    main()