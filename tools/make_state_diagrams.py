"""Render docs/09-state-models.md's tables into one self-contained HTML page.

The MODEL is the tables in `docs/09-state-models.md` and the dictionaries below;
the page is a projection of it. That is the whole reason this is a generator and
not a hand-drawn file: when the state machine changes, the picture is re-derived
rather than remembered.

Drawing rules applied (Moody, "The Physics of Notations", 2009):
  * one symbol one meaning across ALL four views, listed in a legend;
  * never colour alone — every edge type differs in dash pattern too, so the
    page survives greyscale printing and colour-vision deficiency;
  * edge text replaced by numbered chips with the contracts in a table beside
    the canvas, because on-canvas labels at this density collide;
  * everything on an 8 px grid, orthogonal routing, declared crossings.

    python tools/make_state_diagrams.py            # writes docs/diagrams/
    python tools/make_state_diagrams.py --check    # geometry asserts only

Exit 0 = written and geometrically sound · 1 = an assert failed.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "diagrams" / "state-snapshots.html"
GRID = 8

# --------------------------------------------------------------------- model

# kind: state | transient | terminalish | decision | store | note
#   x, y are the top-left corner; every value is a multiple of GRID.
VIEWS: list[dict] = [
    {
        "id": "settings",
        "title": "A ｜ 設定面板",
        "question": "使用者按下「設定」之後，畫面可能處在哪些狀態，各自怎麼離開？",
        "vb": (0, 0, 880, 408),
        "axis": "左→右＝從「還不知道」走向「已經知道」；回頭的邊走上下兩條專用車道。",
        "nodes": [
            {"id": "init", "kind": "init", "x": 40, "y": 144, "w": 16, "h": 16},
            {"id": "unknown", "kind": "transient", "x": 88, "y": 120, "w": 176, "h": 64,
             "label": "unknown", "sub": "問伺服器中"},
            {"id": "unconfigured", "kind": "state", "x": 352, "y": 32, "w": 176, "h": 64,
             "label": "unconfigured", "sub": "檔案裡沒有金鑰"},
            {"id": "configured", "kind": "state", "x": 352, "y": 208, "w": 176, "h": 64,
             "label": "configured", "sub": "檔案裡有金鑰"},
            {"id": "busy", "kind": "state", "x": 632, "y": 120, "w": 176, "h": 64,
             "label": "busy", "sub": "POST 在路上，按鈕全鎖"},
            {"id": "unreachable", "kind": "error", "x": 88, "y": 304, "w": 176, "h": 64,
             "label": "unreachable", "sub": "問不到伺服器"},
        ],
        "edges": [
            {"pts": [(56, 152), (88, 152)], "kind": "plain"},
            {"n": 1, "pts": [(264, 136), (312, 136), (312, 64), (352, 64)], "kind": "ok"},
            {"n": 2, "pts": [(264, 168), (312, 168), (312, 240), (352, 240)], "kind": "ok"},
            {"n": 3, "pts": [(176, 184), (176, 304)], "kind": "fail"},
            {"n": 4, "pts": [(528, 64), (576, 64), (576, 136), (632, 136)], "kind": "act"},
            {"n": 5, "pts": [(528, 240), (576, 240), (576, 168), (632, 168)], "kind": "act"},
            {"n": 6, "pts": [(720, 120), (720, 16), (472, 16), (472, 32)], "kind": "ok"},
            {"n": 7, "pts": [(720, 184), (720, 288), (472, 288), (472, 272)], "kind": "ok"},
            {"n": 8, "pts": [(808, 136), (856, 136), (856, 168), (808, 168)], "kind": "fail"},
            {"n": 9, "pts": [(264, 336), (296, 336), (296, 200), (200, 200), (200, 184)],
             "kind": "act"},
            {"n": 10, "pts": [(408, 32), (408, 16), (104, 16), (104, 120)], "kind": "act"},
            {"n": 11, "pts": [(408, 272), (408, 392), (64, 392), (64, 128), (88, 128)],
             "kind": "act"},
        ],
        "table": [
            (1, "load-ok [檔案沒金鑰]", "顯示「尚未設定」，設定鈕加上 ⚠"),
            (2, "load-ok [檔案有金鑰]", "顯示長度＋末四碼，永遠不顯示值"),
            (3, "load-fail", "顯示讀不到的原因，其餘功能不受影響"),
            (4, "test / save / clear", "鎖住按鈕，開始計時，掛上 60 秒逾時"),
            (5, "test / save / clear", "同上"),
            (6, "post-ok [清除後]", "清空兩個密碼欄；狀態重畫"),
            (7, "post-ok [存檔後]", "清空兩個密碼欄；伺服器同時丟掉舊的 OPS client"),
            (8, "post-fail ／ post-timeout", "回到原狀態，說明是本機沒回話還是送不出去"),
            (9, "open", "重新載入（另一個視窗可能改過檔案）"),
            (10, "open", "同 9"),
            (11, "open", "同 9"),
        ],
    },
    {
        "id": "figures",
        "title": "B ｜ 圖式面板（區域 1：來源與載入）",
        "question": "一張圖從哪裡來、正在載入還是失敗了，讀者看得出來嗎？",
        "vb": (0, 0, 880, 400),
        "axis": ("左→右＝從沒有圖走到看得到圖；上下兩排＝兩個來源，互相可切換。"
                 "每一個狀態都接受 render(card)（換一件專利），"
                 "箭頭省略以免遮蔽——完整的狀態×事件表在 docs/09-state-models.md。"),
        "nodes": [
            {"id": "init2", "kind": "init", "x": 40, "y": 184, "w": 16, "h": 16},
            {"id": "none", "kind": "terminalish", "x": 88, "y": 160, "w": 176, "h": 64,
             "label": "none", "sub": "無圖式，畫面寫出原因"},
            {"id": "gload", "kind": "transient", "x": 376, "y": 40, "w": 176, "h": 64,
             "label": "google.loading", "sub": "免費、已抓好"},
            {"id": "gshown", "kind": "state", "x": 648, "y": 40, "w": 176, "h": 64,
             "label": "google.shown", "sub": "縮圖列"},
            {"id": "oload", "kind": "transient", "x": 376, "y": 264, "w": 176, "h": 64,
             "label": "ops.loading", "sub": "計時中，逐頁計費"},
            {"id": "oshown", "kind": "state", "x": 648, "y": 264, "w": 176, "h": 64,
             "label": "ops.shown", "sub": "頁碼列，本機已有的加底線"},
            {"id": "ferror", "kind": "error", "x": 512, "y": 160, "w": 176, "h": 64,
             "label": "error", "sub": "說出哪一張、為什麼"},
        ],
        "edges": [
            {"pts": [(56, 192), (88, 192)], "kind": "plain"},
            {"n": 1, "pts": [(264, 176), (320, 176), (320, 72), (376, 72)], "kind": "ok"},
            {"n": 2, "pts": [(264, 208), (320, 208), (320, 296), (376, 296)], "kind": "ok"},
            {"n": 3, "pts": [(552, 72), (648, 72)], "kind": "ok"},
            {"n": 4, "pts": [(552, 296), (648, 296)], "kind": "ok"},
            {"n": 5, "pts": [(464, 104), (464, 176), (512, 176)], "kind": "fail"},
            {"n": 6, "pts": [(416, 264), (416, 192), (512, 192)], "kind": "fail"},
            {"n": 7, "pts": [(736, 40), (736, 16), (400, 16), (400, 40)], "kind": "act"},
            {"n": 8, "pts": [(736, 328), (736, 384), (400, 384), (400, 328)], "kind": "act"},
            {"n": 9, "pts": [(688, 176), (856, 176), (856, 88), (824, 88)], "kind": "act"},
            {"n": 10, "pts": [(688, 208), (856, 208), (856, 312), (824, 312)], "kind": "act"},
        ],
        "table": [
            (1, "render(card) [有 Google 圖]", "Google 的 PNG 免費且已在手上，所以它優先"),
            (2, "render(card) [只有 OPS 影像]", "改用 EPO，並先問哪幾頁已經在本機"),
            (3, "img.load", "停掉計時器，量出自然尺寸後才排版"),
            (4, "img.load", "同時把這一頁標成「已付費」"),
            (5, "img.error", "來源拒絕存取；其餘圖片仍可看"),
            (6, "img.error", "同上（OPS 可能是配額或授權）"),
            (7, "swap ／ show(i) ／ ←→", "翻頁保留旋轉角度，歸零縮放"),
            (8, "swap ／ show(i) ／ ←→", "同上"),
            (9, "show(i)：從錯誤中重試", "錯誤不是陷阱狀態"),
            (10, "show(i)：從錯誤中重試", "同上"),
        ],
    },
    {
        "id": "launcher",
        "title": "D ｜ 啟動器",
        "question": "按下 exe 之後會發生什麼，第二次按會發生什麼？",
        "vb": (0, 0, 880, 480),
        "axis": "上→下＝時間；菱形＝判斷，每個判斷的兩條分支都畫出來。",
        "nodes": [
            {"id": "l0", "kind": "init", "x": 128, "y": 24, "w": 16, "h": 16},
            {"id": "lself", "kind": "decision", "x": 40, "y": 64, "w": 192, "h": 64,
             "label": "--selftest ？", "sub": "只回報，不啟動"},
            {"id": "ltest", "kind": "state", "x": 296, "y": 64, "w": 208, "h": 64,
             "label": "逐項 import 能力檢查", "sub": "exit 0 ／ 1"},
            {"id": "ldirs", "kind": "state", "x": 56, "y": 160, "w": 160, "h": 64,
             "label": "建立資料夾", "sub": "%LOCALAPPDATA%"},
            {"id": "lport", "kind": "decision", "x": 40, "y": 256, "w": 192, "h": 64,
             "label": "有空的埠？", "sub": "8000…8011"},
            {"id": "lours", "kind": "decision", "x": 296, "y": 256, "w": 208, "h": 64,
             "label": "是我們的嗎？", "sub": "問 /api/health"},
            {"id": "lsecond", "kind": "state", "x": 584, "y": 256, "w": 208, "h": 64,
             "label": "已經有一份在執行", "sub": "開瀏覽器指向它，exit 0"},
            {"id": "lnone", "kind": "error", "x": 296, "y": 360, "w": 208, "h": 64,
             "label": "12 個埠都被占用", "sub": "印出原因，視窗停住"},
            {"id": "lrun", "kind": "state", "x": 56, "y": 360, "w": 160, "h": 64,
             "label": "uvicorn 127.0.0.1", "sub": "背景執行緒開瀏覽器"},
            {"id": "lcrash", "kind": "error", "x": 584, "y": 360, "w": 208, "h": 64,
             "label": "任何例外", "sub": "寫記錄檔＋留在畫面上"},
        ],
        "edges": [
            {"pts": [(136, 40), (136, 64)], "kind": "plain"},
            {"n": 1, "pts": [(232, 96), (296, 96)], "kind": "act"},
            {"n": 2, "pts": [(136, 128), (136, 160)], "kind": "act"},
            {"pts": [(136, 224), (136, 256)], "kind": "plain"},
            {"n": 3, "pts": [(136, 320), (136, 360)], "kind": "ok"},
            {"n": 4, "pts": [(232, 288), (296, 288)], "kind": "act"},
            {"n": 5, "pts": [(504, 288), (584, 288)], "kind": "ok"},
            {"n": 6, "pts": [(400, 320), (400, 360)], "kind": "fail"},
            {"n": 7, "pts": [(136, 424), (136, 456), (688, 456), (688, 424)], "kind": "fail"},
            {"n": 8, "pts": [(400, 256), (400, 240), (24, 240), (24, 288), (40, 288)],
             "kind": "act"},
        ],
        "table": [
            (1, "是", "只回報這個 build 能做什麼，不啟動伺服器"),
            (2, "否", "資料夾一定先建好，之後任何失敗都有地方可查"),
            (3, "有空的", "直接用它"),
            (4, "沒有空的", "先問占用者是誰，不假設"),
            (5, "認得", "不開第二個伺服器（重複按圖示不是使用者的錯）"),
            (6, "不認得，且 12 個埠都用完了", "說出範圍，建議關掉占用程式或設 PORT"),
            (7, "uvicorn 綁不上／任何例外", "先看到埠是空的、綁定時被搶走也走這條"),
            (8, "不認得，還有下一個埠", "換下一個埠再試"),
        ],
    },
]

KINDS = {
    "state": ("#2f5d50", "#e6efeb", "實線圓角框", "穩定狀態"),
    "transient": ("#6b6b66", "#f2f2ee", "虛線圓角框", "過渡狀態（正在等待，畫面有計時）"),
    "terminalish": ("#6b6b66", "#ffffff", "雙線圓角框", "無事可做，但仍有出口"),
    "error": ("#8a5a1a", "#fdf3e3", "實線圓角框＋琥珀", "失敗狀態"),
    "decision": ("#2f5d50", "#ffffff", "菱形", "判斷"),
    "init": ("#1b1b19", "#1b1b19", "實心圓", "起點"),
}
EDGE_KINDS = {
    "ok": ("#2f5d50", "none", "實線", "正常轉移"),
    "act": ("#3d6ea0", "6 3", "藍虛線", "使用者動作或重新進入"),
    "fail": ("#8a5a1a", "2 4", "琥珀點線", "失敗／逾時／取不到"),
    "plain": ("#1b1b19", "none", "實線（無編號）", "起始邊"),
}


# ----------------------------------------------------------------- rendering

def path_d(pts) -> str:
    return "M " + " L ".join(f"{x} {y}" for x, y in pts)


def chip_at(pts):
    """Midpoint of the longest segment — the least crowded place on the route."""
    best, best_len = None, -1
    for (x1, y1), (x2, y2) in zip(pts, pts[1:]):
        length = abs(x2 - x1) + abs(y2 - y1)
        if length > best_len:
            best_len, best = length, ((x1 + x2) // 2, (y1 + y2) // 2)
    return best


def node_svg(n) -> str:
    stroke, fill, _, _ = KINDS[n["kind"]]
    if n["kind"] == "init":
        return (f'<circle cx="{n["x"] + n["w"] // 2}" cy="{n["y"] + n["h"] // 2}" '
                f'r="{n["w"] // 2}" fill="{stroke}"/>')
    label = html.escape(n.get("label", ""))
    sub = html.escape(n.get("sub", ""))
    cx, cy = n["x"] + n["w"] / 2, n["y"] + n["h"] / 2
    ty = cy - 4 if sub else cy + 5
    if n["kind"] == "decision":
        x, y, w, h = n["x"], n["y"], n["w"], n["h"]
        shape = (f'<polygon points="{x + w / 2},{y} {x + w},{y + h / 2} '
                 f'{x + w / 2},{y + h} {x},{y + h / 2}" fill="{fill}" stroke="{stroke}" '
                 f'stroke-width="2"/>')
    else:
        dash = ' stroke-dasharray="7 4"' if n["kind"] == "transient" else ""
        shape = (f'<rect x="{n["x"]}" y="{n["y"]}" width="{n["w"]}" height="{n["h"]}" '
                 f'rx="10" fill="{fill}" stroke="{stroke}" stroke-width="2"{dash}/>')
        if n["kind"] == "terminalish":
            shape += (f'<rect x="{n["x"] + 5}" y="{n["y"] + 5}" width="{n["w"] - 10}" '
                      f'height="{n["h"] - 10}" rx="6" fill="none" stroke="{stroke}"/>')
    text = (f'<text class="nl" x="{cx}" y="{ty}" data-node="{n["id"]}">{label}</text>')
    if sub:
        text += (f'<text class="ns" x="{cx}" y="{cy + 14}" '
                 f'data-sub="{n["id"]}">{sub}</text>')
    return shape + text


def edge_svg(e) -> str:
    colour, dash, _, _ = EDGE_KINDS[e["kind"]]
    da = f' stroke-dasharray="{dash}"' if dash != "none" else ""
    out = (f'<path class="edge" d="{path_d(e["pts"])}" fill="none" stroke="{colour}" '
           f'stroke-width="2"{da} marker-end="url(#arrow-{e["kind"]})"/>')
    if "n" in e:
        cx, cy = chip_at(e["pts"])
        out += (f'<circle class="chip" cx="{cx}" cy="{cy}" r="10" fill="#ffffff" '
                f'stroke="{colour}" stroke-width="2"/>'
                f'<text class="ct" x="{cx}" y="{cy + 4}" fill="{colour}">{e["n"]}</text>')
    return out


def view_svg(v) -> str:
    x, y, w, h = v["vb"]
    defs = "".join(
        f'<marker id="arrow-{k}" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" '
        f'markerHeight="7" orient="auto-start-reverse">'
        f'<path d="M 0 0 L 10 5 L 0 10 z" fill="{c[0]}"/></marker>'
        for k, c in EDGE_KINDS.items())
    body = "".join(edge_svg(e) for e in v["edges"]) + "".join(node_svg(n) for n in v["nodes"])
    return (f'<svg id="svg-{v["id"]}" viewBox="{x} {y} {w} {h}" '
            f'role="img" aria-label="{html.escape(v["title"])}">'
            f'<defs>{defs}</defs>{body}</svg>')


DECISION_TABLE = """
<h2>C ｜ 本機守門 decision table</h2>
<p class="q">問題：哪些請求會被擋，哪些一定要放行？</p>
<p class="note">狀態機不適合表達三個條件互相作用的判斷，所以這一個檢視刻意是表格而不是圖。
條件空間已窮盡：<code>Host</code> 二分 × <code>Origin</code> 三分 × <code>Sec-Fetch-Site</code>
二分，下面六列涵蓋全部不重複判決，<b>else 分支是明寫的</b>。</p>
<table class="dec">
<thead><tr><th>#</th><th>Host</th><th>Origin</th><th>Sec-Fetch-Site</th><th>判決</th>
<th>擋掉的是什麼</th></tr></thead>
<tbody>
<tr><td>1</td><td>非本機／缺</td><td>＊</td><td>＊</td><td class="no">421</td>
    <td>DNS rebinding：攻擊者把自己的網域指到 127.0.0.1</td></tr>
<tr><td>2</td><td>本機</td><td>缺</td><td>缺／非 cross-site</td><td class="yes">放行</td>
    <td>—　這就是本頁自己的 GET　<b>（正對照）</b></td></tr>
<tr><td>3</td><td>本機</td><td>缺</td><td><code>cross-site</code></td><td class="no">403</td>
    <td>別的網頁用 &lt;img&gt;／&lt;script&gt;／&lt;form&gt; 直接伸手（這種請求不帶 Origin）</td></tr>
<tr><td>4</td><td>本機</td><td>本機</td><td>非 cross-site</td><td class="yes">放行</td>
    <td>—　這就是本頁自己的 POST　<b>（正對照）</b></td></tr>
<tr><td>5</td><td>本機</td><td>本機</td><td><code>cross-site</code></td><td class="no">403</td>
    <td>同 3</td></tr>
<tr><td>6</td><td>本機</td><td>非本機</td><td>＊</td><td class="no">403</td>
    <td>別的網頁對這個埠發的寫入請求</td></tr>
</tbody></table>
<p class="note"><b>只驗拒絕的守門員，全部擋光也是滿分。</b>第 2 與第 4 列在
<code>tools/check_settings.py</code> 與 <code>tools/check_release.py</code> 各有一個
「必須放行」的斷言，和三個「必須拒絕」的斷言並排跑。</p>
"""

GAPS = """
<h2>缺口報告</h2>
<p class="note">畫圖的目的有一半是找洞。下面九項是把上面四個檢視逐格填滿時掉出來的，
不是事後補寫的心得。完整版與來源追溯在 <code>docs/09-state-models.md</code>。</p>
<table class="dec gaps">
<thead><tr><th>ID</th><th>缺口</th><th>處置</th></tr></thead>
<tbody>
<tr><td>G-7</td><td class="hi"><code>__main__.py</code> 用相對 import，PyInstaller 以
    <code>__main__</code> 執行時沒有 package 脈絡 → 打包「成功」但一啟動就死</td>
    <td class="fixed">已修。抓到它的是 <code>tools/check_release.py</code>，不是人眼</td></tr>
<tr><td>G-6</td><td><code>PORT</code> 指定的埠沒有先問「是不是已經有一份在跑」，
    第二次啟動會綁不上而當掉</td><td class="fixed">已修：指定埠也做同樣探測</td></tr>
<tr><td>G-1</td><td>設定面板 <code>busy</code> 沒有逾時，掛住的請求會讓按鈕永遠鎖著</td>
    <td class="fixed">已修：60 秒 AbortController，訊息區分「本機沒回話」與「EPO 慢」</td></tr>
<tr><td>G-3</td><td>兩個新的等待違反本專案「不得靜默等待」規則
    （說了在等什麼，沒說等了多久）</td>
    <td class="fixed">已修：<code>ticker()</code> 一個共用實作，兩處共用</td></tr>
<tr><td>G-4</td><td><code>_host_is_local</code> 用數冒號判斷埠，<code>[::1]:8000</code>
    會被判成非本機</td><td class="fixed">已修：改成解析括號形式</td></tr>
<tr><td>G-5</td><td><code>X-Page-Source</code> 的註解宣稱「頁面會顯示它」——
    <code>&lt;img&gt;</code> 讀不到回應標頭，做不到</td>
    <td class="fixed">已修：註解改成實話</td></tr>
<tr><td>G-2</td><td><code>＊.loading</code> 無法設逾時（<code>&lt;img&gt;</code> 的限制）</td>
    <td class="kept">留著，寫明：改以每 200 ms 的經過秒數＋6 秒後的限流說明取代沉默</td></tr>
<tr><td>G-8</td><td>「環境變數與設定檔不同」的警告會被之後的訊息覆蓋</td>
    <td class="kept">留著：下次打開面板就會重新出現</td></tr>
<tr><td>G-9</td><td>設定面板沒有「語言」之類的未來項目</td>
    <td class="kept">不是缺口：本輪刻意不做沒有作用的下拉選單</td></tr>
</tbody></table>
"""

PAGE = """<!doctype html>
<html lang="zh-Hant"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>PatentsGrabber — 狀態快照 2026-08-29</title>
<style>
:root{{--bg:#f7f7f5;--panel:#fff;--ink:#1b1b19;--muted:#6b6b66;--line:#e2e2dd;
       --accent:#2f5d50;--soft:#e6efeb;--warn:#8a5a1a;--warnsoft:#fdf3e3;--act:#3d6ea0;
       --sans:"Noto Sans TC",-apple-system,"Segoe UI",system-ui,sans-serif;
       --mono:ui-monospace,"Cascadia Mono",Consolas,monospace}}
*{{box-sizing:border-box}}
body{{margin:0;background:var(--bg);color:var(--ink);font:15px/1.75 var(--sans)}}
.wrap{{max-width:1180px;margin:0 auto;padding:34px 26px 90px}}
h1{{font-size:23px;margin:0 0 6px}}
h2{{font-size:17px;margin:44px 0 4px;padding-top:22px;border-top:1px solid var(--line)}}
.lede{{color:var(--muted);font-size:14px;margin:0 0 26px}}
.q{{color:var(--accent);font-size:14px;margin:0 0 4px;font-weight:600}}
.axis{{color:var(--muted);font-size:12.5px;margin:0 0 12px;font-family:var(--mono)}}
.note{{color:var(--muted);font-size:13px;margin:10px 0}}
code{{font-family:var(--mono);font-size:12.5px}}
.fig{{background:var(--panel);border:1px solid var(--line);border-radius:12px;
      padding:14px;overflow-x:auto}}
svg{{display:block;width:100%;height:auto;min-width:760px}}
text{{font-family:var(--sans)}}
.nl{{font-size:13px;font-weight:600;text-anchor:middle;fill:#1b1b19}}
.ns{{font-size:11px;text-anchor:middle;fill:#6b6b66}}
.ct{{font-size:11.5px;font-weight:700;text-anchor:middle}}
table{{width:100%;border-collapse:collapse;font-size:13.5px;margin:14px 0 0}}
th,td{{text-align:left;padding:7px 10px;border-bottom:1px solid var(--line);vertical-align:top}}
th{{color:var(--muted);font-size:11.5px;text-transform:uppercase;letter-spacing:.06em}}
td:first-child{{font-family:var(--mono);color:var(--accent);white-space:nowrap;font-weight:600}}
.dec td:first-child{{width:3em}}
.yes{{color:var(--accent);font-weight:600}}
.no{{color:var(--warn);font-weight:600;font-family:var(--mono)}}
.gaps td:first-child{{width:4em}}
.fixed{{color:var(--accent)}}
.kept{{color:var(--warn)}}
.hi{{background:var(--warnsoft)}}
.legend{{display:flex;flex-wrap:wrap;gap:8px 22px;background:var(--panel);
         border:1px solid var(--line);border-radius:12px;padding:14px 18px;margin:0 0 26px}}
.legend div{{display:flex;align-items:center;gap:9px;font-size:12.5px;color:var(--muted)}}
.legend b{{color:var(--ink);font-weight:600}}
.sw{{width:34px;height:20px;flex:0 0 34px}}
.foot{{margin-top:50px;padding-top:18px;border-top:1px solid var(--line);
       color:var(--muted);font-size:12px;font-family:var(--mono);line-height:2}}
@media (prefers-color-scheme:dark){{
  :root{{--bg:#141519;--panel:#1c1d21;--ink:#e8e8e4;--muted:#9a9a94;--line:#2f3036;
         --accent:#7fc0aa;--soft:#22312c;--warn:#e0b072;--warnsoft:#33291a;--act:#8ab4e0}}
  .fig{{background:#f7f7f5}}      /* 圖用固定亮底：線條顏色是編碼的一部分 */
}}
</style></head><body><div class="wrap">
<h1>PatentsGrabber ｜ 狀態快照</h1>
<p class="lede">2026-08-29 · 這一頁是 <code>docs/09-state-models.md</code> 的投影，由
<code>tools/make_state_diagrams.py</code> 產生。模型改了就重跑，不要直接改這個檔。<br>
四個檢視，四個不同的問題。沒有一張圖想回答全部——一張想說完所有事的圖，
哪一件都說不清楚。</p>

<div class="legend">{legend}</div>
{views}
{decision}
{gaps}
<div class="foot">{receipt}</div>
<script type="application/json" id="model">{model}</script>
</div></body></html>
"""


def legend_html() -> str:
    out = []
    for kind, (stroke, fill, shape_name, meaning) in KINDS.items():
        if kind == "init":
            sw = f'<svg class="sw" viewBox="0 0 34 20"><circle cx="17" cy="10" r="7" fill="{stroke}"/></svg>'
        elif kind == "decision":
            sw = (f'<svg class="sw" viewBox="0 0 34 20"><polygon points="17,2 32,10 17,18 2,10" '
                  f'fill="{fill}" stroke="{stroke}" stroke-width="2"/></svg>')
        else:
            dash = ' stroke-dasharray="5 3"' if kind == "transient" else ""
            inner = ('<rect x="4" y="4" width="26" height="12" rx="3" fill="none" '
                     f'stroke="{stroke}"/>') if kind == "terminalish" else ""
            sw = (f'<svg class="sw" viewBox="0 0 34 20"><rect x="2" y="2" width="30" height="16" '
                  f'rx="5" fill="{fill}" stroke="{stroke}" stroke-width="2"{dash}/>{inner}</svg>')
        out.append(f'<div>{sw}<span><b>{shape_name}</b> {meaning}</span></div>')
    for kind, (colour, dash, name, meaning) in EDGE_KINDS.items():
        da = f' stroke-dasharray="{dash}"' if dash != "none" else ""
        sw = (f'<svg class="sw" viewBox="0 0 34 20"><path d="M 2 10 L 30 10" stroke="{colour}" '
              f'stroke-width="2"{da} fill="none"/><path d="M 26 6 L 32 10 L 26 14 z" '
              f'fill="{colour}"/></svg>')
        out.append(f'<div>{sw}<span><b>{name}</b> {meaning}</span></div>')
    out.append('<div><svg class="sw" viewBox="0 0 34 20"><circle cx="17" cy="10" r="8" '
               'fill="#fff" stroke="#2f5d50" stroke-width="2"/><text x="17" y="14" '
               'font-size="10" font-weight="700" text-anchor="middle" fill="#2f5d50">n</text>'
               '</svg><span><b>編號圓點</b> 邊的說明在旁邊的表格第 n 列</span></div>')
    return "".join(out)


def views_html() -> str:
    parts = []
    for v in VIEWS:
        rows = "".join(f"<tr><td>{n}</td><td>{html.escape(ev)}</td><td>{html.escape(act)}</td></tr>"
                       for n, ev, act in v["table"])
        parts.append(
            f'<h2>{html.escape(v["title"])}</h2>'
            f'<p class="q">問題：{html.escape(v["question"])}</p>'
            f'<p class="axis">{html.escape(v["axis"])}</p>'
            f'<div class="fig">{view_svg(v)}</div>'
            f'<table><thead><tr><th>#</th><th>事件 [條件]</th><th>動作</th></tr></thead>'
            f'<tbody>{rows}</tbody></table>')
    return "".join(parts)


# ------------------------------------------------------- geometry self-checks

def rect_of(n):
    return n["x"], n["y"], n["x"] + n["w"], n["y"] + n["h"]


def seg_hits_rect(p1, p2, rect, pad=0) -> bool:
    """Orthogonal segment against an axis-aligned rectangle (interior only)."""
    x1, y1 = p1
    x2, y2 = p2
    rx1, ry1, rx2, ry2 = rect[0] + pad, rect[1] + pad, rect[2] - pad, rect[3] - pad
    if x1 == x2:
        return rx1 < x1 < rx2 and max(ry1, min(y1, y2)) < min(ry2, max(y1, y2))
    return ry1 < y1 < ry2 and max(rx1, min(x1, x2)) < min(rx2, max(x1, x2))


def touching(pt, rect, eps=1) -> bool:
    x, y = pt
    x1, y1, x2, y2 = rect
    return (x1 - eps <= x <= x2 + eps) and (y1 - eps <= y <= y2 + eps)


def geometry_problems() -> list[dict]:
    """Diagnostics, not bare strings: code / subject / evidence / fix."""
    bad: list[dict] = []
    for v in VIEWS:
        vx, vy, vw, vh = v["vb"]
        by_id = {n["id"]: n for n in v["nodes"]}
        for n in v["nodes"]:
            for key in ("x", "y", "w", "h"):
                if n[key] % GRID:
                    bad.append({"code": "off-grid", "subject": f'{v["id"]}/{n["id"]}',
                                "evidence": f'{key}={n[key]} is not a multiple of {GRID}',
                                "fix": f'round to {round(n[key] / GRID) * GRID}'})
            if not (vx <= n["x"] and n["y"] >= vy
                    and n["x"] + n["w"] <= vx + vw and n["y"] + n["h"] <= vy + vh):
                bad.append({"code": "outside-viewbox", "subject": f'{v["id"]}/{n["id"]}',
                            "evidence": f'{rect_of(n)} vs viewBox {v["vb"]}',
                            "fix": "enlarge the viewBox or move the node"})
        # No two nodes overlap.
        for i, a in enumerate(v["nodes"]):
            for b in v["nodes"][i + 1:]:
                ax1, ay1, ax2, ay2 = rect_of(a)
                bx1, by1, bx2, by2 = rect_of(b)
                if ax1 < bx2 and bx1 < ax2 and ay1 < by2 and by1 < ay2:
                    bad.append({"code": "node-overlap",
                                "subject": f'{v["id"]}/{a["id"]}+{b["id"]}',
                                "evidence": f'{rect_of(a)} ∩ {rect_of(b)}', "fix": "move one"})
        for e in v["edges"]:
            for p in e["pts"]:
                if not (vx <= p[0] <= vx + vw and vy <= p[1] <= vy + vh):
                    bad.append({"code": "waypoint-outside-viewbox",
                                "subject": f'{v["id"]}/edge{e.get("n", "-")}',
                                "evidence": f'{p} outside {v["vb"]}',
                                "fix": "enlarge the viewBox"})
                if p[0] % GRID or p[1] % GRID:
                    bad.append({"code": "off-grid-waypoint",
                                "subject": f'{v["id"]}/edge{e.get("n", "-")}',
                                "evidence": str(p), "fix": "snap to the 8 px grid"})
            for p1, p2 in zip(e["pts"], e["pts"][1:]):
                if p1[0] != p2[0] and p1[1] != p2[1]:
                    bad.append({"code": "non-orthogonal",
                                "subject": f'{v["id"]}/edge{e.get("n", "-")}',
                                "evidence": f'{p1}->{p2}', "fix": "add a waypoint"})
            ends = (e["pts"][0], e["pts"][-1])
            for n in v["nodes"]:
                rect = rect_of(n)
                for p1, p2 in zip(e["pts"], e["pts"][1:]):
                    if seg_hits_rect(p1, p2, rect, pad=1):
                        # Its own endpoints are allowed to sit on the boundary.
                        if any(touching(p, rect, 2) for p in ends):
                            continue
                        bad.append({"code": "edge-through-node",
                                    "subject": f'{v["id"]}/edge{e.get("n", "-")} through {n["id"]}',
                                    "evidence": f'{p1}->{p2} crosses {rect}',
                                    "fix": "route around, or move the node"})
            # Both ends must actually land on some node — and for a diamond,
            # "on the node" means one of its four apexes, not anywhere along the
            # bounding box. An arrow aimed at the box lands on empty canvas.
            for p in ends:
                landed = False
                for n in v["nodes"]:
                    if not touching(p, rect_of(n), 2):
                        continue
                    if n["kind"] == "decision":
                        cx, cy = n["x"] + n["w"] / 2, n["y"] + n["h"] / 2
                        apexes = [(cx, n["y"]), (cx, n["y"] + n["h"]),
                                  (n["x"], cy), (n["x"] + n["w"], cy)]
                        if not any(abs(p[0] - ax) <= 2 and abs(p[1] - ay) <= 2
                                   for ax, ay in apexes):
                            bad.append({"code": "anchor-off-apex",
                                        "subject": f'{v["id"]}/edge{e.get("n", "-")}',
                                        "evidence": f'{p} is on {n["id"]}\'s bounding box, '
                                                    f'not on the diamond (apexes {apexes})',
                                        "fix": "aim at the nearest apex"})
                            continue
                    landed = True
                if not landed:
                    bad.append({"code": "anchor-off-node",
                                "subject": f'{v["id"]}/edge{e.get("n", "-")}',
                                "evidence": f'endpoint {p} touches nothing',
                                "fix": "snap it to a node boundary"})
        # Numbered chips must not sit on a node or on each other.
        chips = [(e["n"], chip_at(e["pts"])) for e in v["edges"] if "n" in e]
        for num, (cx, cy) in chips:
            for n in v["nodes"]:
                x1, y1, x2, y2 = rect_of(n)
                if x1 - 10 < cx < x2 + 10 and y1 - 10 < cy < y2 + 10:
                    bad.append({"code": "chip-on-node", "subject": f'{v["id"]}/chip{num}',
                                "evidence": f'({cx},{cy}) inside {n["id"]} {rect_of(n)}',
                                "fix": "lengthen a different segment so the chip moves"})
        for i, (na, pa) in enumerate(chips):
            for nb, pb in chips[i + 1:]:
                if abs(pa[0] - pb[0]) < 24 and abs(pa[1] - pb[1]) < 24:
                    bad.append({"code": "chip-overlap", "subject": f'{v["id"]}/chip{na}+{nb}',
                                "evidence": f'{pa} vs {pb}', "fix": "re-route one edge"})
        # Every chip has a table row, and every row has a chip.
        drawn = {e["n"] for e in v["edges"] if "n" in e}
        listed = {row[0] for row in v["table"]}
        if drawn != listed:
            bad.append({"code": "chip-table-mismatch", "subject": v["id"],
                        "evidence": f'drawn {sorted(drawn)} vs listed {sorted(listed)}',
                        "fix": "add the missing row or remove the orphan chip"})
        # Every marker referenced is defined (a dangling url(#id) fails silently).
        used = {e["kind"] for e in v["edges"]}
        if not used <= set(EDGE_KINDS):
            bad.append({"code": "dangling-marker", "subject": v["id"],
                        "evidence": str(used - set(EDGE_KINDS)), "fix": "define the marker"})
        _ = by_id
    return bad


def positive_control() -> bool:
    """Would the checker notice a real defect? Feed it one it must catch."""
    probe = {"id": "probe", "vb": (0, 0, 200, 200),
             "nodes": [{"id": "a", "kind": "state", "x": 40, "y": 40, "w": 80, "h": 40,
                        "label": "a"},
                       {"id": "b", "kind": "state", "x": 40, "y": 120, "w": 80, "h": 40,
                        "label": "b"}],
             # deliberately: off-grid, diagonal, and straight through node "a"
             "edges": [{"n": 1, "pts": [(8, 61), (200, 61)], "kind": "ok"}],
             "table": [(1, "x", "y")]}
    VIEWS.append(probe)
    try:
        codes = {p["code"] for p in geometry_problems() if p["subject"].startswith("probe")}
    finally:
        VIEWS.pop()
    return {"off-grid-waypoint", "edge-through-node", "anchor-off-node"} <= codes


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="asserts only, write nothing")
    a = ap.parse_args()

    print("=== the geometry checker itself (positive control) ===")
    ok = positive_control()
    print(f"  {'PASS' if ok else 'FAIL'}  catches an off-grid, node-piercing, unanchored edge")
    if not ok:
        print("\n  the checker does not detect a defect it was handed — refusing to certify.")
        return 1

    print("\n=== geometry of the four views ===")
    problems = geometry_problems()
    for p in problems:
        print(f"  FAIL  [{p['code']}] {p['subject']}\n        {p['evidence']}\n        fix: {p['fix']}")
    if not problems:
        total_nodes = sum(len(v["nodes"]) for v in VIEWS)
        total_edges = sum(len(v["edges"]) for v in VIEWS)
        print(f"  PASS  {len(VIEWS)} views, {total_nodes} nodes, {total_edges} edges — "
              f"on grid, orthogonal, anchored, nothing pierced, chips clear")
    if problems:
        return 1

    if a.check:
        return 0

    model_hash = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()[:16]
    receipt = (f"產生器　tools/make_state_diagrams.py　sha256:{model_hash}<br>"
               f"正本模型　docs/09-state-models.md<br>"
               f"幾何自檢　{len(VIEWS)} 檢視全數通過（含一個必須被抓到的正對照）")
    model = json.dumps({v["id"]: {"vb": v["vb"], "nodes": v["nodes"]} for v in VIEWS},
                       ensure_ascii=False)
    page = PAGE.format(legend=legend_html(), views=views_html(),
                       decision=DECISION_TABLE, gaps=GAPS, receipt=receipt, model=model)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(page, encoding="utf-8", newline="\n")
    print(f"\n  wrote {OUT.relative_to(ROOT)}  ({len(page.encode('utf-8')):,} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
