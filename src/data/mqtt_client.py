"""MQTT client wrapper for publishing sensor data."""

import json
import time
import paho.mqtt.client as mqtt
from typing import Any, Dict, Optional


class MQTTClientWrapper:
    """Wrapper for MQTT client with connection handling and JSON publishing."""
    
    def __init__(self, broker: str = "localhost", port: int = 1883, 
                 client_id: Optional[str] = None):
        """
        Initialize MQTT client wrapper.
        
        Args:
            broker: MQTT broker hostname or IP address
            port: MQTT broker port (default 1883)
            client_id: Optional client ID (auto-generated if None)
        """
        self.broker = broker
        self.port = port
        self.client_id = client_id or f"simulation-{int(time.time())}"
        self.connected = False
        self.reconnect_delay = 1  # Initial reconnect delay in seconds
        self.max_reconnect_delay = 60  # Maximum reconnect delay
        
        # Create MQTT client
        self.client = mqtt.Client(client_id=self.client_id)
        
        # Set up callbacks
        self.client.on_connect = self._on_connect
        self.client.on_disconnect = self._on_disconnect
        
    def _on_connect(self, client, userdata, flags, rc):
        """Callback for when client connects to broker."""
        if rc == 0:
            self.connected = True
            self.reconnect_delay = 1  # Reset reconnect delay
            print(f"[MQTT] Connected to broker at {self.broker}:{self.port}")
        else:
            self.connected = False
            print(f"[MQTT] Connection failed with code {rc}")
            
    def _on_disconnect(self, client, userdata, rc):
        """Callback for when client disconnects from broker."""
        self.connected = False
        if rc != 0:
            print(f"[MQTT] Unexpected disconnection (code {rc}). Will attempt reconnection.")
            
    def connect(self) -> bool:
        """
        Connect to MQTT broker with error handling.
        
        Returns:
            True if connection successful, False otherwise
        """
        try:
            self.client.connect(self.broker, self.port, keepalive=60)
            self.client.loop_start()  # Start background network loop
            
            # Wait for connection (with timeout)
            timeout = 5
            start_time = time.time()
            while not self.connected and (time.time() - start_time) < timeout:
                time.sleep(0.1)
                
            return self.connected
        except Exception as e:
            print(f"[MQTT] Connection error: {e}")
            return False
            
    def reconnect(self) -> bool:
        """
        Attempt to reconnect to broker with exponential backoff.
        
        Returns:
            True if reconnection successful, False otherwise
        """
        if self.connected:
            return True
            
        try:
            print(f"[MQTT] Attempting reconnection (delay: {self.reconnect_delay}s)...")
            time.sleep(self.reconnect_delay)
            
            self.client.reconnect()
            
            # Exponential backoff
            self.reconnect_delay = min(self.reconnect_delay * 2, self.max_reconnect_delay)
            
            # Wait for connection
            timeout = 5
            start_time = time.time()
            while not self.connected and (time.time() - start_time) < timeout:
                time.sleep(0.1)
                
            return self.connected
        except Exception as e:
            print(f"[MQTT] Reconnection error: {e}")
            return False
            
    def publish(self, topic: str, payload: Dict[str, Any], qos: int = 0) -> bool:
        """
        Publish message to MQTT topic with JSON serialization.
        
        Args:
            topic: MQTT topic to publish to
            payload: Dictionary payload to serialize as JSON
            qos: Quality of Service level (0, 1, or 2)
            
        Returns:
            True if publish successful, False otherwise
        """
        # Ensure connection
        if not self.connected:
            if not self.reconnect():
                print(f"[MQTT] Cannot publish - not connected to broker")
                return False
                
        try:
            # Serialize payload to JSON
            json_payload = json.dumps(payload)
            
            # Publish message
            result = self.client.publish(topic, json_payload, qos=qos)
            
            # Check if publish was successful
            if result.rc == mqtt.MQTT_ERR_SUCCESS:
                return True
            else:
                print(f"[MQTT] Publish failed with code {result.rc}")
                return False
        except Exception as e:
            print(f"[MQTT] Publish error: {e}")
            return False
            
    def disconnect(self):
        """Disconnect from MQTT broker and stop network loop."""
        try:
            self.client.loop_stop()
            self.client.disconnect()
            self.connected = False
            print("[MQTT] Disconnected from broker")
        except Exception as e:
            print(f"[MQTT] Disconnect error: {e}")
            
    def __enter__(self):
        """Context manager entry."""
        self.connect()
        return self
        
    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit."""
        self.disconnect()
