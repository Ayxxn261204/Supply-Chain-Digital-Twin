import random
import logging
from typing import Dict, List, Tuple, Any
from src.simulation.entities import TruckType
from src.simulation.agents.warehouse_agent import WarehouseAgent
from src.simulation.agents.retailer_agent import RetailerAgent

logger = logging.getLogger(__name__)

def generate_dynamic_warehouses(config, road_network):
    """
    Generate warehouses with random counts and capacities.
    """
    gen_config = config['agent_generation']['warehouses']
    bbox = config['simulation']['location']['bounding_box']
    
    wh_count = random.randint(*gen_config['count_range'])
    sizes = ['small', 'medium', 'large']
    size_probabilities = gen_config.get('size_probabilities', {'small': 0.4, 'medium': 0.4, 'large': 0.2})
    size_weights = [size_probabilities.get(s, 0.3) for s in sizes]
    
    warehouses = []
    min_distance = config['agent_generation']['min_distances']['warehouse_km']
    max_placement_attempts = config['agent_generation']['max_placement_attempts']
    
    initial_inventory_pct = gen_config.get('initial_inventory_pct', 0.7)
    restock_pct = gen_config.get('restock_pct', 0.6)
    
    for i in range(wh_count):
        size = random.choices(sizes, weights=size_weights)[0]
        size_config = gen_config[size]
        capacity = random.uniform(*size_config['capacity_range_kg'])
        fleet_size = random.randint(*size_config['fleet_range'])
        
        fleet = []
        for truck_type, percentage in size_config['fleet_mix'].items():
            count = int(fleet_size * percentage)
            if count > 0:
                fleet.append({'type': truck_type, 'count': count})
        
        valid_location = None
        for attempt in range(max_placement_attempts):
            lat = random.uniform(bbox['south'], bbox['north'])
            lon = random.uniform(bbox['west'], bbox['east'])
            
            nearest_node = road_network.get_nearest_node(lat, lon)
            if nearest_node is None: continue
            
            too_close = False
            for existing in warehouses:
                ex_lat = existing['location']['lat']
                ex_lon = existing['location']['lon']
                dist = road_network._haversine_distance(lat, lon, ex_lat, ex_lon)
                if dist < min_distance:
                    too_close = True
                    break
            
            if not too_close:
                valid_location = (lat, lon)
                break
        
        if valid_location:
            lat, lon = valid_location
            warehouses.append({
                'id': f'WH{i+1:03d}',
                'name': f'{size.capitalize()} Hub {i+1}',
                'location': {'lat': lat, 'lon': lon},
                'initial_inventory_kg': capacity * initial_inventory_pct,
                'fleet': fleet,
                'restock_schedule': {
                    'day_of_week': 0, 'time_of_day': '06:00',
                    'quantity_kg': capacity * restock_pct
                },
                'max_capacity_kg': capacity
            })
    
    return warehouses

def generate_dynamic_retailers(config, warehouses, road_network):
    """
    Generate retailers with random counts and capacities.
    """
    gen_config = config['agent_generation']['retailers']
    bbox = config['simulation']['location']['bounding_box']
    
    ret_count = random.randint(*gen_config['count_range'])
    retailers = []
    min_distance = config['agent_generation']['min_distances']['retailer_km']
    max_placement_attempts = config['agent_generation']['max_placement_attempts']
    
    capacity_ranges = gen_config.get('capacity_ranges', {
        'small': [500, 2000], 'medium': [2000, 5000], 'large': [5000, 10000]
    })
    
    for i in range(ret_count):
        size_type = random.choices(['small', 'medium', 'large'], weights=[0.4, 0.4, 0.2])[0]
        capacity = random.uniform(*capacity_ranges[size_type])
        
        valid_location = None
        for attempt in range(max_placement_attempts):
            lat = random.uniform(bbox['south'], bbox['north'])
            lon = random.uniform(bbox['west'], bbox['east'])
            
            nearest_node = road_network.get_nearest_node(lat, lon)
            if nearest_node is None: continue
            
            too_close = False
            for existing in retailers + warehouses:
                ex_lat = existing['location']['lat']
                ex_lon = existing['location']['lon']
                dist = road_network._haversine_distance(lat, lon, ex_lat, ex_lon)
                if dist < min_distance:
                    too_close = True
                    break
            
            if not too_close:
                valid_location = (lat, lon)
                break
        
        if valid_location:
            lat, lon = valid_location
            init_pct = random.uniform(*gen_config['initial_stock_percentage'])
            reorder_pct = random.uniform(*gen_config['reorder_point_percentage'])
            
            sub_config = config['agent_generation']['retailer_warehouse_subscription']
            num_subs = random.randint(sub_config['min_warehouses'], min(sub_config['max_warehouses'] or len(warehouses), len(warehouses)))
            
            warehouses_by_dist = sorted(warehouses, key=lambda w: road_network._haversine_distance(lat, lon, w['location']['lat'], w['location']['lon']))
            sub_ids = [wh['id'] for wh in warehouses_by_dist[:num_subs]]
            
            retailers.append({
                'id': f'RET{i+1:03d}',
                'warehouse_ids': sub_ids,
                'location': {'lat': lat, 'lon': lon},
                'initial_stock_kg': capacity * init_pct,
                'storage_capacity': capacity,
                'reorder_point': capacity * reorder_pct
            })
    
    return retailers

def _apply_spatial_zones(road_network):
    """
    Apply a spatial heuristic to tag road segments with zones.
    Nagpur CBD core: 21.1458, 79.0882 (Zero Mile)
    """
    center_lat, center_lon = 21.1458, 79.0882
    
    for seg in road_network.segments.values():
        # Get approximate center of segment
        lat = (seg.start_location[0] + seg.end_location[0]) / 2
        lon = (seg.start_location[1] + seg.end_location[1]) / 2
        
        # Distance to city center
        dist = road_network._haversine_distance(lat, lon, center_lat, center_lon)
        
        # Heuristic classification
        if dist < 2.5:
            seg.zone_type = 'OFFICE'
            seg.zone_multiplier = 1.4  # Higher volume core
        elif dist < 5.5:
            seg.zone_type = 'SHOPPING'
            seg.zone_multiplier = 1.2
        elif seg.road_type in ['motorway', 'trunk', 'primary']:
            seg.zone_type = 'HIGHWAY'
            seg.zone_multiplier = 1.0
        else:
            seg.zone_type = 'RESIDENTIAL'
            seg.zone_multiplier = 0.8
            
    logger.info(f"Applied spatial zones to {len(road_network.segments)} segments")

def parse_truck_types(config) -> Dict[str, TruckType]:
    """Parse truck types from config."""
    truck_types = {}
    for t_name, t_cfg in config['truck_types'].items():
        truck_types[t_name] = TruckType(
            name=t_name,
            capacity_kg=t_cfg['capacity_kg'],
            fuel_tank_liters=t_cfg['fuel_tank_liters'],
            fuel_consumption_empty_l_per_100km=t_cfg['fuel_consumption_empty_l_per_100km'],
            fuel_consumption_full_l_per_100km=t_cfg['fuel_consumption_full_l_per_100km'],
            fuel_efficiency_by_speed=t_cfg['fuel_efficiency_by_speed'],
            max_speed_kmh=t_cfg['max_speed_kmh'],
            cost_per_liter=t_cfg['cost_per_liter'],
            loading_time_per_ton_minutes=t_cfg['loading_time_per_ton_minutes'],
            unloading_time_per_ton_minutes=t_cfg['unloading_time_per_ton_minutes'],
            refueling=t_cfg.get('refueling'),
            shade_factor=t_cfg.get('shade_factor', 0.85),
            maneuverability_index=t_cfg.get('maneuverability_index', 1.0),
            stop_and_go_fuel_multiplier=t_cfg.get('stop_and_go_fuel_multiplier', 1.0)
        )
    return truck_types

def populate_engine(engine, config):
    """
    Full population logic for the simulation engine.
    """
    truck_types = parse_truck_types(config)
    engine.truck_types = truck_types
    
    # Zones are already set per-segment by RoadNetwork._build_segments() using
    # precise GPS coordinate ranges for Nagpur neighbourhoods (MIHAN, Civil Lines,
    # Sitabuldi, etc.).  Do NOT call _apply_spatial_zones() here — it would
    # overwrite those precise zones with a cruder distance-from-centre heuristic.
    
    if config.get('agent_generation', {}).get('enabled', False):
        wh_configs = generate_dynamic_warehouses(config, engine.road_network)
        ret_configs = generate_dynamic_retailers(config, wh_configs, engine.road_network)
    else:
        wh_configs = config.get('warehouses', [])
        ret_configs = config.get('retailers', [])
        
    for wh_cfg in wh_configs:
        wh = WarehouseAgent(
            warehouse_id=wh_cfg['id'],
            location=(wh_cfg['location']['lat'], wh_cfg['location']['lon']),
            initial_inventory_kg=wh_cfg['initial_inventory_kg'],
            fleet_config=wh_cfg['fleet'],
            truck_types=truck_types,
            road_network=engine.road_network,
            router=engine.router,
            config=config,
            event_bus=engine.event_bus,
            ai_manager=engine.ai_manager
        )
        wh.restock_schedule = wh_cfg.get('restock_schedule')
        wh.max_capacity_kg = wh_cfg.get('max_capacity_kg', wh.current_inventory_kg * 2.0)
        engine.warehouses.append(wh)
        # Note: engine.trucks is kept for backward compatibility but all_trucks property
        # is the authoritative source. Extend both to keep them in sync at startup.
        engine.trucks.extend(wh.trucks)
        
    for ret_cfg in ret_configs:
        ret = RetailerAgent(
            retailer_id=ret_cfg['id'],
            location=(ret_cfg['location']['lat'], ret_cfg['location']['lon']),
            initial_inventory_kg=ret_cfg.get('initial_stock_kg', 500),
            warehouse_ids=ret_cfg['warehouse_ids'],
            demand_config=config['retailers']['demand'],
            inventory_config=config['retailers']['inventory'],
            road_network=engine.road_network,
            config=config,
            event_bus=engine.event_bus,
            ai_manager=engine.ai_manager
        )
        engine.retailers.append(ret)
    
    logger.info(f"Populated {len(engine.warehouses)} WH, {len(engine.retailers)} RET, {len(engine.trucks)} TRK")
