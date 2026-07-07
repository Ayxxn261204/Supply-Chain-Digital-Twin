/**
 * ChartContainer Component
 * 
 * Reusable wrapper for displaying Chart.js line charts with consistent styling and ARIA labels.
 * 
 * @component
 */
import React from 'react';
import PropTypes from 'prop-types';
import { Line } from 'react-chartjs-2';

const ChartContainer = React.memo(({ chartData, chartOptions, height = '250px', ariaLabel }) => {
    return (
        <div className="chart-container" style={{ height }} aria-label={ariaLabel}>
            <Line data={chartData} options={chartOptions} />
        </div>
    );
});

ChartContainer.displayName = 'ChartContainer';

ChartContainer.propTypes = {
    chartData: PropTypes.shape({
        labels: PropTypes.array.isRequired,
        datasets: PropTypes.array.isRequired,
    }).isRequired,
    chartOptions: PropTypes.object.isRequired,
    height: PropTypes.string,
    ariaLabel: PropTypes.string.isRequired,
};

export default ChartContainer;
