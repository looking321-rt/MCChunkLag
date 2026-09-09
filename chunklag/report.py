# -*- coding: utf-8 -*-
"""
报告渲染 —— 把分析结果输出为终端文字 或 HTML。

终端文字版：模拟 spark 的占比树 + 下钻 + 排名。
HTML 版：表格化，更接近 spark viewer 的可读性。
"""
import html as _html


def _pct(contribution, total):
    if total <= 0:
        return 0.0
    return 100.0 * contribution / total


def render_text(result, top_n=10):
    """返回终端文字报告。"""
    lines = []
    lines.append("=" * 52)
    lines.append("MC 存档区块卡顿原因分析")
    lines.append("=" * 52)
    lines.append("世界: %s" % result.world_name)
    lines.append("数据版本: %s | 区块数: %d | 总卡顿分: %d"
                 % (result.data_version, result.total_chunks, result.total_score))
    lines.append("")

    total = result.total_score or 1

    # 1. 占比树
    lines.append("[占比树] 哪种卡顿原因占大头? (按加权贡献 / 总卡顿分)")
    lines.append("-" * 52)
    for group, stats in result.groups:
        group_contrib = sum(s.contribution for s in stats)
        lines.append("%s  贡献 %.1f%%" % (group, _pct(group_contrib, total)))
        for s in stats:
            if s.count <= 0:
                continue
            bar = "▓" * int(round(_pct(s.contribution, total) / 2))
            lines.append("  %-14s count=%-5d w=%d  %.1f%%  %s"
                         % (s.label, s.count, s.weight, _pct(s.contribution, total), bar))
    lines.append("")

    # 2. 下钻：占比最高的原因 → 集中在哪些区块
    lines.append("[下钻] 占比最高的原因，集中在哪些区块?")
    lines.append("-" * 52)
    # 按 contribution 排序，取前 3 个有贡献的关键因子
    contributing = sorted(
        (s for s in result.factor_stats.values() if s.count > 0),
        key=lambda s: s.contribution, reverse=True)[:3]
    for s in contributing:
        if not s.chunks:
            continue
        top_blocks = sorted(s.chunks, key=lambda c: c[2], reverse=True)[:3]
        detail = ", ".join("(%d,%d)x%d" % (cx, cz, c) for cx, cz, c in top_blocks)
        lines.append("%-10s 占%.1f%%，最集中: %s"
                     % (s.label, _pct(s.contribution, total), detail))
    lines.append("")

    # 3. 最卡 TOP 榜
    lines.append("[最卡 TOP %d 区块]" % min(top_n, len(result.top_chunks)))
    lines.append("-" * 52)
    for rank, (cx, cz, score, counts) in enumerate(result.top_chunks[:top_n], 1):
        reasons = " ".join("%s%d" % (k.split("_")[-1][:5], v)
                           for k, v in counts.items() if v > 0)
        lines.append("%2d. (%d, %d)  评分=%d  [%s]" % (rank, cx, cz, score, reasons))
    lines.append("")
    lines.append("说明: 评分=Σ(因子计数×经验权重)，是启发式离线估算，非 mspt 实测。")
    lines.append("     对比各区块相对高低即可，spark 需连运行中服务端才能测真实 mspt。")
    return "\n".join(lines)


def render_html(result, top_n=10):
    """返回 HTML 报告字符串。"""
    total = result.total_score or 1
    h = []
    h.append("<!DOCTYPE html><html><head><meta charset='utf-8'>")
    h.append("<title>MC 区块卡顿分析</title>")
    h.append("<style>body{font-family:Segoe UI,Microsoft YaHei,sans-serif;background:#1e1e2e;color:#e5e5e5;margin:24px;}")
    h.append("h1{font-size:20px}h2{font-size:16px;color:#89b4fa;border-bottom:1px solid #444;padding-bottom:4px;}")
    h.append("table{border-collapse:collapse;width:100%;margin:10px 0;font-size:13px;}")
    h.append("th,td{border:1px solid #444;padding:5px 8px;text-align:left;}")
    h.append("th{background:#313244}.pct{font-weight:bold;color:#a6e3a1}.warn{color:#f38ba8;font-size:12px}")
    h.append("</style></head><body>")

    h.append("<h1>MC 存档区块卡顿原因分析</h1>")
    h.append("<p>世界: <b>%s</b> | 数据版本: %s | 区块数: %d | 总卡顿分: %d</p>"
             % (_html.escape(str(result.world_name)), result.data_version,
                result.total_chunks, result.total_score))

    # 占比树
    h.append("<h2>1. 占比树 · 哪种卡顿原因占大头</h2>")
    h.append("<table><tr><th>大类</th><th>子类</th><th>数量</th><th>权重</th><th>加权贡献</th><th>占比</th></tr>")
    for group, stats in result.groups:
        group_contrib = sum(s.contribution for s in stats)
        first = True
        for s in stats:
            if s.count <= 0:
                continue
            if first:
                cell_group = "%s<br>%.1f%%" % (_html.escape(group), _pct(group_contrib, total))
                first = False
            else:
                cell_group = ""
            h.append("<tr><td>%s</td><td>%s</td><td>%d</td><td>%d</td><td>%d</td>"
                     "<td class='pct'>%.1f%%</td></tr>"
                     % (cell_group, _html.escape(s.label), s.count, s.weight,
                        s.contribution, _pct(s.contribution, total)))
    h.append("</table>")

    # 下钻
    h.append("<h2>2. 下钻 · 占比最高的原因集中在哪些区块</h2>")
    contributing = sorted((s for s in result.factor_stats.values() if s.count > 0),
                          key=lambda s: s.contribution, reverse=True)[:3]
    if contributing:
        h.append("<table><tr><th>原因</th><th>占比</th><th>最集中的区块 (cx,cz)x数量</th></tr>")
        for s in contributing:
            if not s.chunks:
                continue
            top_blocks = sorted(s.chunks, key=lambda c: c[2], reverse=True)[:5]
            detail = ", ".join("(%d,%d)×%d" % (cx, cz, c) for cx, cz, c in top_blocks)
            h.append("<tr><td>%s</td><td>%.1f%%</td><td>%s</td></tr>"
                     % (_html.escape(s.label), _pct(s.contribution, total),
                        _html.escape(detail)))
        h.append("</table>")
    else:
        h.append("<p>无显著卡顿因子。</p>")

    # 最卡 TOP
    h.append("<h2>3. 最卡 TOP %d 区块</h2>" % min(top_n, len(result.top_chunks)))
    if result.top_chunks:
        h.append("<table><tr><th>#</th><th>区块坐标</th><th>评分</th><th>因子明细</th></tr>")
        for rank, (cx, cz, score, counts) in enumerate(result.top_chunks[:top_n], 1):
            reasons = " ".join("%s×%d" % (k.split("_")[-1], v) for k, v in counts.items() if v > 0)
            h.append("<tr><td>%d</td><td>(%d, %d)</td><td class='pct'>%d</td><td>%s</td></tr>"
                     % (rank, cx, cz, score, _html.escape(reasons)))
        h.append("</table>")
    else:
        h.append("<p>无区块数据。</p>")

    h.append("<p class='warn'>⚠ 评分为启发式离线估算(Σ计数×权重)，非真实 mspt；仅用于横向对比哪些区块更可能卡。</p>")
    h.append("</body></html>")
    return "\n".join(h)
