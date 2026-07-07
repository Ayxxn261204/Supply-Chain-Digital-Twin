/**
 * App Component - Main Dashboard Application
 * 
 * Real-time supply chain digital twin dashboard for visualizing and monitoring
 * warehouse, retailer, and truck operations across the Nagpur region.
 * 
 * Features:
 * - Interactive Leaflet map with entity markers
 * - Real-time data polling synchronized with simulation speed
 * - Tab navigation between Dashboard and Analytics views
 * - Comprehensive entity panels with live telemetry
 * - Page Visibility API integration for resource efficiency
 * 
 * @component
 */
import { useState, useEffect, useRef, useCallback } from 'react';
import axios from 'axios';
import 'leaflet/dist/leaflet.css';
import './App.css';
import TimePanel from './components/panels/TimePanel';
import WeatherPanel from './components/panels/WeatherPanel';
import AnalyticsTab from './components/analytics/AnalyticsTab';
import AIInsightsPanel from './components/panels/AIInsightsPanel';
import SimulationSelector from './components/dashboard/SimulationSelector';
import MapView from './components/dashboard/MapView';
import StatsCards from './components/dashboard/StatsCards';
import EntityPanel from './components/dashboard/EntityPanel';
import TruckList from './components/dashboard/TruckList';
import ScenarioControlPanel from './components/panels/ScenarioControlPanel';
import { fixLeafletDefaultIcon } from './utils/mapHelpers';
import { fetchWithRetry } from './utils/fetchWithRetry';
import {
  API_URL,
  DEFAULT_SIM_SPEED,
  DEFAULT_TIMESTEP_MINUTES,
  DEFAULT_DURATION_DAYS,
  RETRY_CONFIG,
} from './constants';

// Fix Leaflet default icon issue
fixLeafletDefaultIcon();

function App() {
  const [currentSim, setCurrentSim] = useState(null);
  const [availableSimulations, setAvailableSimulations] = useState([]);
  const [entities, setEntities] = useState({ warehouse_ids: [], retailer_ids: [], truck_ids: [] });
  const [coordinates, setCoordinates] = useState({ warehouses: [], retailers: [] });
  const [trucks, setTrucks] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [simMetadata, setSimMetadata] = useState({
    speed: DEFAULT_SIM_SPEED,
    time_step_minutes: DEFAULT_TIMESTEP_MINUTES,
  });
  const [pollInterval, setPollInterval] = useState(
    (DEFAULT_TIMESTEP_MINUTES * 60) / DEFAULT_SIM_SPEED * 1000
  );

  // State for full entity data (not just coordinates)
  const [warehouseStates, setWarehouseStates] = useState([]);
  const [retailerStates, setRetailerStates] = useState([]);

  // Tab visibility and request cancellation state
  const [isTabVisible, setIsTabVisible] = useState(true);
  const abortControllerRef = useRef(null);

  // Tab navigation state
  const [currentTab, setCurrentTab] = useState('dashboard'); // 'dashboard' or 'analytics'

  // Fetch simulation metadata
  const fetchSimulationMetadata = useCallback(async (runId) => {
    try {
      const response = await axios.get(`${API_URL}/simulations/${runId}`);
      const speed = response.data.speed || DEFAULT_SIM_SPEED;
      const timestep = response.data.time_step_minutes || DEFAULT_TIMESTEP_MINUTES;
      setSimMetadata({ speed, time_step_minutes: timestep });

      // Calculate poll interval: (timestep_minutes * 60 / speed) * 1000ms
      const interval = ((timestep * 60) / speed) * 1000;
      setPollInterval(interval);

      console.log(
        `🚀 Sim: ${runId} | Speed: ${speed}x | Timestep: ${timestep}min | Poll: ${interval}ms (synced with IoT)`
      );
    } catch (error) {
      console.error('Error fetching simulation metadata:', error);
      // Use defaults on error
    }
  }, []);

  // Fetch complete dashboard state in ONE API call with retry logic
  const fetchCompleteState = useCallback(async (runId, signal = null) => {
    try {
      console.log(`📡 Fetching complete state for ${runId}...`);

      // Use retry logic for resilience, pass abort signal
      const response = await fetchWithRetry(
        `${API_URL}/simulations/${runId}/complete-state`,
        signal ? { signal } : {},
        RETRY_CONFIG.maxRetries
      );
      const data = response.data;

      // Update ALL state atomically from single response
      setSimMetadata({
        speed: data.simulation.speed,
        time_step_minutes: data.simulation.time_step_minutes,
        duration_days: data.simulation.duration_days || DEFAULT_DURATION_DAYS,
      });

      // Calculate poll interval from metadata
      const interval = ((data.simulation.time_step_minutes * 60) / data.simulation.speed) * 1000;
      setPollInterval(interval);

      // Set entity IDs (for counts)
      setEntities({
        warehouse_ids: data.warehouses.map((w) => w.id),
        retailer_ids: data.retailers.map((r) => r.id),
        truck_ids: data.trucks.map((t) => t.id),
      });

      // Set coordinates (for map markers)
      setCoordinates({
        warehouses: data.warehouses.map((w) => ({ id: w.id, lat: w.latitude, lon: w.longitude })),
        retailers: data.retailers.map((r) => ({ id: r.id, lat: r.latitude, lon: r.longitude })),
      });

      // Set trucks (with full telemetry)
      setTrucks(
        data.trucks.map((t) => ({
          truck_id: t.id,
          location: [t.latitude ?? 0, t.longitude ?? 0],
          status: t.status ?? 'idle',
          speed_kmh: t.speed_kmh ?? 0,
          fuel_percent: t.fuel_percent ?? 0,
          cargo_kg: t.current_load_kg ?? 0,
          cargo_rsl: t.cargo_rsl ?? null,
          timestamp: t.timestamp ?? 0,
        }))
      );

      // Store full state for dropdowns
      setWarehouseStates(data.warehouses);
      setRetailerStates(data.retailers);

      setLoading(false);
      setError(null); // Clear any previous errors

      console.log(
        `✅ Complete state loaded: ${data.counts.warehouses} WH, ${data.counts.retailers} Ret, ${data.counts.trucks} Trucks`
      );
    } catch (error) {
      // Only show error if NOT a cancelled request
      if (!axios.isCancel(error) && error.code !== 'ERR_CANCELED' && error.name !== 'AbortError') {
        console.error('❌ Fatal error fetching complete state:', error);
        setError('Failed to fetch dashboard data after multiple retries');
      } else {
        console.log('🔄 Request cancelled (tab switch or component unmount)');
      }
      setLoading(false);
    }
  }, []);

  // Page Visibility API - Pause polling when tab is inactive for resource efficiency
  useEffect(() => {
    const handleVisibilityChange = () => {
      const visible = !document.hidden;
      setIsTabVisible(visible);

      if (visible && currentSim) {
        // Tab became visible - fetch fresh data immediately
        console.log('📱 Tab visible - fetching fresh data');
        fetchCompleteState(currentSim);
      } else if (!visible) {
        console.log('🌙 Tab hidden - pausing polling');
      }
    };

    document.addEventListener('visibilitychange', handleVisibilityChange);
    return () => document.removeEventListener('visibilitychange', handleVisibilityChange);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [currentSim]); // fetchCompleteState is stable, safe to omit

  // Fetch all available simulations on component mount
  useEffect(() => {
    fetchSimulations();
    // Empty dependency array - only run once on mount
  }, []);

  useEffect(() => {
    if (currentSim) {
      // Cancel any previous requests when switching simulations
      if (abortControllerRef.current) {
        abortControllerRef.current.abort();
        console.log('🔄 Cancelled previous requests for sim switch');
      }

      // Create new abort controller for this simulation
      abortControllerRef.current = new AbortController();

      fetchSimulationMetadata(currentSim);
      fetchCompleteState(currentSim, abortControllerRef.current.signal);
    }

    // Cleanup: abort requests when component unmounts or sim changes
    return () => {
      if (abortControllerRef.current) {
        abortControllerRef.current.abort();
      }
    };
  }, [currentSim, fetchSimulationMetadata, fetchCompleteState]);

  // Polling logic - only runs when tab is visible
  useEffect(() => {
    if (currentSim && pollInterval && isTabVisible) {
      console.log(`⏱️  Starting polling every ${pollInterval}ms (tab visible)`);
      const interval = setInterval(() => {
        if (document.hidden) {
          console.log('⏸️  Skipping poll - tab hidden');
          return;
        }
        fetchCompleteState(currentSim, abortControllerRef.current?.signal);
      }, pollInterval);

      return () => {
        console.log('🛑 Stopping polling');
        clearInterval(interval);
      };
    }
  }, [currentSim, pollInterval, isTabVisible, fetchCompleteState]);

  const fetchSimulations = async () => {
    try {
      const response = await axios.get(`${API_URL}/simulations`);
      if (response.data.length > 0) {
        // Store all available simulations
        const sorted = response.data.sort((a, b) => b.run_id.localeCompare(a.run_id));
        setAvailableSimulations(sorted);

        // Auto-select the latest simulation ONLY if none selected yet
        // Use functional update to prevent race condition
        setCurrentSim((prev) => prev || sorted[0].run_id);
      } else {
        setError('No simulations found. Please start a simulation first.');
      }
    } catch (error) {
      console.error('Error fetching simulations:', error);
      setError('Failed to connect to backend API');
    } finally {
      setLoading(false);
    }
  };

  if (loading) {
    return <div className="loading">⏳ Loading IoT data from InfluxDB...</div>;
  }

  if (error) {
    return <div className="loading error">❌ {error}</div>;
  }

  return (
    <div className="app">
      <header className="header">
        <h1>🚛 Supply Chain Digital Twin - Nagpur</h1>
        <div className="sim-info">
          <SimulationSelector
            simulations={availableSimulations}
            currentSim={currentSim}
            onSimChange={setCurrentSim}
            onLoadingChange={setLoading}
          />
          <span className="live-badge">● LIVE</span>
        </div>
      </header>

      {/* Tab Navigation */}
      <div className="tab-nav">
        <button
          className={`tab-button ${currentTab === 'dashboard' ? 'active' : ''}`}
          onClick={() => setCurrentTab('dashboard')}
          aria-label="Switch to Dashboard View"
          aria-pressed={currentTab === 'dashboard'}
        >
          🗺️ Dashboard
        </button>
        <button
          className={`tab-button ${currentTab === 'analytics' ? 'active' : ''}`}
          onClick={() => setCurrentTab('analytics')}
          aria-label="Switch to Analytics View"
          aria-pressed={currentTab === 'analytics'}
        >
          📊 Analytics
        </button>
        <button
          className={`tab-button ${currentTab === 'ai' ? 'active' : ''}`}
          onClick={() => setCurrentTab('ai')}
          aria-label="Switch to AI Insights View"
          aria-pressed={currentTab === 'ai'}
        >
          🤖 AI Insights
        </button>
      </div>

      {/* Conditional rendering based on active tab */}
      {currentTab === 'dashboard' ? (
        <>
          {/* Time and Weather Information Panels */}
          <div className="info-panels">
            <TimePanel
              timestamp={trucks[0]?.timestamp || warehouseStates[0]?.timestamp || 0}
              durationDays={simMetadata.duration_days || 7}
              speed={simMetadata.speed}
              timestep={simMetadata.time_step_minutes}
            />
            <WeatherPanel
              runId={currentSim}
              currentTime={trucks[0]?.timestamp || warehouseStates[0]?.timestamp || 0}
            />
          </div>

          <div className="dashboard">
            <div className="map-section">
              <MapView
                warehouses={coordinates.warehouses}
                retailers={coordinates.retailers}
                trucks={trucks}
              />
            </div>

            <div className="stats-section">
              <StatsCards
                warehouseCount={entities.warehouse_ids.length}
                retailerCount={entities.retailer_ids.length}
                truckCount={trucks.length}
                speed={simMetadata.speed}
                pollInterval={pollInterval}
                runId={currentSim}
                trucks={trucks}
              />

              <EntityPanel
                title="Warehouses"
                icon="📦"
                entities={warehouseStates}
                entityIds={entities.warehouse_ids}
                openByDefault={true}
              />

              <EntityPanel
                title="Retailers"
                icon="🏪"
                entities={retailerStates}
                entityIds={entities.retailer_ids}
                runId={currentSim}
                isRetailer={true}
              />

              <TruckList trucks={trucks} runId={currentSim} />

              <ScenarioControlPanel
                trucks={trucks}
                warehouses={warehouseStates}
                retailers={retailerStates}
              />
            </div>
          </div>
        </>
      ) : currentTab === 'analytics' ? (
        <AnalyticsTab
          simulationId={currentSim}
          simData={{
            warehouses: warehouseStates,
            retailers: retailerStates,
            trucks: trucks,
          }}
        />
      ) : (
        /* AI Insights tab */
        <div style={{ padding: '16px', maxWidth: '900px', margin: '0 auto' }}>
          <AIInsightsPanel runId={currentSim} trucks={trucks} />
        </div>
      )}
    </div>
  );
}

export default App;
