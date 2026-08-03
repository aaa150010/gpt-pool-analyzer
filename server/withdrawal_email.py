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


def _status_badge(status: str, label: str) -> str:
    colors = {
        "submitted": ("#067647", "#ecfdf3", "#a6f4c5"),
        "completed": ("#067647", "#ecfdf3", "#a6f4c5"),
        "failed": ("#b42318", "#fef3f2", "#fecdca"),
        "running": ("#175cd3", "#eff8ff", "#b2ddff"),
        "skipped": ("#475467", "#f2f4f7", "#d0d5dd"),
    }
    foreground, background, border = colors.get(status, ("#b54708", "#fffaeb", "#fedf89"))
    return (
        f'<span style="display:inline-block;padding:4px 9px;border:1px solid {border};'
        f'border-radius:999px;color:{foreground};background:{background};font-size:11px;'
        f'font-weight:700;line-height:1.2;white-space:nowrap;">{_text(label)}</span>'
    )


def _section_heading(title: str, note: str = "") -> str:
    note_html = (
        f'<td align="right" style="color:#667085;font-size:12px;font-weight:600;">{_text(note)}</td>'
        if note
        else '<td></td>'
    )
    return (
        '<tr><td style="padding:28px 0 12px;">'
        '<table role="presentation" width="100%" cellspacing="0" cellpadding="0"><tr>'
        f'<td style="color:#101828;font-size:16px;font-weight:800;line-height:1.4;">{_text(title)}</td>'
        f'{note_html}</tr></table></td></tr>'
    )


def _metric(label: str, value: str, color: str, note: str = "") -> str:
    note_html = (
        f'<div style="margin-top:5px;color:#667085;font-size:11px;line-height:1.4;">{_text(note)}</div>'
        if note
        else ""
    )
    return (
        '<td class="metric-cell" width="33.33%" valign="top" style="padding:0 5px;">'
        '<div style="min-height:82px;padding:14px 15px;border:1px solid #e4e7ec;'
        'border-radius:6px;background:#ffffff;">'
        f'<div style="color:#667085;font-size:11px;font-weight:700;line-height:1.4;">{_text(label)}</div>'
        f'<div style="margin-top:7px;color:{color};font-size:22px;font-weight:800;line-height:1.15;">{_text(value)}</div>'
        f'{note_html}</div></td>'
    )


def _table_cell(content: str, *, align: str = "left", strong: bool = False, muted: bool = False) -> str:
    color = "#667085" if muted else "#344054"
    weight = "700" if strong else "500"
    return (
        f'<td align="{align}" style="padding:11px 10px;border-top:1px solid #eaecf0;'
        f'color:{color};font-size:12px;font-weight:{weight};line-height:1.5;">{content}</td>'
    )


def _table_header(label: str, *, align: str = "left") -> str:
    return (
        f'<th align="{align}" style="padding:9px 10px;color:#667085;background:#f9fafb;'
        f'font-size:11px;font-weight:700;line-height:1.4;white-space:nowrap;">{_text(label)}</th>'
    )


def _transfer_summary(settlement: dict[str, Any]) -> tuple[str, str, str]:
    partner_to_owner = float(settlement.get("partnerToOwner") or 0)
    owner_to_partner = float(settlement.get("ownerToPartner") or 0)
    if partner_to_owner > 0:
        return "社会哥 → 星星", _money(partner_to_owner), "社会哥转账给星星"
    if owner_to_partner > 0:
        return "星星 → 社会哥", _money(owner_to_partner), "星星转账给社会哥"
    return "无需互相转账", "0.00", "双方实际到账已符合结算结果"


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

    recovered_amount = float(job.get("costClearedAmount") or 0)
    if recovered_amount <= 0:
        recovered_amount = sum(float(item.get("costRecoveredAmount") or 0) for item in job.get("items") or [])

    cost_rows = []
    for addition in job.get("costHistory") or []:
        cost_rows.append(
            "<tr>"
            + _table_cell(_text(_display_time(addition.get("date"))))
            + _table_cell(f'{_money(addition.get("amount"))} 元', align="right", strong=True)
            + _table_cell(_text(addition.get("note") or "-"))
            + _table_cell(_text(_display_time(addition.get("createdAt") or addition.get("created_at"))), muted=True)
            + "</tr>"
        )
    if not cost_rows:
        cost_rows.append(
            '<tr><td colspan="4" align="center" style="padding:20px;color:#98a2b3;font-size:12px;">'
            "暂无成本历史明细</td></tr>"
        )

    account_rows = []
    for item in job.get("items") or []:
        status_label = item.get("statusLabel") or item.get("status") or "-"
        badge = _status_badge(str(item.get("status") or ""), str(status_label))
        reason = (
            f'<div style="margin-top:4px;color:#b42318;font-size:10px;line-height:1.4;">{_text(item.get("error"))}</div>'
            if item.get("error")
            else ""
        )
        recovery = item.get("costRecoveredAmount")
        recovery_label = f'{_money(recovery)} 元' if item.get("costRecoveredAt") else "-"
        account_rows.append(
            "<tr>"
            + _table_cell(_text(item.get("sequence")), align="center", strong=True)
            + _table_cell(
                f'<div style="font-weight:700;color:#101828;white-space:nowrap;">{_text(item.get("email"))}</div>'
                f'<div style="margin-top:2px;color:#667085;font-size:10px;">{_text(item.get("ownerLabel"))}</div>'
            )
            + _table_cell(_text("微信" if item.get("paymentMethod") == "wechat" else "支付宝"))
            + _table_cell(f'{float(item.get("amount") or 0):.0f} 元', align="right", strong=True)
            + _table_cell(_text(recovery_label), align="right", strong=bool(item.get("costRecoveredAt")))
            + _table_cell(badge + reason)
            + "</tr>"
        )

    cost_status = job.get("costSettlementStatus")
    if cost_status == "cleared":
        cost_note = (
            f"本任务已按账号提交结果逐笔冲减成本，累计回收 {_money(recovered_amount)} 元，"
            f"当前总成本为 {_money(post_cost)} 元。"
        )
    elif cost_status == "partial":
        cost_note = (
            f"本任务已逐笔回收 {_money(recovered_amount)} 元，尚余 {_money(post_cost)} 元成本继续保留。"
        )
    elif cost_status == "not_recovered":
        cost_note = "本次没有成功回收成本，原成本记录继续保留。"
    elif cost_status == "not_applicable":
        cost_note = "任务创建时没有待回收成本，本次未冲减成本。"
    else:
        cost_note = "每个账号提交成功后立即冲减对应成本，失败或未执行的金额不会扣减。"

    if job.get("mode") == "cost":
        formula_title = "成本回收口径"
        formula_html = (
            '<div style="padding:15px 16px;border:1px solid #a6f4c5;border-left:4px solid #12b76a;'
            'border-radius:6px;background:#ecfdf3;color:#05603a;font-size:12px;line-height:1.8;">'
            '<strong style="font-size:13px;">成本提现不参与 60% / 40% 利润分成</strong><br>'
            f'本次成本回收：{_money(settlement.get("costRecovery"))} 元&nbsp;&nbsp;·&nbsp;&nbsp;'
            f'尚未收回：{_money(settlement.get("unrecoveredCost"))} 元&nbsp;&nbsp;·&nbsp;&nbsp;'
            f'舍入余量：{_money(settlement.get("roundingRemainder"))} 元</div>'
        )
    elif float(settlement.get("profit") or 0) > 0:
        formula_title = "利润分配口径"
        formula_html = (
            '<div style="padding:15px 16px;border:1px solid #fedf89;border-left:4px solid #f79009;'
            'border-radius:6px;background:#fffaeb;color:#7a2e0e;font-size:12px;line-height:1.8;">'
            f'<strong style="font-size:13px;">可分配利润：{_money(settlement.get("profit"))} 元</strong><br>'
            f'星星应得 = {_money(settlement.get("cost"))} + {_money(settlement.get("profit"))} × 60% '
            f'= <strong>{_money(settlement.get("ownerExpected"))} 元</strong><br>'
            f'社会哥应得 = {_money(settlement.get("profit"))} × 40% '
            f'= <strong>{_money(settlement.get("partnerExpected"))} 元</strong></div>'
        )
    else:
        formula_title = "亏损处理口径"
        formula_html = (
            '<div style="padding:15px 16px;border:1px solid #fecdca;border-left:4px solid #f04438;'
            'border-radius:6px;background:#fef3f2;color:#912018;font-size:12px;line-height:1.8;">'
            '<strong style="font-size:13px;">本次无可分配利润</strong><br>'
            f'星星应得 {_money(settlement.get("ownerExpected"))} 元；社会哥应得 0.00 元；'
            f'亏损 {_money(settlement.get("unrecoveredCost"))} 元由星星承担。</div>'
        )

    submitted_times = [item.get("submittedAt") for item in job.get("items", []) if item.get("submittedAt")]
    first_time = _display_time(min(submitted_times)) if submitted_times else "-"
    last_time = _display_time(max(submitted_times)) if submitted_times else "-"
    pixel_status = (
        "已提交，等待平台结算"
        if job.get("status") == "completed"
        else "任务已暂停，已提交账号不会重复提交"
    )
    post_balance_label = f"{_money(post_balance)} 元" if post_balance is not None else "暂未获取"
    profit_label = f"{_money(discounted_profit)} 元" if discounted_profit is not None else "暂未计算"
    status_key = "completed" if job.get("status") == "completed" else str(job.get("status") or "")
    status_badge = _status_badge(status_key, status)
    transfer_direction, transfer_amount, transfer_note = _transfer_summary(settlement)

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>91 {_text(mode_label)}任务</title>
  <style>
    @media only screen and (max-width: 620px) {{
      .page-pad {{ padding: 12px 6px !important; }}
      .content-pad {{ padding-left: 16px !important; padding-right: 16px !important; }}
      .metric-cell {{ display: block !important; width: 100% !important; padding: 4px 0 !important; }}
      .mobile-block {{ display: block !important; width: 100% !important; }}
      .mobile-left {{ text-align: left !important; padding-top: 10px !important; }}
      .scroll-note {{ display: block !important; }}
      .hero-amount {{ font-size: 27px !important; }}
    }}
  </style>
</head>
<body style="margin:0;padding:0;background:#f2f4f7;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI','PingFang SC','Microsoft YaHei',Arial,sans-serif;">
<table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="background:#f2f4f7;">
<tr><td class="page-pad" align="center" style="padding:28px 10px;">
<table role="presentation" width="720" cellspacing="0" cellpadding="0" style="width:100%;max-width:720px;border:1px solid #d0d5dd;border-radius:8px;background:#ffffff;overflow:hidden;">
  <tr><td style="height:5px;background:#e5484d;font-size:0;line-height:0;">&nbsp;</td></tr>
  <tr><td class="content-pad" style="padding:25px 28px 23px;background:#171a1f;color:#ffffff;">
    <table role="presentation" width="100%" cellspacing="0" cellpadding="0"><tr>
      <td class="mobile-block" valign="top">
        <div style="color:#f97066;font-size:12px;font-weight:800;letter-spacing:0;">91 · 财务结算通知</div>
        <div style="margin-top:9px;font-size:24px;font-weight:800;line-height:1.25;">{_text(mode_label)}{_text(status)}</div>
        <div style="margin-top:9px;color:#d0d5dd;font-size:11px;line-height:1.6;">任务 {_text(job.get('jobId'))}</div>
      </td>
      <td class="mobile-block mobile-left" align="right" valign="top">
        <div>{status_badge}</div>
        <div style="margin-top:12px;color:#98a2b3;font-size:11px;font-weight:700;">实际提交</div>
        <div class="hero-amount" style="margin-top:3px;color:#ffffff;font-size:31px;font-weight:800;line-height:1.1;">{_money(settlement.get('gross'))} 元</div>
      </td>
    </tr></table>
  </td></tr>
  <tr><td class="content-pad" style="padding:14px 28px;background:#f9fafb;border-bottom:1px solid #eaecf0;">
    <table role="presentation" width="100%" cellspacing="0" cellpadding="0"><tr>
      <td class="mobile-block" style="color:#475467;font-size:11px;line-height:1.7;"><strong>提现模式：</strong>{_text(mode_label)}</td>
      <td class="mobile-block mobile-left" align="right" style="color:#475467;font-size:11px;line-height:1.7;"><strong>余额快照：</strong>{_text(_display_time(job.get('balanceSnapshotAt')))}</td>
    </tr></table>
  </td></tr>
  <tr><td class="content-pad" style="padding:22px 23px 30px;">
    <table role="presentation" width="100%" cellspacing="0" cellpadding="0">
      <tr><td><table role="presentation" width="100%" cellspacing="0" cellpadding="0"><tr>
        {_metric('任务总成本', f'{_money(settlement.get("cost"))} 元', '#b42318', '任务创建时冻结')}
        {_metric('计划提现', f'{_money(job.get("requestedAmount"))} 元', '#175cd3', '按整数金额提交')}
        {_metric('已回收成本', f'{_money(recovered_amount)} 元', '#067647', f'剩余 {_money(post_cost)} 元')}
      </tr></table></td></tr>

      {_section_heading('成本历史明细', f'快照合计 {_money(job.get("costHistoryTotal"))} 元')}
      <tr><td style="overflow-x:auto;">
        <table role="table" width="100%" cellspacing="0" cellpadding="0" style="border:1px solid #e4e7ec;border-radius:6px;border-collapse:separate;border-spacing:0;">
          <thead><tr>{_table_header('成本日期')}{_table_header('金额', align='right')}{_table_header('备注')}{_table_header('录入时间')}</tr></thead>
          <tbody>{''.join(cost_rows)}</tbody>
        </table>
        <div style="margin-top:10px;padding:10px 12px;border-radius:6px;background:#f0fdf4;color:#166534;font-size:11px;font-weight:600;line-height:1.6;">{_text(cost_note)}</div>
      </td></tr>

      {_section_heading(formula_title)}
      <tr><td>{formula_html}</td></tr>

      {_section_heading('账号提现明细', '按固定顺序串行提交')}
      <tr><td style="overflow-x:auto;">
        <table role="table" width="100%" cellspacing="0" cellpadding="0" style="border:1px solid #e4e7ec;border-radius:6px;border-collapse:separate;border-spacing:0;">
          <thead><tr>{_table_header('序号', align='center')}{_table_header('账号 / 归属')}{_table_header('方式')}{_table_header('提现', align='right')}{_table_header('冲减成本', align='right')}{_table_header('状态')}</tr></thead>
          <tbody>{''.join(account_rows)}</tbody>
        </table>
      </td></tr>

      {_section_heading('执行信息')}
      <tr><td style="padding:14px 16px;border:1px solid #e4e7ec;border-radius:6px;background:#f9fafb;">
        <table role="presentation" width="100%" cellspacing="0" cellpadding="0">
          <tr>
            <td class="mobile-block" width="50%" style="padding:4px 8px;color:#475467;font-size:11px;line-height:1.8;"><strong style="color:#101828;">第一笔</strong><br>{_text(first_time)}</td>
            <td class="mobile-block" width="50%" style="padding:4px 8px;color:#475467;font-size:11px;line-height:1.8;"><strong style="color:#101828;">最后一笔</strong><br>{_text(last_time)}</td>
          </tr>
          <tr>
            <td class="mobile-block" width="50%" style="padding:8px 8px 4px;color:#475467;font-size:11px;line-height:1.8;"><strong style="color:#101828;">任务状态</strong><br>{_text(status)}</td>
            <td class="mobile-block" width="50%" style="padding:8px 8px 4px;color:#475467;font-size:11px;line-height:1.8;"><strong style="color:#101828;">PixelAPI</strong><br>{_text(pixel_status)}</td>
          </tr>
        </table>
      </td></tr>

      {_section_heading('提现后汇总')}
      <tr><td><table role="presentation" width="100%" cellspacing="0" cellpadding="0"><tr>
        {_metric('提现后总成本', f'{_money(post_cost)} 元', '#b42318')}
        {_metric('提现后总余额', post_balance_label, '#067647')}
        {_metric('提现后折后利润', profit_label, '#175cd3', '总余额 - 总成本')}
      </tr></table></td></tr>

      <tr><td style="padding:32px 0 12px;">
        <div style="color:#e5484d;font-size:11px;font-weight:800;">最终结算 · 双方重点核对</div>
        <div style="margin-top:5px;color:#101828;font-size:20px;font-weight:800;line-height:1.35;">实际到账归属</div>
      </td></tr>
      <tr><td>
        <table role="presentation" width="100%" cellspacing="0" cellpadding="0"><tr>
          <td class="mobile-block" width="50%" valign="top" style="padding:0 5px 0 0;">
            <div style="padding:16px;border:1px solid #b2ddff;border-radius:6px;background:#eff8ff;">
              <div style="color:#175cd3;font-size:12px;font-weight:800;">星星账号</div>
              <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="margin-top:10px;">
                <tr><td style="padding:5px 0;color:#475467;font-size:11px;">实际收到</td><td align="right" style="padding:5px 0;color:#175cd3;font-size:18px;font-weight:800;">{_money(settlement.get('ownerActual'))} 元</td></tr>
                <tr><td style="padding:5px 0;color:#475467;font-size:11px;">最终应得</td><td align="right" style="padding:5px 0;color:#101828;font-size:14px;font-weight:800;">{_money(settlement.get('ownerExpected'))} 元</td></tr>
              </table>
            </div>
          </td>
          <td class="mobile-block" width="50%" valign="top" style="padding:0 0 0 5px;">
            <div style="padding:16px;border:1px solid #fedf89;border-radius:6px;background:#fffaeb;">
              <div style="color:#b54708;font-size:12px;font-weight:800;">社会哥账号</div>
              <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="margin-top:10px;">
                <tr><td style="padding:5px 0;color:#475467;font-size:11px;">实际收到</td><td align="right" style="padding:5px 0;color:#b54708;font-size:18px;font-weight:800;">{_money(settlement.get('partnerActual'))} 元</td></tr>
                <tr><td style="padding:5px 0;color:#475467;font-size:11px;">最终应得</td><td align="right" style="padding:5px 0;color:#101828;font-size:14px;font-weight:800;">{_money(settlement.get('partnerExpected'))} 元</td></tr>
              </table>
            </div>
          </td>
        </tr></table>
      </td></tr>
      <tr><td style="padding-top:10px;">
        <div style="padding:19px 20px;border-radius:6px;background:#171a1f;color:#ffffff;">
          <table role="presentation" width="100%" cellspacing="0" cellpadding="0"><tr>
            <td class="mobile-block" valign="middle">
              <div style="color:#f97066;font-size:11px;font-weight:800;">结算转账 · 最终需要执行</div>
              <div style="margin-top:5px;color:#ffffff;font-size:18px;font-weight:800;">{_text(transfer_direction)}</div>
              <div style="margin-top:4px;color:#98a2b3;font-size:10px;">{_text(transfer_note)}</div>
            </td>
            <td class="mobile-block mobile-left" align="right" valign="middle" style="color:#ffffff;font-size:29px;font-weight:800;white-space:nowrap;">{_text(transfer_amount)} 元</td>
          </tr></table>
          <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="margin-top:15px;border-top:1px solid #344054;">
            <tr><td style="padding:11px 0 2px;color:#d0d5dd;font-size:11px;">社会哥需要转给星星</td><td align="right" style="padding:11px 0 2px;color:#ffffff;font-size:13px;font-weight:800;">{_money(settlement.get('partnerToOwner'))} 元</td></tr>
            <tr><td style="padding:5px 0;color:#d0d5dd;font-size:11px;">星星需要转给社会哥</td><td align="right" style="padding:5px 0;color:#ffffff;font-size:13px;font-weight:800;">{_money(settlement.get('ownerToPartner'))} 元</td></tr>
          </table>
        </div>
      </td></tr>
    </table>
  </td></tr>
  <tr><td class="content-pad" align="center" style="padding:17px 28px;border-top:1px solid #eaecf0;background:#f9fafb;color:#98a2b3;font-size:10px;line-height:1.6;">
    本邮件由 91 自动生成并发送给双方，仅记录本次任务的提交与结算结果。
  </td></tr>
</table>
</td></tr></table>
</body></html>"""
