"""Bounded live DeepSeek Search/Fetch pilot. No Agent or database writes."""
import argparse
import csv
import json
import re
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from dotenv import dotenv_values

QUERIES = [
  [
    "A01",
    "头皮容易出油 细软塌 控油蓬松洗发水 品牌官网 产品"
  ],
  [
    "A02",
    "欧莱雅PRO 纯净控油洗发水 头皮控油 产品成分 规格"
  ],
  [
    "A03",
    "油头发根易扁塌 想找洗后清爽的洗发水 京东 控油蓬松 商品"
  ],
  [
    "A04",
    "卡诗 双重功能洗发水 清爽型 500ml 官网 控油 蓬松"
  ],
  [
    "B01",
    "经常有头屑 想找去屑洗发水 吡罗克酮乙醇胺盐 品牌官方产品"
  ],
  [
    "B02",
    "薇姿 德科丝 去屑洗发水 油性发质 官方 配方"
  ],
  [
    "B03",
    "清扬男士 深层净澈去屑洗发露 备案 产品"
  ],
  [
    "B04",
    "海飞丝 控油去屑洗发水 规格 淘宝 天猫 商品评价"
  ],
  [
    "C01",
    "漂染发尾干枯打结 柔顺修护洗发水 品牌官网 产品"
  ],
  [
    "C02",
    "卡诗 Première 净水修护洗发水 250ml 官网 成分"
  ],
  [
    "C03",
    "欧莱雅专业美发 Absolut Repair 黄金修护洗发水 品牌官网"
  ],
  [
    "C04",
    "粗硬发洗完毛躁 修护洗发水 规格 价格 抖音商城 公开商品"
  ],
  [
    "D01",
    "草本洗发水 油头 茶籽 无患子 品牌官网 产品"
  ],
  [
    "D02",
    "霸王 侧柏叶洗发水 官方产品 备案 成分"
  ],
  [
    "D03",
    "滋源 无患子 控油洗头水 拼多多 规格 公开商品"
  ],
  [
    "D04",
    "含何首乌或生姜成分的草本洗发水 品牌官网 化妆品备案"
  ],
  [
    "E01",
    "蜂花 控油蓬松洗发露 750ml 品牌官网 京东"
  ],
  [
    "E02",
    "Off&Relax 星缎蕴光修护洗发水 300ml 官方 淘宝"
  ],
  [
    "E03",
    "资生堂 芯诗珀莉 头皮生机洗发水 官网 产品"
  ],
  [
    "E04",
    "馥绿德雅 复合精油洗发水 头皮清洁 官网 配方"
  ]
]
BASE = "https://api.deepseek.com/v1/tools/"
KEY = "DEEPSEEK_API_KEY"
MARKET = ("jd.com","taobao.com","tmall.com","yangkeduo.com","pinduoduo.com","douyin.com","jinritemai.com",
          "suning.com","hktvmall.com","scntwc.com","coupang.com")
OFFICIAL = ("nmpa.gov.cn","beeflower-cn.com","proya-group.com","kerastase.fr","kerastase-usa.com",
            "kerastase.co.uk","kerastase.cn","kerastase.com.cn","lorealprofessionnel.co.uk",
            "lorealprofessionnel.com","lorealprofessionnel.cn","lorealprofessionnel.com.cn",
            "vichy.com","vichy.com.cn","vichy.cn","vichy.co.uk","vichyusa.com","clearhaircare.com.cn",
            "headandshoulders.com","headandshoulders.com.cn","seeyoung.com","shiseido-professional.com",
            "renefurterer.com","zdjt.com","bawang.com.cn","proya.com","renata-hair.com.tw","loccitane.com",
            "head-shoulders.com.cn","ukzooly.com.cn","vichy-me.com","vichy.com.au","chatzutang.com",
            "no3.co.jp","lorealpartnershop.com","offrelax.cn","see-young.com.cn","lorealprofessionnel.ch",
            "lorealprofessionnel.ca","lorealprofessionnel.it","lorealprofessionnel-me.com")
RESTRICTED = re.compile(r"你好，请登录|登录墙|登录后查看|登录/注册|扫码登录|验证码|安全验证|访问受限|仅App|风险验证|captcha|access denied|please wait",re.I)

class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None

def stamp():
    return datetime.now(timezone(timedelta(hours=8))).isoformat(timespec="seconds")

def domain(url):
    try:
        parts=urlsplit(url)
        if (parts.scheme in ("https","http") and parts.hostname and not parts.username
                and not parts.password and parts.port in (None,80,443)):
            return parts.hostname.lower()
    except (TypeError,ValueError):
        pass
    return ""

def matches(host, domains):
    return any(host == d or host.endswith("." + d) for d in domains)

def source_type(url):
    host=domain(url)
    if matches(host,("nmpa.gov.cn",)):
        return "regulator"
    if matches(host,OFFICIAL):
        return "brand_official"
    if matches(host,MARKET):
        return "marketplace"
    if matches(host,("bevol.cn",)):
        return "third_party_reference"
    return "unclassified"

def eligible_fetch(url,title=""):
    host=domain(url)
    if not host or source_type(url)=="unclassified":
        return False
    from urllib.parse import unquote
    parts=urlsplit(url)
    if parts.scheme != "https" or parts.port not in (None,443):
        return False
    return not (RESTRICTED.search(title) or re.search(
        r"(?:(?:^|[\./_-])(?:login|passport|captcha|verify|challenge|risk_handler|signin)(?:[\./_-]|$))",
        host+unquote(parts.path),re.I))

def request_api(tool, payload, key, *, timeout=18):
    request=Request(BASE+tool,method="POST",data=json.dumps(payload,ensure_ascii=False).encode(),
                    headers={"Authorization":"Bearer "+key,"Content-Type":"application/json"})
    start=time.perf_counter()
    status=None
    try:
        with build_opener(NoRedirect()).open(request,timeout=timeout) as response:
            status=response.status
            result=json.loads(response.read(4_000_000))
        if not isinstance(result,dict):
            raise ValueError("invalid envelope")
        body=result.get("results") if tool=="search" else result.get("content")
        expected=list if tool=="search" else str
        if not isinstance(body,expected):
            raise ValueError("invalid field type")
        return dict(status=status,error="",ms=round((time.perf_counter()-start)*1000),
                    billable=bool(body if tool=="search" else body.strip()),data=result)
    except HTTPError as e:
        status=e.code
        e.close()
        error={401:"鉴权失败",402:"账户余额不足",403:"服务安全策略拒绝",404:"无可用内容（目标拒绝、不可达或不可解析等）",
               429:"接口或域名限流",504:"下游超时"}.get(status,"HTTP 错误")
    except (URLError,TimeoutError,OSError):
        error="网络错误或超时（服务端是否收到未知）"
    except (ValueError,TypeError):
        error="响应格式或大小异常"
    return dict(status=status,error=error,ms=round((time.perf_counter()-start)*1000),
                billable=None if status is None or status==200 else False,data={})

def write_json(path, data):
    path.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding="utf-8")

def read_json(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))

def search(out,key):
    path=out/"search_capture.json"
    if path.exists():
        raise SystemExit("Search 已存在，防止重复计费")
    events=[]
    for qid,query in QUERIES:
        call=request_api("search",{"query":query,"count":8,"need_content":False},key)
        results=[{k:r.get(k) for k in ("title","url","summary","snippet","site_name","date","rerank_score")}
                 for r in call.pop("data").get("results",[]) if isinstance(r,dict)]
        events.append(dict(query_id=qid,query=query,at=stamp(),**call,results=results))
        write_json(path,events)
        if call["status"] in (401,402):
            break

def fetch(out,key,selection):
    path=out/"fetch_capture.json"
    if path.exists():
        raise SystemExit("Fetch 已存在，防止重复计费")
    search_events={r["query_id"]:r for r in read_json(out/"search_capture.json")}
    choices=read_json(selection)
    events=[]
    validated=[]
    seen=set()
    for qid, indices in choices.items():
        if qid not in search_events or len(indices)>2 or len(set(indices))!=len(indices):
            raise ValueError("非法选择或超过两页")
        for index in indices:
            items=search_events[qid]["results"]
            if type(index) is not int or not 1<=index<=len(items):
                raise ValueError("非法搜索排名")
            item=items[index-1]
            url=item["url"]
            if not eligible_fetch(url,item["title"] or "") or url in seen:
                raise ValueError("URL 不在允许范围、已知受限或重复")
            seen.add(url)
            validated.append((qid,index,url))
    (out/"pages").mkdir(exist_ok=True)
    write_json(path,events)
    for qid,index,url in validated:
        call=request_api("fetch",{"url":url},key)
        data=call.pop("data")
        body=data.get("content","")
        page_id=f"{qid}-{index}"
        state="usable" if body.strip() else "failed"
        if body.strip() and (RESTRICTED.search((data.get("title") or "")+" "+body[:1000])
                             or re.search(r"欢迎登录|页面不存在|暂时无法访问",data.get("title") or "")):
            state="access_notice"
        if body.strip():
            write_json(out/"pages"/(page_id+".json"),{"title":data.get("title"),"content":body})
        events.append(dict(page_id=page_id,query_id=qid,rank=index,url=url,at=stamp(),
                           title=data.get("title"),chars=len(body),fetch_state=state,**call))
        write_json(path,events)
        if call["status"] in (401,402):
            break

def local_products(path):
    db=sqlite3.connect(path.resolve().as_uri()+"?mode=ro",uri=True)
    try:
        return [dict(product_id=pid,brand=data.get("brand"),product_name=data.get("product_name_sku"))
                for pid,payload in db.execute("SELECT product_id,payload FROM records WHERE kind='formula' ORDER BY product_id")
                for data in (json.loads(payload),)]
    finally:
        db.close()

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase",choices=("search","fetch","local"))
    parser.add_argument("--out",required=True,type=Path)
    parser.add_argument("--env-file",type=Path)
    parser.add_argument("--selection",type=Path)
    parser.add_argument("--db",type=Path,default=Path(__file__).parent/"data"/"haircare.sqlite")
    args=parser.parse_args()
    args.out.mkdir(parents=True,exist_ok=True)
    if args.phase=="local":
        write_json(args.out/"local_products.json",local_products(args.db))
        return
    if not args.env_file or not args.env_file.is_file():
        parser.error("需要现有 .env 文件路径")
    key=(dotenv_values(args.env_file,interpolate=False).get(KEY) or "").strip()
    if not key:
        parser.error("现有 .env 缺少 DEEPSEEK_API_KEY")
    if args.phase=="search":
        search(args.out,key)
    elif not args.selection:
        parser.error("Fetch 需要预先审核的 URL 排名清单")
    else:
        fetch(args.out,key,args.selection)

if __name__=="__main__":
    main()
