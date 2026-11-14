"""Снимки веб-интерфейсов системы в браузере Chromium (Playwright).

    python demo/web_shots.py panel   <base_url> <каталог> <пароль admin>
    python demo/web_shots.py grafana <base_url> <каталог>
    python demo/web_shots.py page    <url> <файл.png>
"""

import asyncio
import sys
from pathlib import Path

from playwright.async_api import async_playwright

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0.0.0 Safari/537.36"
)


async def new_page(browser, width=1440, height=900):
    context = await browser.new_context(
        viewport={"width": width, "height": height},
        device_scale_factor=1.5,
        user_agent=USER_AGENT,
        locale="ru-RU",
    )
    return await context.new_page()


async def open_panel(page, base_url: str, out: Path) -> None:
    """Шлюз может подхватить маршруты не сразу: страница перезагружается до появления формы входа."""
    for _ in range(24):
        try:
            await page.goto(base_url, wait_until="networkidle", timeout=20000)
            await page.wait_for_selector("#login-username", timeout=5000)
            return
        except Exception:  # noqa: BLE001
            await page.wait_for_timeout(5000)
    await page.screenshot(path=str(out / "debug-panel.png"))
    (out / "debug-panel.html").write_text(await page.content(), encoding="utf-8")
    raise RuntimeError("Форма входа не появилась")


async def panel(browser, base_url: str, out: Path, password: str) -> None:
    page = await new_page(browser)
    await open_panel(page, base_url, out)
    await page.fill("#login-username", "admin")
    await page.fill("#login-password", "wrong-password")
    await page.click("#login-form button[type=submit]")
    await page.wait_for_timeout(800)
    await page.screenshot(path=str(out / "login.png"))

    await page.fill("#login-password", password)
    await page.click("#login-form button[type=submit]")
    await page.wait_for_selector("#orders tr")
    await page.wait_for_timeout(6000)  # загрузка тайлов карты
    await page.screenshot(path=str(out / "orders.png"))

    await page.click("button[data-tab=drivers-tab]")
    await page.wait_for_selector("#drivers tr")
    await page.click("#drivers button[data-edit]")
    await page.wait_for_timeout(500)
    await page.screenshot(path=str(out / "drivers.png"))


async def grafana(browser, base_url: str, out: Path) -> None:
    page = await new_page(browser, 1440, 1500)
    await page.goto(f"{base_url}/grafana/login", wait_until="networkidle")
    await page.fill("input[name=user]", "admin")
    await page.fill("input[name=password]", "admin")
    await page.click("button[type=submit]")
    await page.wait_for_timeout(3000)
    await page.goto(
        f"{base_url}/grafana/d/taxi-dispatch/?orgId=1&from=now-15m&to=now&kiosk",
        wait_until="networkidle",
    )
    await page.wait_for_timeout(8000)
    await page.screenshot(path=str(out / "grafana.png"))


async def single(browser, url: str, target: Path) -> None:
    page = await new_page(browser, 1440, 1000)
    await page.goto(url, wait_until="networkidle")
    await page.wait_for_timeout(3000)
    await page.screenshot(path=str(target))


async def main() -> None:
    mode = sys.argv[1]
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        if mode == "panel":
            await panel(browser, sys.argv[2], Path(sys.argv[3]), sys.argv[4])
        elif mode == "grafana":
            await grafana(browser, sys.argv[2], Path(sys.argv[3]))
        elif mode == "page":
            await single(browser, sys.argv[2], Path(sys.argv[3]))
        else:
            raise SystemExit(f"Неизвестный режим: {mode}")
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
