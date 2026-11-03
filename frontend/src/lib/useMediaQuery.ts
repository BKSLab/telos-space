import { useCallback, useSyncExternalStore } from "react";

/** Keeps responsive interaction behaviour in sync after rotation and resizing. */
export function useMediaQuery(query: string): boolean {
    const subscribe = useCallback((notify: () => void) => {
        const media = window.matchMedia(query);
        media.addEventListener("change", notify);
        return () => media.removeEventListener("change", notify);
    }, [query]);
    const snapshot = useCallback(() => window.matchMedia(query).matches, [query]);
    return useSyncExternalStore(subscribe, snapshot, () => false);
}
