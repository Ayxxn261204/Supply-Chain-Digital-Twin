/**
 * Leaflet Map Helper Functions
 * 
 * Utilities for working with Leaflet maps including icon creation and configuration.
 */
import L from 'leaflet';

/**
 * Fix for Leaflet default icon issue in bundled environments
 * This removes the default getIconUrl method that doesn't work with webpack/vite
 */
export const fixLeafletDefaultIcon = () => {
    delete L.Icon.Default.prototype._getIconUrl;
    L.Icon.Default.mergeOptions({
        iconRetinaUrl: 'https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.7.1/images/marker-icon-2x.png',
        iconUrl: 'https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.7.1/images/marker-icon.png',
        shadowUrl: 'https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.7.1/images/marker-shadow.png',
    });
};

/**
 * Creates a custom Leaflet icon using emoji
 * 
 * @param {string} emoji - The emoji to display as the map marker
 * @param {number} size - Icon size in pixels (default: 28)
 * @returns {L.DivIcon} A Leaflet DivIcon instance
 * 
 * @example
 * const warehouseIcon = createEmojiIcon('🏭');
 * const truckIcon = createEmojiIcon('🚛', 32);
 */
export const createEmojiIcon = (emoji, size = 28) => {
    return L.divIcon({
        html: `<div style="font-size: ${size}px;">${emoji}</div>`,
        className: 'custom-icon',
        iconSize: [32, 32],
        iconAnchor: [16, 32],
    });
};

export default { fixLeafletDefaultIcon, createEmojiIcon };
