/**
 * ScenarioControlPanel — Digital twin intervention controls.
 *
 * Allows the user to inject real-time scenarios into the running simulation:
 *   - Inject a road accident on a truck's current segment
 *   - Adjust a retailer's demand rate
 *   - Trigger a warehouse stockout
 *
 * Commands are sent to the FastAPI /api/scenarios/* endpoints, which write
 * them to a shared JSON file that the simulation engine polls each tick.
 */
import React, { useState, useCallback } from 'react';
import PropTypes from 'prop-types';
import axios from 'axios';
import { API_URL } from '../../constants';

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const SEVERITY_OPTIONS = ['minor', 'moderate', 'severe'];

function StatusBadge({ status }) {
    const map = {
        idle:    { color: '#9ca3af', label: 'Ready' },
        sending: { color: '#f59e0b', label: 'Sending…' },
        ok:      { color: '#10b981', label: 'Queued ✓' },
        error:   { color: '#ef4444', label: 'Error ✗' },
    };
    const { color, label } = map[status] || map.idle;
    return (
        <span style={{
            fontSize: '11px', fontWeight: '600', color,
            padding: '2px 8px', borderRadius: '10px',
            background: color + '22', border: `1px solid ${color}44`,
        }}>
            {label}
        </span>
    );
}
StatusBadge.propTypes = { status: PropTypes.string.isRequired };

function SectionHeading({ icon, title }) {
    return (
        <div style={{
            fontSize: '12px', fontWeight: '700', color: '#9ca3af',
            textTransform: 'uppercase', letterSpacing: '0.05em',
            marginBottom: '8px', display: 'flex', alignItems: 'center', gap: '6px',
        }}>
            {icon} {title}
        </div>
    );
}
SectionHeading.propTypes = { icon: PropTypes.string.isRequired, title: PropTypes.string.isRequired };

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

const ScenarioControlPanel = React.memo(({ trucks, warehouses, retailers }) => {
    // Accident injection state
    const [accidentSeverity, setAccidentSeverity] = useState('moderate');
    const [accidentDuration, setAccidentDuration] = useState(30);
    const [accidentStatus,   setAccidentStatus]   = useState('idle');

    // Demand adjustment state
    const [selectedRetailer, setSelectedRetailer] = useState('');
    const [demandMultiplier, setDemandMultiplier] = useState(2.0);
    const [demandStatus,     setDemandStatus]     = useState('idle');

    // Stockout state
    const [selectedWarehouse, setSelectedWarehouse] = useState('');
    const [stockoutStatus,    setStockoutStatus]    = useState('idle');

    // Activity log
    const [log, setLog] = useState([]);

    const addLog = useCallback((msg) => {
        setLog(prev => [{ time: new Date().toLocaleTimeString(), msg }, ...prev].slice(0, 8));
    }, []);

    // ── Inject accident on a random in-transit truck's current segment ──
    const handleInjectAccident = useCallback(async () => {
        // Pick a random in-transit truck to get a realistic segment
        const inTransit = trucks.filter(t => t.status === 'in_transit');
        if (inTransit.length === 0) {
            addLog('⚠ No trucks in transit — cannot inject accident');
            return;
        }
        // Use the truck_id as a proxy; the engine will use its current segment
        const truck = inTransit[Math.floor(Math.random() * inTransit.length)];
        // We don't have the segment_id on the frontend, so we send the truck_id
        // and let the engine resolve it. Use a placeholder segment_id that the
        // engine will map to the truck's current segment.
        const segmentId = `truck:${truck.truck_id}`;

        setAccidentStatus('sending');
        try {
            await axios.post(`${API_URL}/scenarios/inject-accident`, {
                segment_id:       segmentId,
                severity:         accidentSeverity,
                duration_minutes: accidentDuration,
            });
            setAccidentStatus('ok');
            addLog(`🚨 Accident (${accidentSeverity}, ${accidentDuration}min) near ${truck.truck_id}`);
        } catch {
            setAccidentStatus('error');
            addLog('✗ Failed to inject accident');
        }
        setTimeout(() => setAccidentStatus('idle'), 3000);
    }, [trucks, accidentSeverity, accidentDuration, addLog]);

    // ── Adjust retailer demand ──
    const handleAdjustDemand = useCallback(async () => {
        if (!selectedRetailer) { addLog('⚠ Select a retailer first'); return; }
        setDemandStatus('sending');
        try {
            await axios.post(`${API_URL}/scenarios/adjust-demand`, {
                retailer_id: selectedRetailer,
                multiplier:  demandMultiplier,
            });
            setDemandStatus('ok');
            addLog(`📈 Demand for ${selectedRetailer} ×${demandMultiplier}`);
        } catch {
            setDemandStatus('error');
            addLog('✗ Failed to adjust demand');
        }
        setTimeout(() => setDemandStatus('idle'), 3000);
    }, [selectedRetailer, demandMultiplier, addLog]);

    // ── Trigger stockout ──
    const handleTriggerStockout = useCallback(async () => {
        if (!selectedWarehouse) { addLog('⚠ Select a warehouse first'); return; }
        setStockoutStatus('sending');
        try {
            await axios.post(`${API_URL}/scenarios/trigger-stockout`, {
                warehouse_id: selectedWarehouse,
            });
            setStockoutStatus('ok');
            addLog(`📦 Stockout triggered at ${selectedWarehouse}`);
        } catch {
            setStockoutStatus('error');
            addLog('✗ Failed to trigger stockout');
        }
        setTimeout(() => setStockoutStatus('idle'), 3000);
    }, [selectedWarehouse, addLog]);

    const inputStyle = {
        background: '#111827', border: '1px solid #374151', borderRadius: '6px',
        color: '#e5e7eb', padding: '6px 10px', fontSize: '12px', width: '100%',
        boxSizing: 'border-box',
    };
    const btnStyle = (color) => ({
        background: color + '22', border: `1px solid ${color}66`, borderRadius: '6px',
        color, padding: '7px 14px', fontSize: '12px', fontWeight: '600',
        cursor: 'pointer', width: '100%', marginTop: '8px',
        transition: 'background 0.2s',
    });
    const sectionStyle = {
        background: '#1f2937', borderRadius: '8px', padding: '12px',
        marginBottom: '10px', border: '1px solid #374151',
    };

    return (
        <details open>
            <summary style={{
                cursor: 'pointer', padding: '8px 4px', fontWeight: '700',
                fontSize: '13px', color: '#e5e7eb', userSelect: 'none',
            }}>
                🎮 Scenario Control
            </summary>

            <div style={{ paddingTop: '10px' }}>

                {/* ── Inject Accident ── */}
                <div style={sectionStyle}>
                    <SectionHeading icon="🚨" title="Inject Accident" />
                    <div style={{ display: 'flex', gap: '8px', marginBottom: '6px' }}>
                        <div style={{ flex: 1 }}>
                            <label style={{ fontSize: '11px', color: '#9ca3af' }}>Severity</label>
                            <select
                                value={accidentSeverity}
                                onChange={e => setAccidentSeverity(e.target.value)}
                                style={inputStyle}
                            >
                                {SEVERITY_OPTIONS.map(s => (
                                    <option key={s} value={s}>{s}</option>
                                ))}
                            </select>
                        </div>
                        <div style={{ flex: 1 }}>
                            <label style={{ fontSize: '11px', color: '#9ca3af' }}>Duration (min)</label>
                            <input
                                type="number" min={5} max={180} value={accidentDuration}
                                onChange={e => setAccidentDuration(Number(e.target.value))}
                                style={inputStyle}
                            />
                        </div>
                    </div>
                    <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                        <button onClick={handleInjectAccident} style={btnStyle('#ef4444')}>
                            Inject on random truck
                        </button>
                        <StatusBadge status={accidentStatus} />
                    </div>
                </div>

                {/* ── Adjust Demand ── */}
                <div style={sectionStyle}>
                    <SectionHeading icon="📈" title="Adjust Retailer Demand" />
                    <div style={{ marginBottom: '6px' }}>
                        <label style={{ fontSize: '11px', color: '#9ca3af' }}>Retailer</label>
                        <select
                            value={selectedRetailer}
                            onChange={e => setSelectedRetailer(e.target.value)}
                            style={inputStyle}
                        >
                            <option value="">— select —</option>
                            {(retailers || []).map(r => (
                                <option key={r.id || r.retailer_id} value={r.id || r.retailer_id}>
                                    {r.id || r.retailer_id}
                                </option>
                            ))}
                        </select>
                    </div>
                    <div style={{ marginBottom: '6px' }}>
                        <label style={{ fontSize: '11px', color: '#9ca3af' }}>
                            Multiplier: <strong style={{ color: '#e5e7eb' }}>{demandMultiplier}×</strong>
                        </label>
                        <input
                            type="range" min={0.1} max={5.0} step={0.1}
                            value={demandMultiplier}
                            onChange={e => setDemandMultiplier(Number(e.target.value))}
                            style={{ width: '100%', accentColor: '#6366f1' }}
                        />
                    </div>
                    <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                        <button onClick={handleAdjustDemand} style={btnStyle('#6366f1')}>
                            Apply demand change
                        </button>
                        <StatusBadge status={demandStatus} />
                    </div>
                </div>

                {/* ── Trigger Stockout ── */}
                <div style={sectionStyle}>
                    <SectionHeading icon="📦" title="Trigger Stockout" />
                    <div style={{ marginBottom: '6px' }}>
                        <label style={{ fontSize: '11px', color: '#9ca3af' }}>Warehouse</label>
                        <select
                            value={selectedWarehouse}
                            onChange={e => setSelectedWarehouse(e.target.value)}
                            style={inputStyle}
                        >
                            <option value="">— select —</option>
                            {(warehouses || []).map(w => (
                                <option key={w.id || w.warehouse_id} value={w.id || w.warehouse_id}>
                                    {w.id || w.warehouse_id}
                                </option>
                            ))}
                        </select>
                    </div>
                    <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                        <button onClick={handleTriggerStockout} style={btnStyle('#f59e0b')}>
                            Empty warehouse inventory
                        </button>
                        <StatusBadge status={stockoutStatus} />
                    </div>
                </div>

                {/* ── Activity Log ── */}
                {log.length > 0 && (
                    <div style={{ ...sectionStyle, marginBottom: 0 }}>
                        <SectionHeading icon="📋" title="Recent Actions" />
                        {log.map((entry, i) => (
                            <div key={i} style={{ fontSize: '11px', color: '#9ca3af', marginBottom: '3px' }}>
                                <span style={{ color: '#6b7280' }}>{entry.time}</span>
                                {' '}{entry.msg}
                            </div>
                        ))}
                    </div>
                )}

            </div>
        </details>
    );
});

ScenarioControlPanel.displayName = 'ScenarioControlPanel';

ScenarioControlPanel.propTypes = {
    trucks:     PropTypes.array,
    warehouses: PropTypes.array,
    retailers:  PropTypes.array,
};

ScenarioControlPanel.defaultProps = { trucks: [], warehouses: [], retailers: [] };

export default ScenarioControlPanel;
