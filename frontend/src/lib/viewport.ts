/** Keep editors and overlays above the virtual keyboard without disabling zoom. */
export function observeViewport() {
    const root = document.documentElement;
    const viewport = window.visualViewport;
    let frame = 0;
    let restingHeight = viewport?.height ?? window.innerHeight;
    let restingWidth = window.innerWidth;
    const update = () => {
        cancelAnimationFrame(frame);
        frame = requestAnimationFrame(() => {
            // Pinch zoom belongs to the browser; resizing the layout would fight it.
            if (viewport && Math.abs(viewport.scale - 1) > 0.01) return;
            const height = viewport?.height ?? window.innerHeight;
            const active = document.activeElement;
            const editing = Boolean(active instanceof HTMLTextAreaElement || (active instanceof HTMLInputElement && !["checkbox", "radio", "range", "color", "file", "button", "submit"].includes(active.type)) || (active instanceof HTMLElement && active.isContentEditable));
            if (Math.abs(window.innerWidth - restingWidth) > 80) {
                restingHeight = height;
                restingWidth = window.innerWidth;
            }
            if (!editing) restingHeight = Math.max(restingHeight, height);
            root.dataset.keyboardOpen = String(editing && height < restingHeight - 120);
            root.style.setProperty("--app-height", `${height}px`);
            root.style.setProperty("--app-top", `${viewport?.offsetTop ?? 0}px`);
        });
    };
    update();
    viewport?.addEventListener("resize", update);
    viewport?.addEventListener("scroll", update);
    window.addEventListener("resize", update);
    window.addEventListener("focusin", update);
    window.addEventListener("focusout", update);
    return () => {
        cancelAnimationFrame(frame);
        viewport?.removeEventListener("resize", update);
        viewport?.removeEventListener("scroll", update);
        window.removeEventListener("resize", update);
        window.removeEventListener("focusin", update);
        window.removeEventListener("focusout", update);
        delete root.dataset.keyboardOpen;
        root.style.removeProperty("--app-height");
        root.style.removeProperty("--app-top");
    };
}
