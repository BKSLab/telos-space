/** Copy from a user gesture, including HTTP pages without the Clipboard API. */
export async function copyText(text: string): Promise<void> {
    try {
        if (navigator.clipboard?.writeText) {
            await navigator.clipboard.writeText(text);
            return;
        }
    } catch {
        // A permission policy can deny the modern API even on HTTPS.
    }

    // Keep this synchronous when the modern API is absent to retain the gesture.
    const active = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const selection = document.getSelection();
    const ranges = selection ? Array.from({ length: selection.rangeCount }, (_, index) => selection.getRangeAt(index).cloneRange()) : [];
    const control = active instanceof HTMLInputElement || active instanceof HTMLTextAreaElement ? active : null;
    const start = control?.selectionStart ?? null;
    const end = control?.selectionEnd ?? null;
    const direction = control?.selectionDirection ?? undefined;
    const field = document.createElement("textarea");
    field.value = text;
    field.readOnly = true;
    field.tabIndex = -1;
    Object.assign(field.style, {
        position: "fixed", top: "0", left: "0", width: "1px", height: "1px",
        padding: "0", border: "0", opacity: "0", fontSize: "16px", pointerEvents: "none",
    });
    // React Aria traps focus in the dialog: keep the copy target inside it.
    const container = active?.closest('[role="dialog"]') ?? document.body;
    try {
        container.appendChild(field);
        field.focus({ preventScroll: true });
        field.select();
        field.setSelectionRange(0, text.length);
        if (!document.execCommand?.("copy")) throw new Error("Clipboard copy unavailable");
    } catch {
        throw new Error("Clipboard copy unavailable");
    } finally {
        field.value = "";
        field.remove();
        try {
            if (active?.isConnected) active.focus({ preventScroll: true });
            if (selection) {
                selection.removeAllRanges();
                for (const range of ranges) {
                    if (range.startContainer.isConnected && range.endContainer.isConnected) selection.addRange(range);
                }
            }
            if (control?.isConnected && start !== null && end !== null) control.setSelectionRange(start, end, direction);
        } catch {
            // Restoring a removed control or selection must not undo a copy.
        }
    }
}
