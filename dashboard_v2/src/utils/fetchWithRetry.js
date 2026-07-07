/**
 * Utility function for fetching data with automatic retry logic and exponential backoff
 * 
 * Attempts to fetch from a URL multiple times with increasing delays between attempts.
 * Respects cancellation signals and provides detailed logging.
 * 
 * @param {string} url - The URL to fetch from
 * @param {Object} options - Axios request options (e.g., { signal } for cancellation)
 * @param {number} maxRetries - Maximum number of retry attempts
 * @param {number} baseDelay - Initial delay in milliseconds (default: 1000)
 * @param {number} maxDelay - Maximum delay in milliseconds (default: 5000)
 * @returns {Promise<AxiosResponse>} The successful response
 * @throws {Error} If all retry attempts fail or request is cancelled
 * 
 * @example
 * const response = await fetchWithRetry('/api/data', { signal: abortSignal }, 3);
 */
import axios from 'axios';
import { MAX_RETRY_DELAY } from '../constants';

export const fetchWithRetry = async (
    url,
    options = {},
    maxRetries = 3,
    baseDelay = 1000,
    maxDelay = MAX_RETRY_DELAY
) => {
    for (let attempt = 1; attempt <= maxRetries; attempt++) {
        try {
            const response = await axios.get(url, options);
            return response; // Success!
        } catch (error) {
            // Don't retry on cancelled/aborted requests
            if (
                axios.isCancel(error) ||
                error.code === 'ERR_CANCELED' ||
                error.name === 'AbortError'
            ) {
                console.log(`Request cancelled: ${url}`);
                throw error; // Re-throw without retry
            }

            // Last attempt - throw error
            if (attempt === maxRetries) {
                console.error(`Failed after ${maxRetries} attempts: ${url}`, error);
                throw error;
            }

            // Retry with exponential backoff
            const delay = Math.min(baseDelay * Math.pow(2, attempt - 1), maxDelay);
            console.warn(
                `Attempt ${attempt}/${maxRetries} failed for ${url}. Retrying in ${delay}ms...`
            );
            await new Promise((resolve) => setTimeout(resolve, delay));
        }
    }
};

export default fetchWithRetry;
