import { Component, type ReactNode } from 'react';

interface Props { children: ReactNode; }
interface State { error: Error | null; }

export default class ErrorBoundary extends Component<Props, State> {
    state: State = { error: null };

    static getDerivedStateFromError(error: Error) { return { error }; }

    render() {
        if (this.state.error) {
            return (
                <div className="error-boundary">
                    <h3>出错了</h3>
                    <pre>{this.state.error.message}</pre>
                    <button onClick={() => { this.setState({ error: null }); window.location.reload(); }}>
                        重新加载
                    </button>
                </div>
            );
        }
        return this.props.children;
    }
}