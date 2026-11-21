"""Open a page in headless Chromium and print what a person would see.

Usage (from any directory):
    uv run --with playwright python snapshot.py URL [--screenshot FILE] [--full-page]
        [--pdf FILE] [--wait-for CSS] [--timeout SECS] [--max-chars N] [--json]

Prints the final URL, title, visible text (truncated to --max-chars) and the
page's links. --screenshot / --pdf also save those files. Exit code 1 on a
navigation error, with the error on stderr. Set CHROMIUM_PATH to use an
existing Chrome/Chromium (e.g. /usr/bin/chromium) instead of Playwright's.
The sandbox is disabled only as root or with CHROMIUM_NO_SANDBOX=1.
"""

import argparse
import json
import os
import sys

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright


def launch_args() -> list[str]:
    """Chromium flags. The renderer sandbox stays on unless it can't work.

    Chromium refuses to start sandboxed as root (typical in Docker), and some
    containers lack the namespaces it needs: there, CHROMIUM_NO_SANDBOX=1.
    Without the sandbox a malicious page that exploits the renderer runs code
    as this user, so only turn it off when you have to.
    """
    args = ["--disable-dev-shm-usage"]
    is_root = hasattr(os, "geteuid") and os.geteuid() == 0
    if is_root or os.environ.get("CHROMIUM_NO_SANDBOX"):
        args.append("--no-sandbox")
    return args


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("url")
    p.add_argument("--screenshot", help="save a PNG screenshot here")
    p.add_argument("--full-page", action="store_true", help="screenshot the whole page")
    p.add_argument("--pdf", help="save the page as a PDF here")
    p.add_argument("--wait-for", help="CSS selector to wait for before reading")
    p.add_argument("--timeout", type=float, default=30, help="seconds (default 30)")
    p.add_argument(
        "--max-chars", type=int, default=8000, help="text limit (default 8000)"
    )
    p.add_argument("--max-links", type=int, default=50, help="link limit (default 50)")
    p.add_argument("--json", action="store_true", help="print JSON instead of text")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    timeout_ms = args.timeout * 1000
    with sync_playwright() as pw:
        # CHROMIUM_PATH: use an existing Chrome/Chromium instead of Playwright's own
        browser = pw.chromium.launch(
            args=launch_args(), executable_path=os.environ.get("CHROMIUM_PATH") or None
        )
        try:
            page = browser.new_page(viewport={"width": 1280, "height": 900})
            page.set_default_timeout(timeout_ms)
            try:
                page.goto(args.url, wait_until="domcontentloaded")
                try:  # settle client-side rendering, but don't fail on chatty pages
                    page.wait_for_load_state(
                        "networkidle", timeout=min(timeout_ms, 10000)
                    )
                except PlaywrightError:
                    pass
                if args.wait_for:
                    page.wait_for_selector(args.wait_for)
            except PlaywrightError as e:
                print(f"Navigation failed: {e}", file=sys.stderr)
                return 1

            text = page.inner_text("body") if page.query_selector("body") else ""
            links = page.eval_on_selector_all(
                "a[href]",
                "els => els.map(a => [a.innerText.trim().replace(/\\s+/g, ' '), a.href])",
            )
            if args.screenshot:
                page.screenshot(path=args.screenshot, full_page=args.full_page)
            if args.pdf:
                page.pdf(path=args.pdf, print_background=True)

            result = {
                "url": page.url,
                "title": page.title(),
                "text": text[: args.max_chars],
                "text_truncated": len(text) > args.max_chars,
                "links": [{"text": t, "href": h} for t, h in links[: args.max_links]],
                "links_total": len(links),
                "screenshot": args.screenshot,
                "pdf": args.pdf,
            }
        finally:
            browser.close()

    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0
    print(f"URL: {result['url']}\nTitle: {result['title']}")
    for kind in ("screenshot", "pdf"):
        if result[kind]:
            print(f"Saved {kind}: {result[kind]}")
    print("\n--- Text ---")
    print(result["text"] + ("\n[… truncated]" if result["text_truncated"] else ""))
    print(f"\n--- Links ({len(result['links'])} of {result['links_total']}) ---")
    for link in result["links"]:
        print(f"- {link['text'] or '(no text)'} → {link['href']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
