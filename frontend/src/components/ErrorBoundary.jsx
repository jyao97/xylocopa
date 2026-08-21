import { Component } from "react";

export default class ErrorBoundary extends Component {
  constructor(props) {
    super(props);
    this.state = { hasError: false, error: null };
  }

  static getDerivedStateFromError(error) {
    return { hasError: true, error };
  }

  componentDidCatch(error, errorInfo) {
    console.error("ErrorBoundary caught:", error, errorInfo);
  }

  componentDidMount() {
    // Reset error state before HMR updates so a caught error doesn't
    // permanently white-screen the app during development.
    if (import.meta.hot) {
      import.meta.hot.on("vite:beforeUpdate", () => {
        if (this.state.hasError) {
          this.setState({ hasError: false, error: null });
        }
      });
    }
  }

  render() {
    if (this.state.hasError) {
      return (
        <div className="flex flex-col items-center justify-center h-dvh bg-page gap-4 px-6 text-center">
          <div className="text-red-400 text-lg font-semibold">Something went wrong</div>
          <p className="text-dim text-sm max-w-md">
            {this.state.error?.message || "An unexpected error occurred."}
          </p>
          <button
            type="button"
            onClick={() => {
              const msg = this.state.error?.message || "";
              if (/MIME type|dynamically imported module|Importing a module script failed/i.test(msg)) {
                window.location.reload();
              } else {
                this.setState({ hasError: false, error: null });
              }
            }}
            className="px-4 py-2 rounded-lg bg-accent text-white text-sm hover:bg-accent transition-colors"
          >
            Try Again
          </button>
          <button
            type="button"
            onClick={() => window.location.reload()}
            className="px-4 py-2 rounded-lg bg-elevated text-body text-sm hover:bg-hover transition-colors"
          >
            Reload Page
          </button>
        </div>
      );
    }

    return this.props.children;
  }
}
