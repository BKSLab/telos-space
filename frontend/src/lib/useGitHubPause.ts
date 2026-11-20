import { useEffect, useState } from "react";

export function useGitHubPause(until: number | null) {
    const [expired, setExpired] = useState<number | null>(null);
    useEffect(() => {
        if (until === null) return;
        const timer = window.setTimeout(() => setExpired(until), Math.max(0, until - Date.now()));
        return () => window.clearTimeout(timer);
    }, [until]);
    return until !== null && until !== expired;
}
