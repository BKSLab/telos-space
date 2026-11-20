import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { connectRepository, githubRetryAt, type RepositoryDraft } from "./github";

export interface ConnectionAttempt {
    draft: RepositoryDraft;
    state: "waiting" | "connecting" | "connected" | "failed";
    error?: string;
}

export function useRepositoryConnections() {
    const [items, setItems] = useState<ConnectionAttempt[]>([]);
    const [retryAt, setRetryAt] = useState<number | null>(null);
    const { mutateAsync, isPending } = useMutation({
        retry: false,
        mutationFn: async ({ projectId, attempts }: { projectId: number; attempts: ConnectionAttempt[] }) => {
            let result = attempts;
            setItems(result);
            setRetryAt(null);
            const update = (index: number, item: ConnectionAttempt) => {
                result = result.map((current, position) => position === index ? item : current);
                setItems(result);
            };
            for (const [index, item] of attempts.entries()) {
                const { draft } = item;
                if (item.state === "connected") continue;
                update(index, { draft, state: "connecting" });
                try {
                    await connectRepository(projectId, draft);
                    update(index, { draft, state: "connected" });
                } catch (error) {
                    const deadline = githubRetryAt(error);
                    if (deadline !== null) {
                        setRetryAt(deadline);
                        update(index, { draft, state: "waiting" });
                        break;
                    }
                    update(index, { draft, state: "failed", error: error instanceof Error ? error.message : "Не удалось подключить репозиторий." });
                }
            }
            return result.every(item => item.state === "connected");
        },
    });
    return {
        items, retryAt, isPending,
        reset: () => { setItems([]); setRetryAt(null); },
        start: (projectId: number, drafts: RepositoryDraft[]) => mutateAsync({ projectId, attempts: drafts.map(draft => ({ draft, state: "waiting" })) }),
        retry: (projectId: number) => mutateAsync({ projectId, attempts: items }),
    };
}
