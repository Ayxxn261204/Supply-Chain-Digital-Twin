/**
 * EntitySelector Component
 * 
 * Reusable dropdown for selecting entities (warehouses, retailers, or trucks).
 * 
 * @component
 */
import React from 'react';
import PropTypes from 'prop-types';

const EntitySelector = React.memo(({ value, onChange, entities, label, getId }) => {
    return (
        <select
            value={value}
            onChange={(e) => onChange(e.target.value)}
            className="entity-selector"
            aria-label={label}
        >
            {entities?.map((entity) => {
                const id = getId ? getId(entity) : entity.id;
                return (
                    <option key={id} value={id}>
                        {id}
                    </option>
                );
            })}
        </select>
    );
});

EntitySelector.displayName = 'EntitySelector';

EntitySelector.propTypes = {
    value: PropTypes.string.isRequired,
    onChange: PropTypes.func.isRequired,
    entities: PropTypes.array,
    label: PropTypes.string.isRequired,
    getId: PropTypes.func, // Optional function to extract ID from entity
};

EntitySelector.defaultProps = {
    entities: [],
    getId: null,
};

export default EntitySelector;
