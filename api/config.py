"""
API Configuration using Pydantic Settings
Centralizes all environment variables and configuration
"""
from pydantic_settings import BaseSettings
from pydantic import ConfigDict
from typing import List


class Settings(BaseSettings):
    """Application settings from environment variables"""
    
    # InfluxDB Configuration - field names match env vars exactly
    influx_url: str = "http://localhost:8086"
    influx_token: str  # Required, matches INFLUX_TOKEN
    influx_org: str = "digital-twin"
    influx_bucket: str = "supply-chain"
    
    # MQTT Configuration  
    mqtt_broker: str = "localhost"
    mqtt_port: int = 1883
    mqtt_topic_prefix: str = "digital-twin"
    
    # API Configuration
    api_title: str = "Supply Chain Digital Twin API"
    api_version: str = "1.0.0"
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    
    # CORS Configuration
    cors_origins: List[str] = [
        "http://localhost:5173",
        "http://localhost:5174",
        "http://localhost:5175",  # dashboard_v2
        "http://localhost:3000"
    ]
    cors_allow_credentials: bool = True
    cors_allow_methods: List[str] = ["GET", "POST", "PUT", "DELETE"]
    cors_allow_headers: List[str] = ["*"]
    
    # Query Defaults
    default_time_range_days: int = 7
    default_limit: int = 50
    max_limit: int = 1000
    
    model_config = ConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_insensitive=False,
        extra='ignore'  # Ignore extra fields from .env
    )


# Global settings instance
settings = Settings()
