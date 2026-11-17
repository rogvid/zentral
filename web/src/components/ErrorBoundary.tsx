import { Component, type ReactNode } from "react";
import { ErrorState } from "../ui";

/** Keeps one broken view from blanking the whole application. */
export class ErrorBoundary extends Component<{ children: ReactNode; resetKey?: string }, { failed: boolean }> {
  state = { failed: false };

  static getDerivedStateFromError() {
    return { failed: true };
  }

  componentDidUpdate(previous: { resetKey?: string }) {
    if (previous.resetKey !== this.props.resetKey && this.state.failed) this.setState({ failed: false });
  }

  render() {
    if (this.state.failed) {
      return <ErrorState message="This view could not be displayed." retry={() => window.location.reload()} />;
    }
    return this.props.children;
  }
}
