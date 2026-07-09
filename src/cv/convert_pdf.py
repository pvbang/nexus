"""
Convert CV HTML to PDF using Playwright (Chromium headless).

Usage: python convert_pdf.py
Requirements: pip install playwright && python -m playwright install chromium
"""
import asyncio
import sys
from pathlib import Path

from playwright.async_api import async_playwright


async def convert():
    html_path = Path(__file__).parent / "html" / "AI-Engineer-PhanVanBang.html"
    pdf_path = Path(__file__).parent / "files" / "AI-Engineer-PhanVanBang.pdf"

    if not html_path.exists():
        print(f"ERROR: HTML file not found: {html_path}")
        sys.exit(1)

    # Ensure output directory exists
    pdf_path.parent.mkdir(parents=True, exist_ok=True)

    async with async_playwright() as p:
        try:
            browser = await p.chromium.launch()
        except Exception as e:
            print(f"ERROR: Failed to launch Chromium: {e}")
            print("Run: python -m playwright install chromium")
            sys.exit(1)
        page = await browser.new_page()

        # Navigate to local HTML file
        await page.goto(f"file:///{html_path.resolve().as_posix()}", wait_until="networkidle")

        # Generate PDF with A4 size, no browser header/footer
        await page.pdf(
            path=str(pdf_path),
            format="A4",
            margin={"top": "10mm", "right": "12mm", "bottom": "10mm", "left": "12mm"},
            print_background=True,
            display_header_footer=False,
        )

        await browser.close()

    print(f"PDF generated successfully: {pdf_path.resolve()}")


if __name__ == "__main__":
    asyncio.run(convert())
