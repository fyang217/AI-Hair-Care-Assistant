import streamlit as st
from agent import catalog, controller, feedback, safe_url

CLAIM_LABELS={"reason":"推荐理由","formula_analysis":"配方依据",
              "review_summary":"消费者评价总结","suitable_needs":"适用需求",
              "potential_limitations":"潜在不足","unknowns":"信息缺口"}

st.set_page_config(page_title="AI Hair Care Assistant", page_icon="🫧", layout="wide")
st.title("AI Hair Care Assistant")
st.caption("洗护需求 · 配方 · 价格 · 评价证据")

@st.cache_data
def options():
    return {pid:p["name"] for pid,p in catalog().items()}

def source(label, record):
    url=safe_url(record["url"])
    if url:
        st.markdown(f"[{label}]({url})")
    elif record["url"]:
        st.text(str(record["url"]))
    else:
        st.caption("来源 URL 未提供")
    st.caption(f'{record["dataset"]} / {record["sheet"]} / 第 {record["row_number"]} 行 · '
               f'{record["observed_at"] or "采集时间未提供"} · {record["state"]}')

def analysis_view(product):
    analysis=product.get("ai_analysis")
    if analysis:
        st.markdown("**AI 推荐分析**")
        st.caption(f'本次传入有原文的评价 {analysis["reviews_sent"]} 条。'
                   '收录样本数见评价证据。')
        st.caption(analysis["version_notice"])
        for field, label in CLAIM_LABELS.items():
            if field=="unknowns":
                continue
            claim=analysis["claims"].get(field)
            if not claim:
                st.caption(label+" · 证据不足")
                continue
            st.text(f'{label} [{claim["evidence_id"]}] · {claim["status"]}')
            if claim["status"]!=claim["basis_type"]:
                st.text("基于证据的推测：" + claim["text"])
                st.text(f'{claim["basis_type"]} [{claim["evidence_id"]}]：{claim["quote"]}')
            else:
                st.text(claim["quote"])
            source(f'查看 {claim["evidence_id"]} 来源',claim["record"])
            with st.expander(f'原始证据 {claim["evidence_id"]} · {label}'):
                st.text(claim["quote"])
                st.json(claim["record"]["data"],expanded=False)
        if analysis["unknowns"]:
            st.text("暂无证据或待核实事项")
            for item in analysis["unknowns"]:
                st.text(item)

def web_evidence(item):
    st.caption(f'外部网页 · {item["market"]}')
    for record in item["evidence"]:
        data=record["data"]
        eid=record["id"]
        if record["kind"] in {"official","listing"}:
            st.text(f'已核实事实（网页列示的商品名称） [{eid}]：{item["name"]}')
            if data.get("positioning"):
                st.text(f'品牌官方宣称 [{eid}]：{data["positioning"]}')
            if data.get("partial_ingredients"):
                st.text(f'品牌官网披露的部分成分 [{eid}]：{data["partial_ingredients"]}')
            if data.get("specification"):
                st.text(f'页面规格 [{eid}]：{data["specification"]}')
            source(f'查看 {eid} 来源',record)
        with st.expander(f'外部原始证据 {eid}'):
            source("打开外部来源",record)
            st.json(data,expanded=False)
    if item["gaps"]:
        st.caption("暂无证据或待核实事项")
        for gap in item["gaps"]:
            st.text(gap)
    analysis_view(item)

def evidence(product):
    st.write(product["reason"])
    analysis_view(product)
    for quote in product["quotes"]:
        st.write(f'已核验价格证据 ¥{quote["data"]["amount"]:.2f}')
        source("价格来源",quote)
    with st.expander("配方及备案"):
        item=product["formula"]
        data=item["data"]
        st.text(data.get("version_or_registration") or "备案编号或版本未提供")
        st.text(data.get("ingredients_as_published") or "成分原文未提供")
        if data.get("other_trace_ingredients"):
            st.text("其他微量成分")
            st.text(data["other_trace_ingredients"])
        source("配方来源",item)
    with st.expander(f'评价证据（{len(product["reviews"])} 条）'):
        summary=product["review_summary"]
        if not summary["count"]:
            st.write("本轮未收录该产品的消费者评价。")
        else:
            st.caption("评价摘要来自少量样本。请核对来源、购买版本和抽样范围。")
            for label, key in (("满意点","positive"),("不满意点","negative")):
                if summary[key]:
                    st.text(label+" · "+"；".join(dict.fromkeys(summary[key])))
        for review in product["reviews"]:
            data=review["data"]
            source_type={"official":"官网发布","marketplace":"电商渠道"}[data["source_type"]]
            st.caption(" · ".join(str(v) for v in (source_type,data.get("platform"),data.get("review_date"),
                       data.get("purchase_spec"),data.get("collection_scope"),data.get("incentive")) if v))
            if data.get("original_text"):
                st.text(data["original_text"])
            else:
                st.caption("此快照仅保留来源和汇总标注。")
            source("评价来源",review)
    with st.expander("官网及电商资料"):
        for item in product["evidence"]:
            if item["kind"] not in {"official","listing","price","candidate"}:
                continue
            data=item["data"]
            from_keys=("official_name","product_name","candidate_name","market","positioning","introduction",
                       "specification","store","store_type","rating","review_total","displayed_price","official_price",
                       "amount","currency","price_type","conditions","reason","missing_reason")
            st.text(" · ".join(str(data[k]) for k in from_keys if data.get(k) is not None))
            source("打开资料来源",item)

names=options()
recommend, compare, comments=st.tabs(["洗护推荐","产品对比","反馈"])
with recommend:
    with st.form("needs"):
        query=st.text_area("输入洗护需求",placeholder="例如：头皮容易出油，头发细软塌，预算 100 元以内")
        model=st.checkbox("使用已配置的 LLM（DeepSeek，会发送需求文本）")
        web_enabled=st.checkbox("允许按需搜索公开网页（DeepSeek；可能产生搜索费用）",value=True)
        submitted=st.form_submit_button("查找产品",type="primary")
    if submitted and query.strip():
        st.session_state.result=controller(query,use_llm=model,allow_web=web_enabled and model)
    if "result" in st.session_state:
        result=st.session_state.result
        demand=result["demand"]
        if demand.get("llm_error"):
            st.warning(f'{demand["llm_error"]}。本次已回退到本地解析。')
        elif demand["parser"] == "DeepSeek":
            st.success(f'DeepSeek API 已成功解析本次需求（{demand["llm_model"]}）。')
        analysis=result.get("rag")
        if analysis and analysis["status"] != "ok":
            st.warning(analysis["message"])
            rejected=analysis.get("rejected",[])
            if rejected:
                with st.expander("未通过校验的结论",expanded=True):
                    names_by_id={p["id"]:p["name"] for p in result.get("evidence_results",[])}
                    for issue in rejected:
                        label=CLAIM_LABELS[issue["field"]]
                        st.text(f'{names_by_id.get(issue["product_id"],issue["product_id"])} · {label} · {issue["reason"]}')
        elif analysis:
            st.caption(f'证据生成模型：{analysis["model"]} · 引用编号及原文片段已校验')
        st.info(result["notice"])
        labels={"intent":"意图","scalp":"头皮","hair":"发质","goals":"目标","avoid":"成分偏好",
                "budget_min":"预算下限","budget_max":"预算上限","parser":"解析方式"}
        st.subheader("需求拆解")
        st.json({label:result["demand"][key] for key,label in labels.items()},expanded=True)
        for word,note in result["ingredient_notes"].items():
            st.text(f"{word} · {note}")
        web=result.get("web")
        if not result["products"] and not result["candidates"] and not (web and web["candidates"]):
            st.caption("当前查询没有证据充分的产品结果。可尝试产品名、成分名或具体洗护诉求。")
        if web:
            with st.expander("本次工具调用与费用",expanded=bool(web["candidates"])):
                for event in web["tools"]:
                    st.text(f'{event["tool"]} · {event["status"]} · {event["reason"]} · {event["outcome"]}')
                    if event.get("ms") is not None:
                        st.caption(f'{event["ms"]} ms')
                    url=safe_url(event.get("url"))
                    if url:
                        st.markdown(f'[查看请求页面]({url})')
                st.caption(f'已知 Search / Fetch 工具费估算 US${web["cost_usd"]:.5f}；'
                           f'计费状态未知 {web["unknown_billing"]} 次。实际扣费以账户为准。')
                for usage in web["model_usage"]:
                    st.caption(f'{usage["stage"]} · 模型输入 {usage.get("prompt_tokens","未知")} tokens，'
                               f'输出 {usage.get("completion_tokens","未知")} tokens。')
        if web and web["candidates"]:
            st.subheader("本地与外部证据候选")
        for product in result.get("evidence_results",result["products"]):
            st.subheader(product["name"])
            if product.get("origin")=="web":
                web_evidence(product)
            else:
                st.caption("本地 SQLite")
                evidence(product)
        if result["candidates"]:
            st.subheader("价格待核候选")
            for product in result["candidates"]:
                st.markdown("**"+product["name"]+"**")
                evidence(product)
with compare:
    ids=st.multiselect("选择两个或三个产品", list(names), format_func=lambda pid:names[pid],max_selections=3)
    if len(ids)>=2:
        pair=controller("比较产品",selected=ids)["products"]
        for col,product in zip(st.columns(len(pair)),pair):
            with col:
                st.subheader(product["name"])
                evidence(product)
with comments:
    with st.form("feedback"):
        rating=st.slider("使用体验评分",1,5,3)
        message=st.text_area("建议（请勿填写个人信息）",max_chars=2000)
        if st.form_submit_button("保存反馈"):
            if not message.strip():
                st.warning("请填写反馈内容。")
            else:
                feedback(message.strip(),rating)
                st.success("反馈已保存到本机。")
