/**
 * TimePanel Component
 * 
 * Displays the current simulation time, progress, and synchronization parameters.
 * Shows time in "Day X, HH:MM" format with a progress bar and simulation speed info.
 * 
 * @component
 * @param {Object} props - Component props
 * @param {number} props.timestamp - Current simulation time in minutes since start
 * @param {number} props.durationDays - Total duration of the simulation in days
 * @param {number} props.speed - Simulation speed multiplier (e.g., 10x, 50x)
 * @param {number} props.timestep - Simulation timestep in minutes
 */
import React from 'react';
import PropTypes from 'prop-types';

function TimePanel({ timestamp, durationDays, speed, timestep }) {
    // Calculate formatted time from timestamp (minutes since start)
    const totalMinutes = Math.floor(timestamp || 0);
    const days = Math.floor(totalMinutes / (24 * 60));
    const hours = Math.floor((totalMinutes % (24 * 60)) / 60);
    const minutes = Math.floor(totalMinutes % 60);

    // Calculate progress
    const totalDuration = (durationDays || 7) * 24 * 60; // Total minutes in simulation
    const progressPercent = totalDuration > 0 ? (totalMinutes / totalDuration) * 100 : 0;

    return (
        <div className="panel time-panel">
            <h3>⏱️ Simulation Time</h3>
            <div className="time-display">
                <div className="time-formatted">
                    Day {days}, {hours.toString().padStart(2, '0')}:{minutes.toString().padStart(2, '0')}
                </div>
                <div className="time-detail">
                    {totalMinutes.toFixed(0)} / {totalDuration.toFixed(0)} minutes
                </div>
                <div className="progress-bar">
                    <div
                        className="progress-fill"
                        style={{ width: `${Math.min(progressPercent, 100)}%` }}
                    ></div>
                </div>
                <div className="progress-percent">{progressPercent.toFixed(1)}%</div>
            </div>
            <div className="sim-params">
                Speed: {speed || 1}x | Timestep: {timestep || 1}min
            </div>
            <div className="update-info">
                Updates every {((timestep || 1) * 60 / (speed || 1)).toFixed(1)}s (synced with {timestep || 1}min timestep)
            </div>
        </div>
    );
}

// PropTypes validation for type safety
TimePanel.propTypes = {
    timestamp: PropTypes.number.isRequired,
    durationDays: PropTypes.number.isRequired,
    speed: PropTypes.number.isRequired,
    timestep: PropTypes.number.isRequired
};

export default TimePanel;
