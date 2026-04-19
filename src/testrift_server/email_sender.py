"""
Email sender for TestRift.

Sends analysis summary emails and budget warning emails via SMTP.
"""

import asyncio
import json
import logging
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Optional

from .config import EMAIL_CONFIG
from .utils import read_meta_msgpack
from . import database

logger = logging.getLogger(__name__)


async def send_analysis_email(run_id: str, recipients: list[str]):
    """Send an email summarizing AI analysis results for a run."""
    meta = read_meta_msgpack(run_id)
    if not meta:
        logger.warning(f"Cannot send email: run {run_id} metadata not found")
        return

    analyses = await database.db.get_analyses_for_run(run_id)
    if not analyses:
        logger.info(f"No analyses to email for run {run_id}")
        return

    run_name = meta.get("run_name", run_id)
    status = meta.get("status", "unknown")
    failed_count = len(analyses)

    # Count test case totals from meta
    test_cases = meta.get("test_cases", {})
    passed = sum(1 for tc in test_cases.values() if tc.get("status") == "passed")
    failed = sum(1 for tc in test_cases.values() if tc.get("status") in ("failed", "error"))
    skipped = sum(1 for tc in test_cases.values() if tc.get("status") == "skipped")

    subject = f"TestRift: {run_name} — {failed_count} failures analyzed"

    html_body = _build_email_html(
        run_id=run_id,
        run_name=run_name,
        status=status,
        passed=passed,
        failed=failed,
        skipped=skipped,
        analyses=analyses,
    )

    await _send_smtp(subject, html_body, recipients)


async def send_budget_warning_email(usage: dict, config: dict):
    """Send a budget warning email."""
    recipients = EMAIL_CONFIG.get("to_addresses", [])
    if not recipients:
        db_recipients = await database.db.get_setting("email_recipients")
        if db_recipients:
            try:
                recipients = json.loads(db_recipients)
            except json.JSONDecodeError:
                pass
    if not recipients:
        return

    month = usage.get("month", "unknown")
    cost = usage.get("estimated_cost_usd", 0)
    budget = config.get("monthly_budget_usd", 0)

    subject = f"TestRift: AI budget warning — {cost:.2f}/{budget:.2f} USD ({month})"
    html_body = f"""
    <html><body>
    <h2>TestRift AI Budget Warning</h2>
    <p>The AI analysis budget for <strong>{month}</strong> is approaching its limit.</p>
    <table>
        <tr><td>Current spend:</td><td><strong>${cost:.2f}</strong></td></tr>
        <tr><td>Monthly budget:</td><td><strong>${budget:.2f}</strong></td></tr>
        <tr><td>Utilization:</td><td><strong>{cost/budget*100:.0f}%</strong></td></tr>
    </table>
    <p>Analysis will be paused once the budget is fully consumed.</p>
    </body></html>
    """

    await _send_smtp(subject, html_body, recipients)


def _build_email_html(run_id: str, run_name: str, status: str,
                       passed: int, failed: int, skipped: int,
                       analyses: list[dict]) -> str:
    """Build the HTML body for the analysis summary email."""
    rows = []
    for a in analyses:
        tc_name = a.get("tc_full_name", "?")
        tc_id = a.get("tc_id")
        summary_html = a.get("summary_html", "")
        summary = summary_html if summary_html else a.get("summary", "")
        category = a.get("category", "unknown")
        confidence = a.get("confidence", 0)

        # Make TC name a clickable link
        if tc_id:
            tc_display = f'<a href="/testRun/{run_id}/log/{tc_id}.html"><strong>{tc_name}</strong></a>'
        else:
            tc_display = f'<strong>{tc_name}</strong>'

        # Build reference links
        ref_links = []
        refs_json = a.get("references_json", "[]")
        try:
            refs = json.loads(refs_json) if refs_json else []
        except json.JSONDecodeError:
            refs = []

        for ref in refs:
            ref_type = ref.get("type", "")
            url = ref.get("url")
            if ref_type == "commit" and url:
                sha_short = ref.get("sha", "")[:7]
                repo = ref.get("repo", "")
                ref_links.append(f'<a href="{url}">{sha_short} — {repo}</a>')
            elif ref_type == "log_line" and url:
                msg = ref.get("message", "")[:60]
                ref_links.append(f'<a href="{url}">{msg}</a>')

        refs_html = "<br>".join(ref_links) if ref_links else ""

        rows.append(f"""
        <tr>
            <td style="padding:8px;border-bottom:1px solid #eee;">{tc_display}</td>
            <td style="padding:8px;border-bottom:1px solid #eee;">{summary}</td>
            <td style="padding:8px;border-bottom:1px solid #eee;">{category}</td>
            <td style="padding:8px;border-bottom:1px solid #eee;">{confidence:.0%}</td>
            <td style="padding:8px;border-bottom:1px solid #eee;">{refs_html}</td>
        </tr>
        """)

    table_rows = "\n".join(rows)

    return f"""
    <html><body style="font-family: Arial, sans-serif;">
    <h2>Test Run: {run_name}</h2>
    <p>
        Status: <strong>{status}</strong> |
        Results: <span style="color:green">{passed} passed</span>,
        <span style="color:red">{failed} failed</span>,
        <span style="color:gray">{skipped} skipped</span>
    </p>
    <p><a href="/testRun/{run_id}/index.html">View run</a></p>

    <h3>AI Failure Summary</h3>
    <table style="border-collapse:collapse;width:100%;">
        <tr style="background:#f5f5f5;">
            <th style="padding:8px;text-align:left;">Test Case</th>
            <th style="padding:8px;text-align:left;">Summary</th>
            <th style="padding:8px;text-align:left;">Category</th>
            <th style="padding:8px;text-align:left;">Confidence</th>
            <th style="padding:8px;text-align:left;">References</th>
        </tr>
        {table_rows}
    </table>
    </body></html>
    """


async def _send_smtp(subject: str, html_body: str, recipients: list[str]):
    """Send an email via SMTP. Runs blocking SMTP in threadpool."""
    config = EMAIL_CONFIG

    if not config.get("enabled", False):
        logger.info("Email sending disabled in config")
        return

    smtp_host = config.get("smtp_host", "")
    smtp_port = config.get("smtp_port", 587)
    from_addr = config.get("from_address", "")

    if not smtp_host or not from_addr:
        logger.warning("SMTP not configured (missing host or from_address)")
        return

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = from_addr
    msg["To"] = ", ".join(recipients)
    msg.attach(MIMEText(html_body, "html"))

    def _do_send():
        try:
            if config.get("smtp_tls", True):
                server = smtplib.SMTP(smtp_host, smtp_port)
                server.starttls()
            else:
                server = smtplib.SMTP(smtp_host, smtp_port)

            smtp_user = config.get("smtp_user", "")
            smtp_pass = config.get("smtp_password", "")
            if smtp_user and smtp_pass:
                server.login(smtp_user, smtp_pass)

            server.sendmail(from_addr, recipients, msg.as_string())
            server.quit()
            logger.info(f"Email sent to {recipients}: {subject}")
        except Exception as e:
            logger.error(f"Failed to send email: {e}")

    # Run SMTP in threadpool to avoid blocking event loop
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, _do_send)
