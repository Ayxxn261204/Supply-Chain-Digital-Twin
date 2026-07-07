#!/usr/bin/env python3
"""
Test MQTT Publisher for Digital Twin Supply Chain
Publishes sample sensor data to test the data pipeline
"""

import json
import time
import random
from datetime import datetime
import paho.mqtt.client as mqtt


class TestPublisher:
    """Publishes realistic sensor data to MQTT broker"""
    
    def __init__(self, broker="localhost", port=1883):
        self.broker = broker
        self.port = port
        self.client = mqtt.Client(client_id="test_publisher")
        self.connected = False
        
        # Set up callbacks
        self.client.on_connect = self.on_connect
        self.client.on_disconnect = self.on_disconnect
        
    def on_connect(self, client, userdata, flags, rc):
        """Callback when connected to broker"""
        if rc == 0:
            print(f"? Connected to MQTT broker at {self.broker}:{self.port}")
            self.connected = True
        else:
            print(f"? Connection failed with code {rc}")
            
    def on_disconnect(self, client, userdata, rc):
        """Callback when disconnected from broker"""
        print(f"Disconnected from MQTT broker (code: {rc})")
        self.connected = False

    def connect(self):
        """Connect to MQTT broker"""
        try:
            print(f"Connecting to MQTT broker at {self.broker}:{self.port}...")
            self.client.connect(self.broker, self.port, 60)
            self.client.loop_start()
            
            # Wait for connection
            timeout = 5
            start_time = time.time()
            while not self.connected and (time.time() - start_time) < timeout:
                time.sleep(0.1)
                
            if not self.connected:
                raise ConnectionError("Failed to connect within timeout")
                
        except Exception as e:
            print(f"? Error connecting to broker: {e}")
            raise
            
    def disconnect(self):
        """Disconnect from MQTT broker"""
        self.client.loop_stop()
        self.client.disconnect()
        print("Disconnected from broker")
        
    def generate_freight_data(self, freight_id="test-truck-01"):
        """Generate realistic freight sensor data"""
        # Simulate refrigerated transport conditions
        base_temp = 4.0  # Target refrigeration temperature (?C)
        base_humidity = 65.0  # Target humidity (%)
        
        # Add realistic fluctuations
        temperature = base_temp + random.gauss(0, 0.5)
        humidity = base_humidity + random.gauss(0, 2.0)
        
        # Simulate RSL degradation over time
        rsl = max(0, 100 - random.uniform(0, 5))
        
        # Generate location (simulated movement)
        lat = 21.1458 + random.uniform(-0.01, 0.01)
        lon = 79.0882 + random.uniform(-0.01, 0.01)
        
        payload = {
            "timestamp": int(time.time()),
            "freight_id": freight_id,
            "temperature": round(temperature, 2),
            "humidity": round(humidity, 2),
            "rsl": round(rsl, 2),
            "location": {
                "lat": round(lat, 6),
                "lon": round(lon, 6)
            },
            "cargo_batch_id": "batch-test-001",
            "status": "in_transit"
        }
        
        return payload

    def publish_data(self, topic, payload):
        """Publish data to MQTT topic"""
        try:
            json_payload = json.dumps(payload)
            result = self.client.publish(topic, json_payload, qos=0)
            
            if result.rc == mqtt.MQTT_ERR_SUCCESS:
                print(f"? Published to {topic}: {json_payload}")
                return True
            else:
                print(f"? Failed to publish to {topic}")
                return False
                
        except Exception as e:
            print(f"? Error publishing: {e}")
            return False
            
    def run(self, interval=1.0, duration=None):
        """
        Run test publisher
        
        Args:
            interval: Time between messages in seconds
            duration: Total duration in seconds (None = run forever)
        """
        print(f"\nStarting test publisher...")
        print(f"Publishing to topic: iot/freight/test")
        print(f"Interval: {interval}s")
        print(f"Duration: {duration}s" if duration else "Duration: infinite")
        print(f"Press Ctrl+C to stop\n")
        
        start_time = time.time()
        message_count = 0
        
        try:
            while True:
                # Check duration
                if duration and (time.time() - start_time) >= duration:
                    print(f"\n? Completed {duration}s test run")
                    break
                
                # Generate and publish data
                data = self.generate_freight_data()
                self.publish_data("iot/freight/test", data)
                
                message_count += 1
                
                # Wait for next interval
                time.sleep(interval)
                
        except KeyboardInterrupt:
            print(f"\n\n? Stopped by user")
        finally:
            print(f"Total messages published: {message_count}")


def main():
    """Main entry point"""
    import argparse
    
    parser = argparse.ArgumentParser(description="Test MQTT Publisher")
    parser.add_argument("--broker", default="localhost", help="MQTT broker host")
    parser.add_argument("--port", type=int, default=1883, help="MQTT broker port")
    parser.add_argument("--interval", type=float, default=1.0, help="Publish interval (seconds)")
    parser.add_argument("--duration", type=float, help="Test duration (seconds)")
    
    args = parser.parse_args()
    
    # Create and run publisher
    publisher = TestPublisher(broker=args.broker, port=args.port)
    
    try:
        publisher.connect()
        publisher.run(interval=args.interval, duration=args.duration)
    except Exception as e:
        print(f"\n? Error: {e}")
    finally:
        publisher.disconnect()


if __name__ == "__main__":
    main()