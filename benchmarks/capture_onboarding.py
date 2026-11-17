"""Capture an existing public demo project through the real browser UI.

Install the optional screenshot dependency with `pip install playwright`.
The default browser channel uses an installed Microsoft Edge.
"""
from __future__ import annotations

import argparse
import getpass
import json
from pathlib import Path

from playwright.sync_api import expect, sync_playwright


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8766")
    parser.add_argument("--project-name", default="FastAPI Template · 研发入职")
    parser.add_argument("--username", default="admin")
    parser.add_argument("--password-file", type=Path)
    parser.add_argument("--channel", default="msedge")
    parser.add_argument("--output", type=Path, default=Path("docs/assets"))
    parser.add_argument("--report", type=Path, default=Path("benchmarks/results/browser-onboarding.json"))
    args = parser.parse_args()
    password = args.password_file.read_text("utf-8").rstrip("\r\n") if args.password_file else getpass.getpass("Password: ")
    args.output.mkdir(parents=True, exist_ok=True)
    errors, failures, screenshots, checks = [], [], [], []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel=args.channel, headless=True)
        context = browser.new_context(viewport={"width": 1600, "height": 1060}, device_scale_factor=1)
        page = context.new_page()
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(args.base_url)
        page.locator('#login-form input[name="username"]').fill(args.username)
        page.locator('#login-form input[name="password"]').fill(password)
        page.locator('#login-form button[type="submit"]').click()
        expect(page.locator("#workspace")).to_be_visible()
        expect(page.locator("#project-picker option", has_text=args.project_name)).to_have_count(1)
        page.on("response", lambda response: failures.append({"path": response.url.split(args.base_url)[-1],
                    "status": response.status}) if response.status >= 400 else None)
        page.locator("#project-picker").select_option(label=args.project_name)
        expect(page.locator("#breadcrumb")).to_have_text(args.project_name)
        expect(page.locator(".chapter")).to_be_visible(timeout=20000)
        checks.append("login_and_open_assigned_project")

        def capture(name):
            page.evaluate("window.scrollTo(0, 0)")
            page.screenshot(path=str(args.output / name), animations="disabled")
            screenshots.append(name)

        capture("team-course.png")
        page.locator('[data-view="entries"]').click()
        expect(page.locator("#main h1")).to_have_text("源码阅读")
        expect(page.locator("#main .rows .row").first).to_be_visible()
        checks.append("reading_entries_rendered")
        capture("team-reading.png")

        page.locator("#main details").filter(has=page.locator("summary", has_text="核心模块")).locator("summary").click()
        page.locator("#main details").filter(has=page.locator("summary", has_text="核心模块")).get_by_role("button", name="查看源码").first.click()
        expect(page.locator("#source-dialog")).to_be_visible()
        expect(page.locator("#source-body")).not_to_be_empty()
        checks.append("source_snapshot_preview")
        capture("team-source.png")
        page.locator("#close-source").click()

        page.locator('[data-view="knowledge"]').click()
        expect(page.locator("#main h1")).to_have_text("项目知识库")
        expect(page.locator("#main .row").first).to_be_visible()
        checks.append("repository_documents_rendered")
        capture("team-knowledge.png")

        page.locator('[data-view="courses"]').click()
        expect(page.locator(".chapter")).to_be_visible()
        history = page.locator("#conversation-list button")
        if history.count():
            history.first.click()
            expect(page.locator("#chat-panel")).to_be_visible()
            expect(page.locator(".message.assistant").first).to_be_visible()
            expect(page.locator(".message.assistant .references button").first).to_be_attached()
            page.locator("#messages").evaluate("""element => {
                const questions = element.querySelectorAll('.message.user');
                const target = questions[questions.length - 1];
                element.scrollTop += target.getBoundingClientRect().top - element.getBoundingClientRect().top;
            }""")
            checks.append("saved_conversation_with_citations")
            capture("team-followup.png")
            page.locator(".message.assistant .citation").last.click()
            expect(page.locator("#source-dialog")).to_be_visible()
            expect(page.locator("#source-body")).not_to_be_empty()
            checks.append("answer_citation_opens_source")
            page.locator("#close-source").click()
            chapter_title = page.locator(".chapter > h2").inner_text()
            message_count = page.locator(".message").count()
            page.reload()
            expect(page.locator("#chat-panel")).to_be_visible()
            expect(page.locator(".message.assistant").first).to_be_visible()
            expect(page.locator(".chapter > h2")).to_have_text(chapter_title)
            expect(page.locator(".message")).to_have_count(message_count)
            checks.append("conversation_restored_after_reload")
            checks.append("chapter_and_message_count_restored")
        browser.close()
    report = {"browser": args.channel, "viewport": {"width": 1600, "height": 1060},
              "checks": checks, "screenshots": screenshots, "page_errors": errors, "http_failures": failures}
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), "utf-8")
    print(json.dumps(report, ensure_ascii=False))
    if errors or failures:
        raise SystemExit("Browser errors were recorded. Inspect the report before publishing screenshots.")


if __name__ == "__main__":
    main()
