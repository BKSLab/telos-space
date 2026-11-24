import { useEffect, useState } from "react";
import { Check, Copy } from "lucide-react";
import { IconButton } from "@/components/ui/Button";
import { copyText } from "@/lib/clipboard";

export function CopyTaskTitleButton({ taskKey, title }: { taskKey: string; title: string }) {
    const [state, setState] = useState<"idle" | "copying" | "copied" | "error">("idle");

    useEffect(() => {
        if (state !== "copied") return;
        const timeout = window.setTimeout(() => setState("idle"), 2000);
        return () => window.clearTimeout(timeout);
    }, [state]);

    async function copy() {
        if (state === "copying") return;
        setState("copying");
        try {
            await copyText(`${taskKey} ${title}`);
            setState("copied");
        } catch {
            setState("error");
        }
    }

    return <>
        <IconButton label="Скопировать ключ и название" size="sm" aria-disabled={state === "copying"}
            className="text-accent/75 hover:text-accent focus-visible:text-accent"
            aria-busy={state === "copying"} onClick={() => void copy()}>
            {state === "copied"
                ? <Check size={13} aria-hidden="true" className="text-success" />
                : <Copy size={13} aria-hidden="true" />}
        </IconButton>
        <span role="status" aria-live="polite" className="sr-only">{state === "copied" ? "Ключ и название скопированы" : ""}</span>
        {state === "error" && <span role="alert" className="order-last w-full text-[12px] text-danger">Не удалось скопировать ключ и название. Попробуйте ещё раз.</span>}
    </>;
}
