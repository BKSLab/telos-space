import { describe, expect, it } from "vitest";
import { TIM_CHAT_HINT, TIM_NAME, TIM_PERSONA, describePageContext, isForTim, pageContext } from "@/lib/tim";

describe("isForTim — имя в начале и граница слова, как на сервере", () => {
    it.each([
        "Арис, что горит?", "АРИС перенеси срок", "  арис: сводка", "арис!", "Арис", "Арис🙂", "Арис PM, что горит?",
        "ARIS, что горит?", "aris перенеси срок", "  ArIs: сводка", "ARIS!", "ARIS", "ARIS🙂", "ARIS PM, что горит?",
    ])("«%s» — Арису", (text) => {
        expect(isForTim(text)).toBe(true);
    });
    it.each([
        "Аристотель", "Ариса", "Арис2", "АрисO", "Арис𐐀", "Арис²", "АрисА", "Арис_сводка", "ARISTOTLE", "ARIS2", "ARISЖ", "ARIS𐐀", "ARIS²", "ARIS_сводка",
        "коллеги, Арис сказал", "коллеги, ARIS сказал", "@Арис привет", "@ARIS привет", "Вера, сводка", "ВЕРА", "вера!", "Веранда", "Тим, сводка", "ТИМ", "Тим PM, сводка", "", "ар", null, undefined,
    ])("«%s» — не Арису", (text) => {
        expect(isForTim(text)).toBe(false);
    });
});

describe("имя агента", () => {
    it("представляет Ариса как проектного менеджера и показывает обращение по имени", () => {
        expect(TIM_NAME).toBe("Арис");
        expect(TIM_PERSONA).toBe("Арис — проектный менеджер");
        expect(TIM_CHAT_HINT).toContain("Арис, …");
        expect(isForTim("Арис, что горит?")).toBe(true);
        expect(isForTim(`${TIM_NAME}, что горит?`)).toBe(true);
    });
});

describe("pageContext", () => {
    it("берёт проект из адреса и открытую задачу", () => {
        expect(pageContext("/projects/DEMO/board", "", 12)).toEqual({ route: "/projects/DEMO/board", project_key: "DEMO", task_id: 12 });
    });
    it("задачу из ?task=, «new» — не проект", () => {
        expect(pageContext("/my-tasks", "?task=7", null)).toMatchObject({ project_key: null, task_id: 7 });
        expect(pageContext("/projects/new", "", null).project_key).toBeNull();
        expect(pageContext("/projects/DEMO", "?task=abc", null).task_id).toBeNull();
    });
});

describe("контекст доски", () => {
    it("передаёт только выбранную доску со страницы холста", () => {
        expect(pageContext("/boards", "?board=12", null)).toMatchObject({board_id: 12});
        expect(pageContext("/projects/DEMO/whiteboard", "?board=3", null)).toMatchObject({board_id: 3, project_key: "DEMO"});
        expect(pageContext("/my-tasks", "?board=12", null)).not.toHaveProperty("board_id");
        expect(pageContext("/boards", "?board=-1", null)).not.toHaveProperty("board_id");
        expect(describePageContext({route: "/boards", project_key: null, task_id: null, board_id: 12})).toBe("Доска #12");
    });
});

describe("describePageContext", () => {
    it("подписывает проект и задачу", () => {
        expect(describePageContext({ route: "/", project_key: "DEMO", task_id: 12 }, { taskKey: "DEMO-12" })).toBe("Проект DEMO · задача DEMO-12");
        expect(describePageContext({ route: "/", project_key: null, task_id: 12 })).toBe("Задача #12");
        expect(describePageContext({ route: "/", project_key: null, task_id: null })).toBe("");
    });
});

it("не роняет помощника на ошибочной escape-последовательности URL", () => {
    expect(pageContext("/projects/%invalid", "", null).project_key).toBe("%invalid");
});
