/**
 * Application Entry Point
 * 
 * Initializes the React application and mounts it to the DOM.
 * Wraps the main App component with ErrorBoundary for global error handling.
 * Uses React 18 create Root API for concurrent rendering features.
 * 
 * @module main
 */
import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import './index.css';
import App from './App.jsx';
import ErrorBoundary from './components/ErrorBoundary.jsx';

createRoot(document.getElementById('root')).render(
  <StrictMode>
    <ErrorBoundary>
      <App />
    </ErrorBoundary>
  </StrictMode>,
);
