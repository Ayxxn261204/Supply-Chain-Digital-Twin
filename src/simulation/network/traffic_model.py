"""
Linked Traffic Model (LTM) using Continuous Gaussian Waves and Ripple Propagation.

Simulates high-fidelity traffic density and speed based on:
- Location-Specific Zones (OFFICE, SHOPPING, RESIDENTIAL, HIGHWAY)
- Continuous Time Waves (Gaussian Mixture Models)
- Factor Linkage (Weather, Weekday, Seasonal shifts)
- Upstream Backpressure (Ripple Propagation for accidents/congestion)
"""

from typing import Dict, Any, List, Optional
import math
import logging
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

class TrafficModel:
    """
    High-Fidelity Linked Traffic Model (LTM).
    
    Density(s, t) = Base(s) * Wave(t, zone) * WeatherFactor * WeekDayFactor + Ripple(s)
    Speed(s, t) = FreeFlow * (1 - Density/JamDensity) * WeatherReduction * NightReduction
    """
    
    def __init__(self, config: Dict[str, Any]):
        """Initialize the Linked Traffic Model."""
        self.config = config
        self.traffic_config = config.get('traffic', {})
        
        # Physical Parameters
        self.jam_density_per_lane = self.traffic_config.get('jam_density_per_lane', {
            'motorway': 120, 'trunk': 110, 'primary': 100, 
            'secondary': 90, 'tertiary': 80, 'residential': 60
        })
        self.base_density_fraction = self.traffic_config.get('base_density_fraction', {
            'motorway': 0.15, 'trunk': 0.12, 'primary': 0.10,
            'secondary': 0.08, 'tertiary': 0.06, 'residential': 0.05
        })
        
        # Zone-Specific Peak Hour Coefficients (μ: Peak Hour, σ: Spread)
        self.office_peaks = [(9.0, 1.2), (18.5, 1.5)]  # Sharp peaks for commute
        self.shopping_peaks = [(19.5, 2.5)]            # Broad evening peak
        self.residential_peaks = [(8.5, 1.0), (19.0, 1.2)] # Morning exit, Evening return
        self.highway_peaks = [(14.0, 4.0)]             # Steady through-traffic
        
        # Factor Coupling constants
        self.sunday_multiplier = 1.3
        self.monsoon_sigma_stretch = 1.5 # Rains broaden the peak hours
        
        # Propagation State (Backpressure)
        self.current_ripples: Dict[str, float] = {} # segment_id -> density_ripple
        self.propagation_decay = 0.6               # Ripple decays 40% per hop
        
        # Environmental state
        self.current_weather = 'clear'
        self.current_month = 1 # Jan
        self.night_speed_reduction = self.traffic_config.get('night_speed_reduction', 0.90)
        
        # Performance Cache (segment_id -> RoadSegment) - Sync'd during update
        self._segment_cache: Dict[str, Any] = {}
        
        # Temporal Ripple Persistence (The 'Unwinding' effect)
        # 1.0 = persists forever, 0.0 = vanishes instantly
        self.persistence_factor = 0.85 # ~15% decay per tick (clears in ~15-20 min)
        
        # Seasonal trend multipliers (Jan=1, Dec=12)
        self.seasonal_multipliers = {
            1: 0.95, 2: 0.95, 3: 1.0, 4: 1.1, 
            5: 1.25, 6: 1.15, 7: 1.1, 8: 1.1, 
            9: 1.05, 10: 1.0, 11: 0.95, 12: 0.9
        }
        
        logger.info("[LTM] Linked Traffic Model initialized with Gaussian Engine")

    def set_environment(self, weather_state: str, month: int) -> None:
        """Set current environment state (called by Engine/WeatherModel)."""
        self.current_weather = weather_state
        self.current_month = month

    def initialize_network(self, road_network, current_time: float) -> None:
        """Initialize traffic for the entire network at start-up."""
        logger.info(f"Initializing LTM state for {len(road_network.segments)} segments...")
        self.update_all_segments(road_network, current_time, active_accidents=[])

    def get_traffic_density(self, segment, current_time_minutes: float) -> float:
        """Calculate MTI-based density for a segment with Lane-Aware Capacity."""
        hour = (current_time_minutes / 60.0) % 24
        
        # 1. Base Density from Road Type
        road_type = segment.road_type
        if isinstance(road_type, list): road_type = road_type[0]
        base_fraction = self.base_density_fraction.get(road_type, 0.1)
        
        # 2. Robust Weekday logic and Zone Multipliers
        # Use start_date (YYYY-MM-DD) from config — not start_time which doesn't exist.
        start_date_str = self.config.get('simulation', {}).get('start_date', '2024-01-01')
        try:
            start_dt = datetime.strptime(start_date_str, "%Y-%m-%d")
        except (ValueError, TypeError):
            start_dt = datetime(2024, 1, 1)
             
        current_dt = start_dt + timedelta(minutes=current_time_minutes)
        is_weekend = current_dt.weekday() >= 5 # 5=Sat, 6=Sun
        
        zone = segment.zone_type
        factor_mult = 1.0
        
        # Nagpur Realistic Weekend Patterns
        if is_weekend:
            if zone == 'OFFICE': factor_mult *= 0.3 # 70% drop on weekends
            elif zone == 'SHOPPING': factor_mult *= 1.4 # 40% boost (market days)
            elif zone == 'RESIDENTIAL': factor_mult *= 1.1 # Stay home boost
        
        # 3. Continuous Gaussian Wave (Location + Time)
        wave_val = self._calculate_continuous_wave(hour, zone, is_weekend)
        
        # 4. Factor Coupling (Weather + Seasonal)
        weather_inc = self.config.get('traffic', {}).get('weather_density_increase', {}).get(self.current_weather, 1.0)
        factor_mult *= weather_inc
        factor_mult *= self.seasonal_multipliers.get(self.current_month, 1.0)
        
        # 5. Ripple Pass (Backpressure)
        # SCIENTIFIC FIX: Standardized on segment_id (string)
        ripple = self.current_ripples.get(segment.segment_id, 0.0)
        
        # Final Density Score (MTI): Lane-Aware Physics
        density_fraction = base_fraction * wave_val * segment.zone_multiplier * factor_mult
        
        # Correctly multiply by number of lanes
        total_jam_density = self.jam_density_per_lane.get(road_type, 100) * max(1, segment.lanes)
        
        density = (density_fraction * total_jam_density) + ripple
        return max(0.1, min(density, total_jam_density * 0.98))

    def _calculate_continuous_wave(self, hour: float, zone: str, is_weekend: bool = False) -> float:
        """Calculate zone-specific Gaussian mixture wave."""
        
        # Nagpur Realistic Weekend: OFFICE zones have flat baseline traffic (no commute peaks)
        if is_weekend and zone == 'OFFICE':
            return 0.2  # Flat baseline
            
        peaks = []
        if zone == 'OFFICE': peaks = self.office_peaks
        elif zone == 'SHOPPING': peaks = self.shopping_peaks
        elif zone == 'RESIDENTIAL': peaks = self.residential_peaks
        elif zone == 'HIGHWAY': peaks = self.highway_peaks
        else: peaks = [(14.0, 4.0)]
        
        sigma_mult = 1.0
        if self.current_weather in ['rain', 'heavy_rain']:
            sigma_mult = self.monsoon_sigma_stretch
            
        total = 0.2 
        for mu, sigma in peaks:
            sigma_eff = sigma * sigma_mult
            diff = abs(hour - mu)
            diff = min(diff, 24 - diff)
            pulse = math.exp(-(diff**2) / (2 * sigma_eff**2))
            total = max(total, pulse * 1.5)
            
        return total

    def get_current_speed(self, segment, current_time: float) -> float:
        """Calculate speed using Parabolic Greenshields model (Scientific Plateau)."""
        density = segment.current_traffic_density
        road_type = segment.road_type
        if isinstance(road_type, list): road_type = road_type[0]
        
        total_jam_density = self.jam_density_per_lane.get(road_type, 100) * max(1, segment.lanes)
        
        # Normalized Density (0.0 to 1.0)
        k = density / total_jam_density if total_jam_density > 0 else 0
        
        # 1. Parabolic Plateau: Speed stays high until ~20% density
        # Formula: Speed = V_free * (1 - k^2) if k > 0.2 else V_free
        free_flow = segment.speed_limit_kmh
        if k < 0.2:
            speed = free_flow
        else:
            # Accelerated drop after critical density plateau
            speed = free_flow * (1.0 - (k**2))
        
        # 2. Weather & Night Reduction
        weather_red = self.config.get('traffic', {}).get('weather_speed_reduction', {}).get(self.current_weather, 1.0)
        speed *= weather_red
        
        hour = (current_time / 60.0) % 24
        if hour >= 22 or hour < 6:
            speed *= self.night_speed_reduction
            
        return max(2.5, min(speed, free_flow))

    def update_all_segments(self, road_network, current_time: float, active_accidents: Optional[List] = None) -> None:
        """
        Full-network traffic update: iterates all segments for density + BFS propagation.

        Used by initialize_network() at startup and kept for backward compatibility
        with existing tests. During normal simulation ticks, update_smart() is called
        instead (O(active_ripples) vs O(N_segments)).
        """
        # Step 0: Apply Temporal Decay
        for seg_id in list(self.current_ripples.keys()):
            self.current_ripples[seg_id] *= self.persistence_factor
            if self.current_ripples[seg_id] < 1.0:
                 self.current_ripples.pop(seg_id, None)
        
        # Step 1: Baseline pass
        new_ripple_sources: Dict[str, float] = {}
        # SAFETY: Ensure accidents list is iterable
        accidents_list = active_accidents if active_accidents is not None else []
        accident_map = {a.segment_id: a for a in accidents_list}
        
        # Rebuild segment cache for O(1) BFS lookups
        self._segment_cache = {str(s.segment_id): s for s in road_network.segments.values()}
        
        # IMPORTANT: Iterate segment items (keys are Tuples) but use segment_id (String) for model state
        for key, segment in road_network.segments.items():
            s_id = str(segment.segment_id)
            base_density = self.get_traffic_density(segment, current_time)
            road_type = segment.road_type
            if isinstance(road_type, list): road_type = road_type[0]
            
            total_jam_density = self.jam_density_per_lane.get(str(road_type), 100) * max(1, segment.lanes)
            
            accident = accident_map.get(s_id)
            if segment.is_blocked or (base_density / total_jam_density) > 0.7 or accident:
                severity_mult = 1.0
                if accident:
                    severity_mult = {'minor': 1.5, 'moderate': 2.5, 'severe': 4.5}.get(str(accident.severity), 2.5)
                
                ripple_val = (base_density * 0.7 * severity_mult) if (segment.is_blocked or accident) else (base_density * 0.3)
                new_ripple_sources[s_id] = min(ripple_val, total_jam_density * 1.5)
            
            segment.current_traffic_density = base_density

        # Step 2: Multi-Hop BFS Propagation (Type-Aware Depth)
        for source_id, pressure in new_ripple_sources.items():
            # SCIENTIFIC FIX: O(1) Cache Lookup
            source_seg = self._segment_cache.get(source_id)
            if not source_seg: continue
            
            # Highways get deeper propagation (7 hops) vs City streets (3-4 hops)
            max_depth = 7 if ('motorway' in source_seg.road_type or 'trunk' in source_seg.road_type) else 3
            
            queue = [(source_id, pressure * self.propagation_decay, 1)]
            visited = {source_id}
            
            while queue:
                curr_id, curr_pressure, depth = queue.pop(0)
                if depth > max_depth or curr_pressure < 1.0:
                    continue
                
                feeders = road_network.get_upstream_neighbors(curr_id)
                for f_id in feeders:
                    f_seg = self._segment_cache.get(f_id)
                    if not f_seg: continue
                    
                    road_type_f = f_seg.road_type
                    if isinstance(road_type_f, list): road_type_f = road_type_f[0]
                    jam_f = self.jam_density_per_lane.get(str(road_type_f), 100) * max(1, f_seg.lanes)
                    
                    # SCIENTIFIC FIX (S6): Clamp ripple to 1.5x jam density to prevent "super-congestion"
                    max_ripple_f = jam_f * 1.5
                    clamped_pressure = min(curr_pressure, max_ripple_f)
                    
                    if f_id not in visited:
                        self.current_ripples[f_id] = max(self.current_ripples.get(f_id, 0.0), clamped_pressure)
                        visited.add(f_id)
                        queue.append((f_id, clamped_pressure * self.propagation_decay, depth + 1))

        # Step 3: All-segment finalization
        for seg_id_tuple, segment in road_network.segments.items():
            # Correctly use standardized string segment_id for lookup
            s_id = str(segment.segment_id)
            ripple = self.current_ripples.get(s_id, 0.0)
            road_type = segment.road_type
            if isinstance(road_type, list): road_type = road_type[0]
            
            total_jam_density = self.jam_density_per_lane.get(str(road_type), 100) * max(1, segment.lanes)
            
            segment.current_traffic_density = min(segment.current_traffic_density + ripple, total_jam_density * 0.98)
            segment.current_speed_kmh = self.get_current_speed(segment, current_time)

    def _run_surgical_bfs(self, road_network, accident_seg_ids: List[str], current_time: float) -> None:
        """
        BFS ripple propagation restricted to accident zones only.
        
        Starts BFS from the provided accident segment IDs and propagates upstream
        pressure using type-aware depth limits read from config.
        """
        bfs_max_depth_highway = self.traffic_config.get('bfs_max_depth_highway', 7)
        bfs_max_depth_city = self.traffic_config.get('bfs_max_depth_city', 3)
        ripple_min_pressure = self.traffic_config.get('ripple_min_pressure', 1.0)

        # Rebuild segment cache for O(1) BFS lookups
        self._segment_cache = {str(s.segment_id): s for s in road_network.segments.values()}

        for source_id in accident_seg_ids:
            source_seg = self._segment_cache.get(source_id)
            if not source_seg:
                continue

            road_type = source_seg.road_type
            if isinstance(road_type, list):
                road_type = road_type[0]

            # Compute pressure for this accident source
            base_density = self.get_traffic_density(source_seg, current_time)
            total_jam_density = self.jam_density_per_lane.get(road_type, 100) * max(1, source_seg.lanes)
            severity_mult = 2.5  # default moderate
            pressure = min(base_density * 0.7 * severity_mult, total_jam_density * 1.5)

            max_depth = bfs_max_depth_highway if ('motorway' in road_type or 'trunk' in road_type) else bfs_max_depth_city

            queue = [(source_id, pressure * self.propagation_decay, 1)]
            visited = {source_id}

            while queue:
                curr_id, curr_pressure, depth = queue.pop(0)
                if depth > max_depth or curr_pressure < ripple_min_pressure:
                    continue

                feeders = road_network.get_upstream_neighbors(curr_id)
                for f_id in feeders:
                    f_seg = self._segment_cache.get(f_id)
                    if not f_seg:
                        continue

                    road_type_f = f_seg.road_type
                    if isinstance(road_type_f, list):
                        road_type_f = road_type_f[0]
                    jam_f = self.jam_density_per_lane.get(str(road_type_f), 100) * max(1, f_seg.lanes)
                    max_ripple_f = jam_f * 1.5
                    clamped_pressure = min(curr_pressure, max_ripple_f)

                    if f_id not in visited:
                        self.current_ripples[f_id] = max(self.current_ripples.get(f_id, 0.0), clamped_pressure)
                        visited.add(f_id)
                        queue.append((f_id, clamped_pressure * self.propagation_decay, depth + 1))

    def update_smart(self, road_network, trucks, current_time: float, active_accidents: Optional[List] = None) -> None:
        """
        Turbo tick: O(active_ripples + active_accidents × BFS_depth) instead of O(N_segments).

        Three steps:
        1. Decay existing ripples (O(active_ripples))
        2. Surgical BFS only for accident zones (skipped when no accidents)
        3. Lazy density/speed update only for segments trucks are currently on
        """
        accidents_list = active_accidents if active_accidents is not None else []
        ripple_min_pressure = self.traffic_config.get('ripple_min_pressure', 1.0)

        # Step 1: Decay existing ripples — O(active_ripples), not O(N_segments)
        for seg_id in list(self.current_ripples.keys()):
            self.current_ripples[seg_id] *= self.persistence_factor
            if self.current_ripples[seg_id] < ripple_min_pressure:
                del self.current_ripples[seg_id]

        # Step 2: Surgical BFS — only if accidents exist
        if accidents_list:
            accident_seg_ids = [str(a.segment_id) for a in accidents_list]
            self._run_surgical_bfs(road_network, accident_seg_ids, current_time)

        # Step 3: Lazy update — only segments trucks are currently on
        # trucks param is a list of TruckAgent objects; access truck.truck.current_route
        for truck_agent in trucks:
            truck = getattr(truck_agent, 'truck', truck_agent)
            if getattr(truck, 'status', None) != 'in_transit':
                continue
            route = getattr(truck, 'current_route', None)
            if not route:
                continue
            progress = getattr(truck, 'route_progress', 0)
            seg_objects = route.get('segment_objects', [])
            if progress < len(seg_objects):
                seg = seg_objects[progress]
                if seg is not None:
                    seg.current_traffic_density = self.get_traffic_density(seg, current_time)
                    seg.current_speed_kmh = self.get_current_speed(seg, current_time)