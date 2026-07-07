"""
Weather data preprocessing module.

Converts Open-Meteo historical weather data to simulation format.
"""

import pandas as pd
import numpy as np
from pathlib import Path
from typing import Dict, Optional
from datetime import datetime


class WeatherPreprocessor:
    """
    Preprocess Open-Meteo weather data for simulation use.
    
    Maps weather codes to simulation taxonomy and prepares
    data for efficient time-based queries.
    """
    
    # Open-Meteo WMO Weather codes to simulation states mapping
    # Reference: https://open-meteo.com/en/docs
    WEATHER_CODE_MAPPING = {
        0: 'clear',                    # Clear sky
        1: 'partly_cloudy',            # Mainly clear
        2: 'partly_cloudy',            # Partly cloudy
        3: 'cloudy',                   # Overcast
        45: 'fog',                     # Fog
        48: 'fog',                     # Depositing rime fog
        51: 'light_rain',              # Drizzle: Light
        53: 'light_rain',              # Drizzle: Moderate
        55: 'rain',                    # Drizzle: Dense
        56: 'light_rain',              # Freezing Drizzle: Light
        57: 'rain',                    # Freezing Drizzle: Dense
        61: 'light_rain',              # Rain: Slight
        63: 'rain',                    # Rain: Moderate
        65: 'heavy_rain',              # Rain: Heavy
        66: 'rain',                    # Freezing Rain: Light
        67: 'heavy_rain',              # Freezing Rain: Heavy
        71: 'light_rain',              # Snow fall: Slight
        73: 'rain',                    # Snow fall: Moderate
        75: 'heavy_rain',              # Snow fall: Heavy
        77: 'rain',                    # Snow grains
        80: 'light_rain',              # Rain showers: Slight
        81: 'rain',                    # Rain showers: Moderate
        82: 'heavy_rain',              # Rain showers: Violent
        85: 'rain',                    # Snow showers: Slight
        86: 'heavy_rain',              # Snow showers: Heavy
        95: 'heavy_rain',              # Thunderstorm: Slight or moderate
        96: 'heavy_rain',              # Thunderstorm with slight hail
        99: 'heavy_rain',              # Thunderstorm with heavy hail
    }
    
    def __init__(self):
        """Initialize preprocessor."""
        pass
    
    def map_weather_code(self, code: int) -> str:
        """
        Map Open-Meteo weather code to simulation weather state.
        
        Args:
            code: WMO weather code
            
        Returns:
            Weather state string (clear, rain, fog, etc.)
        """
        return self.WEATHER_CODE_MAPPING.get(code, 'clear')
    
    def load_raw_csv(self, csv_path: str) -> pd.DataFrame:
        """
        Load raw Open-Meteo CSV file.
        
        Args:
            csv_path: Path to CSV file
            
        Returns:
            DataFrame with raw weather data
        """
        # Skip the first 2 rows (metadata) and load data
        df = pd.read_csv(csv_path, skiprows=2)
        
        print(f"? Loaded {len(df)} weather records from {csv_path}")
        return df
    
    def preprocess(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Preprocess weather dataframe for simulation.
        
        Args:
            df: Raw weather dataframe
            
        Returns:
            Preprocessed dataframe
        """
        # Make a copy
        processed = df.copy()
        
        # Parse timestamps
        processed['time'] = pd.to_datetime(processed['time'])
        processed['month'] = processed['time'].dt.month
        processed['hour'] = processed['time'].dt.hour
        processed['day_of_year'] = processed['time'].dt.dayofyear
        
        # Clean column names (remove units from column names)
        column_mapping = {
            'temperature_2m (?C)': 'temperature_celsius',
            'relative_humidity_2m (%)': 'humidity_percent',
            'precipitation (mm)': 'precipitation_mm',
            'rain (mm)': 'rain_mm',
            'weather_code (wmo code)': 'weather_code'
        }
        
        for old_name, new_name in column_mapping.items():
            if old_name in processed.columns:
                processed = processed.rename(columns={old_name: new_name})
        
        # Map weather codes to simulation states
        processed['weather_state'] = processed['weather_code'].apply(self.map_weather_code)
        
        # Add timestamp in minutes (for simulation queries)
        # Simulation starts at day 0, hour 0
        processed['simulation_minutes'] = (
            processed['day_of_year'] * 24 * 60 + 
            processed['hour'] * 60
        )
        
        # Sort by time
        processed = processed.sort_values('time').reset_index(drop=True)
        
        print(f"? Preprocessed {len(processed)} records")
        print(f"  - Date range: {processed['time'].min()} to {processed['time'].max()}")
        print(f"  - Weather states: {processed['weather_state'].value_counts().to_dict()}")
        
        return processed
    
    def save_processed(self, df: pd.DataFrame, output_path: str):
        """
        Save processed data to Parquet format.
        
        Args:
            df: Processed dataframe
            output_path: Path to save Parquet file
        """
        # Ensure output directory exists
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        
        # Save as Parquet (compressed)
        df.to_parquet(output_path, compression='gzip', index=False)
        
        file_size = Path(output_path).stat().st_size / 1024  # KB
        print(f"? Saved processed data to {output_path} ({file_size:.1f} KB)")
    
    def process_file(self, input_csv: str, output_parquet: str) -> pd.DataFrame:
        """
        Complete preprocessing pipeline: load, process, save.
        
        Args:
            input_csv: Input CSV file path
            output_parquet: Output Parquet file path
            
        Returns:
            Processed dataframe
        """
        print(f"\n{'='*60}")
        print(f"WEATHER DATA PREPROCESSING")
        print(f"{'='*60}\n")
        
        # Load
        df = self.load_raw_csv(input_csv)
        
        # Preprocess
        processed = self.preprocess(df)
        
        # Save
        self.save_processed(processed, output_parquet)
        
        print(f"\n{'='*60}")
        print(f"PREPROCESSING COMPLETE!")
        print(f"{'='*60}\n")
        
        return processed


def main():
    """Preprocess weather data."""
    # Paths
    input_csv = "data/raw/weather/nagpur_2023.csv"
    output_parquet = "data/processed/weather/nagpur_2023.parquet"
    
    # Process
    preprocessor = WeatherPreprocessor()
    df = preprocessor.process_file(input_csv, output_parquet)
    
    # Display sample
    print("\nSample processed data:")
    print(df[['time', 'temperature_celsius', 'humidity_percent', 
               'weather_state', 'simulation_minutes']].head(10))


if __name__ == '__main__':
    main()