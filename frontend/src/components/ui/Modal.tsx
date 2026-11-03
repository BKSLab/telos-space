import type { ReactNode } from "react";
import { X } from "lucide-react";
import { Dialog, Heading, Modal as RACModal, ModalOverlay } from "react-aria-components";
import { cn } from "@/lib/cn";
import { IconButton } from "@/components/ui/Button";

interface ModalProps {
    title: string;
    description?: string;
    header?: ReactNode;
    children: ReactNode;
    footer?: ReactNode;
    isOpen?: boolean;
    onOpenChange?: (isOpen: boolean) => void;
    /** Ширина диалога: обычные формы — sm, рабочая карточка — xl. */
    size?: "sm" | "md" | "lg" | "xl";
    layout?: "form" | "workspace";
    closeLabel?: string;
    isDismissable?: boolean;
    tall?: boolean;
}

const SIZES = {
    sm: "max-w-md",
    md: "max-w-xl",
    lg: "max-w-3xl",
    xl: "max-w-6xl",
} as const;

export function Modal({
    title,
    description,
    header,
    children,
    footer,
    isOpen,
    onOpenChange,
    size = "sm",
    layout = "form",
    closeLabel = "Закрыть",
    isDismissable = true,
    tall = false,
}: ModalProps) {
    return (
        <ModalOverlay
            isOpen={isOpen}
            onOpenChange={onOpenChange}
            isDismissable={isDismissable}
            className={cn(
                "modal-overlay viewport-overlay fixed inset-0 z-50 flex items-center justify-center bg-black/45",
                "backdrop-blur-[1.5px]",
                layout === "workspace" ? "p-0 sm:p-5" : "p-4",
            )}
        >
            <RACModal
                className={cn(
                    "modal-surface material-panel relative flex w-full flex-col overflow-hidden",
                    layout === "workspace" && "brand-window-edge modal-workspace",
                    layout === "workspace"
                        ? "h-dvh max-h-dvh rounded-none sm:h-[min(88dvh,900px)] sm:rounded-[var(--radius-floating)]"
                        : [tall ? "max-h-[min(92vh,1000px)]" : "max-h-[min(85vh,900px)]", "rounded-[var(--radius-floating)]"],
                    "shadow-panel",
                    SIZES[size],
                )}
            >
                <Dialog aria-label={title} className="flex min-h-0 flex-1 flex-col outline-none">
                    {({ close }) => (
                        <>
                            <header className="material-metal flex shrink-0 items-start justify-between gap-4 border-b border-line px-5 py-4">
                                {header ? <div className="min-w-0 flex-1">{header}</div> : <div className="flex min-w-0 flex-col gap-1">
                                    <Heading
                                        slot="title"
                                        className="min-w-0 text-[15px] font-semibold break-words text-primary"
                                    >
                                        {title}
                                    </Heading>
                                    {description && (
                                        <p className="text-[13px] text-muted">{description}</p>
                                    )}
                                </div>}
                                <IconButton label={closeLabel} onClick={close}>
                                    <X size={16} aria-hidden="true" />
                                </IconButton>
                            </header>

                            <div className={layout === "workspace" ? "flex min-h-0 flex-1 flex-col overflow-hidden" : "modal-form-body scrollbar-thin min-h-0 flex-1 overflow-y-auto px-5 py-5"}>
                                {children}
                            </div>

                            {footer && (
                                <footer className="material-metal flex shrink-0 flex-wrap items-center justify-end gap-2 border-t border-line px-4 py-3 sm:px-5 sm:py-3.5">
                                    {footer}
                                </footer>
                            )}
                        </>
                    )}
                </Dialog>
            </RACModal>
        </ModalOverlay>
    );
}
