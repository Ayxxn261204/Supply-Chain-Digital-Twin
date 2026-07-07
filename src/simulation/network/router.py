"""
Router for finding optimal paths through the road network.

Uses a Two-Stage A* algorithm:
  Stage 1 (Fast Search): A* with base_weight × density_factor — no fuel/RSL calculations.
  Stage 2 (Refine): Full fuel + travel-time metrics computed only for the winner path.
"""

import networkx as nx
from typing import List, Tuple, Dict, Any, Optional
import math
import logging

# Module-level logger
logger = logging.getLogger(__name__)


class Router:
    """
    Router for finding optimal paths in road network.

    Uses Two-Stage A* pathfinding:
    - Stage 1: Fast A* with base_weight × density_factor (no fuel calculations during search)
    - Stage 2: Full fuel + time metrics computed once for the winner path only

    Can avoid blocked segments (accidents) and recalculate routes dynamically.
    """
    
    def __init__(self, road_network, config: Dict[str, Any]):
        """
        Initialize router.
        
        Args:
            road_network: RoadNetwork instance
            config: Configuration dictionary with routing parameters
        """
        self.road_network = road_network
        self.config = config
        
        # Cost function weights
        routing_config = config.get('routing', {})
        cost_weights = routing_config.get('cost_weights', {})
        self.weight_distance = cost_weights.get('distance_km', 1.0)
        self.weight_time = cost_weights.get('time_minutes', 0.5)
        self.weight_fuel = cost_weights.get('fuel_liters', 2.0)
        self.weight_road_quality = cost_weights.get('road_quality', 5.0)  # New weight for road comfort/safety
        
        # Route caching for performance
        self.cache_enabled = routing_config.get('cache_enabled', True)
        self.cache_ttl_minutes = routing_config.get('cache_ttl_minutes', 30)
        self.route_cache: Dict[Tuple, Dict[str, Any]] = {}
        
        logger.info(
            f"Router initialized: distance_wt={self.weight_distance}, "
            f"time_wt={self.weight_time}, fuel_wt={self.weight_fuel}, quality_wt={self.weight_road_quality}"
        )
        if self.cache_enabled:
            logger.info(f"Route caching enabled (TTL: {self.cache_ttl_minutes} min)")

    
    def _fast_edge_cost(self, u: int, v: int, edge_data: Dict[str, Any],
                        current_time: float, avoid_blocked: bool) -> float:
        """
        Stage 1 edge cost: base_weight × density_factor.

        Uses pre-computed base_weight (free-flow time) scaled by current traffic density.
        No fuel or RSL calculations — those happen in Stage 2 for the winner path only.
        Falls back to base_weight alone if traffic model is unavailable.
        """
        segment_id = edge_data.get('segment_id')
        if segment_id:
            segment = self.road_network.get_segment(segment_id)
        else:
            segment = self.road_network.get_segment_by_nodes(u, v, 0)

        if segment is None:
            return float('inf')

        if avoid_blocked and segment.is_blocked:
            return float('inf')

        # Use pre-computed base_weight (length_km / speed_limit_kmh)
        base_w = getattr(segment, 'base_weight', None)
        if base_w is None or base_w <= 0:
            # Fallback: compute on the fly if base_weight not set (e.g., old segment objects)
            base_w = segment.length_km / max(segment.speed_limit_kmh, 1.0)

        # Get traffic model from road network (may be None in tests)
        traffic_model = getattr(self.road_network, 'traffic_model', None)
        if traffic_model is None:
            return base_w  # No traffic info — use free-flow weight

        # Compute analytic density factor from traffic model
        density = traffic_model.get_traffic_density(segment, current_time)
        road_type = segment.road_type
        if isinstance(road_type, list):
            road_type = road_type[0]
        jam_density = traffic_model.jam_density_per_lane.get(road_type, 100) * max(1, segment.lanes)
        density_factor = max(1.0, density / jam_density) if jam_density > 0 else 1.0

        return base_w * density_factor

    def _refine_path(self, node_path: List[int], truck_type_config: Dict[str, Any],
                     load_fraction: float,
                     cost_weights: Dict[str, float] = None) -> Dict[str, Any]:
        """
        Stage 2: Compute full fuel + time metrics for the winner path only.

        Called once after Stage 1 A* finds the optimal node sequence.
        get_fuel_consumption_liters() is called exactly once per segment here.
        """
        segments = []
        total_distance_km = 0.0
        total_time_minutes = 0.0
        total_fuel_liters = 0.0

        # Use override weights if provided, else fall back to instance weights
        w_dist = cost_weights.get('distance_km', self.weight_distance) if cost_weights else self.weight_distance
        w_time = cost_weights.get('time_minutes', self.weight_time) if cost_weights else self.weight_time
        w_fuel = cost_weights.get('fuel_liters', self.weight_fuel) if cost_weights else self.weight_fuel

        for i in range(len(node_path) - 1):
            u = node_path[i]
            v = node_path[i + 1]

            edge_data = self.road_network.graph.get_edge_data(u, v)
            if edge_data is None:
                continue

            # Handle multigraph: pick best (shortest) parallel edge
            if isinstance(edge_data, dict) and len(edge_data) > 1:
                best_key = min(edge_data, key=lambda k: edge_data[k].get('length', float('inf')))
            elif isinstance(edge_data, dict) and 0 in edge_data:
                best_key = 0
            else:
                best_key = 0

            segment = self.road_network.get_segment_by_nodes(u, v, best_key)
            if segment is None:
                continue

            segments.append(segment.segment_id)
            total_distance_km += segment.length_km
            total_time_minutes += segment.get_travel_time_minutes()
            total_fuel_liters += segment.get_fuel_consumption_liters(truck_type_config, load_fraction)

        total_cost = (w_dist * total_distance_km +
                      w_time * total_time_minutes +
                      w_fuel * total_fuel_liters)

        segment_objects = [self.road_network.get_segment(sid) for sid in segments
                           if self.road_network.get_segment(sid) is not None]

        return {
            'nodes': node_path,
            'segments': segments,
            'segment_objects': segment_objects,
            'total_distance_km': round(total_distance_km, 3),
            'total_time_minutes': round(total_time_minutes, 2),
            'total_fuel_liters': round(total_fuel_liters, 3),
            'total_cost': round(total_cost, 2)
        }

    def find_path(self, start_node: int, end_node: int,
                  truck_type_config: Dict[str, Any],
                  load_fraction: float = 0.5,
                  avoid_blocked: bool = True,
                  current_time: float = 0.0,
                  cost_weights: dict = None) -> Optional[Dict[str, Any]]:
        """
        Find optimal path using Two-Stage A*.

        Stage 1: Fast A* with base_weight × density_factor (no fuel/RSL calculations).
        Stage 2: Full fuel + time metrics computed only for the winner path.

        Args:
            start_node: Starting node ID
            end_node: Destination node ID
            truck_type_config: Truck type configuration for fuel calculation
            load_fraction: Fraction of truck capacity loaded (0.0 to 1.0)
            avoid_blocked: If True, avoid blocked segments
            current_time: Current simulation time (for cache invalidation)
            cost_weights: Optional dict with keys 'distance_km', 'time_minutes', 'fuel_liters'
                          to override instance weights. Cache key does NOT include cost_weights.

        Returns:
            Route dict with same shape as before, or None if no path exists.
        """
        # Cache check (unchanged)
        quant_load = round(load_fraction, 1)
        truck_hash = hash(tuple(sorted((k, v) for k, v in truck_type_config.items() if isinstance(v, (int, float, str)))))
        cache_key = (start_node, end_node, avoid_blocked, quant_load, truck_hash)

        if self.cache_enabled and cache_key in self.route_cache:
            cached_route = self.route_cache[cache_key]
            if current_time - cached_route['cached_at'] < self.cache_ttl_minutes:
                if not avoid_blocked or not self._route_has_blocked_segments(cached_route):
                    return cached_route['route']

        # Use override weights if provided, else fall back to instance weights
        w_dist = cost_weights.get('distance_km', self.weight_distance) if cost_weights else self.weight_distance
        w_time = cost_weights.get('time_minutes', self.weight_time) if cost_weights else self.weight_time
        w_fuel = cost_weights.get('fuel_liters', self.weight_fuel) if cost_weights else self.weight_fuel

        # Stage 1: Fast A* — no fuel/RSL calculations
        def fast_edge_cost(u: int, v: int, edge_data: Dict[str, Any]) -> float:
            return self._fast_edge_cost(u, v, edge_data, current_time, avoid_blocked)

        def heuristic(u: int, v: int) -> float:
            pos_u = self.road_network.get_node_position(u)
            pos_v = self.road_network.get_node_position(v)
            if pos_u is None or pos_v is None:
                return 0.0
            distance_km = self._haversine_distance(pos_u[0], pos_u[1], pos_v[0], pos_v[1])
            # True edge cost is in TIME (hours). Assume an absolute max theoretical speed of 120 km/h 
            # to guarantee the heuristic never overestimates the true travel time.
            return distance_km / 120.0

        try:
            node_path = nx.astar_path(
                self.road_network.graph,
                start_node,
                end_node,
                heuristic=heuristic,
                weight=fast_edge_cost
            )
        except (nx.NetworkXNoPath, nx.NodeNotFound):
            return None

        # Stage 2: Refine — full metrics for winner path only
        route = self._refine_path(node_path, truck_type_config, load_fraction, cost_weights)

        # Cache the route
        if self.cache_enabled:
            self.route_cache[cache_key] = {
                'route': route,
                'cached_at': current_time
            }

        return route
    
    def _build_route_info(self, node_path: List[int], 
                         truck_type_config: Dict[str, Any],
                         load_fraction: float) -> Dict[str, Any]:
        """
        Build detailed route information from node path.

        Kept for backward compatibility. New code should use _refine_path() instead,
        which is the Stage 2 component of the Two-Stage A* pipeline.
        
        Args:
            node_path: List of node IDs in path
            truck_type_config: Truck type configuration
            load_fraction: Load fraction
        
        Returns:
            Route information dictionary
        """
        segments = []
        total_distance_km = 0.0
        total_time_minutes = 0.0
        total_fuel_liters = 0.0
        
        # Process each edge in path
        for i in range(len(node_path) - 1):
            u = node_path[i]
            v = node_path[i + 1]
            
            # Get edge data (handle multigraph - may have multiple parallel edges)
            edge_data = self.road_network.graph.get_edge_data(u, v)
            if edge_data is None:
                continue
            
            # Handle multigraph: find best edge among parallel edges
            if isinstance(edge_data, dict) and len(edge_data) > 1:
                # Multiple parallel edges exist, find shortest/fastest
                best_key = None
                best_length = float('inf')
                
                for key, data in edge_data.items():
                    length = data.get('length', float('inf'))
                    if length < best_length:
                        best_length = length
                        best_key = key
                
                key = best_key if best_key is not None else 0
            elif isinstance(edge_data, dict) and 0 in edge_data:
                # Single edge in multigraph format
                key = 0
            else:
                # Simple graph
                key = 0
            
            # Get RoadSegment
            segment = self.road_network.get_segment_by_nodes(u, v, key)
            if segment is None:
                continue
            
            segments.append(segment.segment_id)
            total_distance_km += segment.length_km
            total_time_minutes += segment.get_travel_time_minutes()
            total_fuel_liters += segment.get_fuel_consumption_liters(truck_type_config, load_fraction)
        
        # Calculate total cost
        total_cost = (self.weight_distance * total_distance_km +
                     self.weight_time * total_time_minutes +
                     self.weight_fuel * total_fuel_liters)
        
        # Get actual RoadSegment objects for the route
        segment_objects = []
        for seg_id in segments:
            seg = self.road_network.get_segment(seg_id)
            if seg:
                segment_objects.append(seg)
        
        return {
            'nodes': node_path,
            'segments': segments,  # Keep segment IDs for reference
            'segment_objects': segment_objects,  # Add actual RoadSegment objects
            'total_distance_km': round(total_distance_km, 3),
            'total_time_minutes': round(total_time_minutes, 2),
            'total_fuel_liters': round(total_fuel_liters, 3),
            'total_cost': round(total_cost, 2)
        }
    
    def _route_has_blocked_segments(self, cached_route: Dict[str, Any]) -> bool:
        """
        Check if cached route contains any blocked segments.
        
        Args:
            cached_route: Cached route dictionary
        
        Returns:
            True if any segment is blocked
        """
        route = cached_route['route']
        for segment_id in route['segments']:
            segment = self.road_network.get_segment(segment_id)
            if segment and segment.is_blocked:
                return True
        return False
    
    def _haversine_distance(self, lat1: float, lon1: float, 
                           lat2: float, lon2: float) -> float:
        """
        Calculate great-circle distance between two points.
        
        Args:
            lat1, lon1: First point coordinates
            lat2, lon2: Second point coordinates
        
        Returns:
            Distance in kilometers
        """
        # Earth radius in km
        R = 6371.0
        
        # Convert to radians
        lat1_rad = math.radians(lat1)
        lon1_rad = math.radians(lon1)
        lat2_rad = math.radians(lat2)
        lon2_rad = math.radians(lon2)
        
        # Haversine formula
        dlat = lat2_rad - lat1_rad
        dlon = lon2_rad - lon1_rad
        
        a = math.sin(dlat / 2)**2 + math.cos(lat1_rad) * math.cos(lat2_rad) * math.sin(dlon / 2)**2
        c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
        
        distance = R * c
        return distance
    
    def invalidate_cache(self) -> None:
        """Clear route cache (e.g., when accidents occur)."""
        self.route_cache.clear()
    
    def precompute_common_routes(self, warehouses: List, retailers: List, 
                                truck_types: Dict[str, Any]) -> None:
        """
        OPTIMIZATION 5: Pre-compute routes for common warehouse-retailer pairs.
        
        Called once at simulation start to populate route cache.
        Eliminates pathfinding delays during initial truck dispatches.
        
        Args:
            warehouses: List of warehouse agents
            retailers: List of retailer agents
            truck_types: Dictionary of truck type configurations
        """
        if not self.cache_enabled:
            logger.info("Route pre-computation skipped (cache disabled)")
            return
        
        logger.info("Pre-computing common routes...")
        route_count = 0
        
        # Pre-compute for each warehouse-retailer pair
        for wh in warehouses:
            # Warehouses are WarehouseAgent objects - access node_id directly
            wh_node = wh.node_id if hasattr(wh, 'node_id') else wh.warehouse.location_node if hasattr(wh, 'warehouse') else None
            
            if not wh_node:
                continue
            
            for ret in retailers:
                # Retailers are RetailerAgent objects - access node_id directly  
                ret_node = ret.node_id if hasattr(ret, 'node_id') else ret.retailer.location_node if hasattr(ret, 'retailer') else None
                
                if not ret_node:
                    continue
                
                # Use a typical truck type for pre-computation (medium)
                typical_truck = truck_types.get('medium', next(iter(truck_types.values())))
                
                # Pre-compute route (adds to cache)
                route = self.find_path(
                    wh_node, ret_node,
                    truck_type_config=typical_truck,
                    load_fraction=0.5,  # Assume half-loaded
                    avoid_blocked=True,
                    current_time=0.0
                )
                
                if route:
                    route_count += 1
        
        logger.info(f"Pre-computed {route_count} routes ({len(warehouses)} WH x {len(retailers)} retailers)")

    
    def should_reroute(self, current_route: Dict[str, Any], 
                      start_node: int, end_node: int,
                      truck_type_config: Dict[str, Any],
                      load_fraction: float,
                      current_time: float) -> Tuple[bool, Optional[Dict[str, Any]]]:
        """
        Check if truck should reroute based on current conditions.
        
        Compares current route with alternative route. Recommends rerouting
        if alternative is significantly better (>10% improvement).
        
        Args:
            current_route: Current route information
            start_node: Current position node
            end_node: Destination node
            truck_type_config: Truck type configuration
            load_fraction: Load fraction
            current_time: Current simulation time
        
        Returns:
            Tuple of (should_reroute: bool, new_route: Dict or None)
        """
        # Find alternative route
        alternative_route = self.find_path(
            start_node, end_node,
            truck_type_config, load_fraction,
            avoid_blocked=True,
            current_time=current_time
        )
        
        if alternative_route is None:
            # No alternative exists
            return (False, None)
        
        # Compare costs
        current_cost = current_route['total_cost']
        alternative_cost = alternative_route['total_cost']
        
        # Reroute if alternative is >10% better
        threshold = self.config.get('routing', {}).get('recalculation_threshold', 0.10)
        improvement = (current_cost - alternative_cost) / current_cost
        
        if improvement > threshold:
            return (True, alternative_route)
        
        return (False, None)