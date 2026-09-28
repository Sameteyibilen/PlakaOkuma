#!/usr/bin/env python3
"""Digital Planet IntegrationService — gelen e-irsaliye UBL çekme."""

from __future__ import annotations

import base64
import html
import re
import ssl
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Callable
from xml.etree import ElementTree as ET

from irsaliye import IRSALIYE_DIR, parse_file, prune_old_docs, save_original

DEFAULT_DP_URL = (
    "https://netdespatchintegrationwithoutmtom.digitalplanet.com.tr/"
    "IntegrationService.asmx"
)
NS = "http://tempuri.org/"
SOAP_NS = "http://schemas.xmlsoap.org/soap/envelope/"

LogFn = Callable[[str], None]


def _log(msg: str, log: LogFn | None = None) -> None:
    if log:
        log(msg)
    else:
        print(msg, flush=True)


def _xml_text(s: str) -> str:
    return (
        (s or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _ssl_ctx() -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    try:
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    except Exception:
        pass
    return ctx


def _soap(url: str, action: str, inner: str, timeout: float = 90.0) -> str:
    body = (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<soap:Envelope xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '
        'xmlns:xsd="http://www.w3.org/2001/XMLSchema" '
        'xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/">'
        f"<soap:Body>{inner}</soap:Body></soap:Envelope>"
    ).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={
            "Content-Type": "text/xml; charset=utf-8",
            "SOAPAction": f'"{NS}{action}"',
            "User-Agent": "PlakaOkuma",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=_ssl_ctx()) as resp:
            return resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        err = exc.read().decode("utf-8", "replace") if exc.fp else str(exc)
        raise RuntimeError(f"Digital Planet {action} HTTP {exc.code}: {err[:400]}") from exc


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _first_text(root: ET.Element, name: str) -> str:
    for el in root.iter():
        if _local(el.tag) == name and el.text:
            return el.text.strip()
    return ""


def ticket(cfg: dict) -> str:
    url = (cfg.get("dp_url") or DEFAULT_DP_URL).rstrip("/")
    corp = (cfg.get("dp_corporate") or "").strip()
    login = (cfg.get("dp_login") or "").strip()
    password = cfg.get("dp_password") or ""
    if not corp or not login or not password:
        raise RuntimeError("Digital Planet kurum/kullanıcı/şifre yok.")
    inner = (
        f'<GetFormsAuthenticationTicket xmlns="{NS}">'
        f"<CorporateCode>{_xml_text(corp)}</CorporateCode>"
        f"<LoginName>{_xml_text(login)}</LoginName>"
        f"<Password>{_xml_text(password)}</Password>"
        "</GetFormsAuthenticationTicket>"
    )
    xml = _soap(url, "GetFormsAuthenticationTicket", inner, timeout=40)
    if "faultstring" in xml.lower() or "Fault" in xml[:800]:
        raise RuntimeError("Digital Planet girişi reddedildi.")
    root = ET.fromstring(xml)
    tok = _first_text(root, "GetFormsAuthenticationTicketResult")
    if not tok:
        raise RuntimeError("Digital Planet ticket alınamadı.")
    return html.unescape(tok)


def list_uuids(cfg: dict, tok: str, log: LogFn | None = None) -> list[str]:
    url = (cfg.get("dp_url") or DEFAULT_DP_URL).rstrip("/")
    corp = (cfg.get("dp_corporate") or "").strip()
    days = max(1, int(cfg.get("days") or 1))
    start = datetime.combine(date.today() - timedelta(days=days - 1), datetime.min.time())
    end = datetime.combine(date.today() + timedelta(days=1), datetime.min.time())
    inner = (
        f'<GetAvailableDespatchesByDate xmlns="{NS}">'
        f"<Ticket>{_xml_text(tok)}</Ticket>"
        f"<CorporateCode>{_xml_text(corp)}</CorporateCode>"
        f"<StartDate>{start.strftime('%Y-%m-%dT00:00:00')}</StartDate>"
        f"<EndDate>{end.strftime('%Y-%m-%dT00:00:00')}</EndDate>"
        "</GetAvailableDespatchesByDate>"
    )
    xml = _soap(url, "GetAvailableDespatchesByDate", inner, timeout=120)
    found: list[str] = []
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        root = None
    if root is not None:
        for el in root.iter():
            if _local(el.tag) != "UUID" or not el.text:
                continue
            lu = el.text.strip().lower()
            if re.fullmatch(
                r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
                lu,
            ) and lu not in found:
                found.append(lu)
    _log(f"Digital Planet: {len(found)} gelen irsaliye", log)
    return found


def fetch_despatch(cfg: dict, tok: str, uuid: str, file_type: str = "ubl") -> bytes | None:
    url = (cfg.get("dp_url") or DEFAULT_DP_URL).rstrip("/")
    inner = (
        f'<GetDespatch xmlns="{NS}">'
        f"<Ticket>{_xml_text(tok)}</Ticket>"
        f"<Value>{_xml_text(uuid)}</Value>"
        "<ValueType>uuid</ValueType>"
        "<direction>Incoming</direction>"
        f"<FileType>{_xml_text(file_type)}</FileType>"
        "</GetDespatch>"
    )
    xml = _soap(url, "GetDespatch", inner, timeout=90)
    raw_b64 = ""
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        return None
    for el in root.iter():
        if _local(el.tag) in {"ReturnValue", "GetDespatchResult"} and el.text:
            raw_b64 = "".join(el.text.split())
            if raw_b64:
                break
    if not raw_b64:
        if file_type.lower() == "ubl" and "<DespatchAdvice" in xml:
            start = xml.find("<DespatchAdvice")
            end = xml.find("</DespatchAdvice>")
            if end > start:
                return xml[start : end + len("</DespatchAdvice>")].encode("utf-8")
        return None
    try:
        data = base64.b64decode(raw_b64)
    except Exception:
        return None
    if data[:3] == b"\xef\xbb\xbf":
        data = data[3:]
    return data


def fetch_ubl(cfg: dict, tok: str, uuid: str) -> bytes | None:
    return fetch_despatch(cfg, tok, uuid, "ubl")


def fetch_pdf(cfg: dict, tok: str, uuid: str) -> bytes | None:
    data = fetch_despatch(cfg, tok, uuid, "pdf")
    if data and data[:4] == b"%PDF":
        return data
    return None


def sync(log: LogFn | None = None, cfg: dict | None = None) -> dict:
    if cfg is None:
        from eportal import load_config

        cfg = load_config()
    if not (cfg.get("dp_login") and cfg.get("dp_password") and cfg.get("dp_corporate")):
        return {"total": 0, "added": 0, "failed": 0, "skipped": True}
    IRSALIYE_DIR.mkdir(parents=True, exist_ok=True)
    n_old = prune_old_docs(IRSALIYE_DIR, days=max(1, int(cfg.get("days") or 1)))
    if n_old:
        _log(f"Eski irsaliye silindi: {n_old} dosya", log)
    tok = ticket(cfg)
    _log("Digital Planet oturumu açıldı.", log)
    uuids = list_uuids(cfg, tok, log)
    have: dict[str, Path] = {}
    for path in IRSALIYE_DIR.glob("*.xml"):
        rec = parse_file(path)
        if rec and rec.get("uuid"):
            have[str(rec["uuid"]).lower()] = path
    added = 0
    failed = 0
    for uuid in uuids:
        existing = have.get(uuid)
        if existing is not None:
            pdf_path = existing.with_suffix(".pdf")
            if not pdf_path.exists() or pdf_path.stat().st_size < 100:
                pdf = fetch_pdf(cfg, tok, uuid)
                if pdf:
                    pdf_path.write_bytes(pdf)
            continue
        blob = fetch_ubl(cfg, tok, uuid)
        if not blob or (
            b"DespatchAdvice" not in blob and b"despatchAdvice" not in blob
        ):
            failed += 1
            _log(f"Digital Planet indirilemedi {uuid}", log)
            continue
        pdf = fetch_pdf(cfg, tok, uuid)
        dest = save_original(blob, pdf)
        if dest:
            rec = parse_file(dest)
            plate = (rec or {}).get("plate") or ""
            _log(f"DP kayıt {(rec or {}).get('id') or dest.name}  {plate}", log)
            added += 1
            have[uuid] = dest
        else:
            failed += 1
            _log(f"Digital Planet XML işlenemedi {uuid}", log)
    summary = {"total": len(uuids), "added": added, "failed": failed}
    _log(
        f"Digital Planet senkron: {summary['total']} belge, {added} yeni, {failed} atlanan",
        log,
    )
    return summary
