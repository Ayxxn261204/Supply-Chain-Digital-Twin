/**
 * MapView Component
 */
import React from 'react';
import PropTypes from 'prop-types';
import { MapContainer, TileLayer, Marker, Popup, Pane } from 'react-leaflet';
import { createEmojiIcon } from '../../utils/mapHelpers';
import { MAP_CENTER } from '../../constants';

const MapView = React.memo(({ warehouses, retailers, trucks }) => {
    return (
        <MapContainer
            center={[MAP_CENTER.lat, MAP_CENTER.lon]}
            zoom={MAP_CENTER.zoom}
            style={{ height: '100%', width: '100%' }}
        >
            <TileLayer
                url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
                attribution="&copy; OpenStreetMap"
            />

            {/* Warehouses — lowest layer so trucks render on top but popups still work */}
            <Pane name="warehouses" style={{ zIndex: 400 }}>
                {warehouses?.map((wh) => (
                    <Marker key={wh.id} position={[wh.lat, wh.lon]} icon={createEmojiIcon('🏭')}>
                        <Popup>
                            <strong>📦 {wh.id}</strong><br />
                            Warehouse
                        </Popup>
                    </Marker>
                ))}
            </Pane>

            {/* Retailers — middle layer */}
            <Pane name="retailers" style={{ zIndex: 450 }}>
                {retailers?.map((ret) => (
                    <Marker key={ret.id} position={[ret.lat, ret.lon]} icon={createEmojiIcon('🏪')}>
                        <Popup>
                            <strong>🏪 {ret.id}</strong><br />
                            Retailer
                        </Popup>
                    </Marker>
                ))}
            </Pane>

            {/* Trucks — top layer, but with lower z-index on the marker itself
                so clicking near a warehouse still reaches it via the popup */}
            <Pane name="trucks" style={{ zIndex: 500 }}>
                {trucks
                    .filter((t) => t.location && Array.isArray(t.location))
                    .map((truck) => {
                        const rsl = truck.cargo_rsl;
                        const rslColor = rsl == null
                            ? '#9ca3af'
                            : rsl > 70 ? '#10b981'
                            : rsl > 40 ? '#f59e0b'
                            : '#ef4444';
                        return (
                            <Marker
                                key={truck.truck_id}
                                position={truck.location}
                                icon={createEmojiIcon('🚛')}
                            >
                                <Popup>
                                    <strong>🚛 {truck.truck_id}</strong><br />
                                    Status: {truck.status}<br />
                                    Speed: {(truck.speed_kmh ?? 0).toFixed(1)} km/h<br />
                                    Cargo: {(truck.cargo_kg ?? 0).toFixed(0)} kg<br />
                                    Fuel: {(truck.fuel_percent ?? 0).toFixed(1)}%
                                    {rsl != null && (
                                        <>
                                            <br />
                                            RSL: <span style={{ color: rslColor, fontWeight: 'bold' }}>
                                                {rsl.toFixed(1)}%
                                            </span>
                                        </>
                                    )}
                                </Popup>
                            </Marker>
                        );
                    })}
            </Pane>
        </MapContainer>
    );
});

MapView.displayName = 'MapView';

MapView.propTypes = {
    warehouses: PropTypes.arrayOf(PropTypes.shape({
        id: PropTypes.string.isRequired,
        lat: PropTypes.number.isRequired,
        lon: PropTypes.number.isRequired,
    })),
    retailers: PropTypes.arrayOf(PropTypes.shape({
        id: PropTypes.string.isRequired,
        lat: PropTypes.number.isRequired,
        lon: PropTypes.number.isRequired,
    })),
    trucks: PropTypes.arrayOf(PropTypes.shape({
        truck_id: PropTypes.string.isRequired,
        location: PropTypes.arrayOf(PropTypes.number),
        status: PropTypes.string.isRequired,
        speed_kmh: PropTypes.number,
        cargo_kg: PropTypes.number,
        fuel_percent: PropTypes.number,
        cargo_rsl: PropTypes.number,
    })),
};

MapView.defaultProps = { warehouses: [], retailers: [], trucks: [] };

export default MapView;
