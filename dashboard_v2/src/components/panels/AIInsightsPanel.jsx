/**
 * AIInsightsPanel — Live AI/ML predictions for the digital twin dashboard.
 *
 * Shows:
 *  - RL routing strategy distribution (balanced / speed / fuel)
 *  - Per-retailer demand forecast vs current inventory
 *  - System RSL health summary (ok / warn / reject counts from truck predictions)
 */
import React, { useEffect, useState, useCallback } from 'react';
import PropTypes from 'prop-types';
import axios from 'axios';
import { API_URL } from '../../constants';

// Polling intervals (ms)
const RL_POLL_MS    = 30_000;
const RET_POLL_MS   = 60_000;
const TRUCK_POLL_MS = 30_000;

// Colour palette
const STRATEGY_COLORS = {
    balanced: '#6366f1',
    speed:    '#f59e0b',
    fuel:     '#10b981',
    heuristic:'#9ca3af',
};
const RSL_COLORS = { ok: '#10b981', warn: '#f59e0b', reject: '#ef4444' };
const CONFIDENCE_COLORS = { high: '#10b981', medium: '#f59e0b', low: '#9ca3af' };

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

/** Horizontal bar showing a percentage with a label. */
function BarRow({ label, value, total, color }) {
    const pct = total > 0 ? (value / total) * 100 : 0;
    return (
        <div style={{ marginBottom: '6px' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '12px', marginBottom: '2px' }}>
                <span style={{ color: '#d1d5db' }}>{label}</span>
                <span style={{ color, fontWeight: 'bold' }}>{value} ({pct.toFixed(1)}%)</span>
            </div>
            <div style={{ background: '#374151', borderRadius: '4px', height: '6px', overflow: 'hidden' }}>
                <div style={{ width: `${pct}%`, background: color, height: '100%', borderRadius: '4px', transition: 'width 0.4s ease' }} />
            </div>
        </div>
    );
}
BarRow.propTypes = {
    label: PropTypes.string.isRequired,
    value: PropTypes.number.isRequired,
    total: PropTypes.number.isRequired,
    color: PropTypes.string.isRequired,
};

/** Small coloured badge. */
function Badge({ label, color }) {
    return (
        <span style={{
            display: 'inline-block', padding: '2px 8px', borderRadius: '12px',
            background: color + '22', color, fontSize: '11px', fontWeight: '600',
            border: `1px solid ${color}44`,
        }}>
            {label}
        </span>
    );
}
Badge.propTypes = { label: PropTypes.string.isRequired, color: PropTypes.string.isRequired };

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

const AIInsightsPanel = React.memo(({ runId, trucks }) => {
    const [rlActivity, setRlActivity]       = useState(null);
    const [retailerPreds, setRetailerPreds] = useState([]);
    const [loading, setLoading]             = useState(true);

    // Derive RSL health summary from trucks prop (already fetched by TruckList)
    const rslHealth = React.useMemo(() => {
        const counts = { ok: 0, warn: 0, reject: 0, unknown: 0 };
        (trucks || []).forEach(t => {
            const rsl = t.cargo_rsl;
            if (rsl == null)       counts.unknown++;
            else if (rsl > 70)     counts.ok++;
            else if (rsl > 40)     counts.warn++;
            else                   counts.reject++;
        });
        return counts;
    }, [trucks]);

    const fetchRlActivity = useCallback(() => {
        if (!runId) return;
        axios.get(`${API_URL}/ai/rl-activity?run_id=${runId}`)
            .then(res => setRlActivity(res.data))
            .catch(() => {});
    }, [runId]);

    const fetchRetailerPreds = useCallback(() => {
        if (!runId) return;
        axios.get(`${API_URL}/ai/retailer-predictions?run_id=${runId}`)
            .then(res => { setRetailerPreds(res.data || []); setLoading(false); })
            .catch(() => setLoading(false));
    }, [runId]);

    // Initial fetch + polling
    useEffect(() => {
        fetchRlActivity();
        fetchRetailerPreds();
        const t1 = setInterval(fetchRlActivity,    RL_POLL_MS);
        const t2 = setInterval(fetchRetailerPreds, RET_POLL_MS);
        return () => { clearInterval(t1); clearInterval(t2); };
    }, [fetchRlActivity, fetchRetailerPreds]);

    const sectionStyle = {
        background: '#1f2937', borderRadius: '8px', padding: '12px',
        marginBottom: '12px', border: '1px solid #374151',
    };
    const headingStyle = {
        fontSize: '13px', fontWeight: '700', color: '#e5e7eb',
        marginBottom: '10px', display: 'flex', alignItems: 'center', gap: '6px',
    };

    return (
        <div style={{ padding: '8px', color: '#e5e7eb' }}>

            {/* ── RSL Health ── */}
            <div style={sectionStyle}>
                <div style={headingStyle}>🍊 Cargo RSL Health</div>
                <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap' }}>
                    <Badge label={`✓ ${rslHealth.ok} OK`}      color={RSL_COLORS.ok} />
                    <Badge label={`⚠ ${rslHealth.warn} Warn`}  color={RSL_COLORS.warn} />
                    <Badge label={`✗ ${rslHealth.reject} Risk`} color={RSL_COLORS.reject} />
                    {rslHealth.unknown > 0 && (
                        <Badge label={`? ${rslHealth.unknown} No data`} color="#9ca3af" />
                    )}
                </div>
            </div>

            {/* ── RL Strategy Distribution ── */}
            <div style={sectionStyle}>
                <div style={headingStyle}>🤖 RL Routing Strategy</div>
                {rlActivity ? (
                    <>
                        <div style={{ fontSize: '11px', color: '#9ca3af', marginBottom: '8px' }}>
                            {rlActivity.total_rl_reroutes} RL reroutes · {rlActivity.total_reroutes} total
                        </div>
                        {['balanced', 'speed', 'fuel', 'heuristic'].map(key => (
                            <BarRow
                                key={key}
                                label={key.charAt(0).toUpperCase() + key.slice(1)}
                                value={rlActivity.strategy_counts[key] ?? 0}
                                total={rlActivity.total_reroutes}
                                color={STRATEGY_COLORS[key]}
                            />
                        ))}
                    </>
                ) : (
                    <div style={{ fontSize: '12px', color: '#6b7280' }}>Loading…</div>
                )}
            </div>

            {/* ── Retailer Demand Forecast ── */}
            <div style={sectionStyle}>
                <div style={headingStyle}>📈 Demand Forecast (24h)</div>
                {loading ? (
                    <div style={{ fontSize: '12px', color: '#6b7280' }}>Loading…</div>
                ) : retailerPreds.length === 0 ? (
                    <div style={{ fontSize: '12px', color: '#6b7280' }}>No data yet</div>
                ) : (
                    <div style={{ maxHeight: '220px', overflowY: 'auto' }}>
                        {retailerPreds.map(r => {
                            const inv = r.current_inventory_kg;
                            const demand = r.predicted_demand_kg_24h;
                            const ratio = demand > 0 ? inv / demand : 1;
                            const stockRisk = ratio < 0.5 ? 'reject' : ratio < 1.0 ? 'warn' : 'ok';
                            return (
                                <div key={r.retailer_id} style={{
                                    padding: '8px', marginBottom: '6px',
                                    background: '#111827', borderRadius: '6px',
                                    borderLeft: `3px solid ${RSL_COLORS[stockRisk]}`,
                                }}>
                                    <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: '4px' }}>
                                        <span style={{ fontSize: '12px', fontWeight: '600' }}>{r.retailer_id}</span>
                                        <Badge
                                            label={r.confidence}
                                            color={CONFIDENCE_COLORS[r.confidence] ?? '#9ca3af'}
                                        />
                                    </div>
                                    <div style={{ fontSize: '11px', color: '#9ca3af' }}>
                                        Stock: <span style={{ color: '#e5e7eb' }}>{inv.toFixed(0)} kg</span>
                                        {' · '}
                                        Forecast: <span style={{ color: '#e5e7eb' }}>{demand.toFixed(0)} kg</span>
                                        {' · '}
                                        Coverage: <span style={{ color: RSL_COLORS[stockRisk], fontWeight: '600' }}>
                                            {(ratio * 100).toFixed(0)}%
                                        </span>
                                    </div>
                                </div>
                            );
                        })}
                    </div>
                )}
            </div>

        </div>
    );
});

AIInsightsPanel.displayName = 'AIInsightsPanel';

AIInsightsPanel.propTypes = {
    runId:  PropTypes.string,
    trucks: PropTypes.arrayOf(PropTypes.shape({
        truck_id:  PropTypes.string.isRequired,
        cargo_rsl: PropTypes.number,
    })),
};

AIInsightsPanel.defaultProps = { runId: null, trucks: [] };

export default AIInsightsPanel;
