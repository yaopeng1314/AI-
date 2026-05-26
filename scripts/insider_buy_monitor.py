#!/usr/bin/env python3
"""Generate a Chinese report for recent US insider purchase filings.

The script intentionally uses only the Python standard library so it can run
inside GitHub Actions without dependency installation.
"""

from __future__ import annotations

import argparse
import datetime as dt
import html
import os
import re
import sys
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from typing import Iterable
from zoneinfo import ZoneInfo


SEC_ARCHIVES = "https://www.sec.gov/Archives"
SEC_USER_AGENT = os.environ.get(
    "SEC_USER_AGENT",
    "us-insider-buy-monitor github-actions contact@example.com",
)

TARGET_FORMS = {"4", "4/A", "SC 13D", "SC 13D/A", "SC 13G", "SC 13G/A"}
FORM4_TYPES = {"4", "4/A"}
SCHEDULE_TYPES = {"SC 13D", "SC 13D/A", "SC 13G", "SC 13G/A"}


INDUSTRY_TRANSLATIONS = {
    "Aerospace & Defense": "航空航天与国防",
    "Agricultural Inputs": "农业投入品",
    "Airlines": "航空公司",
    "Apparel Retail": "服装零售",
    "Application Software": "应用软件",
    "Asset Management": "资产管理",
    "Auto Manufacturers": "汽车制造",
    "Banks - Diversified": "综合银行",
    "Banks - Regional": "区域银行",
    "Biotechnology": "生物技术",
    "Building Products & Equipment": "建筑产品与设备",
    "Capital Markets": "资本市场",
    "Chemicals": "化工",
    "Communication Equipment": "通信设备",
    "Computer Hardware": "计算机硬件",
    "Consulting Services": "咨询服务",
    "Consumer Electronics": "消费电子",
    "Credit Services": "信贷服务",
    "Diagnostics & Research": "诊断与研究服务",
    "Electrical Equipment & Parts": "电气设备与零部件",
    "Electronic Components": "电子元件",
    "Entertainment": "娱乐",
    "Farm Products": "农产品",
    "Financial Data & Stock Exchanges": "金融数据与证券交易所",
    "Food Distribution": "食品分销",
    "Healthcare Plans": "医疗保险计划",
    "Insurance Brokers": "保险经纪",
    "Internet Content & Information": "互联网内容与信息",
    "Medical Devices": "医疗器械",
    "Oil & Gas E&P": "油气勘探与生产",
    "Packaged Foods": "包装食品",
    "Real Estate Services": "房地产服务",
    "REIT - Healthcare Facilities": "医疗设施 REIT",
    "REIT - Hotel & Motel": "酒店与住宿 REIT",
    "REIT - Office": "办公物业 REIT",
    "REIT - Residential": "住宅 REIT",
    "REIT - Retail": "零售物业 REIT",
    "Restaurants": "餐饮",
    "Semiconductors": "半导体",
    "Software - Application": "应用软件",
    "Software - Infrastructure": "基础设施软件",
    "Specialty Retail": "专业零售",
    "Telecom Services": "电信服务",
    "Utilities - Regulated Electric": "受监管电力公用事业",
    "Utilities - Regulated Gas": "受监管燃气公用事业",
    "Utilities - Regulated Water": "受监管水务公用事业",
}


@dataclass
class Filing:
    cik: str
    company: str
    form_type: str
    filed_date: str
    filename: str

    @property
    def url(self) -> str:
        return f"{SEC_ARCHIVES}/{self.filename}"


@dataclass
class Owner:
    name: str = "未披露"
    is_director: bool = False
    is_officer: bool = False
    is_ten_percent: bool = False
    officer_title: str = ""

    @property
    def role_cn(self) -> str:
        roles: list[str] = []
        title = self.officer_title.strip()
        if title:
            roles.append(title)
        elif self.is_officer:
            roles.append("高管")
        if self.is_director:
            roles.append("董事")
        if self.is_ten_percent:
            roles.append("10% 股东")
        return "、".join(dict.fromkeys(roles)) or "内部人"


@dataclass
class PurchaseRecord:
    ticker: str
    company: str
    issuer_cik: str
    owner: Owner
    form_type: str
    filing_date: str
    filing_url: str
    transaction_dates: list[str]
    shares: float
    value: float | None
    min_price: float | None
    max_price: float | None
    post_shares: float | None
    direct_indirect: str
    public_market: str
    footnote_text: str
    suspicious: bool = False
    excluded_reason: str = ""
    market_cap: str = "未查到"
    industry: str = "未查到"
    comment: str = ""


class TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.text: list[str] = []

    def handle_data(self, data: str) -> None:
        cleaned = " ".join(data.split())
        if cleaned:
            self.text.append(cleaned)


def request_text(url: str, *, retries: int = 3, sleep_seconds: float = 0.5) -> str:
    user_agent = SEC_USER_AGENT if "sec.gov" in url.lower() else "Mozilla/5.0 us-insider-buy-monitor"
    headers = {
        "User-Agent": user_agent,
        "Accept-Encoding": "identity",
        "Accept": "text/html,application/xhtml+xml,application/xml,text/plain;q=0.9,*/*;q=0.8",
    }
    last_error: Exception | None = None
    for attempt in range(retries):
        req = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=30) as response:
                raw = response.read()
                charset = response.headers.get_content_charset() or "utf-8"
                return raw.decode(charset, errors="replace")
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                raise
            last_error = exc
        except urllib.error.URLError as exc:
            last_error = exc
        if attempt < retries - 1:
            time.sleep(sleep_seconds * (attempt + 1))
    raise RuntimeError(f"Failed to fetch {url}: {last_error}")


def quarter_for(date_value: dt.date) -> int:
    return ((date_value.month - 1) // 3) + 1


def master_index_url(date_value: dt.date) -> str:
    qtr = quarter_for(date_value)
    stamp = date_value.strftime("%Y%m%d")
    return f"{SEC_ARCHIVES}/edgar/daily-index/{date_value.year}/QTR{qtr}/master.{stamp}.idx"


def parse_master_index(text: str) -> list[Filing]:
    filings: list[Filing] = []
    data_started = False
    for line in text.splitlines():
        if not data_started:
            if line.startswith("CIK|Company Name|Form Type|Date Filed|Filename"):
                data_started = True
            continue
        if "|" not in line:
            continue
        parts = line.split("|")
        if len(parts) != 5:
            continue
        cik, company, form_type, filed_date, filename = [p.strip() for p in parts]
        if form_type in TARGET_FORMS:
            filings.append(Filing(cik, company, form_type, filed_date, filename))
    return filings


def find_latest_target_filings(lookback_days: int) -> tuple[dt.date, list[Filing], str]:
    ny_today = dt.datetime.now(ZoneInfo("America/New_York")).date()
    for offset in range(lookback_days):
        candidate = ny_today - dt.timedelta(days=offset)
        url = master_index_url(candidate)
        try:
            text = request_text(url)
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                continue
            raise
        filings = parse_master_index(text)
        if filings:
            reason = "当天已有 SEC 目标披露" if offset == 0 else f"回溯 {offset} 天找到最近 SEC 目标披露日"
            return candidate, filings, reason
    raise RuntimeError(f"No target SEC filings found in the last {lookback_days} days.")


def extract_ownership_xml(sec_txt: str) -> str | None:
    for match in re.finditer(r"<XML>\s*(.*?)\s*</XML>", sec_txt, flags=re.I | re.S):
        block = match.group(1).strip()
        if "<ownershipDocument" in block:
            return block
    if "<ownershipDocument" in sec_txt:
        start = sec_txt.find("<ownershipDocument")
        end = sec_txt.find("</ownershipDocument>", start)
        if end != -1:
            return sec_txt[start : end + len("</ownershipDocument>")]
    return None


def findtext(node: ET.Element, path: str, default: str = "") -> str:
    found = node.find(path)
    if found is None or found.text is None:
        return default
    return found.text.strip()


def parse_float(text: str | None) -> float | None:
    if text is None:
        return None
    cleaned = text.strip().replace(",", "").replace("$", "")
    if not cleaned or cleaned.upper() in {"N/A", "NA"}:
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def yes_flag(text: str) -> bool:
    return text.strip().lower() in {"1", "true", "yes", "y"}


def collect_footnotes(root: ET.Element) -> dict[str, str]:
    notes: dict[str, str] = {}
    for footnote in root.findall(".//footnote"):
        note_id = footnote.attrib.get("id", "")
        if note_id:
            notes[note_id] = " ".join("".join(footnote.itertext()).split())
    return notes


def referenced_footnotes(node: ET.Element, footnotes: dict[str, str]) -> str:
    ids = []
    for footnote_id in node.findall(".//footnoteId"):
        value = footnote_id.attrib.get("id")
        if value:
            ids.append(value)
    parts = [footnotes[note_id] for note_id in dict.fromkeys(ids) if note_id in footnotes]
    return " ".join(parts)


def suspicious_footnote(text: str) -> tuple[bool, str]:
    lowered = text.lower()
    if "in-kind distribution" in lowered or ("distribution" in lowered and "fund" in lowered):
        return True, "疑似基金间分配或非外部净增持"
    if "affiliated" in lowered and ("fund" in lowered or "entity" in lowered):
        return True, "疑似关联主体之间交易"
    if "between" in lowered and ("fund" in lowered or "entities" in lowered):
        return True, "疑似关联主体之间交易"
    return False, ""


def public_market_label(text: str) -> str:
    lowered = text.lower()
    private_words = ["private", "subscription", "purchase agreement", "negotiated", "placement"]
    if any(word in lowered for word in private_words):
        return "否/需核对"
    return "通常是"


def parse_owner(root: ET.Element) -> Owner:
    owner_node = root.find("reportingOwner")
    if owner_node is None:
        return Owner()
    relationship = owner_node.find("reportingOwnerRelationship")
    return Owner(
        name=findtext(owner_node, "reportingOwnerId/rptOwnerName", "未披露"),
        is_director=yes_flag(findtext(relationship, "isDirector")) if relationship is not None else False,
        is_officer=yes_flag(findtext(relationship, "isOfficer")) if relationship is not None else False,
        is_ten_percent=yes_flag(findtext(relationship, "isTenPercentOwner")) if relationship is not None else False,
        officer_title=findtext(relationship, "officerTitle") if relationship is not None else "",
    )


def parse_form4(filing: Filing) -> PurchaseRecord | None:
    sec_txt = request_text(filing.url)
    xml_text = extract_ownership_xml(sec_txt)
    if not xml_text:
        return None
    try:
        root = ET.fromstring(xml_text.encode("utf-8"))
    except ET.ParseError:
        return None

    ticker = findtext(root, "issuer/issuerTradingSymbol", "").upper()
    company = findtext(root, "issuer/issuerName", filing.company)
    issuer_cik = findtext(root, "issuer/issuerCik", filing.cik)
    owner = parse_owner(root)
    footnotes = collect_footnotes(root)

    transactions: list[dict[str, object]] = []
    all_notes: list[str] = []
    for txn in root.findall(".//nonDerivativeTransaction"):
        code = findtext(txn, "transactionCoding/transactionCode").upper()
        acquired = findtext(txn, "transactionAmounts/transactionAcquiredDisposedCode/value").upper()
        if code != "P" or acquired != "A":
            continue
        shares = parse_float(findtext(txn, "transactionAmounts/transactionShares/value"))
        price = parse_float(findtext(txn, "transactionAmounts/transactionPricePerShare/value"))
        txn_date = findtext(txn, "transactionDate/value")
        post = parse_float(findtext(txn, "postTransactionAmounts/sharesOwnedFollowingTransaction/value"))
        direct = findtext(txn, "ownershipNature/directOrIndirectOwnership/value")
        note_text = referenced_footnotes(txn, footnotes)
        if note_text:
            all_notes.append(note_text)
        if shares is None or shares <= 0:
            continue
        transactions.append(
            {
                "date": txn_date,
                "shares": shares,
                "price": price,
                "post": post,
                "direct": direct,
                "notes": note_text,
            }
        )

    if not transactions:
        return None

    total_shares = sum(float(txn["shares"]) for txn in transactions)
    values = [
        float(txn["shares"]) * float(txn["price"])
        for txn in transactions
        if txn["price"] is not None
    ]
    total_value = sum(values) if values else None
    prices = [float(txn["price"]) for txn in transactions if txn["price"] is not None]
    post_values = [float(txn["post"]) for txn in transactions if txn["post"] is not None]
    direct_values = [str(txn["direct"]) for txn in transactions if txn["direct"]]
    dates = sorted({str(txn["date"]) for txn in transactions if txn["date"]})

    note_text = " ".join(dict.fromkeys(all_notes))
    suspicious, reason = suspicious_footnote(note_text)

    return PurchaseRecord(
        ticker=ticker or filing.cik,
        company=company,
        issuer_cik=issuer_cik,
        owner=owner,
        form_type=filing.form_type,
        filing_date=filing.filed_date,
        filing_url=filing.url,
        transaction_dates=dates,
        shares=total_shares,
        value=total_value,
        min_price=min(prices) if prices else None,
        max_price=max(prices) if prices else None,
        post_shares=post_values[-1] if post_values else None,
        direct_indirect="/".join(dict.fromkeys(direct_values)) or "未披露",
        public_market=public_market_label(note_text),
        footnote_text=note_text,
        suspicious=suspicious,
        excluded_reason=reason,
    )


def format_usd(value: float | None) -> str:
    if value is None:
        return "未披露"
    abs_value = abs(value)
    if abs_value >= 1_000_000_000:
        return f"约 ${value / 1_000_000_000:.2f}B"
    if abs_value >= 1_000_000:
        return f"约 ${value / 1_000_000:.2f}M"
    if abs_value >= 1_000:
        return f"约 ${value / 1_000:.1f}K"
    return f"约 ${value:,.0f}"


def format_num(value: float | None) -> str:
    if value is None:
        return "未披露"
    if abs(value - round(value)) < 0.00001:
        return f"{int(round(value)):,}"
    return f"{value:,.2f}"


def price_range(record: PurchaseRecord) -> str:
    if record.min_price is None:
        return "价格未披露"
    if record.max_price is None or abs(record.max_price - record.min_price) < 0.00001:
        return f"${record.min_price:.2f}"
    return f"${record.min_price:.2f}-${record.max_price:.2f}"


def date_range(dates: list[str]) -> str:
    if not dates:
        return "日期未披露"
    if len(dates) == 1:
        return dates[0]
    return f"{dates[0]} 至 {dates[-1]}"


def normalize_ticker_for_stockanalysis(ticker: str) -> str:
    return ticker.lower().replace(".", "-").replace("/", "-").strip()


def parse_stockanalysis(text: str) -> tuple[str, str]:
    parser = TextExtractor()
    parser.feed(text)
    tokens = parser.text

    market_cap = "未查到"
    industry = "未查到"
    for index, token in enumerate(tokens):
        if token == "Market Cap" and index + 1 < len(tokens):
            candidate = tokens[index + 1]
            if re.search(r"\d", candidate):
                market_cap = candidate
        if token == "Industry" and index + 1 < len(tokens):
            industry = tokens[index + 1]
    return market_cap, industry


def translate_industry(industry: str) -> str:
    if not industry or industry == "未查到":
        return "未查到"
    if industry in INDUSTRY_TRANSLATIONS:
        return INDUSTRY_TRANSLATIONS[industry]
    lowered = industry.lower()
    fallback_rules = [
        ("bank", "银行"),
        ("insurance", "保险"),
        ("software", "软件"),
        ("reit", "REIT"),
        ("restaurant", "餐饮"),
        ("biotech", "生物技术"),
        ("medical", "医疗"),
        ("utility", "公用事业"),
        ("semiconductor", "半导体"),
        ("asset management", "资产管理"),
        ("consult", "咨询服务"),
        ("farm", "农产品"),
        ("oil", "油气"),
        ("gas", "油气"),
        ("retail", "零售"),
        ("telecom", "电信服务"),
        ("electrical", "电气设备"),
    ]
    for needle, translated in fallback_rules:
        if needle in lowered:
            return translated
    return industry


def get_market_profile(ticker: str) -> tuple[str, str]:
    if not ticker or ticker.isdigit():
        return "未查到", "未查到"
    url = f"https://stockanalysis.com/stocks/{normalize_ticker_for_stockanalysis(ticker)}/"
    try:
        text = request_text(url, retries=2)
    except Exception:
        return "未查到", "未查到"
    market_cap, industry = parse_stockanalysis(text)
    if market_cap != "未查到" and not market_cap.startswith("$"):
        market_cap = f"约 ${market_cap}"
    elif market_cap.startswith("$"):
        market_cap = f"约 {market_cap}"
    return market_cap, translate_industry(industry)


def value_to_float(record: PurchaseRecord) -> float:
    return record.value if record.value is not None else 0.0


def market_cap_to_float(market_cap: str) -> float | None:
    match = re.search(r"\$?([0-9]+(?:\.[0-9]+)?)\s*([BMK])", market_cap, flags=re.I)
    if not match:
        return None
    value = float(match.group(1))
    unit = match.group(2).upper()
    if unit == "B":
        return value * 1_000_000_000
    if unit == "M":
        return value * 1_000_000
    if unit == "K":
        return value * 1_000
    return value


def build_comment(record: PurchaseRecord) -> str:
    value = value_to_float(record)
    market_cap = market_cap_to_float(record.market_cap)
    role = record.owner.role_cn
    is_repeat = len(record.transaction_dates) >= 2
    is_large_holder = "10% 股东" in role
    is_exec = any(word in role.upper() for word in ["CEO", "CFO", "COO", "EVP", "高管"])
    cap_ratio = value / market_cap if market_cap else None

    parts: list[str] = []
    if record.suspicious:
        return f"降权观察：{record.excluded_reason or '交易脚注显示可能不是外部净增持'}，不宜按普通公开市场增持解读。"
    if record.public_market != "通常是":
        parts.append("不是明确的公开市场买入，信号需要打折")
    elif is_large_holder and value >= 1_000_000:
        parts.append("大股东较大金额买入，信号强")
    elif is_exec and value >= 100_000:
        parts.append("核心管理层投入达到六位数，信号中等偏强")
    elif value >= 500_000:
        parts.append("买入金额较大，值得跟踪")
    else:
        parts.append("金额不算大，更多是观察信号")

    if is_repeat:
        parts.append("连续多日买入提升了参考价值")
    if cap_ratio is not None and cap_ratio >= 0.005:
        parts.append("金额相对公司市值占比不低")
    if market_cap is not None and market_cap < 100_000_000:
        parts.append("但公司市值很小，流动性和波动风险要优先考虑")
    elif market_cap is not None and market_cap > 2_000_000_000 and value < 200_000:
        parts.append("但相对公司体量偏小")
    return "；".join(parts) + "。"


def markdown_escape(text: str) -> str:
    return str(text).replace("|", "\\|").replace("\n", " ")


def enrich_records(records: list[PurchaseRecord]) -> None:
    cache: dict[str, tuple[str, str]] = {}
    for record in records:
        ticker = record.ticker
        if ticker not in cache:
            cache[ticker] = get_market_profile(ticker)
            time.sleep(0.2)
        record.market_cap, record.industry = cache[ticker]
        record.comment = build_comment(record)


def build_transaction_detail(record: PurchaseRecord) -> str:
    return (
        f"{date_range(record.transaction_dates)} 买入 {format_num(record.shares)} 股，"
        f"价格 {price_range(record)}；增持后持股 {format_num(record.post_shares)} 股；"
        f"公开市场：{record.public_market}"
    )


def split_records(records: list[PurchaseRecord]) -> tuple[list[PurchaseRecord], list[PurchaseRecord], list[PurchaseRecord]]:
    excluded = [record for record in records if record.suspicious]
    included = [record for record in records if not record.suspicious]
    included.sort(key=value_to_float, reverse=True)
    priority = [record for record in included if value_to_float(record) >= 100_000][:20]
    secondary = [record for record in included if value_to_float(record) < 100_000][:20]
    return priority, secondary, excluded


def render_records_table(records: list[PurchaseRecord], include_rank: bool = True) -> list[str]:
    if include_rank:
        lines = [
            "| 排名 | 股票 | 公司 | 市值 | 行业 | 买入方 | 身份 | 增持明细 | 估算金额 | 简评 |",
            "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
        ]
        for index, record in enumerate(records, start=1):
            lines.append(
                "| "
                + " | ".join(
                    [
                        str(index),
                        markdown_escape(record.ticker),
                        markdown_escape(record.company),
                        markdown_escape(record.market_cap),
                        markdown_escape(record.industry),
                        markdown_escape(record.owner.name),
                        markdown_escape(record.owner.role_cn),
                        markdown_escape(build_transaction_detail(record)),
                        markdown_escape(format_usd(record.value)),
                        markdown_escape(record.comment),
                    ]
                )
                + " |"
            )
    else:
        lines = [
            "| 股票 | 公司 | 市值 | 行业 | 买入方 | 身份 | 增持明细 | 估算金额 | 简评 |",
            "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
        ]
        for record in records:
            lines.append(
                "| "
                + " | ".join(
                    [
                        markdown_escape(record.ticker),
                        markdown_escape(record.company),
                        markdown_escape(record.market_cap),
                        markdown_escape(record.industry),
                        markdown_escape(record.owner.name),
                        markdown_escape(record.owner.role_cn),
                        markdown_escape(build_transaction_detail(record)),
                        markdown_escape(format_usd(record.value)),
                        markdown_escape(record.comment),
                    ]
                )
                + " |"
            )
    return lines


def render_schedule_watch(filings: Iterable[Filing]) -> list[str]:
    schedules = [filing for filing in filings if filing.form_type in SCHEDULE_TYPES]
    if not schedules:
        return ["本披露日未在目标集合中发现 Schedule 13D/13G 文件。"]
    lines = [
        "| 表格 | 公司 | CIK | 披露日期 | 原始文件 |",
        "| --- | --- | --- | --- | --- |",
    ]
    for filing in schedules[:30]:
        lines.append(
            f"| {filing.form_type} | {markdown_escape(filing.company)} | {filing.cik} | {filing.filed_date} | [SEC]({filing.url}) |"
        )
    return lines


def render_sources(records: list[PurchaseRecord]) -> list[str]:
    lines = []
    for record in records:
        lines.append(f"- {record.ticker} / {record.company}: {record.filing_url}")
    return lines or ["- 无"]


def render_report(
    report_date: dt.date,
    filings: list[Filing],
    records: list[PurchaseRecord],
    reason: str,
) -> str:
    now_cn = dt.datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d %H:%M:%S %z")
    priority, secondary, excluded = split_records(records)
    all_included = priority + secondary

    lines: list[str] = []
    lines.append("# 美股管理层与大股东增持监测")
    lines.append("")
    lines.append(f"运行时间：{now_cn[:19]} +08:00")
    lines.append(f"本次使用披露日：{report_date.isoformat()}")
    lines.append(f"回溯说明：{reason}。")
    lines.append("扫描范围：SEC EDGAR Form 4，并附列当日 Schedule 13D/13G 文件供核对。")
    lines.append("市值与行业：市值为 StockAnalysis 当前页近似值；行业已翻译为中文，缺失时显示“未查到”。")
    lines.append("过滤口径：保留交易代码为 P 且方向为 A 的买入；剔除或降权期权、RSU、自动扣缴、卖出，以及脚注显示非外部净增持的交易。")
    lines.append("")

    lines.append("## 重点增持")
    lines.append("")
    if priority:
        lines.extend(render_records_table(priority, include_rank=True))
    else:
        lines.append("未发现金额达到 10 万美元以上且通过自动规则确认的重点主动增持。")
    lines.append("")

    lines.append("## 次要观察")
    lines.append("")
    if secondary:
        lines.extend(render_records_table(secondary, include_rank=False))
    else:
        lines.append("没有金额低于 10 万美元但仍值得保留的主动买入。")
    lines.append("")

    lines.append("## 剔除或降权")
    lines.append("")
    if excluded:
        lines.extend(
            [
                "| 股票 | 公司 | 买入方 | 表面金额 | 处理 |",
                "| --- | --- | --- | --- | --- |",
            ]
        )
        for record in excluded[:30]:
            lines.append(
                "| "
                + " | ".join(
                    [
                        markdown_escape(record.ticker),
                        markdown_escape(record.company),
                        markdown_escape(record.owner.name),
                        markdown_escape(format_usd(record.value)),
                        markdown_escape(record.comment),
                    ]
                )
                + " |"
            )
    else:
        lines.append("本次没有被自动规则标记为关联主体转让或非外部净增持的买入。")
    lines.append("")

    lines.append("## 13D/13G 待核对")
    lines.append("")
    lines.append("Schedule 13D/13G 往往需要阅读正文才能判断是否为新增买入，本脚本先列出当日文件，避免误判。")
    lines.extend(render_schedule_watch(filings))
    lines.append("")

    lines.append("## 来源")
    lines.append("")
    lines.append("- SEC Daily Index: " + master_index_url(report_date))
    lines.extend(render_sources(all_included + excluded))
    lines.append("")
    lines.append("## 备注")
    lines.append("")
    lines.append("本报告由 GitHub Actions 自动生成。脚本规则偏保守，适合作为每日初筛清单；重大交易仍建议打开 SEC 原文核对脚注、交易性质和后续 13D/13G 修订。")
    lines.append("")
    return "\n".join(lines)


def write_report(report: str, reports_dir: Path, report_date: dt.date) -> tuple[Path, Path]:
    reports_dir.mkdir(parents=True, exist_ok=True)
    dated_path = reports_dir / f"us_insider_buy_monitor_{report_date.isoformat()}.md"
    latest_path = reports_dir / "latest.md"
    dated_path.write_text(report, encoding="utf-8", newline="\n")
    latest_path.write_text(report, encoding="utf-8", newline="\n")
    return dated_path, latest_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate US insider buy monitor report.")
    parser.add_argument("--reports-dir", default="reports", help="Directory for generated Markdown reports.")
    parser.add_argument("--lookback-days", type=int, default=10, help="How many New York calendar days to search backward.")
    parser.add_argument("--max-form4", type=int, default=300, help="Maximum Form 4 filings to parse for the selected day.")
    args = parser.parse_args(argv)

    report_date, filings, reason = find_latest_target_filings(args.lookback_days)
    form4_filings = [filing for filing in filings if filing.form_type in FORM4_TYPES][: args.max_form4]

    records: list[PurchaseRecord] = []
    for index, filing in enumerate(form4_filings, start=1):
        try:
            record = parse_form4(filing)
        except Exception as exc:
            print(f"warn: failed to parse {filing.url}: {exc}", file=sys.stderr)
            record = None
        if record:
            records.append(record)
        if index % 10 == 0:
            time.sleep(0.5)
        else:
            time.sleep(0.12)

    enrich_records(records)
    report = render_report(report_date, filings, records, reason)
    dated_path, latest_path = write_report(report, Path(args.reports_dir), report_date)
    print(f"Wrote {dated_path}")
    print(f"Wrote {latest_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
