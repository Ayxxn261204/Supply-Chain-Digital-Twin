/**
 * EntityPanel Component — with AI demand forecast for retailers
 */
import React, { useEffect, useState } from 'react';
import PropTypes from 'prop-types';
import axios from 'axios';
import { API_URL } from '../../constants';

const CONFIDENCE_COLORS = { high: '#10b981', medium: '#f59e0b', low: '#64748b' };

const EntityPanel = React.memo(({ title, icon, entities, entityIds, openByDefault = false, runId, isRetailer = false }) => {
    const count = entities.length > 0 ? entities.length : entityIds.length;
    const [demandPredictions, setDemandPredictions] = useState({});

    useEffect(() => {
        if (!isRetailer || !runId) return;
        axios.get(`${API_URL}/ai/retailer-predictions?run_id=${runId}`)
            .then(res => {
                const map = {};
                (res.data || []).forEach(p => { map[p.retailer_id] = p; });
                setDemandPredictions(map);
            })
            .catch(() => {});
    }, [runId, isRetailer, entities.length]);

    return (
        <details open={openByDefault}>
            <summary>{icon} {title} ({count})</summary>
            <ul>
                {entities.length > 0
                    ? entities.map(entity => {
                        const pred = isRetailer ? demandPredictions[entity.id] : null;
                        return (
                            <li key={entity.id}>
                                <strong>{entity.id}</strong>
                                {entity.current_inventory_kg !== undefined && (
                                    <><br />📦 Inventory: {Math.round(entity.current_inventory_kg)} kg</>
                                )}
                                {entity.total_orders_fulfilled !== undefined && (
                                    <><br />📋 Orders: {entity.total_orders_fulfilled}</>
                                )}
                                {/* AI demand forecast — retailers only */}
                                {pred && (
                                    <><br />
                                    <span
                                        title={`Demand forecast confidence: ${pred.confidence} (${pred.sales_observations} observations)`}
                                        style={{ color: CONFIDENCE_COLORS[pred.confidence] || '#d1d5db', fontSize: '0.85em' }}
                                    >
                                        🤖 Forecast: ~{Math.round(pred.predicted_demand_kg_24h)} kg/24h
                                        {' '}
                                        <span style={{ opacity: 0.7 }}>({pred.confidence})</span>
                                    </span>
                                    </>
                                )}
                            </li>
                        );
                    })
                    : entityIds.map(id => <li key={id}>{id}</li>)
                }
            </ul>
        </details>
    );
});

EntityPanel.displayName = 'EntityPanel';

EntityPanel.propTypes = {
    title: PropTypes.string.isRequired,
    icon: PropTypes.string.isRequired,
    entities: PropTypes.arrayOf(PropTypes.shape({
        id: PropTypes.string.isRequired,
        current_inventory_kg: PropTypes.number,
        total_orders_fulfilled: PropTypes.number,
    })),
    entityIds: PropTypes.arrayOf(PropTypes.string),
    openByDefault: PropTypes.bool,
    runId: PropTypes.string,
    isRetailer: PropTypes.bool,
};

EntityPanel.defaultProps = {
    entities: [],
    entityIds: [],
    openByDefault: false,
    isRetailer: false,
};

export default EntityPanel;
