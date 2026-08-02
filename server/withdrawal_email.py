from __future__ import annotations

from html import escape
from typing import Any

try:
    from .withdrawals import _display_time, settlement_for
except ImportError:
    from withdrawals import _display_time, settlement_for


def _text(value: Any) -> str:
    return escape(str(value if value not in (None, "") else "-"))


def _money(value: Any) -> str:
    try:
        return f"{float(value or 0):.2f}"
    except (TypeError, ValueError):
        return "0.00"


def _cell(content: str, *, align: str = "left", bold: bool = False) -> str:
    weight = "font-weight:700;" if bold else ""
    return (
        f'<td align="{align}" style="padding:11px 12px;border-top:1px solid #e5e7eb;'
        f'color:#1f2937;font-size:13px;line-height:1.5;{weight}">{content}</td>'
    )


def _metric(label: str, value: str, color: str, note: str = "") -> str:
    note_html = (
        f'<div style="margin-top:5px;color:#6b7280;font-size:11px;line-height:1.4;">{_text(note)}</div>'
        if note
        else ""
    )
    return (
        '<td width="33.33%" valign="top" style="padding:7px;">'
        '<div style="min-height:76px;padding:14px;border:1px solid #e5e7eb;background:#ffffff;">'
        f'<div style="color:#6b7280;font-size:12px;line-height:1.4;">{_text(label)}</div>'
        f'<div style="margin-top:5px;color:{color};font-size:21px;font-weight:700;line-height:1.25;">{_text(value)}</div>'
        f'{note_html}</div></td>'
    )


def _section_title(title: str) -> str:
    return (
        '<tr><td style="padding:24px 0 10px;color:#111827;font-size:16px;'
        f'font-weight:700;line-height:1.4;">{_text(title)}</td></tr>'
    )


def _status_badge(status: str, label: str) -> str:
    colors = {
        "submitted": ("#047857", "#ecfdf5"),
        "completed": ("#047857", "#ecfdf5"),
        "failed": ("#be123c", "#fff1f2"),
        "running": ("#1d4ed8", "#eff6ff"),
        "skipped": ("#4b5563", "#f3f4f6"),
    }
    foreground, background = colors.get(status, ("#a16207", "#fffbeb"))
    return (
        f'<span style="display:inline-block;padding:3px 8px;color:{foreground};background:{background};'
        f'font-size:12px;font-weight:700;line-height:1.4;">{_text(label)}</span>'
    )


def render_withdrawal_email_html(job: dict[str, Any], status: str = "已完成") -> str:
    mode_label = "成本提现" if job.get("mode") == "cost" else "全部提现"
    finalized = job.get("status") in {"completed", "failed"}
    settlement = settlement_for(job) if finalized else (job.get("settlement") or settlement_for(job))
    actual_amount = float(settlement.get("gross") or 0)

    post_cost = job.get("postWithdrawalCost")
    post_balance = job.get("postWithdrawalBalance")
    discounted_profit = job.get("discountedProfit")
    if post_cost is None:
        post_cost = float(settlement.get("cost") or 0)
    if post_balance is None and job.get("balanceSnapshotTotal") is not None:
        post_balance = round(max(float(job.get("balanceSnapshotTotal") or 0) - actual_amount, 0), 2)
    if discounted_profit is None and post_balance is not None:
        discounted_profit = round(float(post_balance) - float(post_cost), 2)

    cost_rows = []
    for addition in job.get("costHistory") or []:
        cost_rows.append(
            "<tr>"
            + _cell(_text(_display_time(addition.get("date"))))
            + _cell(f'{_money(addition.get("amount"))} 元', align="right", bold=True)
            + _cell(_text(addition.get("note") or "-"))
            + _cell(_text(_display_time(addition.get("createdAt") or addition.get("created_at"))))
            + "</tr>"
        )
    if not cost_rows:
        cost_rows.append(
            '<tr><td colspan="4" align="center" style="padding:18px;color:#6b7280;font-size:13px;">'
            "暂无成本历史明细</td></tr>"
        )

    account_rows = []
    for item in job.get("items") or []:
        status_label = item.get("statusLabel") or item.get("status") or "-"
        badge = _status_badge(str(item.get("status") or ""), str(status_label))
        reason = (
            f'<div style="margin-top:4px;color:#be123c;font-size:11px;">{_text(item.get("error"))}</div>'
            if item.get("error")
            else ""
        )
        account_rows.append(
            "<tr>"
            + _cell(_text(item.get("sequence")), align="center", bold=True)
            + _cell(_text(item.get("email")), bold=True)
            + _cell(_text(item.get("ownerLabel")))
            + _cell(f'{float(item.get("amount") or 0):.0f} 元', align="right", bold=True)
            + _cell(badge + reason)
            + "</tr>"
        )

    cost_status = job.get("costSettlementStatus")
    if cost_status == "cleared":
        cost_note = f"本次成本已收回，已自动结清 {_money(job.get('costClearedAmount'))} 元；提现记录继续保留成本快照。"
    elif cost_status == "already_cleared":
        cost_note = "任务成本此前已结清，本次未重复扣减。"
    elif cost_status == "not_recovered":
        cost_note = "本次未完整收回成本，成本记录继续保留。"
    else:
        cost_note = "任务完成并完整收回成本后自动结清。"

    if job.get("mode") == "cost":
        formula_html = (
            '<div style="padding:14px;border-left:4px solid #2563eb;background:#eff6ff;color:#1e3a8a;'
            'font-size:13px;line-height:1.8;"><strong>成本提现不参与 60% / 40% 利润分成。</strong><br>'
            f'本次成本回收：{_money(settlement.get("costRecovery"))} 元；'
            f'舍入余量：{_money(settlement.get("roundingRemainder"))} 元。</div>'
        )
    elif float(settlement.get("profit") or 0) > 0:
        formula_html = (
            '<div style="padding:14px;border-left:4px solid #7c3aed;background:#f5f3ff;color:#4c1d95;'
            'font-size:13px;line-height:1.8;">'
            f'<strong>可分配利润：{_money(settlement.get("profit"))} 元</strong><br>'
            f'星星应得 = {_money(settlement.get("cost"))} + {_money(settlement.get("profit"))} × 60% '
            f'= {_money(settlement.get("ownerExpected"))} 元<br>'
            f'社会哥应得 = {_money(settlement.get("profit"))} × 40% '
            f'= {_money(settlement.get("partnerExpected"))} 元</div>'
        )
    else:
        formula_html = (
            '<div style="padding:14px;border-left:4px solid #e11d48;background:#fff1f2;color:#881337;'
            'font-size:13px;line-height:1.8;"><strong>本次无可分配利润</strong><br>'
            f'星星应得 {_money(settlement.get("ownerExpected"))} 元；社会哥应得 0.00 元；'
            f'亏损 {_money(settlement.get("unrecoveredCost"))} 元由星星承担。</div>'
        )

    submitted_times = [item.get("submittedAt") for item in job.get("items", []) if item.get("submittedAt")]
    first_time = _display_time(min(submitted_times)) if submitted_times else "-"
    last_time = _display_time(max(submitted_times)) if submitted_times else "-"
    pixel_status = (
        "已提交，等待平台结算"
        if job.get("status") == "completed"
        else "任务已暂停，已提交的账号不会重复提交"
    )
    post_balance_label = f"{_money(post_balance)} 元" if post_balance is not None else "暂未获取"
    profit_label = f"{_money(discounted_profit)} 元" if discounted_profit is not None else "暂未计算"

    status_key = "completed" if job.get("status") == "completed" else str(job.get("status") or "")
    status_badge = _status_badge(status_key, status)
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>91 {_text(mode_label)}任务</title>
</head>
<body style="margin:0;padding:0;background:#f3f4f6;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI','PingFang SC','Microsoft YaHei',Arial,sans-serif;">
<table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="background:#f3f4f6;">
<tr><td align="center" style="padding:24px 10px;">
<table role="presentation" width="720" cellspacing="0" cellpadding="0" style="width:100%;max-width:720px;background:#ffffff;border:1px solid #e5e7eb;">
<tr><td style="padding:24px 28px;background:#111827;color:#ffffff;">
  <table role="presentation" width="100%" cellspacing="0" cellpadding="0"><tr>
    <td><div style="font-size:28px;font-weight:800;line-height:1;">91</div><div style="margin-top:8px;color:#d1d5db;font-size:14px;">{_text(mode_label)}任务</div></td>
    <td align="right">{status_badge}</td>
  </tr></table>
</td></tr>
<tr><td style="padding:24px 28px 30px;">
<table role="presentation" width="100%" cellspacing="0" cellpadding="0">
<tr><td style="padding:0 0 16px;color:#374151;font-size:13px;line-height:1.8;">
  <strong>任务编号：</strong>{_text(job.get('jobId'))}<br>
  <strong>余额快照：</strong>{_text(_display_time(job.get('balanceSnapshotAt')))}
</td></tr>
<tr><td><table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="margin:0 -7px;"><tr>
  {_metric('总成本', f'{_money(settlement.get("cost"))} 元', '#7c3aed')}
  {_metric('计划提现', f'{_money(job.get("requestedAmount"))} 元', '#2563eb')}
  {_metric('实际提交', f'{_money(settlement.get("gross"))} 元', '#047857')}
</tr></table></td></tr>
{_section_title('成本历史明细')}
<tr><td>
  <table role="table" width="100%" cellspacing="0" cellpadding="0" style="border:1px solid #e5e7eb;border-collapse:collapse;">
    <thead><tr style="background:#f9fafb;"><th align="left" style="padding:10px 12px;color:#6b7280;font-size:12px;">成本日期</th><th align="right" style="padding:10px 12px;color:#6b7280;font-size:12px;">金额</th><th align="left" style="padding:10px 12px;color:#6b7280;font-size:12px;">备注</th><th align="left" style="padding:10px 12px;color:#6b7280;font-size:12px;">录入时间</th></tr></thead>
    <tbody>{''.join(cost_rows)}</tbody>
    <tfoot><tr style="background:#f9fafb;"><td colspan="4" align="right" style="padding:10px 12px;color:#111827;font-size:13px;font-weight:700;">合计：{_money(job.get('costHistoryTotal'))} 元</td></tr></tfoot>
  </table>
  <div style="margin-top:10px;color:#6b7280;font-size:12px;line-height:1.6;">{_text(cost_note)}</div>
</td></tr>
{_section_title('分配与结算公式')}
<tr><td>{formula_html}</td></tr>
{_section_title('账号提现明细')}
<tr><td><table role="table" width="100%" cellspacing="0" cellpadding="0" style="border:1px solid #e5e7eb;border-collapse:collapse;">
  <thead><tr style="background:#f9fafb;"><th align="center" style="padding:10px 8px;color:#6b7280;font-size:12px;">序号</th><th align="left" style="padding:10px 12px;color:#6b7280;font-size:12px;">账号</th><th align="left" style="padding:10px 12px;color:#6b7280;font-size:12px;">收款方式</th><th align="right" style="padding:10px 12px;color:#6b7280;font-size:12px;">金额</th><th align="left" style="padding:10px 12px;color:#6b7280;font-size:12px;">状态</th></tr></thead>
  <tbody>{''.join(account_rows)}</tbody>
</table></td></tr>
{_section_title('执行时间')}
<tr><td style="padding:14px;background:#f9fafb;color:#374151;font-size:13px;line-height:1.9;">
  <strong>第一笔：</strong>{_text(first_time)}<br><strong>最后一笔：</strong>{_text(last_time)}<br>
  <strong>任务状态：</strong>{_text(status)}<br><strong>PixelAPI：</strong>{_text(pixel_status)}
</td></tr>
{_section_title('提现后汇总')}
<tr><td><table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="margin:0 -7px;"><tr>
  {_metric('提现后总成本', f'{_money(post_cost)} 元', '#7c3aed')}
  {_metric('提现后总余额', post_balance_label, '#047857')}
  {_metric('提现后折后利润', profit_label, '#2563eb', '总余额 - 总成本')}
</tr></table></td></tr>
{_section_title('实际到账归属（重点）')}
<tr><td style="padding:18px;border:2px solid #2563eb;background:#eff6ff;">
  <table role="presentation" width="100%" cellspacing="0" cellpadding="0">
    <tr><td style="padding:6px;color:#1e3a8a;font-size:14px;font-weight:700;">星星账号实际收到</td><td align="right" style="padding:6px;color:#1d4ed8;font-size:18px;font-weight:800;">{_money(settlement.get('ownerActual'))} 元</td></tr>
    <tr><td style="padding:6px;color:#1e3a8a;font-size:14px;font-weight:700;">社会哥账号实际收到</td><td align="right" style="padding:6px;color:#1d4ed8;font-size:18px;font-weight:800;">{_money(settlement.get('partnerActual'))} 元</td></tr>
    <tr><td colspan="2" style="padding:7px 0 3px;border-top:1px solid #bfdbfe;"></td></tr>
    <tr><td style="padding:6px;color:#1e3a8a;font-size:14px;font-weight:700;">星星应得</td><td align="right" style="padding:6px;color:#6d28d9;font-size:18px;font-weight:800;">{_money(settlement.get('ownerExpected'))} 元</td></tr>
    <tr><td style="padding:6px;color:#1e3a8a;font-size:14px;font-weight:700;">社会哥应得</td><td align="right" style="padding:6px;color:#c2410c;font-size:18px;font-weight:800;">{_money(settlement.get('partnerExpected'))} 元</td></tr>
  </table>
</td></tr>
{_section_title('结算转账（重点）')}
<tr><td style="padding:18px;border:2px solid #7c3aed;background:#f5f3ff;">
  <table role="presentation" width="100%" cellspacing="0" cellpadding="0">
    <tr><td style="padding:7px;color:#4c1d95;font-size:15px;font-weight:800;">社会哥需要转给星星</td><td align="right" style="padding:7px;color:#6d28d9;font-size:24px;font-weight:800;">{_money(settlement.get('partnerToOwner'))} 元</td></tr>
    <tr><td style="padding:7px;color:#4c1d95;font-size:15px;font-weight:800;">星星需要转给社会哥</td><td align="right" style="padding:7px;color:#6d28d9;font-size:24px;font-weight:800;">{_money(settlement.get('ownerToPartner'))} 元</td></tr>
  </table>
</td></tr>
</table>
</td></tr></table>
</td></tr></table>
</body></html>"""
