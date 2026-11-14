// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import { DocumentList } from "./DocumentList";
import { documentFixture } from "./documentFixtures.test-utils";

afterEach(cleanup);

it("сохраняет компактный список, метаданные, открытие с клавиатуры и отдельные действия", async () => {
    const document = documentFixture({ caption: "Согласованная версия", tasks: [{ id: 5, key: "TEST-5", title: "Согласование", relation: "linked" }] });
    const onOpen = vi.fn(); const action = vi.fn(); const user = userEvent.setup();
    const { container } = render(<DocumentList documents={[document]} onOpen={onOpen}
        actions={() => <button type="button" aria-label="Снять связь" onClick={action}>Снять связь</button>} />);
    const open = screen.getByRole("button", { name: "Открыть документ План проекта" });
    expect(open.className).toContain("min-h-14");
    expect(open.className).toContain("gap-3 px-3 py-2");
    expect(screen.getByText("Согласованная версия")).toBeTruthy();
    expect(screen.getByText("TEST-5")).toBeTruthy();
    expect(container.querySelector("time")?.getAttribute("datetime")).toBe(document.updated_at);
    expect(container.querySelector("button button")).toBeNull();
    await user.click(screen.getByRole("button", { name: "Снять связь" }));
    expect(action).toHaveBeenCalledOnce();
    expect(onOpen).not.toHaveBeenCalled();
    open.focus();
    await user.keyboard("{Enter}");
    expect(onOpen).toHaveBeenCalledExactlyOnceWith(document);
});
