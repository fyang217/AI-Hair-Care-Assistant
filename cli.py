import argparse
import json
from agent import controller

parser=argparse.ArgumentParser(description="本地洗护检索和推荐")
parser.add_argument("query")
parser.add_argument("--llm",action="store_true")
args=parser.parse_args()
result=controller(args.query,use_llm=args.llm)
print(json.dumps(result,ensure_ascii=False,indent=2))
