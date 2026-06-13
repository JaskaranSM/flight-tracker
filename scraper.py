import asyncio
import random
import json
from playwright.async_api import async_playwright
from playwright_stealth import Stealth

from config import SCRAPE_TIMEOUT


async def scrape_air_india(flightnum, date):
    result = {"found": False}
    api_received = asyncio.Event()

    async def handle_response(response):
        if "status-by-fln" in response.url:
            try:
                content_type = response.headers.get("content-type", "").lower()
                if "json" in content_type:
                    print(f"\n[🎉] Target API Intercepted: {response.url}")
                    print(f"[+] Server Response Code: {response.status}")

                    data = await response.json()
                    result["found"] = True
                    result["data"] = data
                    api_received.set()

                    output_filename = "airindia_flight_status.json"
                    with open(output_filename, "w", encoding="utf-8") as f:
                        json.dump(data, f, indent=4, ensure_ascii=False)

                    print(f"[+] Success! Clean payload written to: {output_filename}")
                    print("[+] Quick Data Preview:")
                    print(json.dumps(data, indent=2)[:400] + "\n...")

            except Exception as e:
                print(f"[!] Error parsing target endpoint response data structure: {e}")

    async with Stealth().use_async(async_playwright()) as p:

        browser = await p.chromium.launch(
            headless=False,
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-blink-features=AutomationControlled",
                "--disable-infobars",
                "--ignore-certificate-errors",
                "--no-startup-window"
            ]
        )

        context = await browser.new_context(
            user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            viewport={"width": 1440, "height": 900},
            locale="en-IN",
            timezone_id="Asia/Kolkata",
            extra_http_headers={
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
                "Accept-Language": "en-IN,en-GB;q=0.9,en-US;q=0.8,en;q=0.7",
                "Sec-Ch-Ua": '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
                "Sec-Ch-Ua-Mobile": "?0",
                "Sec-Ch-Ua-Platform": '"Linux"',
                "Upgrade-Insecure-Requests": "1"
            }
        )

        page = await context.new_page()
        page.set_default_timeout(30000)

        target_url = f"https://www.airindia.com/in/en/manage/flight-status.html?fno={flightnum}&on={date}"
        print("[*] Initiating connection to Air India...")

        page.on("response", handle_response)

        try:
            await page.goto(target_url, wait_until="domcontentloaded")
            print("[+] Bypassed HTTP/2 socket barrier. Syncing background elements...")

            await page.wait_for_timeout(random.randint(3000, 5000))

            if not result["found"]:
                try:
                    btn = page.locator("button.form-btn.booking-flight-btn")
                    if await btn.count() > 0:
                        print("[+] Clicking search button to trigger API request...")
                        await btn.first.click()
                    else:
                        print("[*] Search button not found, API may fire automatically.")
                except Exception as e:
                    print(f"[*] Button click skipped: {e}")

            await page.wait_for_timeout(random.randint(7000, 10000))

            html_data = await page.content()
            print(f"[+] Success! Page content retrieved: {len(html_data)} bytes.")

            if "Access Denied" in html_data:
                print("[!] Warning: Landed on Akamai block screen.")
            else:
                print("[+] Successfully parsed content through the firewall!")

        except Exception as e:
            print(f"[!] Execution failed: {e}")

        finally:
            if not api_received.is_set():
                print(f"[*] Waiting up to {SCRAPE_TIMEOUT}s for API response...")
                try:
                    await asyncio.wait_for(api_received.wait(), timeout=SCRAPE_TIMEOUT)
                except asyncio.TimeoutError:
                    print("[!] API response not received within timeout.")
            await browser.close()

    if not result["found"]:
        return None
    return result["data"]


if __name__ == "__main__":
    asyncio.run(scrape_air_india(481, "20260531"))
