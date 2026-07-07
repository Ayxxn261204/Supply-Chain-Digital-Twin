// Dashboard v2 - Application Constants
// Centralized configuration to avoid magic numbers and hardcoded values

// API Configuration
export const API_URL = import.meta.env.VITE_API_URL || 'http://localhost:8000/api';
export const MAX_RETRY_DELAY = 5000; // Maximum delay between retry attempts (ms)

// Simulation Defaults
export const DEFAULT_SIM_SPEED = 50;
export const DEFAULT_TIMESTEP_MINUTES = 1;
export const DEFAULT_DURATION_DAYS = 7;

// Polling Intervals
export const calculatePollInterval = (timestepMinutes, speed) => {
    return (timestepMinutes * 60 / speed) * 1000; // Returns milliseconds
};

// Map Configuration (Nagpur, India)
export const MAP_CENTER = {
    lat: 21.1458,
    lon: 79.0882,
    zoom: 11
};

// Retry Configuration
export const RETRY_CONFIG = {
    maxRetries: 3,
    baseDelay: 1000, // 1 second
    maxDelay: 5000 // 5 seconds
};

// Chart Configuration
export const CHART_COLORS = {
    inventory: 'rgb(75, 192, 192)',
    inventoryBg: 'rgba(75, 192, 192, 0.2)',
    fleetAvailable: 'rgb(54, 162, 235)',
    fleetAvailableBg: 'rgba(54, 162, 235, 0.5)',
    fleetInTransit: 'rgb(255, 159, 64)',
    fleetInTransitBg: 'rgba(255, 159, 64, 0.5)',
    stock: 'rgb(153, 102, 255)',
    stockBg: 'rgba(153, 102, 255, 0.2)',
    fuel: 'rgb(255, 205, 86)',
    fuelBg: 'rgba(255, 205, 86, 0.2)',
    fatigue: 'rgb(255, 99, 132)',
    fatigueBg: 'rgba(255, 99, 132, 0.2)',
    temperature: 'rgb(255, 99, 132)'
};

// Weather Icons
export const WEATHER_ICONS = {
    'clear': '☀️',
    'light_rain': '🌦️',
    'rain': '🌧️',
    'heavy_rain': '⛈️',
    'fog': '🌫️',
    'default': '🌤️'
};

// Truck Status Display Configuration
export const TRUCK_STATUS_INFO = {
    idle: { icon: '⏸️', color: '#64748b', label: 'Idle' },
    in_transit: { icon: '🚚', color: '#10b981', label: 'In Transit' },
    loading: { icon: '📦', color: '#3b82f6', label: 'Loading' },
    unloading: { icon: '📤', color: '#8b5cf6', label: 'Unloading' },
    returning: { icon: '🔄', color: '#f59e0b', label: 'Returning' },
    crashed: { icon: '💥', color: '#ef4444', label: 'CRASHED' },
    broken: { icon: '🔧', color: '#ef4444', label: 'BROKEN' }
};

// Debug Utility
export const DEBUG = import.meta.env.DEV;
export const debug = DEBUG ? console.log.bind(console) : () => { };
export const debugWarn = DEBUG ? console.warn.bind(console) : () => { };
export const debugError = console.error.bind(console); // Always log errors
