/**
 * WeatherPanel Component — current weather + 3-hour AI forecast
 */
import React, { useState, useEffect, useCallback } from 'react';
import PropTypes from 'prop-types';
import axios from 'axios';
import { API_URL, WEATHER_ICONS, calculatePollInterval, DEFAULT_SIM_SPEED, DEFAULT_TIMESTEP_MINUTES } from '../../constants';

const WEATHER_ICONS_EXTENDED = {
    ...WEATHER_ICONS,
    'clear': '☀️',
    'partly_cloudy': '⛅',
    'cloudy': '☁️',
    'light_rain': '🌦️',
    'rain': '🌧️',
    'heavy_rain': '⛈️',
    'fog': '🌫️',
};

function WeatherPanel({ runId, currentTime }) {
    const [weatherData, setWeatherData] = useState([]);
    const [currentWeather, setCurrentWeather] = useState(null);
    const [forecast, setForecast] = useState([]);
    const [loading, setLoading] = useState(true);
    const [pollInterval, setPollInterval] = useState(
        calculatePollInterval(DEFAULT_TIMESTEP_MINUTES, DEFAULT_SIM_SPEED)
    );

    useEffect(() => {
        if (!runId) return;
        axios.get(`${API_URL}/simulations/${runId}`)
            .then(res => {
                const speed = res.data.speed || 10;
                const timestep = res.data.time_step_minutes || 1;
                setPollInterval((timestep * 60 / speed) * 1000);
            })
            .catch(() => {});
    }, [runId]);

    const fetchWeatherData = useCallback(async () => {
        if (!runId) return;
        try {
            if (weatherData.length === 0) setLoading(true);
            const [tsRes, forecastRes] = await Promise.all([
                axios.get(`${API_URL}/timeseries/weather?run_id=${runId}`),
                axios.get(`${API_URL}/ai/weather-forecast?run_id=${runId}`).catch(() => ({ data: null })),
            ]);
            setWeatherData(tsRes.data || []);
            if (forecastRes.data?.forecast) {
                setForecast(forecastRes.data.forecast);
            }
        } catch (err) {
            // silently fail
        } finally {
            if (weatherData.length === 0) setLoading(false);
        }
    }, [runId, weatherData.length]);

    useEffect(() => {
        fetchWeatherData();
        const interval = setInterval(fetchWeatherData, pollInterval);
        return () => clearInterval(interval);
    }, [runId, pollInterval, fetchWeatherData]);

    useEffect(() => {
        if (!weatherData.length || currentTime == null) return;
        const sorted = [...weatherData].sort((a, b) => a.timestamp - b.timestamp);
        const current = sorted.filter(w => w.timestamp <= currentTime).pop();
        setCurrentWeather(current || sorted[0]);
    }, [weatherData, currentTime]);

    if (loading) {
        return (
            <div className="panel weather-panel">
                <h3>🌦️ Weather</h3>
                <div className="weather-display">Loading...</div>
            </div>
        );
    }

    if (!currentWeather) {
        return (
            <div className="panel weather-panel">
                <h3>🌦️ Weather</h3>
                <div className="weather-display">No data</div>
            </div>
        );
    }

    const icon = WEATHER_ICONS_EXTENDED[currentWeather.state] || '🌤️';
    const stateName = (currentWeather.state || 'unknown')
        .replace(/_/g, ' ')
        .split(' ')
        .map(w => w.charAt(0).toUpperCase() + w.slice(1))
        .join(' ');

    return (
        <div className="panel weather-panel">
            <h3>🌦️ Weather</h3>
            <div style={{ display: 'flex', alignItems: 'flex-start', gap: '0', marginTop: '0.75rem' }}>
                {/* Current weather — left 50% */}
                <div style={{ flex: '0 0 50%', paddingRight: '12px' }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem' }}>
                        <span style={{ fontSize: '1.5rem' }}>{icon}</span>
                        <div>
                            <div style={{ fontSize: '1rem', fontWeight: 500 }}>{stateName}</div>
                            {currentWeather.temperature != null && (
                                <div style={{ fontSize: '0.8rem', opacity: 0.7, marginTop: '2px' }}>
                                    {currentWeather.temperature.toFixed(1)}°C · {currentWeather.humidity?.toFixed(0)}% RH
                                </div>
                            )}
                        </div>
                    </div>
                </div>

                {/* 3-hour forecast — right 50% */}
                {forecast.length > 0 && (
                    <div style={{ flex: '0 0 50%', borderLeft: '1px solid rgba(255,255,255,0.1)', paddingLeft: '12px' }}>
                        <div style={{ fontSize: '0.7rem', opacity: 0.5, textTransform: 'uppercase', letterSpacing: '0.05em', marginBottom: '6px' }}>Forecast</div>
                        <div style={{ display: 'flex', gap: '4px', justifyContent: 'space-around' }}>
                            {forecast.map(f => (
                                <div key={f.hours_ahead} style={{ textAlign: 'center', fontSize: '0.78rem' }}>
                                    <div style={{ fontSize: '1rem' }}>{WEATHER_ICONS_EXTENDED[f.state] || '🌤️'}</div>
                                    <div style={{ opacity: 0.55, marginTop: '1px' }}>+{f.hours_ahead}h</div>
                                    <div style={{ fontWeight: 600 }}>{f.temperature_celsius}°C</div>
                                </div>
                            ))}
                        </div>
                    </div>
                )}
            </div>
        </div>
    );
}

WeatherPanel.propTypes = {
    runId: PropTypes.string.isRequired,
    currentTime: PropTypes.number.isRequired,
};

export default WeatherPanel;
