from __future__ import annotations
import random
import math
import numpy as np
from typing import Dict, Any, List, Optional, Tuple
import logging
from pathlib import Path
from src.data.loaders import WeatherDataLoader
from .entities import Accident

# --- Merged from disruption_model.py ---
"""Disruption model for generating accidents probabilistically."""

logger = logging.getLogger(__name__)

class DisruptionModel:
    """
    Generates traffic accidents probabilistically based on research-backed rates.
    """
    
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        accident_config = config.get('disruptions', {}).get('accidents', {})
        
        # Base accident rates per million vehicle-kilometers by road type
        self.base_rates = accident_config.get('base_rate_per_million_vkm', {})
        self.time_multipliers = accident_config.get('time_multipliers', {})
        self.weather_multipliers = accident_config.get('weather_multipliers', {})
        self.density_effect_enabled = accident_config.get('density_effect_enabled', True)
        
        # Duration parameters
        self.duration_mean = accident_config.get('duration_mean', 45)
        self.duration_std = accident_config.get('duration_std', 20)
        self.duration_min = accident_config.get('duration_min', 15)
        self.duration_max = accident_config.get('duration_max', 180)
        
        # Severity distribution
        severity_dist = accident_config.get('severity_distribution', {})
        self.severity_probs = {
            'minor': severity_dist.get('minor', 0.70),
            'moderate': severity_dist.get('moderate', 0.25),
            'severe': severity_dist.get('severe', 0.05)
        }
        self.severity_duration_mult = accident_config.get('severity_duration_multiplier', {})
        self.accident_counter = 0
        
        logger.info(f"[DISRUPTION] DisruptionModel initialized")

    def generate_accidents(self, current_time: float, road_network, 
                          weather: str = 'clear', 
                          time_step_minutes: float = 1.0,
                          active_truck_segments: set = None,
                          active_trucks: List = None) -> List[Accident]:
        accidents = []
        hour = int((current_time / 60) % 24)
        if 6 <= hour < 18:
            time_category = 'day'
        elif 18 <= hour < 22:
            time_category = 'evening'
        else:
            time_category = 'night'
        
        time_mult = self.time_multipliers.get(time_category, 1.0)
        weather_mult = self.weather_multipliers.get(weather, 1.0)
        
        if active_truck_segments:
            segments_to_check = [
                road_network.get_segment(seg_id) 
                for seg_id in active_truck_segments 
                if seg_id and road_network.get_segment(seg_id) is not None
            ]
        else:
            segments_to_check = road_network.segments.values()
        
        # 1. Background Accidents
        for segment in segments_to_check:
            if segment is None or segment.is_blocked:
                continue
            prob = self._calculate_accident_probability(segment, time_mult, weather_mult, time_step_minutes)
            if random.random() < prob:
                accidents.append(self._create_accident(segment, current_time))
        
        # 2. Targeted Truck Accidents
        if active_trucks:
            for truck in active_trucks:
                if truck.status != "in_transit" or not truck.current_route:
                    continue
                # Calibrated strictly to constrain total simulation accidents to ~3.4/day
                risk_prob = (truck.risk_points / 20000.0) ** 2.0
                risk_prob = min(0.00002, risk_prob)
                
                if random.random() < risk_prob:
                    segments = truck.current_route.get('segment_objects', [])
                    if truck.route_progress < len(segments):
                        segment = segments[truck.route_progress]
                        if not segment.is_blocked:
                            # Phase 3 FIX: Use probabilistic severity and correct CTOR
                            accident = self._create_accident(segment, current_time, involved_truck_id=truck.truck_id)
                            accidents.append(accident)
                            logger.warning(f"[ACCIDENT] Truck {truck.truck_id} crashed due to high risk")
                            truck.risk_points = 0.0
        return accidents

    def _calculate_accident_probability(self, segment, time_mult: float, weather_mult: float, time_step_minutes: float) -> float:
        base_rate = self.base_rates.get(segment.road_type, 1.5)
        rate = base_rate * time_mult * weather_mult
        if self.density_effect_enabled and segment.current_traffic_density > 0:
            jam_density = self.config.get('traffic', {}).get('jam_density_per_lane', {}).get(segment.road_type, 100)
            density_ratio = segment.current_traffic_density / jam_density if jam_density > 0 else 0
            rate *= (0.5 + 1.5 * density_ratio)
        
        if segment.current_speed_kmh > 0:
            time_hours = time_step_minutes / 60.0
            vehicle_km = (segment.current_traffic_density * segment.current_speed_kmh * segment.lanes * segment.length_km * time_hours)
            probability = (rate / 1_000_000) * vehicle_km
            return min(0.01, max(0.0, probability))
        return 0.0

    def _create_accident(self, segment, current_time: float, severity: Optional[str] = None, involved_truck_id: Optional[str] = None) -> Accident:
        self.accident_counter += 1
        final_severity = severity if severity else self._sample_severity()
        base_duration = np.random.normal(self.duration_mean, self.duration_std)
        base_duration = max(self.duration_min, min(self.duration_max, base_duration))
        duration = base_duration * self.severity_duration_mult.get(final_severity, 1.0)
        return Accident(
            accident_id=f"ACC{self.accident_counter:04d}",
            segment_id=segment.segment_id,
            start_time=current_time,
            duration_minutes=duration,
            severity=final_severity,
            involved_truck_id=involved_truck_id
        )

    def _sample_severity(self) -> str:
        rand = random.random()
        cumulative = 0.0
        for severity, prob in self.severity_probs.items():
            cumulative += prob
            if rand < cumulative:
                return severity
        return 'minor'

# --- Merged from weather_model.py ---
class WeatherModel:
    MONTHLY_TEMPS = {1:(13,28), 2:(15,32), 3:(20,36), 4:(24,40), 5:(28,42), 6:(26,38), 7:(24,32), 8:(23,30), 9:(22,31), 10:(20,32), 11:(15,30), 12:(12,28)}
    MONTHLY_HUMIDITY = {1:(40,70), 2:(30,60), 3:(20,45), 4:(20,40), 5:(25,45), 6:(50,75), 7:(75,90), 8:(80,92), 9:(70,90), 10:(55,80), 11:(50,75), 12:(45,75)}
    
    def __init__(self, config: Dict, use_real_data=False, dataset_path: Optional[str] = None):
        self.config = config.get('weather', {})
        self.use_real_data = use_real_data
        self.dataset_path = dataset_path
        self.weather_loader = None
        self.default_probs = self.config.get('state_probabilities', {'clear': 0.70, 'light_rain': 0.15, 'rain': 0.10, 'heavy_rain': 0.04, 'fog': 0.01})
        self.state_durations = {s: h * 60 for s, h in self.config.get('average_duration_hours', {}).items()}
        if not self.state_durations:
            self.state_durations = {'clear': 720, 'partly_cloudy': 480, 'cloudy': 360, 'light_rain': 180, 'rain': 240, 'heavy_rain': 120, 'fog': 180}
        self.current_state = 'clear'
        self.state_start_time = 0
        self.state_duration = self.state_durations['clear']
        if self.use_real_data: self._load_weather_dataset()

    def _load_weather_dataset(self):
        try:
            if not self.dataset_path: self.dataset_path = "data/processed/weather/nagpur_2023.parquet"
            if not Path(self.dataset_path).exists():
                self.use_real_data = False
                return
            self.weather_loader = WeatherDataLoader(self.dataset_path)
            self.weather_loader.load()
        except: self.use_real_data = False

    def update(self, current_time: float, month: int = 1, hour: float = 12.0) -> List[Dict]:
        events = []
        if self.use_real_data and self.weather_loader:
            weather_data = self.weather_loader.get_weather_at_time(current_time)
            new_state = weather_data['state']
            if new_state != self.current_state:
                events.append({'type': 'weather_change', 'time': current_time, 'old_state': self.current_state, 'new_state': new_state, 'data_source': 'real_data'})
                self.current_state = new_state
            return events
        
        if current_time - self.state_start_time >= self.state_duration:
            event = self._change_weather(current_time, month, hour)
            if event: events.append(event)
        return events

    def _change_weather(self, current_time: float, month: int, hour: float) -> Optional[Dict]:
        old_state = self.current_state
        probs = self.default_probs.copy()
        
        if month in [6, 7, 8, 9]: # Monsoon
            probs.update({'clear':0.1, 'light_rain':0.3, 'rain':0.25, 'heavy_rain':0.14})
        elif month in [11, 12, 1, 2]: # Winter
            if 4 <= hour <= 8: probs['fog'] = 0.4
        
        available = [s for s, w in probs.items() if w > 0]
        if not available: return None
        
        self.current_state = random.choices(available, weights=[probs[s] for s in available])[0]
        self.state_duration = self.state_durations.get(self.current_state, 180) * random.uniform(0.7, 1.3)
        self.state_start_time = current_time
        
        if self.current_state == old_state: return None
        return {'type': 'weather_change', 'time': current_time, 'old_state': old_state, 'new_state': self.current_state, 'duration_minutes': self.state_duration}

    def get_temperature(self, month: int, hour: float, current_time: Optional[float] = None) -> float:
        if self.use_real_data and self.weather_loader and current_time is not None:
            return self.weather_loader.get_weather_at_time(current_time)['temperature']
        low, high = self.MONTHLY_TEMPS.get(month, (20, 30))
        diurnal = math.cos((hour - 14) * 2 * math.pi / 24)
        cooling = {'rain':-3.0, 'light_rain':-3.0, 'heavy_rain':-5.0, 'fog':-2.0}.get(self.current_state, 0.0)
        return ((high+low)/2.0) + (((high-low)/2.0) * diurnal) + cooling

    def get_humidity(self, month: int, hour: float, current_time: Optional[float] = None) -> float:
        if self.use_real_data and self.weather_loader and current_time is not None:
            return self.weather_loader.get_weather_at_time(current_time)['humidity']
        min_h, max_h = self.MONTHLY_HUMIDITY.get(month, (40, 70))
        diurnal = -math.cos((hour - 17) * 2 * math.pi / 24)
        humidity = ((min_h+max_h)/2.0) + (((max_h-min_h)/2.0) * diurnal)
        boost = {'rain':15, 'light_rain':15, 'heavy_rain':25, 'fog':40}.get(self.current_state, 0.0)
        return min(100.0, max(10.0, humidity + boost))
