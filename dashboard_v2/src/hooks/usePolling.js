/**
 * usePolling Custom Hook
 * 
 * Executes a callback function on a regular interval with automatic cleanup.
 * Handles initial fetch and subsequent polling cycles.
 * 
 * @param {Function} callback - The function to execute on each poll
 * @param {number} interval - Polling interval in milliseconds
 * @param {Array} dependencies - Additional dependencies to trigger re-polling
 * 
 * @example
 * usePolling(fetchData, 5000, [selectedId]);
 */
import { useEffect } from 'react';

export const usePolling = (callback, interval, dependencies = []) => {
    useEffect(() => {
        if (!callback || !interval) return;

        // Initial fetch
        callback();

        // Set up polling
        const intervalId = setInterval(callback, interval);

        // Cleanup on unmount or dependency change
        return () => clearInterval(intervalId);
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [callback, interval, ...dependencies]);
};

export default usePolling;
