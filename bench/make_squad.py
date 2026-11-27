"""Build a public, reproducible SQuAD 1.1 fixture (CC BY-SA 4.0, Rajpurkar et al. 2016).

Each task = one dev question, sources = the gold paragraph + 2 other paragraphs
from the same article (realistic same-topic distractors), shuffled with a fixed seed.

    curl -O https://rajpurkar.github.io/SQuAD-explorer/dataset/dev-v1.1.json
    python bench/make_squad.py dev-v1.1.json 48 > bench/data/squad48.jsonl
    python bench/make_squad.py dev-v1.1.json 48 5678 bench/data/squad48.jsonl > bench/data/squad48-holdout.jsonl
"""
import json
import random
import sys


def main(path, n, seed=1234, exclude=None):
    rng = random.Random(seed)
    used = set()
    if exclude:
        used = {json.loads(l)["question"] for l in open(exclude) if l.strip()}
    data = json.load(open(path))["data"]
    rng.shuffle(data)
    out = []
    for art in data:
        paras = art["paragraphs"]
        if len(paras) < 3:
            continue
        gi = rng.randrange(len(paras))
        fresh = [q for q in paras[gi]["qas"] if q["question"].strip() not in used]
        if not fresh:
            continue
        qa = rng.choice(fresh)
        others = rng.sample([i for i in range(len(paras)) if i != gi], 2)
        idx = [gi] + others
        rng.shuffle(idx)
        tid = f"{'H' if exclude else 'Q'}{len(out)+1:02d}"
        letters = "abc"
        out.append({
            "id": tid,
            "question": qa["question"].strip(),
            "sources": [{"id": f"{tid}-{letters[k]}", "text": paras[i]["context"]} for k, i in enumerate(idx)],
            "answers": sorted({a["text"].strip() for a in qa["answers"]}),
        })
        if len(out) == n:
            break
    for row in out:
        print(json.dumps(row, ensure_ascii=False))


if __name__ == "__main__":
    main(sys.argv[1], int(sys.argv[2]), int(sys.argv[3]) if len(sys.argv) > 3 else 1234,
         sys.argv[4] if len(sys.argv) > 4 else None)
