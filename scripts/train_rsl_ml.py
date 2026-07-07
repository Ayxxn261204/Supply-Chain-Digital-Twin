import sys
import numpy as np
import pandas as pd
from pathlib import Path
import logging
import pickle

from sklearn.ensemble import RandomForestRegressor
from sklearn.model_selection import train_test_split
from sklearn.metrics import mean_squared_error, r2_score

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger("train_rsl_ml")

ROOT_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT_DIR))

def generate_synthetic_data(num_samples=50000):
    """
    Generate synthetic data for Nagpur Orange shelf life transit decay.
    Uses basic Arrhenius principles but injects complex non-linear penalties
    like road vibration and stochastic environmental mapping.
    """
    np.random.seed(42)
    logger.info(f"Generating {num_samples} synthetic transit records...")
    
    # Random realistic conditions for Indian transit
    temp_c = np.random.uniform(5.0, 48.0, num_samples) # 5C (Reefer) to 48C (Peak Nagpur open truck)
    humidity_pct = np.random.uniform(20.0, 95.0, num_samples)
    vibration_g = np.random.uniform(0.1, 3.5, num_samples) # G-forces on rough roads
    transit_time_hours = np.random.uniform(1.0, 72.0, num_samples)
    
    # Physics Baseline (Arrhenius approximation of Vitamin C / firmness decay)
    # Rate increases exponentially with temp, linearly with humidity deviation from 90%
    T_kelvin = temp_c + 273.15
    Ea_R = 12000.0 # Activation energy / gas constant
    base_rate = 1e16 * np.exp(-Ea_R / T_kelvin)
    
    humidity_penalty = 1.0 + 0.02 * np.abs(90.0 - humidity_pct)
    # Vibration drastically increases mechanical damage and cellular respiration
    vibration_penalty = 1.0 + 0.15 * (vibration_g ** 1.5)
    
    # Calculate truth decay rate (percent per hour)
    decay_rate_hr = base_rate * humidity_penalty * vibration_penalty
    
    # Add noise for real-world stochasticity
    noise = np.random.normal(0, 0.05 * decay_rate_hr)
    final_decay_rate = np.clip(decay_rate_hr + noise, 0.01, 10.0)
    
    df = pd.DataFrame({
        'ambient_temp_c': temp_c,
        'ambient_humidity': humidity_pct,
        'vibration_g': vibration_g,
        'decay_rate_per_hour': final_decay_rate
    })
    
    return df

def train_model():
    df = generate_synthetic_data()
    
    X = df[['ambient_temp_c', 'ambient_humidity', 'vibration_g']]
    y = df['decay_rate_per_hour']
    
    X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2, random_state=42)
    
    logger.info("Training RandomForestRegressor...")
    model = RandomForestRegressor(n_estimators=100, max_depth=15, n_jobs=-1, random_state=42)
    model.fit(X_train, y_train)
    
    y_pred = model.predict(X_test)
    mse = mean_squared_error(y_test, y_pred)
    r2 = r2_score(y_test, y_pred)
    
    logger.info(f"Model Training Complete! MSE: {mse:.4f}, R^2: {r2:.4f}")
    
    # Feature Importance Logging
    importances = model.feature_importances_
    features = ['ambient_temp_c', 'ambient_humidity', 'vibration_g']
    feat_imp = sorted(zip(features, importances), key=lambda x: x[1], reverse=True)
    logger.info("--- Feature Importances ---")
    for fname, fval in feat_imp:
        logger.info(f"  {fname}: {fval*100:.2f}%")
        
    try:
        import matplotlib.pyplot as plt
        plt.figure(figsize=(8,5))
        plt.bar([x[0] for x in feat_imp], [x[1]*100 for x in feat_imp], color='skyblue')
        plt.title('RSL Decay Feature Importance')
        plt.ylabel('Importance (%)')
        plot_path = ROOT_DIR / "models" / "ml" / "rsl_feature_importance.png"
        plot_path.parent.mkdir(parents=True, exist_ok=True)
        plt.savefig(plot_path)
        logger.info(f"Saved feature importance plot to {plot_path}")
    except ImportError:
        logger.warning("Matplotlib not installed. Skipping feature importance plot generation.")
    
    # Create directory and save
    out_dir = ROOT_DIR / "models" / "ml"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "rsl_rf_model.pkl"
    
    logger.info(f"Saving model to {out_path}")
    with open(out_path, 'wb') as f:
        pickle.dump(model, f)
        
    logger.info("Saved! Ready for PredictionPod ingestion.")

if __name__ == "__main__":
    train_model()
