"""
Reusable Data Validation Module.

An independent, standalone validation engine for datasets.
Features:
- Configurable default validation checks (every check can be toggled or configured).
- Required vs. optional field validation (distinguishing errors from missing optional data).
- Primary key duplicate detection.
- Data type checking.
- URL format validation.
- Empty or whitespace-only value detection.
- Numeric range bounds validation.
- Regular expression pattern matching.
- UTF-8 and special character preservation & integrity checks.
- Cross-field record consistency checking with custom validator hooks.
- Produces exactly three report artifacts:
  1. One Markdown report (.md)
  2. One PDF report (.pdf) converted via Playwright (with LibreOffice fallback)
  3. One JSON report (.json) formatted for downstream consumption (e.g., cleaners)
- Completely independent of any cleaner scripts or downstream pipelines.
"""

import asyncio
import datetime
from enum import Enum
import html
import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
from typing import Any, Callable, Dict, Iterable, List, Optional, Pattern, Set, Tuple, Union
from urllib.parse import urlparse

logger = logging.getLogger("validator")


class Severity(str, Enum):
    """Validation issue severity levels."""
    ERROR = "ERROR"        # Critical failure: record is invalid or unrecoverable
    WARNING = "WARNING"    # Potential anomaly or minor inconsistency
    INFO = "INFO"          # Informational notice (e.g., missing optional data)


class ValidationIssue:
    """Represents a single validation issue on a record."""

    def __init__(
        self,
        record_id: Any,
        field: Optional[str],
        code: str,
        message: str,
        severity: Severity = Severity.ERROR,
        actual_value: Any = None,
        index: Optional[int] = None
    ):
        self.record_id = record_id
        self.field = field
        self.code = code
        self.message = message
        self.severity = severity
        self.actual_value = actual_value
        self.index = index

    def to_dict(self) -> Dict[str, Any]:
        """Convert issue to a JSON-serializable dictionary."""
        val_repr = self.actual_value
        if isinstance(val_repr, (datetime.datetime, datetime.date)):
            val_repr = val_repr.isoformat()
        elif not isinstance(val_repr, (str, int, float, bool, list, dict, type(None))):
            val_repr = str(val_repr)

        return {
            "record_id": str(self.record_id) if self.record_id is not None else None,
            "index": self.index,
            "field": self.field,
            "code": self.code,
            "severity": self.severity.value,
            "message": self.message,
            "actual_value": val_repr,
        }

    def __repr__(self) -> str:
        return (
            f"<ValidationIssue [{self.severity.value}] record_id={self.record_id} "
            f"field={self.field} code={self.code}: {self.message}>"
        )


class FieldRule:
    """
    Validation specification for an individual field.
    """

    def __init__(
        self,
        name: str,
        required: bool = False,
        expected_type: Optional[Union[type, Tuple[type, ...]]] = None,
        allow_none: bool = True,
        allow_empty_string: bool = True,
        is_url: bool = False,
        allowed_url_schemes: Tuple[str, ...] = ("http", "https"),
        regex: Optional[Union[str, Pattern]] = None,
        min_value: Optional[Union[int, float]] = None,
        max_value: Optional[Union[int, float]] = None,
        custom_validator: Optional[Callable[[Any, Dict[str, Any]], Optional[str]]] = None
    ):
        self.name = name
        self.required = required
        self.expected_type = expected_type
        self.allow_none = allow_none if not required else False
        self.allow_empty_string = allow_empty_string
        self.is_url = is_url
        self.allowed_url_schemes = allowed_url_schemes
        self.regex = re.compile(regex) if isinstance(regex, str) else regex
        self.min_value = min_value
        self.max_value = max_value
        self.custom_validator = custom_validator


class ValidationChecks:
    """
    Configurable flags controlling which validation checks are performed.
    Every check has a sensible default and can be independently enabled or disabled.
    """

    def __init__(
        self,
        check_required_fields: bool = True,
        check_optional_fields: bool = True,
        check_duplicate_keys: bool = True,
        check_data_types: bool = True,
        check_url_formats: bool = True,
        check_empty_values: bool = True,
        check_numeric_ranges: bool = True,
        check_regex_patterns: bool = True,
        check_special_chars: bool = True,
        check_custom_rules: bool = True,
        check_record_consistency: bool = True,
        check_unknown_fields: bool = False,
    ):
        self.check_required_fields = check_required_fields
        self.check_optional_fields = check_optional_fields
        self.check_duplicate_keys = check_duplicate_keys
        self.check_data_types = check_data_types
        self.check_url_formats = check_url_formats
        self.check_empty_values = check_empty_values
        self.check_numeric_ranges = check_numeric_ranges
        self.check_regex_patterns = check_regex_patterns
        self.check_special_chars = check_special_chars
        self.check_custom_rules = check_custom_rules
        self.check_record_consistency = check_record_consistency
        self.check_unknown_fields = check_unknown_fields

    def to_dict(self) -> Dict[str, bool]:
        """Return dict of current check configurations."""
        return {
            "check_required_fields": self.check_required_fields,
            "check_optional_fields": self.check_optional_fields,
            "check_duplicate_keys": self.check_duplicate_keys,
            "check_data_types": self.check_data_types,
            "check_url_formats": self.check_url_formats,
            "check_empty_values": self.check_empty_values,
            "check_numeric_ranges": self.check_numeric_ranges,
            "check_regex_patterns": self.check_regex_patterns,
            "check_special_chars": self.check_special_chars,
            "check_custom_rules": self.check_custom_rules,
            "check_record_consistency": self.check_record_consistency,
            "check_unknown_fields": self.check_unknown_fields,
        }

    def set_check(self, check_name: str, enabled: bool) -> None:
        """Enable or disable a specific check by name."""
        target_name = check_name if hasattr(self, check_name) else f"check_{check_name}"
        if hasattr(self, target_name):
            setattr(self, target_name, bool(enabled))
        else:
            raise KeyError(f"Unknown validation check flag: '{check_name}'")


class ValidationConfig:
    """
    Validation configuration including primary key, field rules,
    custom record validators, and check toggles.
    """

    def __init__(
        self,
        primary_key: Optional[str] = "id",
        field_rules: Optional[Dict[str, FieldRule]] = None,
        record_validators: Optional[List[Callable[[Dict[str, Any], int], List[ValidationIssue]]]] = None,
        checks: Optional[Union[ValidationChecks, Dict[str, bool]]] = None,
        fail_fast: bool = False,
        allow_unknown_fields: Optional[bool] = None,
        preserve_special_chars: Optional[bool] = None,
        **kwargs: Any
    ):
        self.primary_key = primary_key
        self.field_rules: Dict[str, FieldRule] = field_rules or {}
        self.record_validators: List[Callable[[Dict[str, Any], int], List[ValidationIssue]]] = record_validators or []
        self.fail_fast = fail_fast

        if isinstance(checks, ValidationChecks):
            self.checks = checks
        elif isinstance(checks, dict):
            self.checks = ValidationChecks(**checks)
        else:
            self.checks = ValidationChecks()

        if allow_unknown_fields is not None:
            self.checks.check_unknown_fields = not allow_unknown_fields
        if preserve_special_chars is not None:
            self.checks.check_special_chars = preserve_special_chars

    def add_field_rule(self, rule: FieldRule) -> "ValidationConfig":
        """Add or update an individual field rule."""
        self.field_rules[rule.name] = rule
        return self

    def add_record_validator(
        self, validator_fn: Callable[[Dict[str, Any], int], List[ValidationIssue]]
    ) -> "ValidationConfig":
        """Add a custom cross-field or whole-record validator function."""
        self.record_validators.append(validator_fn)
        return self

    def enable_check(self, check_name: str) -> "ValidationConfig":
        """Enable a check flag."""
        self.checks.set_check(check_name, True)
        return self

    def disable_check(self, check_name: str) -> "ValidationConfig":
        """Disable a check flag."""
        self.checks.set_check(check_name, False)
        return self

    def set_required(self, field_name: str, required: bool = True) -> "ValidationConfig":
        """
        Change a field's requirement status between required and optional.
        Creates a rule if the field does not yet exist.
        """
        if field_name in self.field_rules:
            rule = self.field_rules[field_name]
            rule.required = required
            rule.allow_none = not required
        else:
            self.field_rules[field_name] = FieldRule(name=field_name, required=required, allow_none=not required)
        return self

    def make_required(self, field_name: str) -> "ValidationConfig":
        """Mark a field as required (missing values will trigger ERROR)."""
        return self.set_required(field_name, required=True)

    def make_optional(self, field_name: str) -> "ValidationConfig":
        """Mark a field as optional (missing values will be recorded as INFO)."""
        return self.set_required(field_name, required=False)


URL_PATTERN = re.compile(
    r"^(?:http|https)://"
    r"(?:(?:[A-Z0-9](?:[A-Z0-9-]{0,61}[A-Z0-9])?\.)+[A-Z]{2,63}|"
    r"localhost|"
    r"\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})"
    r"(?::\d+)?"
    r"(?:/?|[/?]\S+)$",
    re.IGNORECASE,
)


def is_valid_url(url: Any, allowed_schemes: Tuple[str, ...] = ("http", "https")) -> bool:
    """Check whether a value is a valid HTTP/HTTPS URL string."""
    if not isinstance(url, str):
        return False
    val = url.strip()
    if not val or " " in val:
        return False
    try:
        parsed = urlparse(val)
        if parsed.scheme.lower() not in allowed_schemes:
            return False
        if not parsed.netloc:
            return False
        return bool(URL_PATTERN.match(val))
    except Exception:
        return False


def _convert_markdown_to_pdf(markdown_text: str, output_pdf_path: str, report_title: str = "Data Validation Report") -> bool:
    """
    Convert Markdown report to a styled PDF using Playwright (with LibreOffice fallback).
    """
    html_content = _markdown_to_styled_html(markdown_text, report_title)

    try:
        from playwright.async_api import async_playwright

        async def _render():
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                page = await browser.new_page()
                await page.set_content(html_content, wait_until="networkidle")
                await page.pdf(
                    path=output_pdf_path,
                    format="A4",
                    print_background=True,
                    margin={"top": "20mm", "bottom": "20mm", "left": "15mm", "right": "15mm"}
                )
                await browser.close()

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop and loop.is_running():
            try:
                import nest_asyncio
                nest_asyncio.apply()
                loop.run_until_complete(_render())
            except Exception:
                asyncio.run(_render())
        else:
            asyncio.run(_render())

        if os.path.exists(output_pdf_path) and os.path.getsize(output_pdf_path) > 0:
            logger.info("PDF report successfully created via Playwright: %s", output_pdf_path)
            return True
    except Exception as exc:
        logger.warning("Playwright PDF generation encountered issue: %s. Attempting fallback...", exc)

    libreoffice_bin = shutil.which("libreoffice") or shutil.which("soffice")
    if libreoffice_bin:
        with tempfile.NamedTemporaryFile("w+", suffix=".html", delete=False, encoding="utf-8") as tmp_html:
            tmp_html.write(html_content)
            tmp_html_path = tmp_html.name

        try:
            out_dir = os.path.dirname(os.path.abspath(output_pdf_path))
            cmd = [
                libreoffice_bin,
                "--headless",
                "--convert-to", "pdf",
                "--outdir", out_dir,
                tmp_html_path
            ]
            subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
            generated_pdf = os.path.splitext(tmp_html_path)[0] + ".pdf"
            if os.path.exists(generated_pdf):
                shutil.move(generated_pdf, output_pdf_path)
                logger.info("PDF report created via LibreOffice fallback: %s", output_pdf_path)
                return True
        except Exception as lo_exc:
            logger.error("LibreOffice fallback conversion failed: %s", lo_exc)
        finally:
            if os.path.exists(tmp_html_path):
                os.remove(tmp_html_path)

    logger.error("Unable to generate PDF report: neither Playwright nor LibreOffice succeeded.")
    return False


def _markdown_to_styled_html(markdown_text: str, report_title: str) -> str:
    """
    Convert validation markdown report into standalone, beautifully styled HTML
    for PDF rendering.
    """
    lines = markdown_text.splitlines()
    body_html_parts = []
    in_table = False
    in_code_block = False

    for line in lines:
        stripped = line.strip()

        if stripped.startswith("```"):
            if in_code_block:
                body_html_parts.append("</pre>")
                in_code_block = False
            else:
                body_html_parts.append("<pre>")
                in_code_block = True
            continue

        if in_code_block:
            body_html_parts.append(html.escape(line) + "\n")
            continue

        if "|" in line and (line.startswith("|") or line.endswith("|")):
            cells = [c.strip() for c in line.split("|")[1:-1]]
            if all(set(c).issubset({"-", ":", " "}) for c in cells):
                continue
            if not in_table:
                body_html_parts.append("<table>\n<thead>\n<tr>")
                for cell in cells:
                    body_html_parts.append(f"<th>{_format_inline(cell)}</th>")
                body_html_parts.append("</tr>\n</thead>\n<tbody>")
                in_table = True
            else:
                body_html_parts.append("<tr>")
                for cell in cells:
                    body_html_parts.append(f"<td>{_format_inline(cell)}</td>")
                body_html_parts.append("</tr>")
            continue
        else:
            if in_table:
                body_html_parts.append("</tbody>\n</table>\n")
                in_table = False

        if not stripped:
            continue

        if stripped.startswith("# "):
            body_html_parts.append(f"<h1>{_format_inline(stripped[2:])}</h1>")
        elif stripped.startswith("## "):
            body_html_parts.append(f"<h2>{_format_inline(stripped[3:])}</h2>")
        elif stripped.startswith("### "):
            body_html_parts.append(f"<h3>{_format_inline(stripped[4:])}</h3>")
        elif stripped.startswith("- "):
            body_html_parts.append(f"<li>{_format_inline(stripped[2:])}</li>")
        elif stripped.startswith("> "):
            body_html_parts.append(f"<blockquote>{_format_inline(stripped[2:])}</blockquote>")
        elif stripped.startswith("---"):
            body_html_parts.append("<hr/>")
        else:
            body_html_parts.append(f"<p>{_format_inline(stripped)}</p>")

    if in_table:
        body_html_parts.append("</tbody>\n</table>\n")

    body_html = "\n".join(body_html_parts)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>{html.escape(report_title)}</title>
<style>
  @page {{
    size: A4;
    margin: 18mm 15mm;
    @bottom-right {{
      content: "Page " counter(page);
    }}
  }}
  body {{
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
    color: #1f2328;
    line-height: 1.5;
    font-size: 13px;
    margin: 0;
    padding: 0;
  }}
  h1 {{
    font-size: 22px;
    color: #0969da;
    border-bottom: 2px solid #d0d7de;
    padding-bottom: 6px;
    margin-top: 0;
  }}
  h2 {{
    font-size: 16px;
    color: #24292f;
    border-bottom: 1px solid #d0d7de;
    padding-bottom: 4px;
    margin-top: 18px;
  }}
  h3 {{
    font-size: 14px;
    color: #57606a;
    margin-top: 14px;
  }}
  p, li {{
    margin: 4px 0;
  }}
  table {{
    width: 100%;
    border-collapse: collapse;
    margin: 12px 0;
    font-size: 12px;
    page-break-inside: auto;
  }}
  tr {{
    page-break-inside: avoid;
    page-break-after: auto;
  }}
  th, td {{
    border: 1px solid #d0d7de;
    padding: 6px 10px;
    text-align: left;
    vertical-align: top;
  }}
  th {{
    background-color: #f6f8fa;
    font-weight: 600;
  }}
  .badge-error {{
    display: inline-block;
    background-color: #ffebe9;
    color: #cf222e;
    font-weight: 700;
    padding: 2px 7px;
    border-radius: 4px;
    border: 1px solid #ff8182;
    font-size: 11px;
  }}
  .badge-warning {{
    display: inline-block;
    background-color: #fff8c5;
    color: #9a6700;
    font-weight: 700;
    padding: 2px 7px;
    border-radius: 4px;
    border: 1px solid #d4a72c;
    font-size: 11px;
  }}
  .badge-info {{
    display: inline-block;
    background-color: #ddf4ff;
    color: #0969da;
    font-weight: 600;
    padding: 2px 7px;
    border-radius: 4px;
    border: 1px solid #54aeff;
    font-size: 11px;
  }}
  code {{
    font-family: ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, monospace;
    background-color: #eff1f3;
    padding: 2px 5px;
    border-radius: 4px;
    font-size: 11px;
  }}
  hr {{
    border: none;
    border-top: 1px solid #d0d7de;
    margin: 16px 0;
  }}
  blockquote {{
    border-left: 4px solid #0969da;
    padding-left: 12px;
    margin: 8px 0;
    color: #57606a;
    background: #f6f8fa;
  }}
</style>
</head>
<body>
{body_html}
</body>
</html>
"""


def _format_inline(text: str) -> str:
    """Format inline markdown elements: bold, code, badges."""
    t = html.escape(text)
    t = re.sub(r"\bERROR\b", '<span class="badge-error">ERROR</span>', t)
    t = re.sub(r"\bWARNING\b", '<span class="badge-warning">WARNING</span>', t)
    t = re.sub(r"\bINFO\b", '<span class="badge-info">INFO</span>', t)
    t = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", t)
    t = re.sub(r"`(.+?)`", r"<code>\1</code>", t)
    return t


class ValidationReport:
    """
    Encapsulates the full result of a validation run.
    Responsible for generating exactly three independent report formats:
    1. One Markdown report (.md)
    2. One PDF report (.pdf)
    3. One JSON report (.json)
    """

    def __init__(
        self,
        total_records: int,
        issues: List[ValidationIssue],
        checks_config: Optional[Dict[str, bool]] = None,
        report_timestamp: Optional[str] = None
    ):
        self.total_records = total_records
        self.issues = issues
        self.checks_config = checks_config or {}
        self.report_timestamp = report_timestamp or datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        self.errors = [i for i in issues if i.severity == Severity.ERROR]
        self.warnings = [i for i in issues if i.severity == Severity.WARNING]
        self.infos = [i for i in issues if i.severity == Severity.INFO]

        self.error_record_ids: Set[Any] = {i.record_id for i in self.errors if i.record_id is not None}
        self.warning_record_ids: Set[Any] = {
            i.record_id for i in self.warnings if i.record_id is not None and i.record_id not in self.error_record_ids
        }
        self.info_record_ids: Set[Any] = {
            i.record_id
            for i in self.infos
            if i.record_id is not None and i.record_id not in self.error_record_ids and i.record_id not in self.warning_record_ids
        }

        self.invalid_records_count = len(self.error_record_ids)
        self.valid_records_count = max(0, self.total_records - self.invalid_records_count)

    @property
    def is_valid(self) -> bool:
        """True if there are zero ERROR-level issues."""
        return len(self.errors) == 0

    def get_summary_dict(self) -> Dict[str, Any]:
        """Generate high-level summary statistics dictionary."""
        code_counts: Dict[str, int] = {}
        field_counts: Dict[str, int] = {}
        for issue in self.issues:
            code_counts[issue.code] = code_counts.get(issue.code, 0) + 1
            fld = issue.field or "_general_"
            field_counts[fld] = field_counts.get(fld, 0) + 1

        return {
            "timestamp": self.report_timestamp,
            "total_records": self.total_records,
            "valid_records": self.valid_records_count,
            "invalid_records": self.invalid_records_count,
            "valid_records_percentage": round(self.valid_records_count / max(1, self.total_records) * 100, 2),
            "records_with_warnings": len(self.warning_record_ids),
            "records_with_info": len(self.info_record_ids),
            "total_issues": len(self.issues),
            "issues_by_severity": {
                "ERROR": len(self.errors),
                "WARNING": len(self.warnings),
                "INFO": len(self.infos),
            },
            "issue_counts_by_code": code_counts,
            "issue_counts_by_field": field_counts,
            "active_checks": self.checks_config,
        }

    def to_dict(self) -> Dict[str, Any]:
        """Generate a complete dictionary representation for JSON export."""
        return {
            "summary": self.get_summary_dict(),
            "issues": [issue.to_dict() for issue in self.issues],
        }

    def save_json(self, filepath: str, indent: int = 2) -> None:
        """Save structured JSON report file for downstream consumption (e.g., cleaners)."""
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, indent=indent, ensure_ascii=False)
        logger.info("JSON validation report saved: %s", filepath)

    def generate_markdown_report(self) -> str:
        """
        Generate full GitHub-flavored Markdown text report.
        """
        summary = self.get_summary_dict()
        md_lines = [
            f"# Data Validation Report",
            f"",
            f"> **Generated at**: `{summary['timestamp']}`  ",
            f"> **Status**: **{'PASSED (Clean Dataset)' if self.is_valid else 'ERRORS DETECTED (Review Required)'}**",
            f"",
            f"---",
            f"",
            f"## Executive Summary",
            f"",
            f"| Metric | Count | Percentage |",
            f"| :--- | :--- | :--- |",
            f"| **Total Records Inspected** | `{summary['total_records']:,}` | `100.0%` |",
            f"| **Valid Records (Ready for Use)** | `{summary['valid_records']:,}` | `{summary['valid_records_percentage']}%` |",
            f"| **Invalid Records (Fatal Errors)** | `{summary['invalid_records']:,}` | `{round(summary['invalid_records'] / max(1, summary['total_records']) * 100, 2)}%` |",
            f"| **Records with Non-Fatal Warnings** | `{summary['records_with_warnings']:,}` | `{round(summary['records_with_warnings'] / max(1, summary['total_records']) * 100, 2)}%` |",
            f"| **Records with Missing Optional Data (INFO)** | `{summary['records_with_info']:,}` | `{round(summary['records_with_info'] / max(1, summary['total_records']) * 100, 2)}%` |",
            f"",
            f"### Issues by Severity",
            f"",
            f"| Severity | Issues Count | Description |",
            f"| :--- | :--- | :--- |",
            f"| **ERROR** | `{summary['issues_by_severity']['ERROR']}` | Critical schema violations, invalid primary keys, type errors, or malformed data |",
            f"| **WARNING** | `{summary['issues_by_severity']['WARNING']}` | Non-fatal anomalies (e.g. unknown fields or date sequencing cautions) |",
            f"| **INFO** | `{summary['issues_by_severity']['INFO']}` | Missing optional fields and expected nullable attributes (distinguished from errors) |",
            f"",
            f"---",
            f"",
            f"## Issue Breakdown by Code",
            f"",
            f"| Issue Code | Count | Severity |",
            f"| :--- | :--- | :--- |",
        ]

        code_sev_map: Dict[str, str] = {}
        for issue in self.issues:
            if issue.code not in code_sev_map:
                code_sev_map[issue.code] = issue.severity.value

        for code, count in sorted(summary["issue_counts_by_code"].items(), key=lambda x: -x[1]):
            sev_badge = code_sev_map.get(code, "ERROR")
            md_lines.append(f"| `{code}` | `{count:,}` | **{sev_badge}** |")

        md_lines.extend([
            f"",
            f"---",
            f"",
            f"## Detailed Issues Log",
            f"",
            f"| Index | Record ID | Field | Severity | Code | Message |",
            f"| :--- | :--- | :--- | :--- | :--- | :--- |",
        ])

        if not self.issues:
            md_lines.append(f"| - | - | - | **INFO** | `NONE` | Zero issues found across all records. |")
        else:
            display_limit = 100
            for idx, issue in enumerate(self.issues[:display_limit], start=1):
                rec_id = str(issue.record_id) if issue.record_id is not None else "<none>"
                fld = f"`{issue.field}`" if issue.field else "_record_"
                safe_msg = issue.message.replace("|", "/")
                md_lines.append(
                    f"| {issue.index if issue.index is not None else idx} "
                    f"| `{rec_id}` | {fld} | **{issue.severity.value}** "
                    f"| `{issue.code}` | {safe_msg} |"
                )

            if len(self.issues) > display_limit:
                md_lines.append(f"| ... | ... | ... | ... | ... | Showing first {display_limit} of {len(self.issues)} issues. See JSON report for full log. |")

        md_lines.append("")
        return "\n".join(md_lines)

    def generate_text_report(self) -> str:
        """Alias for generate_markdown_report for backward compatibility."""
        return self.generate_markdown_report()

    def save_markdown(self, filepath: str) -> None:
        """Save the single Markdown report file (.md)."""
        content = self.generate_markdown_report()
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(content)
        logger.info("Markdown validation report saved: %s", filepath)

    def save_pdf(self, filepath: str) -> bool:
        """
        Save the single PDF report file (.pdf), rendered and styled from the report.
        """
        md_text = self.generate_markdown_report()
        success = _convert_markdown_to_pdf(md_text, filepath, report_title="Data Validation Report")
        return success

    def generate_reports(
        self,
        base_name: str = "validation_report",
        md_path: Optional[str] = None,
        pdf_path: Optional[str] = None,
        json_path: Optional[str] = None
    ) -> Dict[str, str]:
        """
        Generate exactly three validation reports:
        1. One Markdown file (.md)
        2. One PDF file (.pdf)
        3. One JSON file (.json)

        Returns dict mapping report types to their file paths.
        """
        target_md = md_path or f"{base_name}.md"
        target_pdf = pdf_path or f"{base_name}.pdf"
        target_json = json_path or f"{base_name}.json"

        self.save_markdown(target_md)
        self.save_pdf(target_pdf)
        self.save_json(target_json)

        return {
            "markdown": target_md,
            "pdf": target_pdf,
            "json": target_json,
        }


class Validator:
    """
    Independent validation engine.
    Completely decoupled from cleaner logic.
    """

    def __init__(self, config: Optional[ValidationConfig] = None):
        self.config = config or ValidationConfig()

    def validate_record(
        self,
        record: Dict[str, Any],
        index: int,
        seen_keys: Set[Any]
    ) -> List[ValidationIssue]:
        """
        Validate an individual record dictionary according to configured rules and check toggles.
        """
        issues: List[ValidationIssue] = []
        checks = self.config.checks
        pk_field = self.config.primary_key
        record_id = record.get(pk_field) if pk_field else index

        # 1. Primary Key and Duplicate Checking
        if checks.check_duplicate_keys and pk_field:
            if pk_field not in record or record[pk_field] is None:
                issues.append(
                    ValidationIssue(
                        record_id=f"<index-{index}>",
                        field=pk_field,
                        code="MISSING_PRIMARY_KEY",
                        message=f"Primary key '{pk_field}' is missing or null.",
                        severity=Severity.ERROR,
                        actual_value=None,
                        index=index
                    )
                )
            elif record[pk_field] == "" or (isinstance(record[pk_field], str) and record[pk_field].strip() == ""):
                issues.append(
                    ValidationIssue(
                        record_id=f"<index-{index}>",
                        field=pk_field,
                        code="EMPTY_PRIMARY_KEY",
                        message=f"Primary key '{pk_field}' cannot be empty string.",
                        severity=Severity.ERROR,
                        actual_value=record[pk_field],
                        index=index
                    )
                )
            else:
                pk_val = record[pk_field]
                if pk_val in seen_keys:
                    issues.append(
                        ValidationIssue(
                            record_id=pk_val,
                            field=pk_field,
                            code="DUPLICATE_PRIMARY_KEY",
                            message=f"Duplicate primary key value '{pk_val}' detected.",
                            severity=Severity.ERROR,
                            actual_value=pk_val,
                            index=index
                        )
                    )
                else:
                    seen_keys.add(pk_val)

        # 2. Field-Level Rules Validation
        for field_name, rule in self.config.field_rules.items():
            value_exists = field_name in record
            val = record.get(field_name)

            # Check Missing Fields
            if not value_exists or val is None:
                if rule.required:
                    if checks.check_required_fields:
                        issues.append(
                            ValidationIssue(
                                record_id=record_id,
                                field=field_name,
                                code="MISSING_REQUIRED_FIELD",
                                message=f"Required field '{field_name}' is missing or null.",
                                severity=Severity.ERROR,
                                actual_value=val,
                                index=index
                            )
                        )
                else:
                    if checks.check_optional_fields:
                        issues.append(
                            ValidationIssue(
                                record_id=record_id,
                                field=field_name,
                                code="MISSING_OPTIONAL_DATA",
                                message=f"Optional field '{field_name}' is absent or null.",
                                severity=Severity.INFO,
                                actual_value=val,
                                index=index
                            )
                        )
                continue

            # Check Empty Strings
            if checks.check_empty_values and isinstance(val, str):
                if val.strip() == "":
                    if not rule.allow_empty_string:
                        sev = Severity.ERROR if rule.required else Severity.WARNING
                        issues.append(
                            ValidationIssue(
                                record_id=record_id,
                                field=field_name,
                                code="EMPTY_VALUE",
                                message=f"Field '{field_name}' contains empty or blank whitespace string.",
                                severity=sev,
                                actual_value=val,
                                index=index
                            )
                        )
                        continue

            # Check Special Characters UTF-8 Integrity
            if checks.check_special_chars and isinstance(val, str):
                try:
                    val.encode("utf-8")
                except UnicodeEncodeError as uerr:
                    issues.append(
                        ValidationIssue(
                            record_id=record_id,
                            field=field_name,
                            code="ENCODING_ERROR",
                            message=f"Field '{field_name}' contains invalid non-UTF-8 characters: {uerr}",
                            severity=Severity.ERROR,
                            actual_value=val,
                            index=index
                        )
                    )

            # Check Expected Types
            if checks.check_data_types and rule.expected_type is not None:
                expected = rule.expected_type
                is_type_match = False

                if expected is bool or (isinstance(expected, tuple) and bool in expected):
                    if isinstance(val, bool):
                        is_type_match = True
                elif expected is int or (isinstance(expected, tuple) and int in expected and bool not in expected):
                    if isinstance(val, int) and not isinstance(val, bool):
                        is_type_match = True
                else:
                    is_type_match = isinstance(val, expected)

                if not is_type_match:
                    type_name = getattr(expected, "__name__", str(expected))
                    actual_type = type(val).__name__
                    issues.append(
                        ValidationIssue(
                            record_id=record_id,
                            field=field_name,
                            code="TYPE_MISMATCH",
                            message=f"Field '{field_name}' expected type {type_name}, but got {actual_type}.",
                            severity=Severity.ERROR,
                            actual_value=val,
                            index=index
                        )
                    )
                    continue

            # Check Numeric Range Bounds
            if checks.check_numeric_ranges and isinstance(val, (int, float)) and not isinstance(val, bool):
                if rule.min_value is not None and val < rule.min_value:
                    issues.append(
                        ValidationIssue(
                            record_id=record_id,
                            field=field_name,
                            code="VALUE_BELOW_MIN",
                            message=f"Field '{field_name}' value {val} is below minimum {rule.min_value}.",
                            severity=Severity.ERROR,
                            actual_value=val,
                            index=index
                        )
                    )
                if rule.max_value is not None and val > rule.max_value:
                    issues.append(
                        ValidationIssue(
                            record_id=record_id,
                            field=field_name,
                            code="VALUE_ABOVE_MAX",
                            message=f"Field '{field_name}' value {val} is above maximum {rule.max_value}.",
                            severity=Severity.ERROR,
                            actual_value=val,
                            index=index
                        )
                    )

            # Check URL Format
            if checks.check_url_formats and rule.is_url and val:
                if not is_valid_url(val, allowed_schemes=rule.allowed_url_schemes):
                    issues.append(
                        ValidationIssue(
                            record_id=record_id,
                            field=field_name,
                            code="MALFORMED_URL",
                            message=f"Field '{field_name}' has invalid URL format: '{val}'.",
                            severity=Severity.ERROR,
                            actual_value=val,
                            index=index
                        )
                    )

            # Check Regex Patterns
            if checks.check_regex_patterns and rule.regex and isinstance(val, str):
                if not rule.regex.match(val):
                    issues.append(
                        ValidationIssue(
                            record_id=record_id,
                            field=field_name,
                            code="REGEX_MISMATCH",
                            message=f"Field '{field_name}' value does not match required regex pattern.",
                            severity=Severity.ERROR,
                            actual_value=val,
                            index=index
                        )
                    )

            # Check Custom Field Validator
            if checks.check_custom_rules and rule.custom_validator:
                try:
                    custom_err = rule.custom_validator(val, record)
                    if custom_err:
                        issues.append(
                            ValidationIssue(
                                record_id=record_id,
                                field=field_name,
                                code="CUSTOM_VALIDATION_FAILED",
                                message=custom_err,
                                severity=Severity.ERROR,
                                actual_value=val,
                                index=index
                            )
                        )
                except Exception as exc:
                    issues.append(
                        ValidationIssue(
                            record_id=record_id,
                            field=field_name,
                            code="CUSTOM_VALIDATOR_EXCEPTION",
                            message=f"Custom validator raised error: {exc}",
                            severity=Severity.ERROR,
                            actual_value=val,
                            index=index
                        )
                    )

        # 3. Check Unknown Fields (if check enabled)
        if checks.check_unknown_fields:
            known_fields = set(self.config.field_rules.keys())
            if pk_field:
                known_fields.add(pk_field)
            for k in record.keys():
                if k not in known_fields:
                    issues.append(
                        ValidationIssue(
                            record_id=record_id,
                            field=k,
                            code="UNKNOWN_FIELD",
                            message=f"Field '{k}' is not defined in validation schema.",
                            severity=Severity.WARNING,
                            actual_value=record[k],
                            index=index
                        )
                    )

        # 4. Custom Cross-Field / Record-Level Validators
        if checks.check_record_consistency:
            for rec_val_fn in self.config.record_validators:
                try:
                    rec_issues = rec_val_fn(record, index)
                    if rec_issues:
                        issues.extend(rec_issues)
                except Exception as exc:
                    issues.append(
                        ValidationIssue(
                            record_id=record_id,
                            field=None,
                            code="RECORD_VALIDATOR_EXCEPTION",
                            message=f"Record validator failed with exception: {exc}",
                            severity=Severity.ERROR,
                            actual_value=None,
                            index=index
                        )
                    )

        return issues

    def validate(self, records: Iterable[Dict[str, Any]]) -> ValidationReport:
        """
        Validate an iterable of record dictionaries.
        Returns a ValidationReport.
        """
        all_issues: List[ValidationIssue] = []
        seen_keys: Set[Any] = set()
        total_records = 0

        for index, record in enumerate(records):
            total_records += 1
            issues = self.validate_record(record, index=index, seen_keys=seen_keys)
            all_issues.extend(issues)

            if self.config.fail_fast and any(i.severity == Severity.ERROR for i in issues):
                logger.warning("Fail-fast triggered on record index %d.", index)
                break

        return ValidationReport(
            total_records=total_records,
            issues=all_issues,
            checks_config=self.config.checks.to_dict()
        )
