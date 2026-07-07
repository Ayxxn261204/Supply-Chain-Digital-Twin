from __future__ import annotations
import random
import time



# --- Merged from sensor.py ---
"""IoT Sensor models with noise and delays."""



class Sensor:
    """
    Base sensor class with realistic delays and noise.
    
    Enhanced for Phase 2:
    - Communication delays (15-90 seconds depending on sensor)
    - Occasional packet loss (2-5%)
    - Sensor-specific noise characteristics
    """
    
    def __init__(self, sensor_id: str, config: dict):
        self.sensor_id = sensor_id
        self.config = config
        self.noise_std_dev = config.get('noise_std_dev', 0.0)
        
        # Communication delays (NEW - Phase 2)
        self.delay_seconds = config.get('delay_seconds', (15, 45))  # Range
        self.packet_loss_rate = config.get('packet_loss_rate', 0.03)  # 3% loss
        self.last_reading = None
        self.last_reading_time = 0
        
    def read(self, true_value: float, current_time: float = 0) -> float:
        """
        Read sensor value with noise and delay.
        
        Simulates realistic sensor behavior:
        - Reading might be delayed (uses last reading if within delay window)
        - Occasional packet loss (returns None)
        - Gaussian noise added to true value
        
        Args:
            true_value: Actual true value
            current_time: Current simulation time (minutes)
            
        Returns:
            Sensor reading with noise and delay, or None if packet lost
        """
        # Simulate packet loss (NEW)
        if random.random() < self.packet_loss_rate:
            return None
        
        # Simulate communication delay (NEW)
        if isinstance(self.delay_seconds, tuple):
            delay_min = random.uniform(*self.delay_seconds) / 60.0  # Convert to minutes
        else:
            delay_min = self.delay_seconds / 60.0
        
        # If recently updated, return cached reading (simulates delay)
        if self.last_reading is not None and current_time > 0 and (current_time - self.last_reading_time) < delay_min:
            return self.last_reading
        
        # Generate new reading with noise
        noise = random.gauss(0, self.noise_std_dev)
        reading = true_value + noise
        
        # Cache reading
        self.last_reading = reading
        self.last_reading_time = current_time
        
        return reading


class GPSSensor(Sensor):
    """
    GPS sensor for truck location.
    
    Characteristics:
    - Delay: 15-45 seconds (satellite communication)
    - Noise: ~5 meters positional error
    - Packet loss: 2%
    """
    
    def __init__(self, sensor_id: str, config: dict):
        # GPS-specific defaults
        config.setdefault('noise_std_dev', 0.00005)  # ~5 meters in degrees
        config.setdefault('delay_seconds', (15, 45))
        config.setdefault('packet_loss_rate', 0.02)
        super().__init__(sensor_id, config)
    
    def read(self, true_value, current_time: float):
        """
        Read GPS coordinates with noise applied to each dimension.
        
        Args:
            true_value: Tuple of (latitude, longitude)
            current_time: Current simulation time
            
        Returns:
            Noisy (lat, lon) tuple or None if packet lost
        """
        
        # Packet loss
        if random.random() < self.packet_loss_rate:
            return None
        
        # Simulate communication delay
        if isinstance(self.delay_seconds, tuple):
            delay_min = random.uniform(*self.delay_seconds) / 60.0
        else:
            delay_min = self.delay_seconds / 60.0
        
        # Return cached reading if within delay window
        if self.last_reading is not None and current_time > 0 and (current_time - self.last_reading_time) < delay_min:
            return self.last_reading
        
        # Add noise to each coordinate separately
        if isinstance(true_value, tuple) and len(true_value) == 2:
            lat_noise = random.gauss(0, self.noise_std_dev)
            lon_noise = random.gauss(0, self.noise_std_dev)
            reading = (true_value[0] + lat_noise, true_value[1] + lon_noise)
        else:
            # Fallback for non-tuple values
            noise = random.gauss(0, self.noise_std_dev)
            reading = true_value + noise
        
        # Cache reading
        self.last_reading = reading
        self.last_reading_time = current_time
        
        return reading


class TemperatureSensor(Sensor):
    """
    Temperature sensor for cargo monitoring.
    
    Characteristics:
    - Delay: 30-90 seconds (thermal sensor response time)
    - Noise: ?0.5?C
    - Packet loss: 3%
    """
    
    def __init__(self, sensor_id: str, config: dict):
        # Temperature-specific defaults
        config.setdefault('noise_std_dev', 0.5)
        config.setdefault('delay_seconds', (30, 90))
        config.setdefault('packet_loss_rate', 0.03)
        super().__init__(sensor_id, config)


class StockSensor(Sensor):
    """
    Stock/weight sensor for cargo.
    
    Characteristics:
    - Delay: 5-15 seconds (load cell stabilization)
    - Noise: ?2 kg
    - Packet loss: 5%
    """
    
    def __init__(self, sensor_id: str, config: dict):
        # Stock-specific defaults
        config.setdefault('noise_std_dev', 2.0)
        config.setdefault('delay_seconds', (5, 15))
        config.setdefault('packet_loss_rate', 0.05)
        super().__init__(sensor_id, config)