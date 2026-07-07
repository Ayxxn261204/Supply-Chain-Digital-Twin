/**
 * StatsCards Component
 *
 * Displays summary statistics cards for warehouses, retailers, trucks,
 * and live RL routing metrics (reroute count + RSL health).
 *
 * @component
 */
import React, { useEffect, useState } from 'react';
import PropTypes from 'prop-types';
import axios from 'axios';
import { API_URL } from '../../constants';

const RSL_COLORS = { ok: '#10b981', warn: '#f59e0b', reject: '#ef4444' };

const StatsCards = React.memo(({ warehouseCount, retailerCount, truckCount, speed, pollInterval, runId, trucks }) => {
    const [rlReroutes, setRlReroutes] = useState(0);

    // Fetch RL activity every 30 seconds
    useEffect(() => {
        if (!runId) return;
        const fetch = () => {
            axios.get(`${API_URL}/ai/rl-activity?run_id=${runId}`)
                .then(res => setRlReroutes(res.data?.total_rl_reroutes ?? 0))
                .catch(() => {});
        };
        fetch();
        const t = setInterval(fetch, 30_000);
        return () => clearInterval(t);
    }, [runId]);

    // Derive RSL health from trucks prop
    const rslOk = (trucks || []).filter(t => t.cargo_rsl != null && t.cargo_rsl > 70).length;
    const rslWarn = (trucks || []).filter(t => t.cargo_rsl != null && t.cargo_rsl <= 70 && t.cargo_rsl > 40).length;
    const rslRisk = (trucks || []).filter(t => t.cargo_rsl != null && t.cargo_rsl <= 40).length;
    const hasRslData = rslOk + rslWarn + rslRisk > 0;

    return (
        <>
            <div className="stat-card">
                <div className="stat-icon">📦</div>
                <div className="stat-content">
                    <div className="stat-value">{warehouseCount}</div>
                    <div className="stat-label">Warehouses</div>
                </div>
            </div>

            <div className="stat-card">
                <div className="stat-icon">🏪</div>
                <div className="stat-content">
                    <div className="stat-value">{retailerCount}</div>
                    <div className="stat-label">Retailers</div>
                </div>
            </div>

            <div className="stat-card active">
                <div className="stat-icon">🚛</div>
                <div className="stat-content">
                    <div className="stat-value">{truckCount}</div>
                    <div className="stat-label">Trucks</div>
                </div>
            </div>

            <div className="stat-card">
                <div className="stat-icon">🤖</div>
                <div className="stat-content">
                    <div className="stat-value">{rlReroutes}</div>
                    <div className="stat-label">RL Reroutes</div>
                </div>
            </div>

            {hasRslData && (
                <div className="stat-card">
                    <div className="stat-icon">🍊</div>
                    <div className="stat-content">
                        <div className="stat-value" style={{ fontSize: '13px', display: 'flex', gap: '6px', alignItems: 'center' }}>
                            <span style={{ color: RSL_COLORS.ok }}>{rslOk}✓</span>
                            {rslWarn > 0 && <span style={{ color: RSL_COLORS.warn }}>{rslWarn}⚠</span>}
                            {rslRisk > 0 && <span style={{ color: RSL_COLORS.reject }}>{rslRisk}✗</span>}
                        </div>
                        <div className="stat-label">RSL Health</div>
                    </div>
                </div>
            )}

            <div className="info-box">
                <small>
                    Speed: {speed}x | Updates every {(pollInterval / 1000).toFixed(2)}s (synced
                    with simulation timestep)
                </small>
            </div>
        </>
    );
});

StatsCards.displayName = 'StatsCards';

StatsCards.propTypes = {
    warehouseCount: PropTypes.number.isRequired,
    retailerCount:  PropTypes.number.isRequired,
    truckCount:     PropTypes.number.isRequired,
    speed:          PropTypes.number.isRequired,
    pollInterval:   PropTypes.number.isRequired,
    runId:          PropTypes.string,
    trucks:         PropTypes.array,
};

StatsCards.defaultProps = { runId: null, trucks: [] };

export default StatsCards;
