import type { GitFilePage } from "@/lib/projectKnowledge";

/** Python offsets count Unicode code points; JavaScript indices count UTF-16 units. */
export function sourcePageWindow(page: GitFilePage) {
    const boundary = page.next_offset !== null ? page.text.lastIndexOf("\n") + 1 : 0;
    const text = boundary > 0 ? page.text.slice(0, boundary) : page.text;
    return { text, next: boundary > 0 ? page.offset + Array.from(text).length : page.next_offset };
}
