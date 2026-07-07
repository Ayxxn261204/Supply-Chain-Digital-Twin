"""
Road segment entity representing a single road link in the network.

Each segment has realistic properties from OSM data and dynamic traffic state.
"""

from typing import Dict, Any, Optional, List, Tuple
from dataclasses import dataclass
import logging
logger = logging.getLogger(__name__)

MIN_SPEED_FALLBACK_KMH = 10.0


@dataclass
class RoadSegment:
    """
    A single road segment (edge) in the network.
    
    Represents a directed link between two nodes with realistic properties:
    - Static: length, road type, speed limit, lanes (from OSM)
    - Dynamic: traffic density, current speed, blocked status
    """
    
    # Static properties (from OSM)
    segment_id: str
    start_node: int
    end_node: int
    length_km: float
    road_type: str  # motorway, trunk, primary, secondary, tertiary, residential
    speed_limit_kmh: float
    lanes: int
    osm_data: Dict[str, Any]  # Original OSM data for reference
    is_oneway: bool = False  # True if one-way street
    surface_type: str = 'paved'  # paved, unpaved, gravel, dirt
    # Locations
    start_location: Tuple[float, float] = (0.0, 0.0)  # (lat, lon) of start node
    end_location: Tuple[float, float] = (0.0, 0.0)  # (lat, lon) of end node
    
    # Curved path interpolation (NEW)
    geometry: Optional[List[Tuple[float, float]]] = None  # Way geometry: [(lat, lon), ...]
    geometry_cumulative_distances: Optional[List[float]] = None  # Cumulative distances in km
    
    # Zone-based behavior (NEW)
    zone_type: str = 'RESIDENTIAL'  # OFFICE, SHOPPING, RESIDENTIAL, HIGHWAY
    zone_multiplier: float = 1.0     # Multiplier for the Gaussian wave amplitude
    
    # Dynamic properties (updated during simulation)
    current_traffic_density: float = 0.0  # vehicles per km per lane
    current_speed_kmh: float = 0.0  # Current average speed
    is_blocked: bool = False  # True if accident blocks this segment
    accident_speed_reduction: float = 0.0  # 0.0 to 1.0 (0 = no effect, 1.0 = complete block)

    # Pre-computed static weight for Stage 1 fast A*
    # Placeholder default; real value is always set in __post_init__
    base_weight: float = 0.0

    def __post_init__(self):
        """Initialize dynamic properties and enforce defaults."""
        # Enforce valid road type
        valid_types = [
            'motorway', 'motorway_link', 
            'trunk', 'trunk_link', 
            'primary', 'primary_link', 
            'secondary', 'secondary_link', 
            'tertiary', 'tertiary_link', 
            'residential', 'living_street', 'unclassified'
        ]
        
        # Normalize road type (handle lists or unknown strings)
        if isinstance(self.road_type, list):
            self.road_type = self.road_type[0]
        
        if self.road_type not in valid_types:
            # Map unknown types to closest reasonable default
            self.road_type = 'residential'
            
        # Enforce valid lane count
        if not self.lanes or str(self.lanes) == 'nan':
            # Default lanes based on road type if missing
            defaults = {
                'motorway': 3, 'trunk': 2, 'primary': 2,
                'secondary': 1, 'tertiary': 1, 'residential': 1
            }
            # Handle 'link' types (usually 1 lane)
            if 'link' in self.road_type:
                self.lanes = 1
            else:
                self.lanes = defaults.get(self.road_type, 1)
        
        try:
            if isinstance(self.lanes, list):
                self.lanes = int(self.lanes[0])
            else:
                self.lanes = int(float(self.lanes)) # Handle possible string '2.0'
        except (ValueError, TypeError):
             self.lanes = 1
             
        # Enforce surface type
        if not self.surface_type or str(self.surface_type) == 'nan':
            # Assume paved unless it's a very minor road
            if self.road_type in ['track', 'path']:
                self.surface_type = 'dirt'
            else:
                self.surface_type = 'paved'

        # OPTIMIZATION: Geometry interpolation cache
        self._interp_cache = {}  # distance_key -> (lat, lon)
        self._cache_resolution = 0.01  # 10 meters (~0.01 km)

        # Start with free-flow speed (no traffic)
        self.current_speed_kmh = self.speed_limit_kmh

        # Pre-compute static weight for Stage 1 fast A*
        # base_weight = free-flow traversal time (hours) — lower bound on actual cost
        if self.speed_limit_kmh > 0:
            self.base_weight = self.length_km / self.speed_limit_kmh
        else:
            self.base_weight = self.length_km / MIN_SPEED_FALLBACK_KMH
    
    def get_travel_time_minutes(self, speed_kmh: Optional[float] = None, 
                                truck_type_config: Optional[Dict[str, Any]] = None) -> float:
        """
        Calculate travel time for this segment.
        
        Args:
            speed_kmh: Speed to use (if None, uses current_speed_kmh)
            truck_type_config: Optional truck config for maneuverability index (NEW)
        
        Returns:
            Travel time in minutes
        """
        if self.is_blocked:
            # Blocked segments have infinite travel time
            return float('inf')
        
        speed = speed_kmh if speed_kmh is not None else self.current_speed_kmh
        
        # SAFETY: If speed not updated or zero, fall back to speed limit
        if speed <= 0:
            speed = self.speed_limit_kmh

        # Apply truck-type maneuverability (Linked Logic)
        if truck_type_config and 'maneuverability_index' in truck_type_config:
            # Small trucks (index 1.2) are faster in dense traffic
            # Large trucks (index 0.8) are slower
            # The effect scales with traffic density
            density_factor = self.current_traffic_density / 50.0 # Normalize against moderate traffic
            maneuver_mult = 1.0 + (truck_type_config['maneuverability_index'] - 1.0) * density_factor
            speed *= max(0.5, maneuver_mult)
        
        # Apply surface type speed reduction
        speed *= self._get_surface_speed_multiplier()
        
        # Apply accident speed reduction if present (partial blockage)
        if self.accident_speed_reduction > 0:
            speed *= (1.0 - self.accident_speed_reduction)
        
        # Final safety check to avoid division by zero
        if speed <= 0:
            speed = 1.0  # Absolute minimum
        
        # time = distance / speed
        time_hours = self.length_km / speed
        time_minutes = time_hours * 60
        
        return time_minutes
    
    def _get_surface_speed_multiplier(self) -> float:
        """
        Get speed multiplier based on surface type.
        
        Unpaved roads reduce speed due to rough surface.
        
        Returns:
            Speed multiplier (1.0 = no reduction, <1.0 = slower)
        """
        multipliers = {
            'paved': 1.0,      # No reduction
            'asphalt': 1.0,    # Explicit asphalt
            'concrete': 1.0,   # Concrete
            'unpaved': 0.8,    # 20% slower
            'gravel': 0.7,     # 30% slower
            'dirt': 0.6,       # 40% slower
            'earth': 0.5,      # 50% slower
            'grass': 0.4       # 60% slower
        }
        # Handle list case just in case
        stype = self.surface_type
        if isinstance(stype, list):
            stype = stype[0]
            
        return multipliers.get(stype, 1.0)
    
    def get_fuel_consumption_liters(self, truck_type_config: Dict[str, Any], 
                                   load_fraction: float) -> float:
        """
        Calculate fuel consumption for traversing this segment.
        
        Uses realistic fuel consumption model:
        - Base consumption depends on truck type and load
        - Speed-dependent efficiency multiplier
        
        Args:
            truck_type_config: Truck type configuration dict
            load_fraction: Fraction of capacity loaded (0.0 to 1.0)
        
        Returns:
            Fuel consumption in liters
        """
        # Get base fuel consumption rates (L/100km)
        fuel_empty = truck_type_config['fuel_consumption_empty_l_per_100km']
        fuel_full = truck_type_config['fuel_consumption_full_l_per_100km']
        
        # Interpolate based on load
        base_consumption_per_100km = fuel_empty + (fuel_full - fuel_empty) * load_fraction
        
        # Get speed-dependent efficiency multiplier
        speed_multiplier = self._get_speed_efficiency_multiplier(
            truck_type_config, 
            self.current_speed_kmh
        )
        
        # Get surface-dependent fuel multiplier
        surface_multiplier = self._get_surface_fuel_multiplier()
        
        # Calculate fuel consumption for this segment
        # consumption = (base_rate / 100) * distance * speed_multiplier * surface_multiplier
        fuel_liters = (base_consumption_per_100km / 100.0) * self.length_km * speed_multiplier * surface_multiplier
        
        return fuel_liters
    
    def _get_surface_fuel_multiplier(self) -> float:
        """
        Get fuel consumption multiplier based on surface type.
        
        Unpaved roads increase fuel consumption due to rolling resistance.
        
        Returns:
            Fuel multiplier (1.0 = no increase, >1.0 = more fuel)
        """
        multipliers = {
            'paved': 1.0,      # No increase
            'asphalt': 1.0,
            'concrete': 1.0,
            'unpaved': 1.15,   # 15% more fuel
            'gravel': 1.25,    # 25% more fuel
            'dirt': 1.35,      # 35% more fuel
            'earth': 1.45,     # 45% more fuel
            'grass': 1.50      # 50% more fuel
        }
        stype = self.surface_type
        if isinstance(stype, list):
            stype = stype[0]
        return multipliers.get(stype, 1.0)
    
    def _get_speed_efficiency_multiplier(self, truck_type_config: Dict[str, Any], 
                                        speed_kmh: float) -> float:
        """
        Get fuel efficiency multiplier based on speed.
        
        Trucks are most efficient at ~50 km/h. Higher or lower speeds
        increase fuel consumption due to aerodynamic drag or inefficient gearing.
        
        Uses linear interpolation between speed brackets for accuracy.
        
        Args:
            truck_type_config: Truck type configuration dict
            speed_kmh: Current speed in km/h
        
        Returns:
            Efficiency multiplier (1.0 = optimal, >1.0 = worse)
        """
        efficiency_curve = truck_type_config.get('fuel_efficiency_by_speed', {})
        speed_brackets = [20, 30, 40, 50, 60, 70, 80]
        
        # Clamp speed to bracket range
        speed_kmh = max(speed_brackets[0], min(speed_kmh, speed_brackets[-1]))
        
        # Find surrounding brackets for interpolation
        lower_bracket = max([b for b in speed_brackets if b <= speed_kmh], default=speed_brackets[0])
        upper_bracket = min([b for b in speed_brackets if b >= speed_kmh], default=speed_brackets[-1])
        
        # Get multipliers for brackets
        lower_key = f"{lower_bracket}_kmh"
        upper_key = f"{upper_bracket}_kmh"
        lower_mult = efficiency_curve.get(lower_key, 1.0)
        upper_mult = efficiency_curve.get(upper_key, 1.0)
        
        # If exact match or same bracket, return directly
        if lower_bracket == upper_bracket:
            return lower_mult
        
        # Linear interpolation
        fraction = (speed_kmh - lower_bracket) / (upper_bracket - lower_bracket)
        multiplier = lower_mult + (upper_mult - lower_mult) * fraction
        
        return multiplier
    
    def update_traffic(self, density: float, speed: float) -> None:
        """
        Update traffic conditions on this segment.
        
        Called by TrafficModel during simulation.
        
        Args:
            density: Traffic density (vehicles per km per lane)
            speed: Current average speed (km/h)
        """
        self.current_traffic_density = density
        self.current_speed_kmh = speed
    
    
    def set_accident(self, severity: str = 'moderate') -> None:
        """
        Apply partial speed reduction due to accident (realistic model).
        
        Accidents don't block entire segment - only reduce speed based on:
        - Segment length (50m accident on 5km road has less impact than on 100m road)
        - Accident severity (minor/moderate/severe)
        
        Args:
            severity: 'minor', 'moderate', or 'severe'
        """
        # Calculate blockage as percentage of segment length
        # Assume accident blocks ~50 meters (0.05 km)
        accident_size_km = 0.05  # 50 meters
        
        if self.length_km > 0:
            blockage_fraction = accident_size_km / self.length_km
        else:
            blockage_fraction = 1.0
        
        # Severity modifiers (validate input)
        VALID_SEVERITY_MULTIPLIERS = {
            'minor': 0.3,      # Minor reduces speed by 30% of blockage
            'moderate': 0.5,   # Moderate reduces by 50%
            'severe': 0.8      # Severe reduces by 80%
        }
        mult = VALID_SEVERITY_MULTIPLIERS.get(severity, None)
        if mult is None:
            logger.warning(f"[ACCIDENT] Unknown severity '{severity}' on segment {self.segment_id} - using 'moderate' (0.5)")
            mult = 0.5
        
        # Calculate speed reduction
        # Short segment (100m): blockage_fraction = 0.5 ? 25-40% speed reduction
        # Long segment (5km): blockage_fraction = 0.01 ? 0.3-0.8% speed reduction
        self.accident_speed_reduction = min(1.0, blockage_fraction * mult)
        
        # Keep is_blocked for backward compatibility (set if >80% reduction)
        self.is_blocked = (self.accident_speed_reduction > 0.8)
    
    def clear_accident(self) -> None:
        """Clear accident and restore normal speed."""
        self.accident_speed_reduction = 0.0
        self.is_blocked = False
    
    # Legacy methods for backward compatibility
    def block(self) -> None:
        """Block this segment (e.g., due to accident). DEPRECATED: Use set_accident()."""
        self.set_accident(severity='severe')  # Severe = nearly complete block
        self.is_blocked = True
    
    def unblock(self) -> None:
        """Unblock this segment (accident cleared). DEPRECATED: Use clear_accident()."""
        self.clear_accident()
    
    def get_state(self) -> Dict[str, Any]:
        """
        Get current state of segment for logging.
        
        Returns:
            Dictionary with segment state
        """
        return {
            'segment_id': self.segment_id,
            'start_node': self.start_node,
            'end_node': self.end_node,
            'length_km': self.length_km,
            'road_type': self.road_type,
            'speed_limit_kmh': self.speed_limit_kmh,
            'lanes': self.lanes,
            'surface_type': self.surface_type,  # Log surface type too
            'current_traffic_density': self.current_traffic_density,
            'current_speed_kmh': self.current_speed_kmh,
            'is_blocked': self.is_blocked
        }
    
    def interpolate_position(self, distance_along_segment_km: float) -> Tuple[float, float]:
        """
        Interpolate position along segment at given distance (CACHE-OPTIMIZED).
        
        Uses curved geometry if available, otherwise falls back to linear.
        Caches results at fixed intervals for performance.
        
        Args:
            distance_along_segment_km: Distance traveled along segment (0 to length_km)
        
        Returns:
            (lat, lon) tuple
        """
        # Clamp distance to valid range
        distance_along_segment_km = max(0, min(distance_along_segment_km, self.length_km))
        
        # OPTIMIZATION: Check cache first (round to resolution)
        cache_key = round(distance_along_segment_km / self._cache_resolution)
        if cache_key in self._interp_cache:
            return self._interp_cache[cache_key]
        
        # Fallback to linear interpolation if no geometry
        if not self.geometry or len(self.geometry) < 2:
            progress_fraction = distance_along_segment_km / self.length_km if self.length_km > 0 else 0
            start_lat, start_lon = self.start_location
            end_lat, end_lon = self.end_location
            position = (
                start_lat + (end_lat - start_lat) * progress_fraction,
                start_lon + (end_lon - start_lon) * progress_fraction
            )
            self._interp_cache[cache_key] = position
            return position
        
        # Use binary search to find sub-segment
        cumul_dists = self.geometry_cumulative_distances
        
        # Safety check: if distances not calculated, fall back to linear
        if not cumul_dists or len(cumul_dists) != len(self.geometry):
            progress_fraction = distance_along_segment_km / self.length_km if self.length_km > 0 else 0
            start_lat, start_lon = self.start_location
            end_lat, end_lon = self.end_location
            position = (
                start_lat + (end_lat - start_lat) * progress_fraction,
                start_lon + (end_lon - start_lon) * progress_fraction
            )
            self._interp_cache[cache_key] = position
            return position
        
        idx = self._binary_search_segment(distance_along_segment_km, cumul_dists)
        
        # Interpolate between geometry[idx] and geometry[idx+1]
        if idx >= len(self.geometry) - 1:
            position = self.geometry[-1]  # At end
            self._interp_cache[cache_key] = position
            return position
        
        # Distance within this sub-segment
        dist_before = cumul_dists[idx]
        dist_after = cumul_dists[idx + 1]
        sub_segment_length = dist_after - dist_before
        
        if sub_segment_length < 0.001:  # Avoid division by zero
            position = self.geometry[idx]
            self._interp_cache[cache_key] = position
            return position
        
        progress = (distance_along_segment_km - dist_before) / sub_segment_length
        
        lat1, lon1 = self.geometry[idx]
        lat2, lon2 = self.geometry[idx + 1]
        
        position = (
            lat1 + (lat2 - lat1) * progress,
            lon1 + (lon2 - lon1) * progress
        )
        
        # Cache the result
        self._interp_cache[cache_key] = position
        
        # Limit cache size to prevent memory bloat (keep last 100 per segment)
        if len(self._interp_cache) >100:
            # Remove oldest entries (simple FIFO)
            oldest_keys = sorted(self._interp_cache.keys())[:50]
            for k in oldest_keys:
                del self._interp_cache[k]
        
        return position
    
    def _binary_search_segment(self, target_dist: float, cumul_dists: List[float]) -> int:
        """Binary search to find geometry index for target distance."""
        left, right = 0, len(cumul_dists) - 1
        
        while left < right:
            mid = (left + right + 1) // 2
            if cumul_dists[mid] <= target_dist:
                left = mid
            else:
                right = mid - 1
        
        return left
    
    def __repr__(self) -> str:
        return (f"RoadSegment(id={self.segment_id}, "
                f"type={self.road_type}, "
                f"lanes={self.lanes}, "
                f"surface={self.surface_type}, "
                f"length={self.length_km:.2f}km, "
                f"speed={self.current_speed_kmh:.1f}km/h, "
                f"blocked={self.is_blocked})")
