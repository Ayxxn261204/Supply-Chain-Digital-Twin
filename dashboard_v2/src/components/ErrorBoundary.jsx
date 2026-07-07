/**
 * ErrorBoundary Component
 * 
 * React Error Boundary that catches JavaScript errors anywhere in the child component tree,
 * logs the errors, and displays a fallback UI instead of crashing the entire application.
 * 
 * Error Handling Strategy:
 * - Catches errors during rendering, in lifecycle methods, and in constructors
 * - Does NOT catch errors in event handlers, async code, or errors in the boundary itself
 * - Provides a user-friendly error message with a reload button
 * - Shows detailed error stack trace in development mode only
 * - Implements componentDidCatch for error logging to external services (if configured)
 * 
 * @component
 * @see {@link https://react.dev/reference/react/Component#catching-rendering-errors-with-an-error-boundary}
 */
import React from 'react';

class ErrorBoundary extends React.Component {
    constructor(props) {
        super(props);
        this.state = { hasError: false, error: null, errorInfo: null };
    }


    // eslint-disable-next-line no-unused-vars
    static getDerivedStateFromError(_error) {
        // Update state so the next render will show the fallback UI
        return { hasError: true };
    }

    componentDidCatch(error, errorInfo) {
        // Log the error to console for debugging
        console.error('Error Boundary caught an error:', error, errorInfo);
        this.setState({
            error,
            errorInfo
        });
    }

    render() {
        if (this.state.hasError) {
            // Fallback UI
            return (
                <div style={{
                    display: 'flex',
                    flexDirection: 'column',
                    alignItems: 'center',
                    justifyContent: 'center',
                    height: '100vh',
                    background: '#0f172a',
                    color: '#f1f5f9',
                    fontFamily: 'system-ui, sans-serif',
                    padding: '2rem'
                }}>
                    <h1 style={{ fontSize: '2.5rem', marginBottom: '1rem', color: '#ef4444' }}>
                        ⚠️ Something Went Wrong
                    </h1>
                    <p style={{ fontSize: '1.2rem', marginBottom: '2rem', color: '#94a3b8' }}>
                        The dashboard encountered an unexpected error.
                    </p>
                    <button
                        onClick={() => window.location.reload()}
                        style={{
                            padding: '0.75rem 1.5rem',
                            fontSize: '1rem',
                            background: 'linear-gradient(135deg, #667eea 0%, #764ba2 100%)',
                            color: 'white',
                            border: 'none',
                            borderRadius: '8px',
                            cursor: 'pointer',
                            fontWeight: '600'
                        }}
                    >
                        🔄 Reload Dashboard
                    </button>
                    {import.meta.env.DEV && this.state.error && (
                        <details style={{
                            marginTop: '2rem',
                            padding: '1rem',
                            background: '#1e293b',
                            borderRadius: '8px',
                            maxWidth: '800px',
                            width: '100%'
                        }}>
                            <summary style={{ cursor: 'pointer', fontWeight: '600', marginBottom: '0.5rem' }}>
                                Error Details (Dev Mode)
                            </summary>
                            <pre style={{
                                fontSize: '0.875rem',
                                color: '#ef4444',
                                overflow: 'auto',
                                padding: '1rem',
                                background: '#0f172a',
                                borderRadius: '4px'
                            }}>
                                {this.state.error.toString()}
                                {'\n\n'}
                                {this.state.errorInfo?.componentStack}
                            </pre>
                        </details>
                    )}
                </div>
            );
        }

        return this.props.children;
    }
}

export default ErrorBoundary;
