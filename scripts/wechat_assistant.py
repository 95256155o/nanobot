#!/usr/bin/env python3
"""
WeChat Friend Assistant - Discord Bot
Usage: python3 wechat_assistant.py

Requires DISCORD_BOT_TOKEN environment variable.
"""

import discord
from discord.ext import commands
import sqlite3
import re
import random
import asyncio
import csv
import io
from datetime import datetime
from pathlib import Path

# Configuration
DB_PATH = Path("/home/jl/.nanobot/workspace/wechat/wechat_friends.db")
BOT_PREFIX = "!"

# Database setup
def init_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    
    # Create tables
    c.executescript('''
        CREATE TABLE IF NOT EXISTS categories (
            id INTEGER PRIMARY KEY,
            name TEXT UNIQUE NOT NULL,
            description TEXT,
            color TEXT DEFAULT '#808080'
        );
        
        CREATE TABLE IF NOT EXISTS friends (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            wechat_id TEXT,
            nickname TEXT,
            remark TEXT,
            source TEXT,
            category_id INTEGER REFERENCES categories(id),
            decision TEXT DEFAULT 'undecided',
            decision_notes TEXT,
            first_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            decided_at TIMESTAMP
        );
        
        CREATE TABLE IF NOT EXISTS decision_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            friend_id INTEGER,
            action TEXT,
            old_value TEXT,
            new_value TEXT,
            logged_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        
        CREATE TABLE IF NOT EXISTS session_stats (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_date DATE UNIQUE,
            processed_count INTEGER DEFAULT 0,
            kept_count INTEGER DEFAULT 0,
            deleted_count INTEGER DEFAULT 0,
            skipped_count INTEGER DEFAULT 0
        );
        
        -- Insert default categories if empty
        INSERT OR IGNORE INTO categories (name, description, color) VALUES
            ('家人', 'Family', '#FF6B6B'),
            ('摯友', 'Close friends', '#4ECDC4'),
            ('同事', 'Work', '#45B7D1'),
            ('客戶', 'Clients', '#96CEB4'),
            ('同學', 'School/Uni', '#FFEAA7'),
            ('業務', 'Business', '#DDA0DD'),
            ('陌生人', 'Barely know', '#C0C0C0'),
            ('待處理', 'Needs review', '#A0A0A0');
    ''')
    conn.commit()
    return conn

# Bot setup
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix=BOT_PREFIX, intents=intents)
db_conn = None

@bot.event
async def on_ready():
    global db_conn
    db_conn = init_db()
    print(f"✅ WeChat Assistant logged in as {bot.user}")
    print(f"📁 Database: {DB_PATH}")

@bot.event
async def on_message(message):
    # Ignore bot messages
    if message.author.bot:
        return
    # Process commands
    await bot.process_commands(message)

def parse_friend_info(text):
    """Parse friend info from natural language input"""
    info = {
        'nickname': None,
        'wechat_id': None,
        'remark': None,
        'source': None
    }
    
    # Patterns to extract various fields
    patterns = {
        'wechat_id': r'(?:wxid|微信號?|微信号)[:\s]*(\w+)',
        'remark': r'(?:備注?|备注|remark)[:\s]*(.+?)(?=\s+(?:来自|wxid|微|$))',
        'source': r'來自?[:\s]*(.+?)(?:\s+備注|$)',
    }
    
    # Extract WeChat ID
    match = re.search(patterns['wechat_id'], text, re.IGNORECASE)
    if match:
        info['wechat_id'] = match.group(1)
    
    # Extract remark
    match = re.search(patterns['remark'], text)
    if match:
        info['remark'] = match.group(1).strip()
    
    # Extract source
    match = re.search(patterns['source'], text)
    if match:
        info['source'] = match.group(1).strip()
    
    # Nickname is everything before any special keywords
    nickname = re.split(r'\s+(?:wxid|備注|备注|来自|remark|微信)', text)[0].strip()
    nickname = re.sub(r'^[!#]*(?:friend|朋友)', '', nickname, flags=re.IGNORECASE).strip()
    if nickname:
        info['nickname'] = nickname
    
    return info

def analyze_friend(info):
    """Generate analysis based on available info"""
    suggestions = []
    
    nickname = info.get('nickname', '') or ''
    remark = info.get('remark', '') or ''
    source = info.get('source', '') or ''
    wechat_id = info.get('wechat_id', '') or ''
    
    combined = f"{nickname} {remark} {source}".lower()
    
    # Category suggestions based on keywords
    if any(kw in combined for kw in ['家人', '爸', '媽', '哥', '姐', '弟', '妹', 'uncle', 'aunt', '姨媽', '舅舅']):
        suggestions.append(("家人", "high"))
    elif any(kw in combined for kw in ['同學', '同学', '大學', '大学', '高中', '國中', '國小', '小學', 'college', 'university']):
        suggestions.append(("同學", "medium"))
    elif any(kw in combined for kw in ['同事', '公司', '老板', '上司', '下屬', '同事', 'boss', 'coworker']):
        suggestions.append(("同事", "medium"))
    elif any(kw in combined for kw in ['客戶', '客户', '合作', 'client']):
        suggestions.append(("客戶", "medium"))
    elif any(kw in combined for kw in ['業務', '业务', 'sales', 'bd']):
        suggestions.append(("業務", "medium"))
    elif '刪' in remark or '刪除' in combined:
        suggestions.append(("陌生人", "high"))
    
    # ID pattern analysis
    if wechat_id:
        if wechat_id.startswith('wxid_'):
            suggestions.append(("个人账号", "info"))
        elif any(c.isdigit() for c in wechat_id) and len(wechat_id) > 15:
            suggestions.append(("可能是业务号", "medium"))
    
    # Ask for more info if no suggestions
    if len(suggestions) == 0:
        suggestions.append(("待處理", "tip: 请提供更多信息如备注或来源群"))
    
    return suggestions

@bot.command(name='friend', help='Add a new friend: !friend 名字 备注:xxx 来自:xxx')
async def add_friend(ctx, *, text: str = ""):
    await asyncio.sleep(random.uniform(0.3, 1.0))  # Human-like delay
    
    if not text.strip():
        await ctx.send("📝 用法: `!friend 名字` 或 `!friend 名字 备注:xxx 来自:xxx`")
        return
    
    info = parse_friend_info(text)
    
    if not info['nickname']:
        await ctx.send("❓ 請提供朋友的名字")
        return
    
    # Check if already exists
    c = db_conn.cursor()
    c.execute("SELECT id, decision FROM friends WHERE nickname = ? OR remark = ?", 
              (info['nickname'], info.get('remark', '')))
    existing = c.fetchone()
    
    if existing:
        await ctx.send(f"⚠️ **{info['nickname']}** 已在資料庫中 (決策: {existing[1]})")
        return
    
    # Insert new friend
    c.execute('''
        INSERT INTO friends (nickname, wechat_id, remark, source, decision)
        VALUES (?, ?, ?, ?, 'undecided')
    ''', (info['nickname'], info.get('wechat_id'), info.get('remark'), info.get('source')))
    db_conn.commit()
    
    friend_id = c.lastrowid
    
    # Log action
    c.execute("INSERT INTO decision_log (friend_id, action, new_value) VALUES (?, 'added', ?)",
              (friend_id, info['nickname']))
    db_conn.commit()
    
    # Update session stats
    today = datetime.now().date().isoformat()
    c.execute('''INSERT INTO session_stats (session_date, processed_count) 
                 VALUES (?, 1) ON CONFLICT(session_date) 
                 DO UPDATE SET processed_count = processed_count + 1''',
              (today,))
    db_conn.commit()
    
    # Generate analysis
    suggestions = analyze_friend(info)
    
    # Build response
    response = f"""📋 **新朋友**: {info['nickname']}
━━━━━━━━━━━━━━━━━━━━━━
👤 暱稱: {info['nickname'] or '未知'}
🔖 備注: {info['remark'] or '未設置'}
🆔 微信號: {info.get('wechat_id') or '未知'}"""

    if info.get('source'):
        response += f"\n📍 來自: {info['source']}"
    
    response += f"""
━━━━━━━━━━━━━━━━━━━━━━
💡 **分析**:
"""
    
    for suggestion, conf in suggestions:
        emoji = "🎯" if conf == "high" else "💭" if conf == "medium" else "💡"
        response += f"{emoji} 可能分类: **{suggestion}**\n"
    
    response += f"""━━━━━━━━━━━━━━━━━━━━━━
📝 **操作**:
`!cat {info['nickname']} <分類>` - 設置分類
`!keep {info['nickname']}` - 標記保留
`!delete {info['nickname']}` - 標記刪除
`!skip {info['nickname']} <原因>` - 跳過"""
    
    await ctx.send(response)

@bot.command(name='cat', help='Categorize friend: !cat 名字 分類')
async def categorize(ctx, name: str, *, category: str = ""):
    await asyncio.sleep(random.uniform(0.2, 0.8))
    
    if not category:
        # List available categories
        c = db_conn.cursor()
        c.execute("SELECT name, description FROM categories")
        cats = c.fetchall()
        response = "📂 **可用分類**:\n"
        response += "家人 | 摯友 | 同事 | 客戶 | 同學 | 業務 | 陌生人 | 待處理\n"
        response += "━━━━━━━━━━━━━━━\n"
        for cat, desc in cats:
            response += f"• {cat} - {desc}\n"
        await ctx.send(response)
        return
    
    # Map common aliases
    aliases = {
        '家人': '家人', 'family': '家人',
        '朋友': '摯友', 'friend': '摯友', 'friends': '摯友',
        '同事': '同事', 'coworker': '同事', 'work': '同事',
        '客户': '客戶', 'client': '客戶', '客户': '客戶',
        '同学': '同學', '同學': '同學', 'school': '同學',
        '业务': '業務', 'business': '業務',
        '陌生': '陌生人', '陌生人': '陌生人',
        '待处理': '待處理', '待處理': '待處理',
    }
    
    category = aliases.get(category.lower(), category)
    
    c = db_conn.cursor()
    c.execute("SELECT id FROM categories WHERE name = ?", (category,))
    cat_row = c.fetchone()
    
    if not cat_row:
        await ctx.send(f"❌ 未知分類: {category}，使用 `!cat {name}` 查看可用分類")
        return
    
    c.execute("SELECT id FROM friends WHERE nickname LIKE ?", (f"%{name}%",))
    friend = c.fetchone()
    
    if not friend:
        await ctx.send(f"❌ 找不到朋友: {name}")
        return
    
    c.execute("UPDATE friends SET category_id = ? WHERE id = ?", (cat_row[0], friend[0]))
    c.execute("INSERT INTO decision_log (friend_id, action, new_value) VALUES (?, 'categorized', ?)",
              (friend[0], category))
    db_conn.commit()
    
    await ctx.send(f"✅ **{name}** 已分類為 **{category}**")

@bot.command(name='keep', help='Mark friend for keeping: !keep 名字')
async def keep_friend(ctx, *, name: str):
    await asyncio.sleep(random.uniform(0.2, 0.8))
    
    c = db_conn.cursor()
    c.execute("SELECT id, nickname FROM friends WHERE nickname LIKE ?", (f"%{name}%",))
    friend = c.fetchone()
    
    if not friend:
        await ctx.send(f"❌ 找不到朋友: {name}")
        return
    
    c.execute("UPDATE friends SET decision = 'keep', decided_at = CURRENT_TIMESTAMP WHERE id = ?",
              (friend[0],))
    c.execute("INSERT INTO decision_log (friend_id, action, new_value) VALUES (?, 'decided', 'keep')",
              (friend[0],))
    
    today = datetime.now().date().isoformat()
    c.execute('''INSERT INTO session_stats (session_date, kept_count) 
                 VALUES (?, 1) ON CONFLICT(session_date) 
                 DO UPDATE SET kept_count = kept_count + 1''', (today,))
    db_conn.commit()
    
    await ctx.send(f"✅ **{friend[1]}** 已標記為 **保留**")

@bot.command(name='delete', help='Mark friend for deletion: !delete 名字')
async def delete_friend(ctx, *, name: str):
    await asyncio.sleep(random.uniform(0.2, 0.8))
    
    c = db_conn.cursor()
    c.execute("SELECT id, nickname FROM friends WHERE nickname LIKE ?", (f"%{name}%",))
    friend = c.fetchone()
    
    if not friend:
        await ctx.send(f"❌ 找不到朋友: {name}")
        return
    
    # Confirmation required for delete
    await ctx.send(f"""⚠️ **確認刪除**: {friend[1]}
━━━━━━━━━━━━━━━━━━━━━━
此操作將標記 **{friend[1]}** 為刪除候選。
在 WeChat 中手動刪除後，輸入 `!confirm {name}` 確認。""")

@bot.command(name='confirm', help='Confirm deletion: !confirm 名字')
async def confirm_delete(ctx, *, name: str):
    await asyncio.sleep(random.uniform(0.2, 0.8))
    
    c = db_conn.cursor()
    c.execute("SELECT id, nickname FROM friends WHERE nickname LIKE ?", (f"%{name}%",))
    friend = c.fetchone()
    
    if not friend:
        await ctx.send(f"❌ 找不到朋友: {name}")
        return
    
    c.execute("UPDATE friends SET decision = 'delete', decided_at = CURRENT_TIMESTAMP WHERE id = ?",
              (friend[0],))
    c.execute("INSERT INTO decision_log (friend_id, action, new_value) VALUES (?, 'confirmed_delete', 'delete')",
              (friend[0],))
    
    today = datetime.now().date().isoformat()
    c.execute('''INSERT INTO session_stats (session_date, deleted_count) 
                 VALUES (?, 1) ON CONFLICT(session_date) 
                 DO UPDATE SET deleted_count = deleted_count + 1''', (today,))
    db_conn.commit()
    
    await ctx.send(f"🗑️ **{friend[1]}** 已確認 **刪除**")

@bot.command(name='skip', help='Skip friend: !skip 名字 原因')
async def skip_friend(ctx, name: str, *, reason: str = ""):
    await asyncio.sleep(random.uniform(0.2, 0.8))
    
    c = db_conn.cursor()
    c.execute("SELECT id, nickname FROM friends WHERE nickname LIKE ?", (f"%{name}%",))
    friend = c.fetchone()
    
    if not friend:
        await ctx.send(f"❌ 找不到朋友: {name}")
        return
    
    c.execute("UPDATE friends SET decision = 'skip', decision_notes = ?, decided_at = CURRENT_TIMESTAMP WHERE id = ?",
              (reason, friend[0]))
    c.execute("INSERT INTO decision_log (friend_id, action, new_value) VALUES (?, 'skipped', ?)",
              (friend[0], reason))
    
    today = datetime.now().date().isoformat()
    c.execute('''INSERT INTO session_stats (session_date, skipped_count) 
                 VALUES (?, 1) ON CONFLICT(session_date) 
                 DO UPDATE SET skipped_count = skipped_count + 1''', (today,))
    db_conn.commit()
    
    reason_text = f"（原因: {reason}）" if reason else ""
    await ctx.send(f"⏭️ **{friend[1]}** 已跳過 {reason_text}")

@bot.command(name='status', help='Show session status')
async def status(ctx):
    await asyncio.sleep(random.uniform(0.1, 0.5))
    
    c = db_conn.cursor()
    today = datetime.now().date().isoformat()
    
    c.execute('''SELECT processed_count, kept_count, deleted_count, skipped_count 
                 FROM session_stats WHERE session_date = ?''', (today,))
    today_stats = c.fetchone()
    
    c.execute('''SELECT decision, COUNT(*) FROM friends GROUP BY decision''')
    total_stats = c.fetchall()
    
    total_processed = sum(row[1] for row in total_stats if row[0] != 'undecided')
    total_kept = sum(row[1] for row in total_stats if row[0] == 'keep')
    total_deleted = sum(row[1] for row in total_stats if row[0] == 'delete')
    total_undecided = sum(row[1] for row in total_stats if row[0] == 'undecided')
    
    # Count remaining (rough estimate of 1400 - processed)
    total_in_db = sum(row[1] for row in total_stats)
    remaining = max(0, 1400 - total_in_db)  # Assuming ~1400 total friends
    
    response = f"""📊 **今日進度** ({today})
━━━━━━━━━━━━━━━━━━━━━━
處理: **{today_stats[0] if today_stats else 0}** 人
✅ 保留: {today_stats[1] if today_stats else 0}
🗑️ 刪除: {today_stats[2] if today_stats else 0}
⏭️ 跳過: {today_stats[3] if today_stats else 0}

📈 **總計**
資料庫中: {total_in_db}
已處理: {total_processed}
待處理: {total_undecided}
保留/刪除: {total_kept}/{total_deleted}

📝 **估計**
約剩: {remaining} 位（基於 ~1400 總數）"""
    
    await ctx.send(response)

@bot.command(name='stats', help='Show overall statistics')
async def stats(ctx):
    await asyncio.sleep(random.uniform(0.1, 0.5))
    
    c = db_conn.cursor()
    c.execute('''SELECT c.name, COUNT(f.id) 
                 FROM categories c LEFT JOIN friends f ON c.id = f.category_id 
                 GROUP BY c.id ORDER BY COUNT(f.id) DESC''')
    by_category = c.fetchall()
    
    c.execute('''SELECT decision, COUNT(*) FROM friends GROUP BY decision''')
    by_decision = c.fetchall()
    
    total = sum(row[1] for row in by_decision)
    
    response = f"""📈 **統計概覽**
━━━━━━━━━━━━━━━━━━━━━━
總朋友數: **{total}**
━━━━━━━━━━━━━━━━━━━━━━

**按分類**:
"""
    for cat, count in by_category:
        pct = (count/total*100) if total > 0 else 0
        bar = "█" * int(pct/5) + "░" * (20 - int(pct/5))
        response += f"{cat}: {count:3d} ({pct:5.1f}%) {bar}\n"
    
    response += "\n**按決策**:\n"
    for decision, count in by_decision:
        pct = (count/total*100) if total > 0 else 0
        emoji = "✅" if decision == "keep" else "🗑️" if decision == "delete" else "⏭️" if decision == "skip" else "❓"
        response += f"{emoji} {decision}: {count}\n"
    
    await ctx.send(response)

@bot.command(name='review', help='Show undecided friends')
async def review(ctx, limit: int = 10):
    await asyncio.sleep(random.uniform(0.1, 0.5))
    
    c = db_conn.cursor()
    c.execute('''SELECT nickname, remark, source FROM friends 
                 WHERE decision = 'undecided' ORDER BY first_seen DESC LIMIT ?''', (limit,))
    undecided = c.fetchall()
    
    if not undecided:
        await ctx.send("✅ 所有朋友都已處理完畢！")
        return
    
    response = f"❓ **待處理** ({limit} 位):\n━━━━━━━━━━━━━━━━━━━━━━\n"
    for nickname, remark, source in undecided:
        info = f"🔖 {remark}" if remark else ""
        src = f"📍 {source}" if source else ""
        response += f"• {nickname} {info} {src}\n"
    
    await ctx.send(response)

@bot.command(name='list', help='List friends in category: !list 分類')
async def list_category(ctx, *, category: str = ""):
    await asyncio.sleep(random.uniform(0.1, 0.5))
    
    c = db_conn.cursor()
    
    if not category:
        c.execute("SELECT name FROM categories")
        cats = [row[0] for row in c.fetchall()]
        await ctx.send(f"📂 可用分類: {', '.join(cats)}\n用法: `!list 分類名`")
        return
    
    # Map aliases
    aliases = {
        '家人': '家人', 'family': '家人',
        '朋友': '摯友', 'friend': '摯友',
        '同事': '同事', 'work': '同事',
        '客户': '客戶', 'client': '客戶',
        '同学': '同學', 'school': '同學',
        '业务': '業務', 'business': '業務',
        '陌生': '陌生人',
        '待处理': '待處理',
    }
    category = aliases.get(category.lower(), category)
    
    c.execute("SELECT id FROM categories WHERE name = ?", (category,))
    cat_row = c.fetchone()
    
    if not cat_row:
        await ctx.send(f"❌ 未知分類: {category}")
        return
    
    c.execute('''SELECT f.nickname, f.remark, f.decision FROM friends f 
                 WHERE f.category_id = ? ORDER BY f.nickname LIMIT 50''', (cat_row[0],))
    friends = c.fetchall()
    
    if not friends:
        await ctx.send(f"📂 **{category}** 分類中暫無朋友")
        return
    
    response = f"📂 **{category}** ({len(friends)} 位):\n━━━━━━━━━━━━━━━━━━━━━━\n"
    for nickname, remark, decision in friends:
        status = "✅" if decision == "keep" else "🗑️" if decision == "delete" else ""
        info = f" ({remark})" if remark else ""
        response += f"• {nickname}{info} {status}\n"
    
    await ctx.send(response)

@bot.command(name='export', help='Export results to CSV')
async def export(ctx):
    await asyncio.sleep(random.uniform(0.2, 0.8))
    
    c = db_conn.cursor()
    c.execute('''SELECT f.nickname, f.wechat_id, f.remark, f.source, c.name as category, f.decision, f.decision_notes
                 FROM friends f LEFT JOIN categories c ON f.category_id = c.id
                 ORDER BY f.decision, f.nickname''')
    rows = c.fetchall()
    
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['Nickname', 'WeChat ID', 'Remark', 'Source', 'Category', 'Decision', 'Notes'])
    writer.writerows(rows)
    
    csv_content = output.getvalue()
    output.close()
    
    # Save to file
    export_path = DB_PATH.parent / f"wechat_friends_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    export_path.write_text(csv_content)
    
    await ctx.send(f"📤 已匯出 **{len(rows)}** 位朋友至 `{export_path.name}`")
    
    # Also send as attachment
    await ctx.send(file=discord.File(export_path))

@bot.command(name='undo', help='Undo last decision')
async def undo(ctx):
    await asyncio.sleep(random.uniform(0.2, 0.8))
    
    c = db_conn.cursor()
    c.execute('''SELECT id, friend_id, action, old_value, new_value FROM decision_log 
                 ORDER BY id DESC LIMIT 1''')
    last = c.fetchone()
    
    if not last:
        await ctx.send("❌ 沒有可撤銷的操作")
        return
    
    log_id, friend_id, action, old_val, new_val = last
    
    # Get friend name for response
    c.execute("SELECT nickname FROM friends WHERE id = ?", (friend_id,))
    friend_name = c.fetchone()[0] if c.fetchone() else "Unknown"
    
    # Revert based on action type
    if action in ['decided', 'confirmed_delete']:
        c.execute("UPDATE friends SET decision = 'undecided' WHERE id = ?", (friend_id,))
    elif action == 'categorized':
        c.execute("UPDATE friends SET category_id = NULL WHERE id = ?", (friend_id,))
    elif action == 'skipped':
        c.execute("UPDATE friends SET decision = 'undecided', decision_notes = NULL WHERE id = ?", (friend_id,))
    
    c.execute("DELETE FROM decision_log WHERE id = ?", (log_id,))
    db_conn.commit()
    
    await ctx.send(f"↩️ 已撤銷 **{friend_name}** 的 **{action}** 操作")

@bot.command(name='help', help='Show help')
async def help_cmd(ctx):
    response = """🤖 **WeChat 好友助手** - 命令列表
━━━━━━━━━━━━━━━━━━━━━━
**新增朋友**:
`!friend 名字` - 添加朋友
`!friend 名字 备注:xxx` - 帶備注
`!friend 名字 来自:群名` - 帶來源

**分類操作**:
`!cat 名字 分類` - 設置分類
`!keep 名字` - 標記保留
`!delete 名字` - 標記刪除（需確認）
`!confirm 名字` - 確認刪除
`!skip 名字 原因` - 跳過

**查詢**:
`!status` - 今日進度
`!stats` - 總體統計
`!review` - 待處理列表
`!list 分類` - 查看某分類

**工具**:
`!export` - 匯出 CSV
`!undo` - 撤銷上一個操作
`!help` - 顯示此幫助

━━━━━━━━━━━━━━━━━━━━━━
💡 提示: 先用 `!friend` 添加，再做決策"""
    
    await ctx.send(response)

# Run bot
if __name__ == "__main__":
    import os
    
    DISCORD_TOKEN = os.environ.get("DISCORD_BOT_TOKEN")
    
    if not DISCORD_TOKEN:
        print("❌ 請設置 DISCORD_BOT_TOKEN 環境變量")
        print()
        print("   1. 在 Discord Developer Portal 創建 Bot，獲取 Token")
        print("   2. 設置環境變量:")
        print("      export DISCORD_BOT_TOKEN='your-token-here'")
        print()
        print("   3. 將 Bot 添加到你的 Discord Server:")
        print("      https://discord.com/api/oauth2/authorize?client_id=YOUR_CLIENT_ID&permissions=2048&scope=bot")
        exit(1)
    
    print("🚀 Starting WeChat Friend Assistant...")
    bot.run(DISCORD_TOKEN)
