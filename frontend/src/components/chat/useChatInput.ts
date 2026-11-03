import { useLayoutEffect, useRef, type KeyboardEvent } from "react";

export function isTouchChatInput() {
    return typeof window.matchMedia === "function" && window.matchMedia("(pointer: coarse)").matches;
}

/** На экранной клавиатуре Enter оставляет новую строку; отправка — отдельной кнопкой. */
export function shouldSubmitChat(event: KeyboardEvent<HTMLTextAreaElement>) {
    return event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing && event.nativeEvent.keyCode !== 229
        && (event.ctrlKey || event.metaKey || !isTouchChatInput());
}

export function useChatInput(value: string) {
    const input = useRef<HTMLTextAreaElement>(null);
    useLayoutEffect(() => {
        const element = input.current;
        if (!element) return;
        const resize = () => {
            element.style.height = "0px";
            element.style.height = `${element.scrollHeight}px`;
        };
        resize();
        window.addEventListener("resize", resize);
        window.visualViewport?.addEventListener("resize", resize);
        return () => {
            window.removeEventListener("resize", resize);
            window.visualViewport?.removeEventListener("resize", resize);
        };
    }, [value]);
    return input;
}
