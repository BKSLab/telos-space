import type { ReactNode } from "react";
import { X } from "lucide-react";
import { Dialog, Modal as RACModal, ModalOverlay } from "react-aria-components";
import { cn } from "@/lib/cn";
import { IconButton } from "@/components/ui/Button";

interface DrawerProps {
    /** Доступное имя панели: у неё нет одного короткого заголовка. */
    label: string;
    header: ReactNode;
    children: ReactNode;
    footer?: ReactNode;
    isOpen: boolean;
    onClose: () => void;
    closeLabel?: string;
}

/**
 * Правая панель деталей: рабочая область остаётся видимой, поэтому контекст
 * доски или карты не теряется (раздел 11). На узких экранах разворачивается
 * в полноэкранный лист (раздел 19).
 */
export function Drawer({ label, header, children, footer, isOpen, onClose, closeLabel = "Закрыть панель задачи" }: DrawerProps) {
    return (
        <ModalOverlay
            isOpen={isOpen}
            onOpenChange={(open) => {
                if (!open) {
                    onClose();
                }
            }}
            isDismissable
            className="drawer-overlay viewport-overlay fixed inset-0 z-50 flex justify-end bg-black/40"
        >
            <RACModal
                className={cn(
                    "drawer-surface material-panel flex h-full w-full flex-col overflow-hidden shadow-panel",
                    "sm:w-[min(580px,100vw)] sm:rounded-l-[var(--radius-panel)]",
                )}
            >
                <Dialog aria-label={label} className="flex min-h-0 flex-1 flex-col outline-none">
                    <header className="material-metal sticky top-0 z-10 flex shrink-0 items-start gap-3 border-b border-line px-5 py-4">
                        <div className="min-w-0 flex-1">{header}</div>
                        <IconButton label={closeLabel} onClick={onClose}>
                            <X size={16} aria-hidden="true" />
                        </IconButton>
                    </header>

                    <div className="scrollbar-thin min-h-0 flex-1 overflow-y-auto">{children}</div>

                    {footer && (
                        <footer className="material-metal flex shrink-0 flex-wrap items-center justify-between gap-2 border-t border-line px-4 py-3 sm:px-5">
                            {footer}
                        </footer>
                    )}
                </Dialog>
            </RACModal>
        </ModalOverlay>
    );
}

interface DrawerSectionProps {
    title: string;
    count?: number | null;
    action?: ReactNode;
    children: ReactNode;
}

export function DrawerSection({ title, count, action, children }: DrawerSectionProps) {
    return (
        <section className="border-b border-line-subtle px-5 py-4 first:pt-5 last:border-b-0 last:pb-6">
            <div className="mb-3 flex items-center justify-between gap-2">
                <h3 className="flex items-center gap-1.5 text-[11px] font-semibold tracking-[0.08em] text-muted uppercase">
                    {title}
                    {typeof count === "number" && (
                        <span className="font-mono text-[11px] text-disabled">{count}</span>
                    )}
                </h3>
                {action}
            </div>
            {children}
        </section>
    );
}
