import { expect, it } from "vitest";
import type { GitFilePage } from "@/lib/projectKnowledge";
import { sourcePageWindow } from "./sourcePage";
it("continues at the next complete line using Unicode offsets for CRLF and emoji", () => {
    const page = { text: "😀Привет\r\nслед", offset: 100, next_offset: 113 } as GitFilePage;
    expect(sourcePageWindow(page)).toEqual({ text: "😀Привет\r\n", next: 109 });
    expect(sourcePageWindow({ ...page, text: "😀безпереносов" })).toEqual({ text: "😀безпереносов", next: 113 });
    expect(sourcePageWindow({ ...page, next_offset: null })).toEqual({ text: page.text, next: null });
});
