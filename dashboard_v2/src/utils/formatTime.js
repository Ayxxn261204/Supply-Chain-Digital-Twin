/**
 * Time Formatting Utilities
 * 
 * Functions for converting simulation timestamps to human-readable formats.
 */

/**
 * Formats simulation time for display in charts and UI
 * 
 * @param {string} isoString - ISO 8601 timestamp string (fallback)
 * @param {number} timestamp - Simulation time in minutes since start (preferred)
 * @returns {string} Formatted time string in "Day X, HH:MM" format or locale time string
 * 
 * @example
 * formatTime(null, 1440) // "Day 1, 00:00"
 * formatTime(null, 90)   // "Day 0, 01:30"
 */
export const formatTime = (isoString, timestamp) => {
    if (timestamp !== undefined) {
        // Convert timestamp (minutes since start) to Day X, HH:MM format
        const day = Math.floor(timestamp / 1440); // 1440 minutes in a day
        const minutesInDay = timestamp % 1440;
        const hours = Math.floor(minutesInDay / 60);
        const minutes = Math.floor(minutesInDay % 60);
        return `Day ${day}, ${String(hours).padStart(2, '0')}:${String(minutes).padStart(2, '0')}`;
    }
    // Fallback to ISO time if timestamp not available
    const date = new Date(isoString);
    return date.toLocaleTimeString();
};

export default formatTime;
