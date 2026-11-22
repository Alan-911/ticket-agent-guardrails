"""Capture full-resolution console screenshots for the write-up.

Needs the console running (ui/server.py). Drives your installed Microsoft Edge
through Playwright, so no browser download. Runs three real agent emails
(a few cents of Bedrock usage).

    uv run --group infra --with playwright python ui/capture_screenshots.py
"""
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "screenshots"
URL = "http://127.0.0.1:8765/"


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge")
        page = browser.new_page(viewport={"width": 1600, "height": 1000}, device_scale_factor=2,
                                color_scheme="light")
        page.goto(URL)
        page.wait_for_selector("#inbox .row")

        def shot(name):
            page.screenshot(path=str(OUT / name))
            print("saved", name)

        def clear_toast():
            page.evaluate("document.getElementById('toast').style.display = 'none'")

        def toast():
            # wait for THIS action's toast and for every spinner to finish
            page.wait_for_selector("#toast", state="visible", timeout=300_000)
            page.wait_for_function("!document.querySelector('.spin')", timeout=300_000)
            page.wait_for_timeout(600)

        def click(text):
            clear_toast()
            page.locator("button", has_text=text).first.click()
            toast()

        def run_email(subject_or_id):
            page.locator("#inbox .row", has_text=subject_or_id).first.click()
            clear_toast()
            page.locator("#send").click()
            toast()

        def scroll_detail(where):
            page.evaluate(f"document.getElementById('detail').scrollTop = {where}")
            page.wait_for_timeout(300)

        # 1-2. A live run of the agent on a normal reorder.
        run_email("Business cards for Maya")
        shot("01-agent-draft.png")
        scroll_detail("1e6")
        shot("02-agent-vs-database.png")
        scroll_detail(0)

        # 3. Human workflow, then the attacker tries to approve from the Reply-To address.
        click("Estimator: mark reviewed")
        click("Send proof to address on file")
        click("Attack: approve from Reply-To address")
        shot("03-replyto-approval-denied.png")

        # 4. Legitimate approval and release to the hot folder.
        click("Client approves")
        click("Release to press")
        shot("04-released-to-hot-folder.png")
        page.locator("[data-hot]").click()
        page.wait_for_timeout(500)
        shot("05-hot-folders.png")

        # 6. Approve, then change the job: release refused.
        run_email("Spring catalog brochure RFQ")
        click("Estimator: mark reviewed")
        click("Send proof to address on file")
        click("Client approves")
        click("Attack: change quantity after approval")
        page.wait_for_timeout(500)
        click("Release to press")
        shot("06-change-after-approval-denied.png")

        # 7. Prompt injection hidden in a PDF attachment.
        page.locator("[data-f=attack]").click()
        run_email("Postcards")
        shot("07-pdf-injection-flagged.png")

        # 8. Prohibited work refused by Guardrails before the model runs.
        run_email("Props for our theatre night")
        shot("08-guardrails-refusal.png")

        browser.close()


if __name__ == "__main__":
    main()
