/**
 * SimulationSelector Component
 * 
 * Dropdown selector for choosing between available simulation runs.
 * Displays simulation metadata including number of warehouses, retailers, and trucks.
 * 
 * @component
 */
import React from 'react';
import PropTypes from 'prop-types';

const SimulationSelector = ({ simulations, currentSim, onSimChange, onLoadingChange }) => {
    if (!simulations || simulations.length === 0) return null;

    const handleChange = (e) => {
        onSimChange(e.target.value);
        if (onLoadingChange) {
            onLoadingChange(true); // Show loading while switching
        }
    };

    return (
        <div className="sim-selector">
            <label htmlFor="sim-select">Simulation: </label>
            <select
                id="sim-select"
                value={currentSim || ''}
                onChange={handleChange}
                className="sim-dropdown"
                aria-label="Select simulation run"
            >
                {simulations.map((sim) => (
                    <option key={sim.run_id} value={sim.run_id}>
                        {sim.run_id} ({sim.num_warehouses}WH, {sim.num_retailers}Ret,{' '}
                        {sim.num_trucks}Trucks)
                    </option>
                ))}
            </select>
        </div>
    );
};

SimulationSelector.propTypes = {
    simulations: PropTypes.arrayOf(
        PropTypes.shape({
            run_id: PropTypes.string.isRequired,
            num_warehouses: PropTypes.number.isRequired,
            num_retailers: PropTypes.number.isRequired,
            num_trucks: PropTypes.number.isRequired,
        })
    ).isRequired,
    currentSim: PropTypes.string,
    onSimChange: PropTypes.func.isRequired,
    onLoadingChange: PropTypes.func,
};

export default SimulationSelector;
