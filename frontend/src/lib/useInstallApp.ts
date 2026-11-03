import { useSyncExternalStore } from "react";

interface InstallPrompt extends Event {
    prompt: () => Promise<void>;
    userChoice: Promise<{ outcome: "accepted" | "dismissed" }>;
}
interface InstallState {
    available: boolean;
    installed: boolean;
    pending: boolean;
    error: string | null;
}

let prompt: InstallPrompt | null = null;
let initialized = false;
let state: InstallState = { available: false, installed: false, pending: false, error: null };
const listeners = new Set<() => void>();
function publish(patch: Partial<InstallState>) {
    state = { ...state, ...patch };
    listeners.forEach(listener => listener());
}

/** Capture the browser offer at startup, before the user opens the profile. */
export function initializeInstallPrompt() {
    if (initialized) return;
    initialized = true;
    const standalone = window.matchMedia("(display-mode: standalone)");
    const updateDisplayMode = () => publish({
        installed: standalone.matches || Boolean((navigator as Navigator & { standalone?: boolean }).standalone),
    });
    updateDisplayMode();
    standalone.addEventListener("change", updateDisplayMode);
    window.addEventListener("beforeinstallprompt", event => {
        event.preventDefault();
        prompt = event as InstallPrompt;
        publish({ available: true, error: null });
    });
    window.addEventListener("appinstalled", () => {
        prompt = null;
        publish({ installed: true, available: false, pending: false, error: null });
    });
}

async function install() {
    const offer = prompt;
    if (!offer || state.pending) return;
    prompt = null; // Each browser offer can be used only once.
    publish({ available: false, pending: true, error: null });
    try {
        await offer.prompt();
        await offer.userChoice;
    } catch {
        publish({ error: "Откройте меню браузера и выберите «Добавить на главный экран»." });
    } finally {
        publish({ pending: false });
    }
}

export function useInstallApp() {
    const current = useSyncExternalStore(
        listener => { listeners.add(listener); return () => { listeners.delete(listener); }; },
        () => state,
    );
    return { ...current, install };
}
