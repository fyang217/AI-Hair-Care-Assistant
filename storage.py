import argparse
import hashlib
import json
import re
import sqlite3
from contextlib import closing
from pathlib import Path

DB = Path(__file__).parent / "data" / "haircare.sqlite"
SOURCES = {
    "formula_records_17": "formula",
    "official_products": "official",
    "ecommerce_products": "listing",
    "ecommerce_reviews": "review",
    "price_history": "price",
    "unmatched_products": "candidate",
}
REAL_REVIEW_SOURCES = {"official", "marketplace"}
LEGACY_MARKETPLACES = {"京东", "淘宝", "天猫", "拼多多", "抖音商城"}

def review_source_type(row, dataset):
    """Respect explicit provenance. Infer only for the existing round-one schema."""
    if "source_type" in row:
        value = row["source_type"]
        return value if isinstance(value,str) and value in ("official","marketplace","synthetic") else "unknown"
    if dataset == "round1":
        if row.get("platform") == "品牌官网":
            return "official"
        if row.get("platform") in LEGACY_MARKETPLACES:
            return "marketplace"
    return "unknown"

def decode_record(row):
    record = dict(row, data=json.loads(row["payload"]))
    if record["kind"] == "review":
        record["data"]["source_type"] = review_source_type(record["data"], record["dataset"])
    return record

def is_production_record(record):
    if record["kind"] == "review":
        return record["data"]["source_type"] in REAL_REVIEW_SOURCES
    return not (record["sheet"] == "review_analysis"
                and record["data"].get("source_type") == "synthetic")

def connect(path=DB, readonly=False):
    if readonly:
        return sqlite3.connect(Path(path).resolve().as_uri()+"?mode=ro", uri=True)
    return sqlite3.connect(path)

def build(original, market, output=DB):
    from openpyxl import load_workbook
    output = Path(output)
    if output.exists():
        raise FileExistsError(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(".building")
    if temporary.exists():
        raise FileExistsError(temporary)
    try:
        with closing(connect(temporary)) as db, db:
            db.executescript("""
            CREATE TABLE imports (dataset TEXT PRIMARY KEY, filename TEXT, sha256 TEXT);
            CREATE TABLE records (id INTEGER PRIMARY KEY, dataset TEXT, sheet TEXT,
                row_number INTEGER, product_id TEXT, kind TEXT, state TEXT,
                url TEXT, observed_at TEXT, payload TEXT,
                UNIQUE(dataset,sheet,row_number));
            CREATE INDEX by_product ON records(product_id,kind);
            """)
            for dataset, path in (("v0.9", Path(original)), ("round1", Path(market))):
                db.execute("INSERT INTO imports VALUES (?,?,?)",
                           (dataset,path.name,hashlib.sha256(path.read_bytes()).hexdigest()))
                workbook = load_workbook(path, read_only=True, data_only=False)
                try:
                    for sheet in workbook:
                        iterator = iter(sheet.values)
                        headers = next(iterator)
                        for number, values in enumerate(iterator, 2):
                            if not any(v is not None for v in values):
                                continue
                            row = dict(zip(headers, values))
                            pid = str(row.get("product_id") or "")
                            pid = pid if re.fullmatch(r"P\d{3}",pid) else None
                            kind = SOURCES.get(sheet.title, "archive")
                            url = row.get("source_url") or row.get("official_source_url")
                            if sheet.title in {"ecommerce_reviews","review_analysis"} and row.get("source_type") == "synthetic":
                                raise ValueError("模拟评价及标注须保存在独立测试数据集")
                            if kind == "review":
                                state = row.get("match_status") or "待核验"
                            elif kind == "formula":
                                state = "原始配方证据，适用版本见原表"
                            elif kind == "official":
                                state = "官方页面资料，版本和缺失项见原记录"
                            else:
                                state = "待核验" if kind in {"listing","candidate","price"} else "原表记录"
                            db.execute("""INSERT INTO records(dataset,sheet,row_number,product_id,
                                kind,state,url,observed_at,payload) VALUES (?,?,?,?,?,?,?,?,?)""",
                                (dataset,sheet.title,number,pid,kind,state,url,row.get("collected_at"),
                                 json.dumps(row,ensure_ascii=False,default=str)))
                finally:
                    workbook.close()
        temporary.replace(output)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise

def records(path=DB, product_id=None, kinds=None):
    with closing(connect(path, readonly=True)) as db:
        db.row_factory=sqlite3.Row
        sql="SELECT * FROM records WHERE product_id IS NOT NULL"
        params=[]
        if product_id:
            sql+=" AND product_id=?"
            params.append(product_id)
        if kinds:
            sql+=" AND kind IN ("+",".join("?" for _ in kinds)+")"
            params.extend(kinds)
        cursor=db.execute(sql+" ORDER BY id",params)
        rows = map(decode_record, cursor)
        return [r for r in rows if is_production_record(r)]

def evidence_record(evidence_id, path=DB):
    if not isinstance(evidence_id,str) or not re.fullmatch(r"E[1-9][0-9]{0,17}",evidence_id):
        return None
    with closing(connect(path, readonly=True)) as db:
        db.row_factory=sqlite3.Row
        row=db.execute("SELECT * FROM records WHERE id=?", (int(evidence_id[1:]),)).fetchone()
        if row is None:
            return None
        record = decode_record(row)
        return record if is_production_record(record) else None

if __name__=="__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("original")
    parser.add_argument("market")
    parser.add_argument("--output",type=Path,default=DB)
    args=parser.parse_args()
    build(args.original,args.market,args.output)
