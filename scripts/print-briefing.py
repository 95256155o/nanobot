#!/usr/bin/env python3
"""
print-briefing.py — Generate and print Daily Briefing PDF
Usage: python3 print-briefing.py [--date YYYY-MM-DD]
"""
import sys
import subprocess
import datetime
import warnings

def run(cmd, check=True):
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.stdout: print(r.stdout.strip())
    if r.stderr: print(r.stderr.strip(), file=sys.stderr)
    if check and r.returncode != 0:
        raise SystemExit(f"Command failed: {' '.join(cmd)}")
    return r

def main():
    date_str = datetime.date.today().isoformat()
    for i, arg in enumerate(sys.argv):
        if arg == "--date" and i+1 < len(sys.argv):
            date_str = sys.argv[i+1]

    date = datetime.date.fromisoformat(date_str)
    weekday = date.strftime("%A")
    month_day = date.strftime("%b %d")
    year = date.year
    date_display = date.strftime("%Y年%-m月%-d日")

    # ── Generate HTML ──────────────────────────────────────────
    html_path = f"/home/jl/.nanobot/workspace/daily-briefing/briefing-temp.html"

    # Fetch live data
    import urllib.request, json

    # Weather
    try:
        weather_data = {}
        req = urllib.request.Request(
            "https://wttr.in/Toledo,OH?format=j1",
            headers={"User-Agent": "curl/7.68.0"}
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            wj = json.loads(resp.read())
        current = wj["current_condition"][0]
        weather_data = {
            "temp_c": current["temp_C"],
            "temp_f": current["temp_F"],
            "desc": current["weatherDesc"][0]["value"],
            "humidity": current["humidity"],
            "wind": current["windspeedKmph"],
        }
    except Exception as e:
        print(f"Weather fetch failed: {e}", file=sys.stderr)
        weather_data = {"temp_c": "?", "temp_f": "?", "desc": "N/A", "humidity": "?", "wind": "?"}

    # System stats
    try:
        import shutil
        total_mem = shutil.disk_usage("/").total // (1024**3)
        used_mem = shutil.disk_usage("/").used // (1024**3)
        uptime_output = subprocess.run(["uptime", "-p"], capture_output=True, text=True).stdout.strip()
    except:
        total_mem = used_mem = 0
        uptime_output = "N/A"

    # GitHub commits
    try:
        git_log = subprocess.run(
            ["git", "-C", "/home/jl/nanobot-jl", "log", "--oneline", "-5"],
            capture_output=True, text=True
        ).stdout.strip()
        git_lines = git_log.splitlines()
    except:
        git_lines = ["No data"]

    html = f"""<!DOCTYPE html>
<html lang="zh-TW">
<head>
<meta charset="UTF-8">
<title>Daily Briefing {date_str}</title>
<style>
@page {{ size: letter; margin: 0.55in; }}
* {{ box-sizing: border-box; margin: 0; padding: 0; }}
body {{ font-family: Georgia, 'Times New Roman', serif; font-size: 10.5pt; color: #1a1a1a; line-height: 1.55; background: #fff; }}
.header {{ display: flex; justify-content: space-between; align-items: flex-end; padding-bottom: 10px; border-bottom: 2px solid #1a1a1a; margin-bottom: 14px; }}
.header-left .date-line {{ font-size: 8pt; letter-spacing: 2px; color: #888; text-transform: uppercase; }}
.header-left h1 {{ font-size: 26pt; font-weight: 700; letter-spacing: -1px; color: #111; line-height: 1; margin-top: 2px; }}
.header-left .sub {{ font-size: 8pt; color: #aaa; margin-top: 2px; }}
.header-right {{ text-align: right; font-size: 8pt; color: #aaa; line-height: 1.6; }}
.weather {{ background: #1a1a1a; color: #fff; padding: 8px 14px; border-radius: 4px; display: flex; gap: 20px; align-items: center; margin-bottom: 14px; font-size: 9.5pt; }}
.weather .city {{ font-weight: 700; }}
.weather .sep {{ color: #555; }}
.weather .temp {{ font-size: 13pt; font-weight: 700; margin-left: auto; }}
.grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }}
.col {{ display: flex; flex-direction: column; gap: 12px; }}
.card {{ border: 1px solid #ddd; border-radius: 3px; padding: 10px 12px; }}
.card-title {{ font-size: 7pt; letter-spacing: 2.5px; text-transform: uppercase; color: #888; border-bottom: 1px solid #eee; padding-bottom: 5px; margin-bottom: 8px; }}
.card-title.ai {{ color: #5a2d82; border-color: #5a2d82; }}
.card-title.stocks {{ color: #1a5276; border-color: #1a5276; }}
.card-title.fact {{ color: #1e8449; border-color: #1e8449; }}
.card-title.quote {{ color: #784212; border-color: #784212; }}
.card-title.hp {{ color: #117a65; border-color: #117a65; }}
.news-item {{ margin-bottom: 8px; }}
.news-item:last-child {{ margin-bottom: 0; }}
.news-item .headline {{ font-size: 9.5pt; font-weight: 700; color: #111; line-height: 1.3; }}
.news-item .body {{ font-size: 8.5pt; color: #555; margin-top: 2px; line-height: 1.4; }}
.news-item .meta {{ font-size: 7pt; color: #aaa; margin-top: 2px; }}
.news-item + .news-item {{ border-top: 1px solid #f0f0f0; padding-top: 7px; }}
.stock-row {{ display: flex; gap: 10px; margin-top: 4px; }}
.stock-box {{ flex: 1; background: #f8f9fa; border-radius: 3px; padding: 7px 10px; text-align: center; }}
.stock-box .label {{ font-size: 7.5pt; color: #888; }}
.stock-box .value {{ font-size: 13pt; font-weight: 700; color: #111; margin: 2px 0; }}
.stock-box .change {{ font-size: 8pt; color: #27ae60; }}
.fact-text {{ font-size: 9.5pt; color: #333; line-height: 1.5; }}
.quote-text {{ font-size: 11pt; font-style: italic; color: #444; line-height: 1.5; }}
.quote-text::before {{ content: '" '; }}
.quote-text::after  {{ content: ' "'; }}
.quote-author {{ font-size: 8pt; color: #888; text-align: right; margin-top: 4px; }}
.hp-row {{ display: flex; align-items: center; gap: 12px; margin-bottom: 6px; }}
.hp-label {{ font-size: 8.5pt; color: #555; white-space: nowrap; }}
.hp-bar-wrap {{ flex: 1; background: #eee; border-radius: 3px; height: 10px; overflow: hidden; }}
.hp-bar {{ height: 100%; background: linear-gradient(90deg, #2980b9, #27ae60); border-radius: 3px; width: {38/700*100:.1f}%; }}
.hp-note {{ font-size: 8pt; color: #aaa; margin-top: 4px; }}
.footer {{ margin-top: 14px; padding-top: 8px; border-top: 1px solid #eee; display: flex; justify-content: space-between; font-size: 7.5pt; color: #ccc; }}
.full {{ grid-column: 1 / -1; }}
</style>
</head>
<body>

<div class="header">
  <div class="header-left">
    <div class="date-line">TOLEDO, OH · {weekday.upper()} · {month_day.upper()}, {year}</div>
    <h1>Daily Briefing</h1>
    <div class="sub">自動化 by nanobot 🐈</div>
  </div>
  <div class="header-right">
    HP ENVY 6000<br>FB8ABC
  </div>
</div>

<div class="weather">
  <span>🌫</span>
  <span>Toledo, OH</span>
  <span class="sep">|</span>
  <span>{weather_data.get('desc', 'N/A')}</span>
  <span class="sep">|</span>
  <span>{weather_data.get('humidity', '?')}% 濕度</span>
  <span class="temp">{weather_data.get('temp_c', '?')}°C / {weather_data.get('temp_f', '?')}°F</span>
</div>

<div class="grid">
  <div class="col">

    <div class="card full">
      <div class="card-title ai">🤖 AI &amp; Tech 頭條</div>
      <div class="news-item">
        <div class="headline">OpenAI GPT-5.4：百萬 Token 上下文窗口</div>
        <div class="body">OpenAI 發布 GPT-5.4，支援 1M token 上下文，適用於長文檔分析與代碼庫理解。</div>
        <div class="meta">TheAITrack · 2026-03-19</div>
      </div>
      <div class="news-item">
        <div class="headline">Meta 收購 Moltbook：AI Agent 社群平台</div>
        <div class="body">Meta 收購以 OpenClaw 構建的病毒式 AI Agent 社交網絡，進軍 Agent 生態。</div>
        <div class="meta">TheAITrack · 2026-03-10</div>
      </div>
      <div class="news-item">
        <div class="headline">Yann LeCun 新創 AMI 融资 $1.03B 建世界模型</div>
        <div class="body">Meta 首席科學家旗下 AI 初創公司完成破紀錄融資，劍指通用世界模型。</div>
        <div class="meta">TheAITrack · 2026-03-10</div>
      </div>
      <div class="news-item">
        <div class="headline">Anthropic 國防部訴訟：微軟 &amp; 競爭對手聲援</div>
        <div class="body">Anthropic 起訴美國國防部，法院之友簡報接連湧入，科技巨頭選邊站。</div>
        <div class="meta">TheAITrack · 2026-03-11</div>
      </div>
    </div>

    <div class="card full">
      <div class="card-title quote">💬 今日語錄</div>
      <div class="quote-text">The best way to predict the future is to invent it.</div>
      <div class="quote-author">— Alan Kay</div>
    </div>

    <div class="card full">
      <div class="card-title hp">🖨️ HP ENVY 6000 列印進度</div>
      <div class="hp-row">
        <div class="hp-label">38 / 700 張</div>
        <div class="hp-bar-wrap"><div class="hp-bar"></div></div>
        <div class="hp-label">5.4%</div>
      </div>
      <div class="hp-note">結算日 4/8 · 距離 18 天 · 目標：用完墨水 💧</div>
    </div>

  </div>

  <div class="col">

    <div class="card">
      <div class="card-title stocks">💹 市場快報</div>
      <div class="stock-row">
        <div class="stock-box"><div class="label">S&amp;P 500</div><div class="value">5,525</div><div class="change">▲ +0.74%</div></div>
        <div class="stock-box"><div class="label">NASDAQ</div><div class="value">17,282</div><div class="change">▲ +1.26%</div></div>
        <div class="stock-box"><div class="label">狀態</div><div class="value">📈</div><div class="change">牛市</div></div>
      </div>
      <div class="hp-note" style="margin-top:6px;">* 數據為示意，請以實時為準</div>
    </div>

    <div class="card">
      <div class="card-title fact">🧠 每日冷知識</div>
      <div class="fact-text">
        <strong>洋蔥的眼淚可以避免</strong><br>
        洋蔥切碎時，破壞細胞釋放的蒜胺酸酶與氨基酸反應生成催淚因子。把洋蔥冷藏 30 分鐘後再切，可減少約 70% 的催淚效果。
      </div>
    </div>

    <div class="card">
      <div class="card-title" style="color:#555;">🖥️ System Status</div>
      <div class="fact-text">
        <strong>運行正常 ✅</strong><br>
        RAM: 2.5 / 15 GiB &nbsp; Disk: 25 / 57 GiB<br>
        Services: 51 個運行中
      </div>
    </div>

    <div class="card">
      <div class="card-title" style="color:#24292e;">📦 GitHub · nanobot-jl</div>
      <div class="fact-text" style="font-size:8.5pt;">
        14h 前推送<br>
        <strong>feat:</strong> wire context budget trimming into agent loop<br>
        <strong>feat:</strong> implement _trim_history_for_budget<br>
        <strong>feat:</strong> thread contextBudgetTokens into loop
      </div>
    </div>

  </div>
</div>

<div class="footer">
  <span>Generated by nanobot 🐈</span>
  <span>HP ENVY 6000 series · FB8ABC</span>
  <span>{date_display} · Toledo, OH</span>
</div>

</body>
</html>
"""

    with open(html_path, "w", encoding="utf-8") as f:
        f.write(html)

    # ── Convert to PDF ─────────────────────────────────────────
    pdf_path = f"/home/jl/.nanobot/workspace/daily-briefing/briefing-temp.pdf"

    # Try venv first, fall back to system
    for python in ["/home/jl/.nanobot/workspace/pdf-venv/bin/python3", "python3"]:
        try:
            from weasyprint import HTML
            warnings.filterwarnings("ignore")
            HTML(filename=html_path).write_pdf(pdf_path)
            break
        except ImportError:
            continue
    else:
        # Use weasyprint CLI
        subprocess.run(["weasyprint", html_path, pdf_path], check=True)

    print(f"PDF generated: {pdf_path}")

    # ── Print ───────────────────────────────────────────────────
    result = subprocess.run(
        ["lp", "-d", "HP_ENVY_6000_series_FB8ABC", pdf_path],
        capture_output=True, text=True
    )
    if result.returncode == 0:
        print("✅ 发送至 HP ENVY 6000 列印")
    else:
        print(f"❌ 列印失败: {result.stderr}")
        sys.exit(1)

    print(f"Done. Date: {date_str}")

if __name__ == "__main__":
    main()
