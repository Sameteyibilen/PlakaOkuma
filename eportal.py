#!/usr/bin/env python3
"""IFS e-Portal (Tresol) — gelen e-irsaliye XML'lerini otomatik çeker."""

from __future__ import annotations

import html as html_lib
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, timedelta
from http.cookiejar import CookieJar
from pathlib import Path
from typing import Callable

from irsaliye import IRSALIYE_DIR, parse_file, save_original

ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "irsaliye_config.json"
DEFAULT_PORTAL = "http://172.16.1.53/pagero"
DEFAULT_SERVICE = "http://172.16.1.53/ePortalService"

LogFn = Callable[[str], None]


def _log(msg: str, log: LogFn | None = None) -> None:
    if log:
        log(msg)
    else:
        print(msg, flush=True)


def load_config() -> dict:
    cfg = {
        "portal_url": DEFAULT_PORTAL,
        "service_url": DEFAULT_SERVICE,
        "username": os.environ.get("EPORTAL_USER", ""),
        "password": os.environ.get("EPORTAL_PASS", ""),
        "days": 7,
        "max_pages": 4,
        "company": "BC",
        "module": "EDESPATCH",
        "dp_url": "https://netdespatchintegrationwithoutmtom.digitalplanet.com.tr/IntegrationService.asmx",
        "dp_corporate": os.environ.get("DP_CORPORATE", ""),
        "dp_branch": "",
        "dp_login": os.environ.get("DP_USER", ""),
        "dp_password": os.environ.get("DP_PASS", ""),
    }
    if CONFIG_PATH.exists():
        try:
            data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                cfg.update({k: v for k, v in data.items() if v is not None})
        except Exception:
            pass
    cfg["portal_url"] = str(cfg.get("portal_url") or DEFAULT_PORTAL).rstrip("/")
    cfg["service_url"] = str(cfg.get("service_url") or DEFAULT_SERVICE).rstrip("/")
    cfg["days"] = int(cfg.get("days") or 7)
    cfg["max_pages"] = int(cfg.get("max_pages") or 4)
    cfg["dp_url"] = str(cfg.get("dp_url") or "").rstrip("/")
    cfg["dp_corporate"] = str(cfg.get("dp_corporate") or "").strip()
    cfg["dp_branch"] = str(cfg.get("dp_branch") or "").strip()
    cfg["dp_login"] = str(cfg.get("dp_login") or "").strip()
    return cfg


def save_config(cfg: dict) -> None:
    out = {
        "portal_url": cfg.get("portal_url") or DEFAULT_PORTAL,
        "service_url": cfg.get("service_url") or DEFAULT_SERVICE,
        "username": cfg.get("username") or "",
        "password": cfg.get("password") or "",
        "days": int(cfg.get("days") or 7),
        "max_pages": int(cfg.get("max_pages") or 4),
        "company": cfg.get("company") or "BC",
        "module": cfg.get("module") or "EDESPATCH",
        "dp_url": cfg.get("dp_url") or "",
        "dp_corporate": cfg.get("dp_corporate") or "",
        "dp_branch": cfg.get("dp_branch") or "",
        "dp_login": cfg.get("dp_login") or "",
        "dp_password": cfg.get("dp_password") or "",
    }
    CONFIG_PATH.write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _hidden(html: str) -> dict[str, str]:
    found: dict[str, str] = {}
    for m in re.finditer(
        r'<input[^>]+type="hidden"[^>]*>', html, re.I
    ):
        tag = m.group(0)
        name_m = re.search(r'name="([^"]+)"', tag, re.I)
        val_m = re.search(r'value="([^"]*)"', tag, re.I)
        if name_m:
            found[html_lib.unescape(name_m.group(1))] = html_lib.unescape(
                val_m.group(1) if val_m else ""
            )
    return found


def _opener() -> urllib.request.OpenerDirector:
    jar = CookieJar()
    return urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))


def _open(
    opener: urllib.request.OpenerDirector,
    url: str,
    data: dict | bytes | None = None,
    headers: dict | None = None,
    timeout: float = 40.0,
) -> tuple[str, bytes, str]:
    body: bytes | None = None
    hdrs = {
        "User-Agent": "Mozilla/5.0 PlakaOkuma",
        "Accept": "text/html,application/json,application/xml,text/xml,*/*",
    }
    if headers:
        hdrs.update(headers)
    if isinstance(data, dict):
        body = urllib.parse.urlencode(data).encode("utf-8")
        hdrs.setdefault(
            "Content-Type", "application/x-www-form-urlencoded; charset=utf-8"
        )
    elif isinstance(data, bytes):
        body = data
    req = urllib.request.Request(url, data=body, headers=hdrs)
    with opener.open(req, timeout=timeout) as resp:
        raw = resp.read()
        ctype = resp.headers.get("Content-Type", "")
        final = resp.geturl()
    return final, raw, ctype


def login(opener: urllib.request.OpenerDirector, cfg: dict, log: LogFn | None = None) -> None:
    user = (cfg.get("username") or "").strip()
    password = cfg.get("password") or ""
    if not user or not password:
        raise RuntimeError(
            "e-Portal kullanıcı/şifre yok."
        )
    portal = cfg["portal_url"]
    url, raw, _ = _open(opener, f"{portal}/login.aspx")
    html = raw.decode("utf-8", "replace")
    form = _hidden(html)
    form["txtUser"] = user
    form["txtPassword"] = password
    form["btnLogin"] = "Giriş"
    url, raw, _ = _open(opener, url or f"{portal}/login.aspx", form)
    html = raw.decode("utf-8", "replace")
    if 'id="txtPassword"' in html or "placeholder=\"Şifre\"" in html:
        raise RuntimeError("e-Portal girişi reddedildi — kullanıcı/şifre kontrol edin.")
    _log("e-Portal oturumu açıldı.", log)


def _uuids_from_html(html: str) -> list[str]:
    found: list[str] = []
    patterns = (
        r"showIncomingDespatchPopUp\('([0-9a-fA-F-]{36})'\)",
        r'hddUuid[^>]*value="([0-9a-fA-F-]{36})"',
        r"DespatchIncoming\.aspx\?uuid=([0-9a-fA-F-]{36})",
        r"CreateResponseAdvice\.aspx\?uuid=([0-9a-fA-F-]{36})",
    )
    for pat in patterns:
        for m in re.finditer(pat, html, re.I):
            u = m.group(1).lower()
            if u not in found:
                found.append(u)
        if found:
            return found
    return found


def _pager_target(html: str, page: int) -> str | None:
    m = re.search(
        rf'class="NumericButton"[^>]*__doPostBack\(&#39;([^&]+?)&#39;[^>]*>\s*{page}\s*<',
        html,
        re.I,
    )
    if m:
        return html_lib.unescape(m.group(1))
    m = re.search(
        rf"__doPostBack\('([^']+DataPager[^']+)'[^>]*>\s*{page}\s*<",
        html,
        re.I,
    )
    return html_lib.unescape(m.group(1)) if m else None


def list_incoming(
    opener: urllib.request.OpenerDirector, cfg: dict, log: LogFn | None = None
) -> list[str]:
    portal = cfg["portal_url"]
    max_pages = max(1, int(cfg.get("max_pages") or 4))
    url = f"{portal}/IncomingDespatches.aspx?s=New"
    uuids: list[str] = []
    try:
        _, raw, _ = _open(opener, url, timeout=120)
    except Exception as exc:
        _log(f"liste alınamadı {url}: {exc}", log)
        return []
    html = raw.decode("utf-8", "replace")
    if 'id="txtPassword"' in html:
        _log("liste oturumu düştü — yeniden giriş gerekli.", log)
        return []

    for page in range(1, max_pages + 1):
        got = _uuids_from_html(html)
        for u in got:
            if u not in uuids:
                uuids.append(u)
        if page >= max_pages:
            break
        target = _pager_target(html, page + 1)
        if not target:
            break
        post = _hidden(html)
        post["__EVENTTARGET"] = target
        post["__EVENTARGUMENT"] = ""
        try:
            _, raw, _ = _open(opener, url, post, timeout=120)
        except Exception as exc:
            _log(f"sayfa {page + 1} alınamadı: {exc}", log)
            break
        html = raw.decode("utf-8", "replace")

    _log(f"Gelen irsaliye: {len(uuids)} belge", log)
    return uuids


def soap_get_incoming(
    opener: urllib.request.OpenerDirector, cfg: dict, log: LogFn | None = None
) -> list[str]:
    """ServiceCaller'daki GetIncomingDespatches — SOAP + JSON PageMethod."""
    start = (date.today() - timedelta(days=int(cfg.get("days") or 7))).isoformat()
    end = date.today().isoformat()
    uuids: list[str] = []
    portal = cfg["portal_url"]
    payloads = [
        {"startDate": start, "endDate": end},
        {"StartDate": start, "EndDate": end},
        {
            "company": cfg.get("company") or "BC",
            "module": cfg.get("module") or "EDESPATCH",
            "startDate": start,
            "endDate": end,
        },
    ]
    for path in (
        "/IncomingDespatches.aspx/GetIncomingDespatches",
        "/ServiceCaller.aspx/GetIncomingDespatches",
    ):
        for payload in payloads:
            body = json.dumps(payload).encode("utf-8")
            try:
                _, raw, ctype = _open(
                    opener,
                    portal + path,
                    body,
                    {
                        "Content-Type": "application/json; charset=utf-8",
                        "X-Requested-With": "XMLHttpRequest",
                    },
                )
            except urllib.error.HTTPError:
                continue
            except Exception:
                continue
            text = raw.decode("utf-8", "replace")
            if "txtPassword" in text:
                continue
            if "json" in (ctype or "").lower() or text.lstrip().startswith("{") or text.lstrip().startswith("["):
                for u in re.findall(
                    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}",
                    text,
                ):
                    lu = u.lower()
                    if lu not in uuids:
                        uuids.append(lu)
                if uuids:
                    _log(f"GetIncomingDespatches: {len(uuids)} UUID", log)
                    return uuids
    return uuids


def _save_despatch(raw: bytes | str, dest_hint: str, log: LogFn | None = None) -> Path | None:
    if isinstance(raw, bytes):
        blob = raw
        text = raw.decode("utf-8", "replace")
    else:
        text = raw
        blob = raw.encode("utf-8")
    if "<DespatchAdvice" not in text and "<despatchAdvice" not in text:
        return None
    dest = save_original(blob)
    if dest:
        rec = parse_file(dest)
        plate = (rec or {}).get("plate") or ""
        _log(f"kayıt {(rec or {}).get('id') or dest.name}  {plate}", log)
    return dest


def download_xml(
    opener: urllib.request.OpenerDirector,
    cfg: dict,
    uuid: str,
    log: LogFn | None = None,
) -> Path | None:
    portal = cfg["portal_url"]
    url = f"{portal}/DespatchIncoming.aspx?uuid={uuid}"
    _, raw, ctype = _open(opener, url, timeout=60)
    if b"DespatchAdvice" in raw and (
        "xml" in (ctype or "").lower() or raw.lstrip().startswith(b"<")
    ):
        return _save_despatch(raw, uuid[:8], log)
    html = raw.decode("utf-8", "replace")
    saved = _save_despatch(html, uuid[:8], log)
    if saved:
        return saved
    form = _hidden(html)
    if re.search(r'name="btnExportXML"', html, re.I):
        post = dict(form)
        post["btnExportXML.x"] = "8"
        post["btnExportXML.y"] = "8"
        try:
            _, raw2, ctype2 = _open(opener, url, post, timeout=90)
        except Exception:
            raw2, ctype2 = b"", ""
        if b"DespatchAdvice" in raw2:
            dest = _save_despatch(raw2, uuid[:8], log)
            if dest:
                return dest
    targets: list[str] = []
    for m in re.finditer(
        r'<input[^>]*(?:id|name|value)="([^"]*(?:xml|ubl|indir|download)[^"]*)"[^>]*>',
        html,
        re.I,
    ):
        tag = m.group(0)
        name_m = re.search(r'name="([^"]+)"', tag, re.I)
        if name_m:
            targets.append(html_lib.unescape(name_m.group(1)))
    for m in re.finditer(r"__doPostBack\('([^']+)'", html):
        t = m.group(1)
        if re.search(r"xml|ubl|indir|download", t, re.I):
            targets.append(t)
    for target in dict.fromkeys(targets):
        if target.lower().startswith("btnexportxml"):
            continue
        post = dict(form)
        post["__EVENTTARGET"] = target
        post["__EVENTARGUMENT"] = ""
        try:
            _, raw2, ctype2 = _open(opener, url, post, timeout=90)
        except Exception:
            continue
        if b"DespatchAdvice" in raw2 or "xml" in (ctype2 or "").lower():
            dest = _save_despatch(raw2, uuid[:8], log)
            if dest:
                return dest
    return None


def sync(log: LogFn | None = None, cfg: dict | None = None) -> dict:
    cfg = cfg or load_config()
    IRSALIYE_DIR.mkdir(parents=True, exist_ok=True)
    dp_info = {"total": 0, "added": 0, "failed": 0}
    use_dp = bool(
        cfg.get("dp_login") and cfg.get("dp_password") and cfg.get("dp_corporate")
    )
    if use_dp:
        try:
            from digitalplanet import sync as sync_dp

            dp_info = sync_dp(log=log, cfg=cfg)
        except Exception as exc:
            _log(f"Digital Planet: {exc}", log)
    else:
        _log("Digital Planet ayarı yok — e-irsaliye çekilmedi.", log)
    summary = {
        "total": int(dp_info.get("total") or 0),
        "added": int(dp_info.get("added") or 0),
        "failed": int(dp_info.get("failed") or 0),
    }
    _log(
        f"senkron: {summary['total']} belge, {summary['added']} yeni, {summary['failed']} atlanan",
        log,
    )
    return summary


def _sync_pagero(log: LogFn | None = None, cfg: dict | None = None) -> dict:
    cfg = cfg or load_config()
    IRSALIYE_DIR.mkdir(parents=True, exist_ok=True)
    opener = _opener()
    login(opener, cfg, log)
    uuids = list_incoming(opener, cfg, log)
    have: set[str] = set()
    for path in IRSALIYE_DIR.glob("*.xml"):
        rec = parse_file(path)
        if rec and rec.get("uuid"):
            have.add(str(rec["uuid"]).lower())
    added = 0
    failed = 0
    for uuid in uuids:
        if uuid.lower() in have:
            continue
        dest = download_xml(opener, cfg, uuid, log)
        if dest:
            added += 1
            have.add(uuid.lower())
        else:
            failed += 1
            _log(f"indirilemedi {uuid}", log)
    summary = {"total": len(uuids), "added": added, "failed": failed}
    _log(
        f"e-Portal senkron: {summary['total']} belge, {added} yeni, {failed} atlanan",
        log,
    )
    return summary


def main(argv: list[str] | None = None) -> int:
    import sys

    args = argv if argv is not None else sys.argv[1:]
    cfg = load_config()
    if args[:1] == ["set"] and len(args) >= 3:
        cfg["username"] = args[1]
        cfg["password"] = args[2]
        save_config(cfg)
        print(f"Kaydedildi: {CONFIG_PATH}  kullanıcı={cfg['username']}")
        return 0
    try:
        sync(cfg=cfg)
    except Exception as exc:
        print(exc)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
