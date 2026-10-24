"""
Google Chrome CDP + Playwright bridge for Odysseus on Windows.
Connects to a persistent Google Chrome instance on 127.0.0.1:9222 so web_fetch
and builtin_browser can read JS-heavy and logged-in social pages (Facebook,
Threads, Instagram, X, TikTok, YouTube).
"""

import json
import logging
import os
import subprocess
import time
import urllib.request
from urllib.parse import urlsplit

logger = logging.getLogger(__name__)

CHROME_EXE = os.environ.get(
    "ODYSSEUS_BROWSER_EXECUTABLE",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
)
NODE_EXE = os.environ.get(
    "ODYSSEUS_NODE_EXECUTABLE",
    r"C:\Users\truon\AppData\Local\OpenAI\Codex\runtimes\cua_node\b2c4f0c90eadfac4\bin\node.exe",
)
NODE_MODULES = os.environ.get(
    "ODYSSEUS_NODE_MODULES",
    r"C:\Users\truon\AppData\Local\OpenAI\Codex\runtimes\cua_node\b2c4f0c90eadfac4\bin\node_modules",
)
CHROME_USER_DATA_DIR = os.environ.get(
    "ODYSSEUS_CHROME_USER_DATA_DIR",
    r"C:\Users\truon\odysseus-ai\data\local\chrome-user-data",
)
CDP_PORT = int(os.environ.get("ODYSSEUS_CHROME_CDP_PORT", "9222"))
CDP_URL = f"http://127.0.0.1:{CDP_PORT}"

CHROME_PRIORITY_DOMAINS = (
    "facebook.com",
    "fb.com",
    "threads.com",
    "threads.net",
    "instagram.com",
    "x.com",
    "twitter.com",
    "tiktok.com",
    "reddit.com",
)


def is_chrome_priority_url(url: str) -> bool:
    try:
        host = (urlsplit(url).hostname or "").lower()
    except Exception:
        return False
    return any(host == d or host.endswith("." + d) for d in CHROME_PRIORITY_DOMAINS)


def _cdp_alive() -> bool:
    try:
        with urllib.request.urlopen(f"{CDP_URL}/json/version", timeout=1.5) as resp:
            return resp.status == 200
    except Exception:
        return False


def ensure_chrome_cdp() -> bool:
    """Ensure Google Chrome is running with --remote-debugging-port=9222."""
    if _cdp_alive():
        return True
    if not os.path.isfile(CHROME_EXE):
        logger.warning(f"Chrome executable not found at {CHROME_EXE}")
        return False
    os.makedirs(CHROME_USER_DATA_DIR, exist_ok=True)
    creationflags = 0
    if os.name == "nt":
        creationflags = (
            getattr(subprocess, "DETACHED_PROCESS", 0x00000008)
            | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)
            | getattr(subprocess, "CREATE_BREAKAWAY_FROM_JOB", 0x01000000)
        )
    cmd = [
        CHROME_EXE,
        f"--remote-debugging-port={CDP_PORT}",
        f"--user-data-dir={CHROME_USER_DATA_DIR}",
        "--no-first-run",
        "--no-default-browser-check",
        "https://www.facebook.com/",
        "https://www.threads.com/",
        "https://www.instagram.com/?hl=en",
    ]
    try:
        subprocess.Popen(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=creationflags,
            close_fds=True,
        )
    except Exception:
        # Fallback without CREATE_BREAKAWAY_FROM_JOB if job prohibits breakaway
        try:
            subprocess.Popen(
                cmd,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=(
                    getattr(subprocess, "DETACHED_PROCESS", 0x00000008)
                    | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)
                ),
                close_fds=True,
            )
        except Exception as e:
            logger.warning(f"Failed to launch Chrome CDP: {e}")
            return False

    for _ in range(25):
        if _cdp_alive():
            return True
        time.sleep(0.2)
    return _cdp_alive()


_PLAYWRIGHT_SCRIPT = r"""
const { chromium } = require('playwright');

(async () => {
  const payload = JSON.parse(process.argv[1] || '{}');
  const action = payload.action || 'fetch';
  const cdpUrl = payload.cdpUrl || 'http://127.0.0.1:9222';
  const targetUrl = payload.url || '';
  const timeoutMs = (payload.timeoutSec || 20) * 1000;

  const browser = await chromium.connectOverCDP(cdpUrl);
  const ctx = browser.contexts()[0] || (await browser.newContext());

  const normHost = (u) => {
    try { return new URL(u).hostname.replace(/^www\./, '').toLowerCase(); } catch { return ''; }
  };
  const normPath = (u) => {
    try { return new URL(u).pathname.replace(/\/+$/, '') || '/'; } catch { return '/'; }
  };

  if (action === 'tabs') {
    const tabs = await Promise.all(ctx.pages().map(async (p, idx) => ({
      index: idx,
      url: p.url(),
      title: await p.title().catch(() => ''),
    })));
    console.log(JSON.stringify({ ok: true, tabs }));
    process.exit(0);
  }

  let page = null;
  let reused = false;
  if (targetUrl) {
    const th = normHost(targetUrl);
    const tp = normPath(targetUrl);
    for (const p of ctx.pages()) {
      const pu = p.url();
      if (pu === targetUrl || (th && normHost(pu) === th && normPath(pu) === tp)) {
        page = p;
        reused = true;
        break;
      }
    }
  }

  if (!page) {
    if (action === 'snapshot' && ctx.pages().length > 0 && !targetUrl) {
      page = ctx.pages()[ctx.pages().length - 1];
      reused = true;
    } else {
      page = await ctx.newPage();
    }
  }

  if (targetUrl && (!reused || payload.reload)) {
    await page.goto(targetUrl, { waitUntil: 'domcontentloaded', timeout: timeoutMs }).catch(() => {});
    await page.waitForTimeout(2500);
  } else if (reused) {
    await page.bringToFront().catch(() => {});
    await page.waitForTimeout(1200);
  }

  if (action === 'click' && payload.selector) {
    await page.locator(payload.selector).first().click({ timeout: 8000 });
    await page.waitForTimeout(1500);
  } else if (action === 'type' && payload.selector) {
    await page.locator(payload.selector).first().fill(payload.text || '', { timeout: 8000 });
    if (payload.submit) {
      await page.keyboard.press('Enter');
    }
    await page.waitForTimeout(1500);
  } else {
    // Scroll slightly to trigger lazy feed rendering on Facebook/Threads/Instagram
    await page.mouse.wheel(0, 700).catch(() => {});
    await page.waitForTimeout(1000);
  }

  const title = await page.title().catch(() => '');
  const finalUrl = page.url();
  const text = await page.evaluate(() => (document.body ? document.body.innerText : '')).catch(() => '');
  const html = await page.content().catch(() => '');
  const links = await page.evaluate(() => {
    const out = [];
    const seen = new Set();
    for (const a of Array.from(document.querySelectorAll('a[href]'))) {
      const href = a.href || '';
      const label = (a.innerText || a.getAttribute('aria-label') || '').replace(/\s+/g, ' ').trim();
      if (!href.startsWith('http') || !label || seen.has(href)) continue;
      seen.add(href);
      out.push({ title: label.slice(0, 140), url: href });
      if (out.length >= 40) break;
    }
    return out;
  }).catch(() => []);

  // Close temporary tab only if we opened a new one and there are many tabs open
  if (!reused && ctx.pages().length > 6) {
    await page.close().catch(() => {});
  }

  console.log(JSON.stringify({
    ok: true,
    url: finalUrl || targetUrl,
    title,
    text,
    html,
    links,
    reused,
  }));
  process.exit(0);
})().catch((err) => {
  console.log(JSON.stringify({ ok: false, error: String(err && err.message || err) }));
  process.exit(1);
});
"""


def run_chrome_action(payload: dict, timeout_sec: int = 25) -> dict:
    """Execute a Playwright action against the persistent Google Chrome instance."""
    if not os.path.isfile(NODE_EXE):
        return {"ok": False, "error": f"Node executable not found at {NODE_EXE}"}
    if not ensure_chrome_cdp():
        return {"ok": False, "error": "Google Chrome CDP (port 9222) could not be started"}

    req = dict(payload)
    req.setdefault("cdpUrl", CDP_URL)
    req.setdefault("timeoutSec", timeout_sec)

    env = os.environ.copy()
    env["NODE_PATH"] = NODE_MODULES

    try:
        proc = subprocess.run(
            [NODE_EXE, "-e", _PLAYWRIGHT_SCRIPT, json.dumps(req)],
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_sec + 10,
        )
        stdout = (proc.stdout or "").strip()
        for line in reversed(stdout.splitlines()):
            line = line.strip()
            if line.startswith("{") and line.endswith("}"):
                return json.loads(line)
        return {
            "ok": False,
            "error": (proc.stderr or stdout or f"Chrome bridge exited with {proc.returncode}")[:500],
        }
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


_LOGIN_WALL_MARKERS = (
    "đăng nhập vào facebook",
    "log in to facebook",
    "create new account",
    "tạo tài khoản mới",
    "quên mật khẩu?",
    "forgot password?",
    "sign in to x",
    "log in to instagram",
    "đăng nhập instagram",
)

_VN_OPEN_NEWS_SITES = (
    "https://dantri.com.vn",
    "https://vnexpress.net",
    "https://tuoitre.vn",
)


def _is_login_wall_text(text: str, url: str = "") -> bool:
    low = (text or "").lower()
    if not low:
        return False
    hits = sum(1 for m in _LOGIN_WALL_MARKERS if m in low)
    if hits >= 2 and len(low) < 2500:
        return True
    if ("facebook.com" in (url or "").lower() or "fb.com" in (url or "").lower()) and (
        "đăng nhập vào facebook" in low or "log in to facebook" in low
    ) and len(low) < 1500:
        return True
    return False


def _fallback_open_news_via_agent_reach(original_url: str, timeout_sec: int = 20) -> dict | None:
    """When a social page hits a login wall, leave the login screen alone and
    automatically fetch latest Vietnamese open news via agent-reach (Jina/Exa)."""
    ar_exe = os.path.join(os.path.expanduser("~"), ".local", "bin", "agent-reach.exe" if os.name == "nt" else "agent-reach")
    if not os.path.isfile(ar_exe):
        import shutil
        ar_exe = shutil.which("agent-reach") or ""
    if not ar_exe:
        return None

    sections = [
        f"[Auto-Fallback: {original_url} requires login. Left login screen untouched and fetched open Vietnamese news from Dân Trí (dantri.com.vn) via Agent Reach]",
    ]
    links_out = []
    try:
        proc = subprocess.run(
            [ar_exe, "web", "search", "site:dantri.com.vn OR site:vnexpress.net tin tức sự kiện nóng mới nhất", "--limit", "5"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=min(20, max(10, timeout_sec)),
        )
        for line in reversed((proc.stdout or "").strip().splitlines()):
            line = line.strip()
            if line.startswith("{") and line.endswith("}"):
                data = json.loads(line)
                if data.get("ok") and isinstance(data.get("items"), list):
                    for item in data["items"]:
                        t = str(item.get("title") or "").strip()
                        u = str(item.get("url") or "").strip()
                        txt = str(item.get("text") or "").strip()
                        if t and u:
                            links_out.append({"title": t, "url": u})
                        if txt:
                            sections.append(f"## {t} ({u})\n{txt[:3500]}")
                break
    except Exception as e:
        logger.warning(f"agent-reach fallback search failed: {e}")

    if len(sections) <= 1:
        try:
            proc2 = subprocess.run(
                [ar_exe, "web", "read", "https://dantri.com.vn/su-kien.htm"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=min(20, max(10, timeout_sec)),
            )
            for line in reversed((proc2.stdout or "").strip().splitlines()):
                line = line.strip()
                if line.startswith("{") and line.endswith("}"):
                    data = json.loads(line)
                    if data.get("ok") and data.get("items"):
                        item = data["items"][0]
                        sections.append(str(item.get("text") or "")[:6000])
                    break
        except Exception:
            pass

    if len(sections) <= 1:
        return None

    combined = "\n\n".join(sections)
    return {
        "ok": True,
        "url": "https://dantri.com.vn/su-kien.htm",
        "title": "Báo Dân Trí & Báo chí Việt Nam (Tự động cập nhật thay thế trang yêu cầu đăng nhập)",
        "text": combined,
        "html": "",
        "links": links_out,
        "reused": True,
    }


def fetch_page_with_chrome(url: str, timeout_sec: int = 20) -> dict:
    """Fetch rendered page content using Google Chrome via CDP, with automatic
    zero-login fallback to Vietnamese news portals when a login wall is encountered."""
    res = run_chrome_action({"action": "fetch", "url": url}, timeout_sec=timeout_sec)
    if isinstance(res, dict) and res.get("ok"):
        if _is_login_wall_text(str(res.get("text") or ""), str(res.get("url") or url)):
            fallback = _fallback_open_news_via_agent_reach(url, timeout_sec=timeout_sec)
            if fallback:
                return fallback
    return res


def build_webpage_content_result(url: str, timeout: int = 20) -> dict | None:
    """Return a fetch_webpage_content-compatible dict rendered via Google Chrome."""
    res = fetch_page_with_chrome(url, timeout_sec=max(15, int(timeout or 15)))
    if not isinstance(res, dict) or not res.get("ok"):
        return None
    text = str(res.get("text") or "").strip()
    html = str(res.get("html") or "")
    title = str(res.get("title") or "").strip()
    links = res.get("links") if isinstance(res.get("links"), list) else []
    if not text:
        return None
    linked_lines = [text]
    if links:
        linked_lines.append("\n### Links on page (Chrome):")
        for item in links[:25]:
            if isinstance(item, dict) and item.get("title") and item.get("url"):
                linked_lines.append(f"- [{item['title']}](<{item['url']}>)")
    return {
        "url": str(res.get("url") or url),
        "title": title or url,
        "content": text,
        "linked_content": "\n".join(linked_lines),
        "page_entries": links[:25],
        "lists": [],
        "tables": [],
        "code_blocks": [],
        "meta_description": "",
        "meta_keywords": "",
        "og_image": "",
        "js_rendered": True,
        "js_message": "Rendered via Google Chrome (CDP port 9222).",
        "success": True,
        "error": "",
        "truncated": False,
        "fetched_bytes": len((html or text).encode("utf-8", errors="ignore")),
        "total_bytes": None,
    }
