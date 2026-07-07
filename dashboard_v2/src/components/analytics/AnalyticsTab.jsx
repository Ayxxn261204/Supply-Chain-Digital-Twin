/**
 * AnalyticsTab Component
 * 
 * Displays comprehensive analytics and time-series data visualizations for the supply chain simulation.
 * Includes charts for weather, warehouse inventory, fleet utilization, retailer stock, and truck metrics.
 * 
 * @component
 */
import React, { useState, useEffect, useCallback } from 'react';
import PropTypes from 'prop-types';
import { Line } from 'react-chartjs-2';
import {
    Chart as ChartJS,
    CategoryScale,
    LinearScale,
    PointElement,
    LineElement,
    Title,
    Tooltip,
    Legend,
    Filler
} from 'chart.js';
import './AnalyticsTab.css';
import { API_URL, calculatePollInterval, DEFAULT_SIM_SPEED, DEFAULT_TIMESTEP_MINUTES } from '../../constants';

// Register Chart.js components
ChartJS.register(
    CategoryScale,
    LinearScale,
    PointElement,
    LineElement,
    Title,
    Tooltip,
    Legend,
    Filler
);

const AnalyticsTab = ({ simulationId, simData }) => {
    const [selectedWarehouse, setSelectedWarehouse] = useState('');
    const [selectedRetailer, setSelectedRetailer] = useState('');
    const [selectedTruck, setSelectedTruck] = useState('');

    const [weatherData, setWeatherData] = useState([]);
    const [warehouseInventory, setWarehouseInventory] = useState([]);
    const [warehouseFleet, setWarehouseFleet] = useState([]);
    const [retailerStock, setRetailerStock] = useState([]);
    const [truckFuel, setTruckFuel] = useState([]);
    const [truckFatigue, setTruckFatigue] = useState([]);

    const [warehouseCache, setWarehouseCache] = useState({});
    const [retailerCache, setRetailerCache] = useState({});
    const [truckCache, setTruckCache] = useState({});

    const getAggregationWindow = useCallback(() => {
        if (!simData?.time_step_minutes) return '1m';
        return `${simData.time_step_minutes}m`;
    }, [simData]);

    const [pollInterval, setPollInterval] = useState(
        calculatePollInterval(DEFAULT_TIMESTEP_MINUTES, DEFAULT_SIM_SPEED)
    );

    useEffect(() => {
        if (simData?.warehouses?.length > 0 && !selectedWarehouse) setSelectedWarehouse(simData.warehouses[0].id);
        if (simData?.retailers?.length > 0 && !selectedRetailer) setSelectedRetailer(simData.retailers[0].id);
        if (simData?.trucks?.length > 0 && !selectedTruck) setSelectedTruck(simData.trucks[0].truck_id);
    }, [simData]);

    useEffect(() => {
        if (!simulationId) return;
        fetch(`${API_URL}/simulations/${simulationId}`)
            .then(res => res.json())
            .then(metadata => {
                const speed = metadata.speed || 10;
                const timestep = metadata.time_step_minutes || 1;
                const interval = (timestep * 60 / speed) * 1000;
                setPollInterval(interval);
            })
            .catch(err => console.error('Metadata fetch error:', err));
    }, [simulationId]);

    // Data Fetching Functions
    const fetchWeatherData = useCallback(() => {
        if (!simulationId) return;
        fetch(`${API_URL}/timeseries/weather?run_id=${simulationId}`)
            .then(res => res.json())
            .then(data => {
                const sortedData = data.sort((a, b) => (a.timestamp || 0) - (b.timestamp || 0));
                let targetPoints = 1500;
                // Simple downsampling if needed
                if (sortedData.length > targetPoints) {
                    const step = Math.ceil(sortedData.length / targetPoints);
                    setWeatherData(sortedData.filter((_, index) => index % step === 0));
                } else {
                    setWeatherData(sortedData);
                }
            })
            .catch(console.error);
    }, [simulationId]);

    const fetchWarehouseData = useCallback((forceRefresh = false) => {
        if (!simulationId || !selectedWarehouse) return;
        if (!forceRefresh && warehouseCache[selectedWarehouse]) {
            setWarehouseInventory(warehouseCache[selectedWarehouse].inventory);
            setWarehouseFleet(warehouseCache[selectedWarehouse].fleet);
            return;
        }
        const window = getAggregationWindow();
        Promise.all([
            fetch(`${API_URL}/timeseries/warehouse/inventory?run_id=${simulationId}&warehouse_id=${selectedWarehouse}&window=${window}`),
            fetch(`${API_URL}/timeseries/warehouse/fleet?run_id=${simulationId}&warehouse_id=${selectedWarehouse}&window=${window}`)
        ])
            .then(([inv, fleet]) => Promise.all([inv.json(), fleet.json()]))
            .then(([invData, fleetData]) => {
                setWarehouseInventory(invData);
                setWarehouseFleet(fleetData);
                setWarehouseCache(prev => ({ ...prev, [selectedWarehouse]: { inventory: invData, fleet: fleetData } }));
            })
            .catch(console.error);
    }, [simulationId, selectedWarehouse, getAggregationWindow, warehouseCache]);

    const fetchRetailerData = useCallback((forceRefresh = false) => {
        if (!simulationId || !selectedRetailer) return;
        if (!forceRefresh && retailerCache[selectedRetailer]) {
            setRetailerStock(retailerCache[selectedRetailer].stock);
            return;
        }
        const window = getAggregationWindow();
        fetch(`${API_URL}/timeseries/retailer/stock?run_id=${simulationId}&retailer_id=${selectedRetailer}&window=${window}`)
            .then(res => res.json())
            .then(data => {
                setRetailerStock(data);
                setRetailerCache(prev => ({ ...prev, [selectedRetailer]: { stock: data } }));
            })
            .catch(console.error);
    }, [simulationId, selectedRetailer, getAggregationWindow, retailerCache]);

    const fetchTruckData = useCallback((forceRefresh = false) => {
        if (!simulationId || !selectedTruck) return;
        if (!forceRefresh && truckCache[selectedTruck]) {
            setTruckFuel(truckCache[selectedTruck].fuel);
            setTruckFatigue(truckCache[selectedTruck].fatigue);
            return;
        }
        const window = getAggregationWindow();
        Promise.all([
            fetch(`${API_URL}/timeseries/truck/fuel?run_id=${simulationId}&truck_id=${selectedTruck}&window=${window}`),
            fetch(`${API_URL}/timeseries/truck/fatigue?run_id=${simulationId}&truck_id=${selectedTruck}&window=${window}`)
        ])
            .then(([fuel, fatigue]) => Promise.all([fuel.json(), fatigue.json()]))
            .then(([fuelData, fatigueData]) => {
                setTruckFuel(fuelData);
                setTruckFatigue(fatigueData);
                setTruckCache(prev => ({ ...prev, [selectedTruck]: { fuel: fuelData, fatigue: fatigueData } }));
            })
            .catch(console.error);
    }, [simulationId, selectedTruck, getAggregationWindow, truckCache]);

    useEffect(() => { fetchWeatherData(); const i = setInterval(fetchWeatherData, pollInterval); return () => clearInterval(i); }, [fetchWeatherData, pollInterval]);
    useEffect(() => { fetchWarehouseData(); const i = setInterval(() => fetchWarehouseData(true), pollInterval); return () => clearInterval(i); }, [fetchWarehouseData, pollInterval]);
    useEffect(() => { fetchRetailerData(); const i = setInterval(() => fetchRetailerData(true), pollInterval); return () => clearInterval(i); }, [fetchRetailerData, pollInterval]);
    useEffect(() => { fetchTruckData(); const i = setInterval(() => fetchTruckData(true), pollInterval); return () => clearInterval(i); }, [fetchTruckData, pollInterval]);

    const formatTime = (isoString, timestamp) => {
        if (timestamp !== undefined) {
            const day = Math.floor(timestamp / 1440);
            const hours = Math.floor((timestamp % 1440) / 60);
            const minutes = Math.floor((timestamp % 1440) % 60);
            return `Day ${day}, ${String(hours).padStart(2, '0')}:${String(minutes).padStart(2, '0')}`;
        }
        return new Date(isoString).toLocaleTimeString();
    };

    // --- CHART COLORS (MODERN DARK THEME) ---
    const colors = {
        grid: '#374054',      // More Visible Grid Lines
        text: '#d1d5db',      // Light Grey Text for Axes (High Contrast)
        textMain: '#f3f4f6',  // Very Light Text for Legend
        tooltipBg: '#1e2330', // Dark Tooltip Background
        tooltipText: '#f3f4f6', // Very Light Tooltip Text
        lines: {
            red: '#ef4444',
            blue: '#3b82f6',
            green: '#10b981',
            amber: '#f59e0b',
            purple: '#8b5cf6'
        }
    };

    const chartOptions = {
        responsive: true,
        maintainAspectRatio: false,
        color: colors.text,
        borderColor: colors.grid,
        plugins: {
            legend: {
                position: 'top',
                labels: {
                    color: colors.textMain,
                    font: { family: '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif', weight: 500 },
                    padding: 12
                }
            },
            tooltip: {
                backgroundColor: colors.tooltipBg,
                titleColor: colors.tooltipText,
                bodyColor: colors.tooltipText,
                borderColor: colors.grid,
                borderWidth: 1,
                padding: 12,
                displayColors: true,
                cornerRadius: 6
            }
        },
        scales: {
            y: {
                beginAtZero: true,
                min: 0,
                grid: { color: colors.grid, borderColor: colors.grid },
                ticks: {
                    color: colors.text,
                    font: { family: 'monospace' },
                    callback: (val) => Number.isInteger(val) ? val : val.toFixed(1)
                }
            },
            x: {
                grid: { color: colors.grid, borderColor: colors.grid },
                ticks: {
                    color: colors.text,
                    font: { family: 'monospace' },
                    maxTicksLimit: 8,
                    autoSkip: true,
                    autoSkipPadding: 10
                }
            }
        }
    };

    const percentageChartOptions = {
        ...chartOptions,
        scales: {
            ...chartOptions.scales,
            y: {
                beginAtZero: true, min: 0, max: 100,
                grid: { color: colors.grid, borderColor: colors.grid },
                ticks: { color: colors.text, font: { family: 'monospace' }, callback: (val) => val + '%' }
            }
        }
    };

    // Chart Data Objects
    const weatherChartOptions = {
        ...chartOptions,
        scales: {
            ...chartOptions.scales,
            y: {
                grid: { color: colors.grid, borderColor: colors.grid },
                ticks: {
                    color: colors.text,
                    font: { family: 'monospace' },
                    callback: (val) => val.toFixed(1) + '°C'
                }
                // No beginAtZero / min:0 — auto-scale so 16-27°C variation is visible
            }
        }
    };
    const weatherChartData = {
        labels: weatherData.map(w => formatTime(w.time, w.timestamp)),
        datasets: [{ label: 'Temperature (°C)', data: weatherData.map(w => w.temperature), borderColor: colors.lines.red, tension: 0.4 }]
    };
    const warehouseInventoryChartData = {
        labels: warehouseInventory.map(d => formatTime(d.time, d.timestamp)),
        datasets: [{ label: 'Inventory (kg)', data: warehouseInventory.map(d => d.value), borderColor: colors.lines.green, backgroundColor: 'rgba(16, 185, 129, 0.1)', fill: true, tension: 0.1 }]
    };
    const warehouseFleetChartData = {
        labels: warehouseFleet.sort((a, b) => (a.timestamp || 0) - (b.timestamp || 0)).map(d => formatTime(d.time, d.timestamp)),
        datasets: [
            { label: 'Available Trucks', data: warehouseFleet.sort((a, b) => (a.timestamp || 0) - (b.timestamp || 0)).map(d => d.available), borderColor: colors.lines.blue, backgroundColor: 'rgba(59, 130, 246, 0.2)', fill: true },
            { label: 'In Transit', data: warehouseFleet.sort((a, b) => (a.timestamp || 0) - (b.timestamp || 0)).map(d => d.in_transit), borderColor: colors.lines.amber, backgroundColor: 'rgba(245, 158, 11, 0.2)', fill: true }
        ]
    };
    const retailerStockChartData = {
        labels: retailerStock.map(d => formatTime(d.time, d.timestamp)),
        datasets: [{ label: 'Stock (kg)', data: retailerStock.map(d => d.value), borderColor: colors.lines.purple, backgroundColor: 'rgba(139, 92, 246, 0.1)', fill: true, tension: 0.1 }]
    };
    const truckFuelChartData = {
        labels: truckFuel.map(d => formatTime(d.time, d.timestamp)),
        datasets: [{ label: 'Fuel Level (%)', data: truckFuel.map(d => d.value), borderColor: colors.lines.amber, backgroundColor: 'rgba(245, 158, 11, 0.1)', fill: true, tension: 0.1 }]
    };
    const truckFatigueChartData = {
        labels: truckFatigue.map(d => formatTime(d.time, d.timestamp)),
        datasets: [{ label: 'Driver Fatigue (hours)', data: truckFatigue.map(d => d.value), borderColor: colors.lines.red, backgroundColor: 'rgba(244, 63, 94, 0.1)', fill: true, tension: 0.1 }]
    };

    return (
        <div className="analytics-tab">
            <h2>📊 Analytics & Trends</h2>

            <div className="analytics-section">
                <h3>🌦️ Weather History</h3>
                <div className="chart-container" style={{ height: '250px' }}>
                    <Line data={weatherChartData} options={weatherChartOptions} />
                </div>
                <div className="weather-list">
                    {weatherData.slice(-5).reverse().map((w) => (
                        <div key={`weather-${w.timestamp || w.time}`} className="weather-item">
                            <span className="weather-time">{formatTime(w.time, w.timestamp)}</span>
                            <span className={`weather-state state-${w.state}`}>{w.state}</span>
                            <span className="weather-temp">{w.temperature?.toFixed(1)}°C</span>
                        </div>
                    ))}
                </div>
            </div>

            <div className="analytics-section">
                <h3>🏭 Warehouse Analytics</h3>
                <select value={selectedWarehouse} onChange={(e) => setSelectedWarehouse(e.target.value)} className="entity-selector">
                    {simData?.warehouses?.map(wh => <option key={wh.id} value={wh.id}>{wh.id}</option>)}
                </select>
                <div className="chart-row">
                    <div className="chart-col">
                        <h4>Inventory Over Time</h4>
                        <div className="chart-container" style={{ height: '250px' }}><Line data={warehouseInventoryChartData} options={chartOptions} /></div>
                    </div>
                    <div className="chart-col">
                        <h4>Fleet Utilization</h4>
                        <div className="chart-container" style={{ height: '250px' }}><Line data={warehouseFleetChartData} options={chartOptions} /></div>
                    </div>
                </div>
            </div>

            <div className="analytics-section">
                <h3>🏪 Retailer Analytics</h3>
                <select value={selectedRetailer} onChange={(e) => setSelectedRetailer(e.target.value)} className="entity-selector">
                    {simData?.retailers?.map(ret => <option key={ret.id} value={ret.id}>{ret.id}</option>)}
                </select>
                <div className="chart-container" style={{ height: '250px' }}>
                    <Line data={retailerStockChartData} options={chartOptions} />
                </div>
            </div>

            <div className="analytics-section">
                <h3>🚛 Truck Analytics</h3>
                <select value={selectedTruck} onChange={(e) => setSelectedTruck(e.target.value)} className="entity-selector">
                    {simData?.trucks?.map(truck => <option key={truck.truck_id} value={truck.truck_id}>{truck.truck_id}</option>)}
                </select>
                <div className="chart-row">
                    <div className="chart-col">
                        <h4>Fuel Level</h4>
                        <div className="chart-container" style={{ height: '250px' }}><Line data={truckFuelChartData} options={percentageChartOptions} /></div>
                    </div>
                    <div className="chart-col">
                        <h4>Fatigue</h4>
                        <div className="chart-container" style={{ height: '250px' }}><Line data={truckFatigueChartData} options={chartOptions} /></div>
                    </div>
                </div>
            </div>
        </div>
    );
};

AnalyticsTab.propTypes = {
    simulationId: PropTypes.string.isRequired,
    simData: PropTypes.object.isRequired
};

export default AnalyticsTab;
