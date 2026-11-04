import { Navigate } from "react-router-dom";

/** Client-side bookmark compatibility; the server /mcp transport is unchanged. */
export function McpPage() {
    return <Navigate to="/help/mcp" replace />;
}
