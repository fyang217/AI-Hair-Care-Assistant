"""Historical evaluation tool for rendering archived Search / Fetch pilot inputs."""
import argparse
import statistics
from collections import Counter
from pathlib import Path

from search_pilot import QUERIES, domain, read_json, source_type
import csv

FIELDS=("product_name","full_formula","partial_ingredients","positioning","specification",
        "price","review_text","filing")

def csv_value(value):
    if isinstance(value,str) and value.lstrip().startswith(("=","+","-","@","\t","\r")):
        return "'"+value
    return value

def write_csv(path,rows,cols):
    with path.open("w",encoding="utf-8-sig",newline="") as file:
        writer=csv.DictWriter(file,cols,extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({k:csv_value(v) for k,v in row.items()})

def rate(n,d):
    return f"{n}/{d}（{n/d:.1%}）" if d else "不适用（无请求）"

def timing(rows):
    values=sorted(row["ms"] for row in rows)
    if not values:
        return "无样本"
    p95=values[(95*len(values)+99)//100-1]
    return f"中位数 {statistics.median(values):g} ms，P95 {p95} ms，最大 {max(values)} ms（n={len(values)}）"

def cell(value):
    return str(value).replace("|","\\|").replace("\n"," ").replace("\r"," ")

def render(out):
    by_search={row["query_id"]:row for row in read_json(out/"search_capture.json")}
    captures=read_json(out/"fetch_capture.json") if (out/"fetch_capture.json").exists() else []
    local=read_json(out/"local_products.json")
    audit=read_json(out/"audit_annotations.json")
    summary,search_rows,fetch_rows=[],[],[]
    for qid,query in QUERIES:
        event=by_search.get(qid,{"results":[],"error":"未调用"})
        items=event["results"]
        annotated=audit["search"].get(qid,{})
        relevant=set(annotated.get("relevant",[]))
        official=set(annotated.get("official",[]))
        official_product=set(annotated.get("official_product",[]))
        assert official_product<=official and relevant<=set(range(1,len(items)+1))
        assert official<=set(range(1,len(items)+1))
        assert all(source_type(items[i-1]["url"]) in ("regulator","brand_official") for i in official)
        fetched=[r for r in captures if r["query_id"]==qid]
        base=dict(query_id=qid,query=query,result_count=len(items),status=event.get("status"),
                  response_ms=event.get("ms"),observed_at=event.get("at"),error=event["error"])
        summary.append(dict(base,category=qid[0],related_product_lead=bool(relevant),
                            official_source_hit=bool(official),official_product_hit=bool(official_product),
                            fetch_attempts=len(fetched),fetch_usable=sum(
                                audit["fetch"].get(r["page_id"],{}).get("state",r["fetch_state"])=="usable" for r in fetched),
                            assessment=annotated.get("note","")))
        for i,item in enumerate(items,1):
            search_rows.append(dict(base,rank=i,title=item["title"],source_url=item["url"],
                                    domain=domain(item["url"]),
                                    source_type=source_type(item["url"]),summary=item["summary"] or item["snippet"],
                                    related_product_lead=i in relevant,official_source_hit=i in official,
                                    official_product_hit=i in official_product,evidence_scope="仅发现线索"))
        if not items:
            search_rows.append(dict(base,evidence_scope="无搜索线索"))
    for row in captures:
        a=audit["fetch"].get(row["page_id"],{})
        fields=a.get("fields",{})
        state=a.get("state",row["fetch_state"])
        assert set(fields)<=set(FIELDS)
        if fields:
            assert state=="usable", row["page_id"]
        fetch_rows.append(dict(row,fetch_state=state,source_type=source_type(row["url"]),domain=domain(row["url"]),
                               protocol_has_content=bool(row["chars"]),fetched_usable=state=="usable",
                               failure_reason=row["error"] or (a.get("note","访问提示页") if state=="access_notice"
                                                              else "无正文" if not row["chars"] else ""),
                               response_ms=row["ms"],content_file=f'pages/{row["page_id"]}.json' if row["chars"] else "",
                               matched_local_id=a.get("local",""),new_product_key="；".join(a.get("new_keys",[])),
                               market=a.get("market",""),assessment=a.get("note",""),
                               **{f:fields.get(f,"") for f in FIELDS}))
    write_csv(out/"query_results.csv",summary,list(summary[0]))
    write_csv(out/"search_results.csv",search_rows,
              "query_id query result_count rank title source_url domain source_type summary status response_ms observed_at error related_product_lead official_source_hit official_product_hit evidence_scope".split())
    write_csv(out/"fetch_results.csv",fetch_rows,
              "page_id query_id rank url domain source_type at title status response_ms protocol_has_content fetch_state fetched_usable failure_reason chars content_file matched_local_id new_product_key market assessment".split()+list(FIELDS))
    nonempty=sum(bool(row["results"]) for row in by_search.values())
    status200=sum(row["status"]==200 for row in by_search.values())
    usable=sum(row["fetched_usable"] for row in fetch_rows)
    bill_search=sum(row["billable"] is True for row in by_search.values())
    bill_fetch=sum(row["billable"] is True for row in captures)
    unknown=sum(row["billable"] is None for row in [*by_search.values(),*captures])
    new={}
    for row in fetch_rows:
        if row["new_product_key"]:
            assert row["product_name"] and row["fetched_usable"]
            for key in row["new_product_key"].split("；"):
                new.setdefault(key,row)
    coverage=Counter(row["source_type"] for row in fetch_rows if row["fetched_usable"])
    lines=[
      "# DeepSeek Search / Fetch 能力试点",
      "",f"执行时间（北京时间）{min(r['at'] for r in by_search.values()) if by_search else '未执行'} 至 {max(r['at'] for r in [*by_search.values(),*captures]) if by_search else '未执行'}。",
      "",
      "## 实际调用、成功率与费用","",
      f"- Search 发起 **{len(by_search)} 次**，HTTP 200 为 {rate(status200,len(by_search))}，非空返回成功率为 **{rate(nonempty,len(by_search))}**，共 {sum(len(r['results']) for r in by_search.values())} 条索引结果。",
      f"- Fetch 发起 **{len(captures)} 次**，HTTP 200 且返回非空正文 {rate(sum(bool(r['chars']) for r in captures),len(captures))}，排除访问提示页后的可用率 **{rate(usable,len(captures))}**。可用页面仍可能只有系列或导航内容。",
      f"- 有相关产品线索的查询 {rate(sum(r['related_product_lead'] for r in summary),len(summary))}。相关官方来源命中率 **{rate(sum(r['official_source_hit'] for r in summary),len(summary))}**，其中命中可辨认的官方具体产品或直接涵盖目标洗发产品的系列页 {rate(sum(r['official_product_hit'] for r in summary),len(summary))}。",
      f"- Search 耗时 {timing(list(by_search.values()))}。Fetch 耗时 {timing(captures)}。包含响应和记录到的错误，不代表生产流量。",
      f"- 可确认可计费 Search {bill_search} 次、Fetch {bill_fetch} 次，按官方每千次 $0.75 推算 **US${(bill_search+bill_fetch)*0.00075:.5f}**。计费状态未知 {unknown} 次。接口未返回逐次账单金额或 token usage，账户实际扣费未知。本轮没有调用聊天模型。",
      "",
      "配置为每条查询一次 Search、最多两页 Fetch，无重试，单次网络超时 18 秒。Fetch 仅从返回排名中手工挑选已知公开来源。命中率按本轮 20 条有意设计的查询计算，不外推市场覆盖。",
      "",
      "[DeepSeek Search & Fetch 官方接口与计费口径](https://api-docs.deepseek.com/guides/search/)",
      "","## 20 条查询逐项结果","",
      "| ID | 原始查询 | 结果数 | Search ms | 相关产品线索 | 相关官方来源 | Fetch 可用/尝试 | 核对说明 |",
      "|---|---|---:|---:|---|---|---:|---|"]
    for r in summary:
        vals=(r["query_id"],r["query"],r["result_count"],r["response_ms"] if r["response_ms"] is not None else "—",
              "有" if r["related_product_lead"] else "无","有" if r["official_source_hit"] else "无",
              f"{r['fetch_usable']}/{r['fetch_attempts']}",r["assessment"] or r["error"])
        lines.append("| "+" | ".join(map(cell,vals))+" |")
    lines.extend(["","## 可用页面的字段覆盖","",
      "| 字段 | 有可回查字段的页面 / 可用页面 |","|---|---:|"])
    labels=("具体产品名称","完整配方","部分成分","产品定位","规格","价格","消费者评价原文","监管备案")
    for field,label in zip(FIELDS,labels):
        lines.append(f"| {label} | {rate(sum(bool(r[field]) for r in fetch_rows),usable)} |")
    lines.append("")
    lines.append("字段按具体产品关联、手工核对 Fetch Markdown 后计数。空白代表缺少可用证据。站点通用价格、评论入口或计数、摘要中的配方与备案编号均不计入。完整配方须在官方产品上下文有完整清单且能对应页面市场和版本。")
    lines.append(f"可用页面来源分布为 {dict(coverage)}。")
    lines.extend(["","## 对照本地 17 款与新增候选","",
                  f"只读对照本地 {len(local)} 条配方产品（见 `local_products.json`）。按产品线及具体洗发品身份去重，同一款的补充装、不同包装或海外版本不直接重复计为新增。**{len(new)} 款新候选有 Fetch 页面证据**。地区不同的候选保留市场标签。"])
    lines.append(f"其中 {sum(row['source_type']=='brand_official' for row in new.values())} 款有品牌来源，其他由电商或第三方页面提供身份线索。")
    for name,row in new.items():
        lines.append(f"- {name} · {row['market'] or '市场未确认'} · {row['page_id']}。[页面]({row['url']})")
    lines.append("")
    lines.append("搜索结果中另有 P009、P012、P013、P014 的同系列或近似产品线索。它们没有在本轮形成足以确认同款版本的 Fetch 证据。")
    overlaps={}
    for row in fetch_rows:
        if row["matched_local_id"]:
            overlaps.setdefault(row["matched_local_id"],[]).append(row["page_id"])
    if overlaps:
        lines.extend(["","已取证的本地产品线或候选映射如下。映射仅表示有相符线索，具体销售版本仍见页面审核备注。"])
        for pid,ids in overlaps.items():
            lines.append(f"- {pid} · {', '.join(ids)}")
    lines.extend(["","## 限制与下一步",""])
    lines.extend("- "+note for note in audit["notes"])
    lines.extend(["","原始观察见 `search_capture.json`、`fetch_capture.json` 及 `pages/`。字段判断见 `audit_annotations.json`。仅通过 Fetch 留存的 Markdown 用于字段核验。未修改或写入正式 SQLite。"])
    (out/"SEARCH_PILOT_REPORT.md").write_text("\n".join(lines)+"\n",encoding="utf-8")

if __name__=="__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("out",type=Path)
    render(parser.parse_args().out)
