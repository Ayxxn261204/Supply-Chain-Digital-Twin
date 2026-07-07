/**
 * TruckList Component — with AI predictions overlay
 */
import React, { useMemo, useEffect, useState } from 'react';
import PropTypes from 'prop-types';
import axios from 'axios';
import { TRUCK_STATUS_INFO, API_URL } from '../../constants';

const RSL_COLORS = { ok: '#10b981', warn: '#f59e0b', reject: '#ef4444' };

const TruckList = React.memo(({ trucks, runId }) => {
    const [aiPredictions, setAiPredictions] = useState({});

    // Fetch AI predictions when runId or trucks change
    useEffect(() => {
        if (!runId) return;
        axios.get(`${API_URL}/ai/truck-predictions?run_id=${runId}`)
            .then(res => {
                const map = {};
                (res.data || []).forEach(p => { map[p.truck_id] = p; });
                setAiPredictions(map);
            })
            .catch(() => {}); // Silently fail — AI predictions are optional
    }, [runId, trucks.length]);

    const sortedTrucks = useMemo(() => {
        return [...trucks].sort((a, b) => {
            const activeTrucks = ['in_transit', 'loading', 'unloading', 'returning'];
            const isActiveA = activeTrucks.includes(a.status);
            const isActiveB = activeTrucks.includes(b.status);
            const isCrashedA = ['crashed', 'broken'].includes(a.status);
            const isCrashedB = ['crashed', 'broken'].includes(b.status);
            if (isActiveA && !isActiveB) return -1;
            if (!isActiveA && isActiveB) return 1;
            if (isCrashedA && !isCrashedB) return -1;
            if (!isCrashedA && isCrashedB) return 1;
            return a.truck_id.localeCompare(b.truck_id);
        });
    }, [trucks]);

    const stats = useMemo(() => {
        const active = trucks.filter(t => ['in_transit', 'loading', 'unloading', 'returning'].includes(t.status)).length;
        const idle = trucks.filter(t => t.status === 'idle').length;
        const crashed = trucks.filter(t => ['crashed', 'broken'].includes(t.status)).length;
        return { active, idle, crashed, summary: `• ${active} Active, ${idle} Idle${crashed > 0 ? `, ${crashed} ⚠️` : ''}` };
    }, [trucks]);

    return (
        <details>
            <summary>🚛 Trucks ({trucks.length}) {stats.summary}</summary>
            <ul style={{ listStyle: 'none', padding: '0', marginTop: '10px' }}>
                {sortedTrucks.map(truck => {
                    const info = TRUCK_STATUS_INFO[truck.status] || TRUCK_STATUS_INFO.idle;
                    const fuel = truck.fuel_percent ?? 0;
                    const cargo = truck.cargo_kg ?? 0;
                    const speed = truck.speed_kmh ?? 0;
                    const fuelColor = fuel < 20 ? 'var(--rose-500)' : fuel < 50 ? 'var(--amber-500)' : 'var(--emerald-500)';
                    const ai = aiPredictions[truck.truck_id];
                    const isInTransit = truck.status === 'in_transit';

                    return (
                        <li key={truck.truck_id} className="truck-list-item">
                            <div className="truck-id">{truck.truck_id}</div>
                            <div className="truck-stats">
                                <div className="truck-stat-row">
                                    <span>{info.icon}</span>
                                    <span style={{ color: info.color, fontWeight: '600' }}>{info.label}</span>
                                </div>
                                <div className="truck-stat-row">
                                    <span>⛽</span>
                                    <span style={{ color: fuelColor, fontFamily: 'monospace', fontWeight: 'bold' }}>{fuel.toFixed(1)}%</span>
                                </div>
                                <div className="truck-stat-row">
                                    <span>📦</span>
                                    <span className="truck-stat-value">{cargo.toFixed(0)} kg</span>
                                </div>
                                <div className="truck-stat-row">
                                    <span>⚡</span>
                                    <span className="truck-stat-value">{speed.toFixed(1)} km/h</span>
                                </div>

                                {/* AI Predictions — only for in-transit trucks */}
                                {isInTransit && ai && ai.predicted_rsl_at_delivery != null && (
                                    <div className="truck-stat-row" title="Predicted RSL when cargo arrives (AI)">
                                        <span>🍊</span>
                                        <span style={{ color: RSL_COLORS[ai.rsl_recommendation] || '#d1d5db', fontFamily: 'monospace' }}>
                                            {ai.predicted_rsl_at_delivery}% at delivery
                                        </span>
                                    </div>
                                )}
                                {isInTransit && ai && ai.estimated_eta_minutes != null && (
                                    <div className="truck-stat-row" title="Estimated time to delivery (AI)">
                                        <span>🕐</span>
                                        <span className="truck-stat-value">ETA ~{ai.estimated_eta_minutes} min</span>
                                    </div>
                                )}
                                {ai && ai.rl_reroutes > 0 && (
                                    <div className="truck-stat-row" title="Number of RL-policy reroutes">
                                        <span>🤖</span>
                                        <span className="truck-stat-value">{ai.rl_reroutes} RL reroute{ai.rl_reroutes !== 1 ? 's' : ''}</span>
                                    </div>
                                )}
                            </div>
                        </li>
                    );
                })}
            </ul>
        </details>
    );
});

TruckList.displayName = 'TruckList';

TruckList.propTypes = {
    trucks: PropTypes.arrayOf(PropTypes.shape({
        truck_id: PropTypes.string.isRequired,
        status: PropTypes.string.isRequired,
        fuel_percent: PropTypes.number,
        cargo_kg: PropTypes.number,
        speed_kmh: PropTypes.number,
    })).isRequired,
    runId: PropTypes.string,
};

export default TruckList;
