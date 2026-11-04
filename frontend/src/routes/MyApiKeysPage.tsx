import { Navigate } from "react-router-dom";

/** Keep existing bookmarks while key settings live in the profile. */
export function MyApiKeysPage() {
    return <Navigate to="/profile?tab=keys" replace />;
}
