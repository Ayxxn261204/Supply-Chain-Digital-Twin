"""
Road network loader using OpenStreetMap data.

This module loads real road networks from OSM using osmnx and creates
a graph of RoadSegment entities for realistic routing and traffic simulation.
"""

import osmnx as ox
import networkx as nx
from typing import Dict, Any, List, Tuple, Optional
import pickle
from pathlib import Path
from scipy.spatial import KDTree

from .road_segment import RoadSegment

import logging
logger = logging.getLogger(__name__)


class RoadNetwork:
    """
    Road network loaded from OpenStreetMap data.
    
    Uses osmnx to download real road network data and creates a graph
    of RoadSegment entities with realistic properties (length, speed limits,
    road types, lanes).
    """
    
    def __init__(self, config: Dict[str, Any], cache_dir: str = "data/cache/osm"):
        """
        Initialize road network.
        
        Args:
            config: Configuration dictionary with location bounding box
            cache_dir: Directory to cache downloaded OSM data
        """
        self.config = config
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        
        # Network data
        self.graph: Optional[nx.MultiDiGraph] = None
        self.segments: Dict[Tuple[int, int, int], RoadSegment] = {}  # (u, v, key) -> RoadSegment
        self.segment_neighbors: Dict[Tuple[int, int, int], List[Tuple[int, int, int]]] = {} # segment_key -> list of neighbor keys
        self.nodes: Dict[int, Dict[str, float]] = {}  # node_id -> {lat, lon}
        
        # Spatial indexing for fast nearest-node lookups
        self._node_kdtree: Optional[KDTree] = None
        self._node_coords: List[List[float]] = []  # [[lat, lon], ...]
        self._node_ids: List[int] = []  # node IDs corresponding to coords
        
        bbox = config['simulation']['location']['bounding_box']
        self.north = bbox['north']
        self.south = bbox['south']
        self.east = bbox['east']
        self.west = bbox['west']
        
        logger.info(f"[MAP] RoadNetwork initialized for bounding box: N={self.north}, S={self.south}, E={self.east}, W={self.west}")
    
    def load(self, use_cache: bool = True) -> None:
        """
        Load road network from OpenStreetMap.
        
        Priority order:
        1. Load from cache if available
        2. Download from OSM API
        
        Args:
            use_cache: If True, use cached data if available
        """
        cache_file = self.cache_dir / f"osm_graph_{self.north}_{self.south}_{self.east}_{self.west}.pkl"
        refined_cache_file = self.cache_dir / f"refined_network_{self.north}_{self.south}_{self.east}_{self.west}.pkl"
        
        # 1. Try to load from "Refined" cache (Fastest: has segments, neighbors, KDTree)
        if use_cache and refined_cache_file.exists():
            logger.info(f"[LOAD] Loading refined road network from cache: {refined_cache_file}")
            try:
                with open(refined_cache_file, 'rb') as f:
                    data = pickle.load(f)
                    self.graph = data['graph']
                    self.segments = data['segments']
                    self.segment_neighbors = data['segment_neighbors']
                    self.nodes = data['nodes']
                    self._node_kdtree = data['kdtree']
                    self._node_ids = data['node_ids']
                    self._node_coords = data['node_coords']
                logger.info(f"[OK] Instantly loaded {len(self.segments)} segments and spatial index.")
                return
            except Exception as e:
                logger.warning(f"Refined cache load failed: {e}. Falling back...")


        # 2. Try to load from standard OSM cache
        if use_cache and cache_file.exists():
            logger.info(f"[CACHE] Loading OSM graph from cache: {cache_file}")
            try:
                with open(cache_file, 'rb') as f:
                    self.graph = pickle.load(f)
                logger.info("[OK] Loaded cached graph. Building segments...")
                self._build_segments()
                self._save_refined_cache(refined_cache_file)
            except Exception as e:
                logger.warning(f"[WARNING] Cache load failed: {e}. Re-downloading...")
                self.graph = None
        
        # Try to load from local file (PBF/OSM)
        if self.graph is None:
            local_file, large_file_detected = self._find_local_pbf_file()
            
            if local_file:
                logger.info(f"[FILE] Found local map file: {local_file}")
                logger.info(f"   Attempting to load from file (this may take a moment)...")
                try:
                    # Try loading as XML (standard .osm)
                    self.graph = ox.graph_from_xml(local_file)
                    logger.info(f"[OK] Loaded road network from local file")

                    
                    # Build segments from loaded graph
                    self._build_segments()
                    
                    # Save to refined cache for future instant loads
                    self._save_refined_cache(refined_cache_file)
                        
                except Exception as e:
                    print(f"[ERROR] Failed to load local file: {e}", flush=True)
                    raise
            
            elif large_file_detected:
                logger.warning(f"[WARNING] LARGE MAP FILE DETECTED (>200MB)")
                logger.warning(f"   Skipping local file to avoid memory crash.")
                logger.warning(f"   Will fall back to downloading specific bounding box from API.")
                # Fall through to API download logic
                pass

        # Download from OSM API (Only if no local file found)
        if self.graph is None:
            logger.info(f"[OSM] Downloading road network from OpenStreetMap API...")
            logger.info(f"   Bounding box: N={self.north}, S={self.south}, E={self.east}, W={self.west}")
            logger.info(f"   [TIME] Estimated time: 2-5 minutes (one-time download)")

            
            try:
                import time
                start_time = time.time()
                
                # Download road network for bounding box
                print(f"   [Step 1/3] Querying Overpass API...", flush=True)
                self.graph = ox.graph_from_bbox(
                    bbox=(self.north, self.south, self.east, self.west),
                    network_type='drive',
                    simplify=True
                )
                
                elapsed = time.time() - start_time
                print(f"\n[OK] Downloaded road network in {elapsed/60:.1f} minutes", flush=True)
                
                # Save to refined cache
                print(f"   [Step 2/3] Building segments and saving refined cache...", flush=True)
                self._build_segments()
                self._save_refined_cache(refined_cache_file)
            
            except Exception as e:
                print(f"\n[ERROR] Failed to download OSM data: {e}", flush=True)
                print(f"   [WARNING]  OSM servers are likely busy or unreachable.", flush=True)
                print(f"   [ERROR] Could not load road network. Please check internet or provide local .osm file.", flush=True)
                raise
    

    
    def _find_local_pbf_file(self) -> Tuple[Optional[str], bool]:
        """
        Auto-detect local map file in Datasets folder.
        
        PRIORITY ORDER:
        1. nagpur_bbbike.osm (preferred - latest, handles boundaries & edge cases)
        2. nagpur_cropped.osm
        3. nagpur_city.osm
        4. Any other .osm/.xml file under 200MB
        
        Returns:
            Tuple (file_path, is_large_file_detected)
        """
        datasets_dir = Path("data/Datasets")
        if not datasets_dir.exists():
            return None, False
        
        # EXPLICIT PRIORITY: Check for bbbike file first (latest & best quality)
        priority_files = [
            "nagpur_bbbike.osm",
            "nagpur_city.osm",
        ]
        
        for filename in priority_files:
            file_path = datasets_dir / filename
            if file_path.exists():
                size_mb = file_path.stat().st_size / (1024 * 1024)
                if size_mb <= 200:
                    print(f"[FILE] Using priority file: {filename} ({size_mb:.2f} MB)")
                    return str(file_path), False
                else:
                    print(f"[WARNING]  Priority file {filename} is too large ({size_mb:.1f} MB), skipping")
        
        # Fallback: Look for any supported file (but glob order is unpredictable)
        # NOTE: We do NOT support .pbf directly as osmnx.graph_from_xml cannot read them.
        extensions = ["*.osm", "*.xml"]
        
        large_file_found = False
        
        for ext in extensions:
            files = sorted(list(datasets_dir.glob(ext)))  # Sort for predictability
            for file_path in files:
                # Skip if already checked in priority list
                if file_path.name in priority_files:
                    continue
                    
                # Check size (skip if > 200 MB)
                size_mb = file_path.stat().st_size / (1024 * 1024)
                if size_mb > 200:
                    print(f"[WARNING]  Found local file {file_path.name} (Size: {size_mb:.1f} MB)")
                    large_file_found = True
                    # If we find a large OSM file, we should definitely hint to usage of cropper
                    # even if we don't return it as loadable.
                    continue
                
                # Skip empty files
                if size_mb < 0.01:
                    print(f"[WARNING]  Skipping empty file: {file_path.name}")
                    continue
                    
                print(f"[FILE] Using fallback file: {file_path.name} ({size_mb:.2f} MB)")
                return str(file_path), False
                
        # If we didn't find a loadable .osm file but found a .pbf, we should treat it 
        # as a "large/unsupported" file that needs processing.
        pbf_files = list(datasets_dir.glob("*.osm.pbf"))
        if pbf_files:
            print(f"[WARNING]  Found PBF file(s): {[f.name for f in pbf_files]}")
            print(f"   Note: PBF files cannot be loaded directly. Please convert to .osm (XML) or use the cropper.")
            large_file_found = True
        
        return None, large_file_found
    
    def _build_segments(self) -> None:
        """Build RoadSegment entities from OSM graph."""
        print(f"[BUILD] Building road segments...")
        
        # Extract node positions
        for node_id, data in self.graph.nodes(data=True):
            self.nodes[node_id] = {
                'lat': data['y'],
                'lon': data['x']
            }
        
        print(f"   [OK] Extracted {len(self.nodes)} nodes")
        
        # Create RoadSegment for each edge
        total_edges = self.graph.number_of_edges()
        print(f"   [PROCESS] Processing {total_edges} edges...")
        
        segment_count = 0
        skipped_boundaries = 0
        skipped_non_roads = 0
        
        for u, v, key, data in self.graph.edges(keys=True, data=True):
            # ============================================
            # CRITICAL FIX: Filter out non-road features
            # ============================================
            
            # Safety: Skip non-driveable features that shouldn't be in network
            # (But keep tags temporarily for zone detection if needed)
            if any(tag in data for tag in ['building', 'boundary', 'natural', 'leisure']):
                skipped_non_roads += 1
                continue
            
            # Require highway tag - all roads MUST have this
            if 'highway' not in data:
                skipped_non_roads += 1
                continue
            
            # ============================================
            # End of filtering - process valid road
            # ============================================
            
            # Standardized segment identifier
            segment_id = f"{u}_{v}_{key}"
            
            # Progress indicator every 5000 segments
            segment_count += 1
            if segment_count % 5000 == 0:
                logger.info(f"      Progress: {segment_count}/{total_edges} segments ({100*segment_count/total_edges:.1f}%)")
            
            # Length in meters (osmnx provides this)
            length_m = data.get('length', 0)
            length_km = float(length_m) / 1000.0
            
            # ------------------------------------------------------------
            # NAGPUR INTELLIGENT ZONING (NEW)
            # ------------------------------------------------------------
            # Determine zone based on GPS location and road type
            start_lat = self.nodes[u]['lat']
            start_lon = self.nodes[u]['lon']
            end_lat = self.nodes[v]['lat']
            end_lon = self.nodes[v]['lon']
            avg_lat = (start_lat + end_lat) / 2
            avg_lon = (start_lon + end_lon) / 2
            
            # Get road type for Highway detection
            road_type = data.get('highway', 'residential')
            if isinstance(road_type, list): road_type = road_type[0]
            
            zone_type = 'RESIDENTIAL'  # Default
            zone_multiplier = 1.0       # Daily amplitude multiplier
            
            # 1. HIGHWAY DETECTION (Main Arterial Roads)
            if road_type in ['motorway', 'trunk', 'primary', 'motorway_link', 'trunk_link']:
                zone_type = 'HIGHWAY'
                zone_multiplier = 1.2 # Constant high volume
            
            # 2. OFFICE ZONES (MIHAN SEZ & Civil Lines)
            # MIHAN Area (South-West)
            elif (21.05 <= avg_lat <= 21.10) and (79.04 <= avg_lon <= 79.08):
                zone_type = 'OFFICE'
                zone_multiplier = 1.5 # Extreme rush hour peaks
            # Civil Lines Area (Center-West)
            elif (21.14 <= avg_lat <= 21.16) and (79.05 <= avg_lon <= 79.08):
                zone_type = 'OFFICE'
                zone_multiplier = 1.4
                
            # 3. SHOPPING ZONES (Sitabuldi & Itwari Markets)
            elif (21.14 <= avg_lat <= 21.16) and (79.08 <= avg_lon <= 79.12):
                zone_type = 'SHOPPING'
                zone_multiplier = 1.6 # High evening rush, massive on weekends
            
            # ------------------------------------------------------------
            
            # Road type (highway tag in OSM)
            road_type = self._classify_road_type(data.get('highway', 'unclassified'))
            
            # Speed limit (maxspeed tag, or default based on road type)
            speed_limit_kmh = self._get_speed_limit(data, road_type)
            
            # Number of lanes (lanes tag, or default based on road type)
            lanes = self._get_lanes(data, road_type)
            
            # Check if one-way street
            is_oneway = self._is_oneway(data)
            
            # Get surface type
            surface_type = self._get_surface_type(data)
            
            # Get node locations
            start_loc = (self.nodes[u]['lat'], self.nodes[u]['lon'])
            end_loc = (self.nodes[v]['lat'], self.nodes[v]['lon'])
            
            # NEW: Extract way geometry if available and feature enabled
            geometry = None
            geometry_cumulative_distances = None
            
            # Check if curved interpolation is enabled in config
            use_curved = self.config['simulation'].get('routing', {}).get('use_curved_interpolation', True)
            
            if use_curved:
                # Check if OSM data contains geometry
                if 'geometry' in data:
                    try:
                        osm_geometry = data['geometry']  # Shapely LineString
                        
                        # Extract coords as list of (lat, lon) tuples
                        # NOTE: OSM geometry is in (lon, lat) format, so we swap
                        geometry = [(lat, lon) for lon, lat in osm_geometry.coords]
                        
                        # Pre-calculate cumulative distances using Haversine
                        geometry_cumulative_distances = self._calculate_cumulative_distances(geometry)
                    except (ValueError, TypeError, AttributeError) as e:
                        # Geometry data structure errors
                        logger.warning(f"      [WARNING] Failed to extract geometry for segment {segment_id} (data error): {type(e).__name__}")
                        geometry = None
                        geometry_cumulative_distances = None
                    except Exception as e:
                        # Unexpected geometry extraction errors (fall back to linear)
                        logger.warning(f"      [WARNING] Failed to extract geometry for segment {segment_id} (unexpected): {type(e).__name__}")
                        geometry = None
                        geometry_cumulative_distances = None
                else:
                    # No geometry available, use start/end points
                    geometry = [start_loc, end_loc]
                    geometry_cumulative_distances = [0.0, length_km]
            
            # Create RoadSegment
            segment = RoadSegment(
                segment_id=segment_id,
                start_node=u,
                end_node=v,
                length_km=length_km,
                road_type=road_type,
                speed_limit_kmh=speed_limit_kmh,
                lanes=lanes,
                osm_data=data,  # Store original OSM data for reference
                is_oneway=is_oneway,
                surface_type=surface_type,
                start_location=start_loc,
                end_location=end_loc,
                geometry=geometry,  # NEW
                geometry_cumulative_distances=geometry_cumulative_distances,  # NEW
                zone_type=zone_type,
                zone_multiplier=zone_multiplier
            )
            
            self.segments[(u, v, key)] = segment
            
            # Store segment_id in graph edge for router to use
            self.graph[u][v][key]['segment_id'] = segment_id
        
        # Report filtering statistics
        print(f"   [OK] Built {len(self.segments)} road segments")
        if skipped_boundaries > 0:
            print(f"   [FILTERED] Skipped {skipped_boundaries} administrative boundaries (not roads)")
        if skipped_non_roads > 0:
            print(f"   [FILTERED] Skipped {skipped_non_roads} non-road features")
        
        # Build adjacency list for fast neighbor lookup
        self._build_adjacency_list()
        
        # Build spatial index
        self._build_spatial_index()

    def _save_refined_cache(self, cache_path: Path):
        """Save fully processed network data to cache."""
        try:
            cache_data = {
                'graph': self.graph,
                'segments': self.segments,
                'segment_neighbors': self.segment_neighbors,
                'nodes': self.nodes,
                'kdtree': self._node_kdtree,
                'node_ids': self._node_ids,
                'node_coords': self._node_coords
            }
            with open(cache_path, 'wb') as f:
                pickle.dump(cache_data, f)
            print(f"   [SAVED] Refined network cached to: {cache_path}")
        except Exception as e:
            print(f"   [WARNING] Refined cache save failed: {e}")

    def _build_adjacency_list(self) -> None:
        """Pre-compute neighbors for all segments for O(1) lookup."""
        print(f"[BUILD] Building segment adjacency list...")
        
        # Helper maps: node -> connected segments
        incoming_to_node = {} # segments ending at node
        outgoing_from_node = {} # segments starting at node
        
        for key in self.segments:
            u, v, k = key
            
            if u not in outgoing_from_node: outgoing_from_node[u] = []
            outgoing_from_node[u].append(key)
            
            if v not in incoming_to_node: incoming_to_node[v] = []
            incoming_to_node[v].append(key)
            
        # Build neighbor list for each segment
        count = 0
        processed = 0
        total_segments = len(self.segments)
        
        for key in self.segments:
            processed += 1
            # Progress indicator - more frequent near the end
            if processed % 10000 == 0 or processed > total_segments - 1000:
                print(f"      Indexing neighbors: {processed}/{total_segments} segments ({100*processed/total_segments:.1f}%)", flush=True)
            
            u, v, k = key
            neighbors = []
            
            # Outgoing neighbors (segments starting from end node v)
            if v in outgoing_from_node:
                for n_key in outgoing_from_node[v]:
                    if n_key != key:
                        neighbors.append(n_key)
            
            # Incoming neighbors (segments ending at start node u)
            if u in incoming_to_node:
                for n_key in incoming_to_node[u]:
                    if n_key != key:
                        neighbors.append(n_key)
            
            self.segment_neighbors[key] = neighbors
            count += len(neighbors)
            
        print(f"[OK] Indexed neighbors for {len(self.segments)} segments (avg {count/len(self.segments):.1f} neighbors/segment)")

    def get_segment_neighbors(self, segment_key: Tuple[int, int, int]) -> List[Tuple[int, int, int]]:
        """Get list of neighbor segment keys (O(1))."""
        return self.segment_neighbors.get(segment_key, [])
    
    def _classify_road_type(self, highway_tag: Any) -> str:
        """
        Classify OSM highway tag into our road type categories.
        
        OSM uses many highway types. We map them to our 6 categories:
        motorway, trunk, primary, secondary, tertiary, residential
        
        Args:
            highway_tag: OSM highway tag (can be string or list)
        
        Returns:
            Road type string
        """
        # Handle list of tags (take first)
        if isinstance(highway_tag, list):
            highway_tag = highway_tag[0]
        
        highway_tag = str(highway_tag).lower()
        
        # Map OSM types to our categories
        if 'motorway' in highway_tag:
            return 'motorway'
        elif 'trunk' in highway_tag:
            return 'trunk'
        elif 'primary' in highway_tag:
            return 'primary'
        elif 'secondary' in highway_tag:
            return 'secondary'
        elif 'tertiary' in highway_tag:
            return 'tertiary'
        elif any(x in highway_tag for x in ['residential', 'living_street', 'unclassified']):
            return 'residential'
        else:
            # Default to residential for unknown types
            return 'residential'
    
    def _get_speed_limit(self, edge_data: Dict[str, Any], road_type: str) -> float:
        """
        Get speed limit for road segment.
        
        Uses OSM maxspeed tag if available, otherwise defaults based on road type.
        
        Args:
            edge_data: OSM edge data dictionary
            road_type: Classified road type
        
        Returns:
            Speed limit in km/h
        """
        # Try to get from OSM data
        maxspeed = edge_data.get('maxspeed', None)
        
        if maxspeed is not None:
            try:
                # Handle different formats: "50", "50 mph", ["50", "60"]
                if isinstance(maxspeed, list):
                    maxspeed = maxspeed[0]
                
                maxspeed_str = str(maxspeed).lower()
                
                # Extract number
                speed_value = float(''.join(c for c in maxspeed_str if c.isdigit() or c == '.'))
                
                # Convert mph to km/h if needed
                if 'mph' in maxspeed_str:
                    speed_value = speed_value * 1.60934
                
                return speed_value
            
            except (ValueError, AttributeError):
                pass  # Fall through to defaults
        
        # Default speed limits by road type (km/h) - India
        defaults = {
            'motorway': 100,
            'trunk': 80,
            'primary': 60,
            'secondary': 50,
            'tertiary': 40,
            'residential': 30
        }
        
        return defaults.get(road_type, 40)
    
    def _get_lanes(self, edge_data: Dict[str, Any], road_type: str) -> int:
        """
        Get number of lanes for road segment.
        
        Uses OSM lanes tag if available, otherwise defaults based on road type.
        
        Args:
            edge_data: OSM edge data dictionary
            road_type: Classified road type
        
        Returns:
            Number of lanes
        """
        # Try to get from OSM data
        lanes = edge_data.get('lanes', None)
        
        if lanes is not None:
            try:
                # Handle different formats: "2", 2, ["2", "3"]
                if isinstance(lanes, list):
                    lanes = lanes[0]
                
                return int(lanes)
            
            except (ValueError, TypeError):
                pass  # Fall through to defaults
        
        # Default lanes by road type
        defaults = {
            'motorway': 4,
            'trunk': 3,
            'primary': 2,
            'secondary': 2,
            'tertiary': 1,
            'residential': 1
        }
        
        return defaults.get(road_type, 1)
    
    def _is_oneway(self, edge_data: Dict[str, Any]) -> bool:
        """
        Check if road is one-way.
        
        Uses OSM oneway tag.
        
        Args:
            edge_data: OSM edge data dictionary
        
        Returns:
            True if one-way street
        """
        oneway = edge_data.get('oneway', False)
        
        # OSM uses various formats for oneway
        if oneway in ['yes', 'true', '1', 1, True]:
            return True
        elif oneway in ['-1', 'reverse']:
            # Reverse one-way (edge goes against traffic)
            # osmnx should handle this by creating reverse edge
            return True
        
        return False
    
    def _get_surface_type(self, edge_data: Dict[str, Any]) -> str:
        """
        Get road surface type.
        
        Uses OSM surface tag.
        
        Args:
            edge_data: OSM edge data dictionary
        
        Returns:
            Surface type string
        """
        surface = edge_data.get('surface', 'paved')
        
        if isinstance(surface, list):
            surface = surface[0]
        
        surface = str(surface).lower()
        
        # Classify into our categories
        if surface in ['paved', 'asphalt', 'concrete', 'paving_stones']:
            return 'paved'
        elif surface in ['unpaved', 'compacted', 'fine_gravel']:
            return 'unpaved'
        elif surface in ['gravel', 'pebblestone']:
            return 'gravel'
        elif surface in ['dirt', 'earth', 'ground', 'mud', 'sand']:
            return 'dirt'
        else:
            # Default to paved for unknown
            return 'paved'
    
    def _calculate_cumulative_distances(self, geometry: List[Tuple[float, float]]) -> List[float]:
        """
        Calculate cumulative distances along geometry using Haversine formula.
        
        Args:
            geometry: List of (lat, lon) tuples
        
        Returns:
            List of cumulative distances in km
        """
        cumulative = [0.0]
        total_dist = 0.0
        
        for i in range(1, len(geometry)):
            lat1, lon1 = geometry[i-1]
            lat2, lon2 = geometry[i]
            
            # Haversine distance
            dist_km = self._haversine_distance(lat1, lon1, lat2, lon2)
            total_dist += dist_km
            cumulative.append(total_dist)
        
        return cumulative
    
    def _haversine_distance(self, lat1: float, lon1: float, lat2: float, lon2: float) -> float:
        """Calculate great-circle distance between two points in km."""
        import math
        
        R = 6371.0  # Earth radius in km
        
        lat1_rad = math.radians(lat1)
        lat2_rad = math.radians(lat2)
        dlat = math.radians(lat2 - lat1)
        dlon = math.radians(lon2 - lon1)
        
        a = (math.sin(dlat / 2) ** 2 +
             math.cos(lat1_rad) * math.cos(lat2_rad) *
             math.sin(dlon / 2) ** 2)
        
        c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
        
        return R * c
    
    def get_segment(self, segment_id: str) -> Optional[RoadSegment]:
        """
        Get road segment by ID.
        
        Args:
            segment_id: Segment ID (format: "u_v_key")
        
        Returns:
            RoadSegment or None if not found
        """
        if not segment_id or not isinstance(segment_id, str):
            return None
            
        # Parse segment_id
        try:
            parts = segment_id.split('_')
            u = int(parts[0])
            v = int(parts[1])
            key = int(parts[2])
            return self.segments.get((u, v, key))
        except (ValueError, IndexError, AttributeError):
            return None
    
    def get_segment_by_nodes(self, u: int, v: int, key: int = 0) -> Optional[RoadSegment]:
        """
        Get road segment by node IDs.
        
        Args:
            u: Start node ID
            v: End node ID
            key: Edge key (for multigraph, default 0)
        
        Returns:
            RoadSegment or None if not found
        """
        return self.segments.get((u, v, key))
    
    def get_node_position(self, node_id: int) -> Optional[Tuple[float, float]]:
        """
        Get lat/lon position of node.
        
        Args:
            node_id: Node ID
        
        Returns:
            (lat, lon) tuple or None if not found
        """
        node_data = self.nodes.get(node_id)
        if node_data:
            return (node_data['lat'], node_data['lon'])
        return None
    
    def _build_spatial_index(self) -> None:
        """Build KD-tree for fast nearest-node spatial queries.
        
        This is called automatically when needed by get_nearest_node().
        Builds a KD-tree from all node coordinates for O(log n) lookups.
        """
        if self._node_kdtree is not None:
            return  # Already built
        
        print("[SPATIAL] Building spatial index (KD-tree) for fast node lookups...")
        
        # Extract all node coordinates and IDs from graph
        self._node_coords = []
        self._node_ids = []
        
        for node_id, data in self.graph.nodes(data=True):
            # KD-tree expects [lat, lon] format
            self._node_coords.append([data['y'], data['x']])
            self._node_ids.append(node_id)
        
        # Build KD-tree
        self._node_kdtree = KDTree(self._node_coords)
        
        print(f"[OK] Spatial index built for {len(self._node_ids)} nodes")
    
    def get_nearest_node(self, lat: float, lon: float) -> Optional[int]:
        """
        Find nearest node to coordinates using high-performance KDTree.
        
        Args:
            lat: Latitude
            lon: Longitude
            
        Returns:
            Node ID (OSM ID)
        """
        if self._node_kdtree is None:
            # Lazy build if not present (should be built in initialize_road_network)
            try:
                self._build_spatial_index()
            except Exception as e:
                logger.error(f"Failed to build spatial index in get_nearest_node: {e}")
                return None
            
        if self._node_kdtree is None:
            return None
            
        try:
            # Query KDTree (returns distance and index)
            _, index = self._node_kdtree.query([lat, lon])
            
            # Mapping index back to OSM Node ID
            return self._node_ids[index]
        except Exception as e:
            logger.error(f"Error querying KDTree: {e}")
            return None

    def get_adjacent_nodes(self, node_id: int) -> List[int]:
        """
        Get all nodes adjacent to a given node in the graph.
        
        Used by RL agents to determine available movement actions.
        """
        if self.graph is None:
            return []
        try:
            return list(self.graph.neighbors(node_id))
        except Exception as e:
            logger.error(f"Error getting neighbors for node {node_id}: {e}")
            return []

    def get_segment_id_between(self, u: int, v: int) -> Optional[str]:
        """
        Get segment ID connecting node u to node v.
        
        Args:
            u: Start node ID
            v: End node ID
        """
        if self.graph is None:
            return None
        try:
            # Check edge data in graph
            if self.graph.has_edge(u, v):
                edge_data = self.graph.get_edge_data(u, v)
                # If multiple parallel edges exist, pick the shortest as the primary segment
                best_key = min(edge_data.keys(), key=lambda k: edge_data[k].get('length', float('inf')))
                return edge_data[best_key].get('segment_id')
        except Exception:
            pass
        return None
    
    def get_upstream_neighbors(self, segment_id: str) -> List[str]:
        """
        Identify segments that feed into the start of this segment.
        
        Used by the TrafficModel for Ripple Propagation (Backpressure).
        
        Args:
            segment_id: Target segment ID
            
        Returns:
            List of upstream segment IDs
        """
        # segment_id is formatted as "u_v_key"
        if not segment_id or not isinstance(segment_id, str):
            return []
            
        try:
            parts = segment_id.split('_')
            if len(parts) < 2: return []
            u = int(parts[0])
            
            # Find all edges ending at node 'u'
            upstream_segments = []
            if self.graph.has_node(u):
                # In-edges of node 'u' are upstream of the segment u->v
                for pre_u, _, key, data in self.graph.in_edges(u, keys=True, data=True):
                    up_id = data.get('segment_id', f"{pre_u}_{u}_{key}")
                    upstream_segments.append(up_id)
            return upstream_segments
        except (ValueError, IndexError):
            return []

    def update_traffic(self, traffic_model: Any, current_time: float, active_accidents: Optional[List] = None, trucks: Optional[List] = None) -> None:
        """
        Update traffic conditions on all segments using the provided model.
        
        Args:
            traffic_model: The LTM TrafficModel instance
            current_time: Current simulation time in minutes
            active_accidents: Optional list of current accidents
            trucks: Optional list of TruckAgent objects for lazy segment updates
        """
        if traffic_model:
            traffic_model.update_smart(self, trucks or [], current_time, active_accidents=active_accidents)
    
    def get_stats(self) -> Dict[str, Any]:
        """
        Get network statistics.
        
        Returns:
            Dictionary with network stats
        """
        # Count segments by road type
        road_type_counts = {}
        total_length_km = 0
        
        for segment in self.segments.values():
            road_type_counts[segment.road_type] = road_type_counts.get(segment.road_type, 0) + 1
            total_length_km += segment.length_km
        
        return {
            'num_nodes': len(self.nodes),
            'num_segments': len(self.segments),
            'total_length_km': round(total_length_km, 2),
            'road_type_counts': road_type_counts,
            'bounding_box': {
                'north': self.north,
                'south': self.south,
                'east': self.east,
                'west': self.west
            }
        }
