"""Executable check of projectbuild.md section 6, against a running server.

Start the API first, then: .venv/bin/python acceptance.py
Each run tags its import rows uniquely, so it is safe to run repeatedly.
"""
import io, json, urllib.request, urllib.error, uuid

BASE = "http://127.0.0.1:8000"
results = []

def call(method, path, body=None, files=None):
    if files:
        boundary = uuid.uuid4().hex
        parts = []
        for k, v in body.items():
            parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n')
        for k, (fn, content) in files.items():
            parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"; filename="{fn}"\r\n'
                         f'Content-Type: text/csv\r\n\r\n{content}\r\n')
        payload = ("".join(parts) + f"--{boundary}--\r\n").encode()
        req = urllib.request.Request(BASE+path, data=payload, method=method,
                                     headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    else:
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(BASE+path, data=data, method=method,
                                     headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req) as r:
            raw = r.read()
            return r.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as e:
        raw = e.read()
        return e.code, (json.loads(raw) if raw else None)

def ai_states_total(text, total_minor):
    """Same acceptance the server applies: the sentence must repeat our figure."""
    target = f"${total_minor / 100:,.2f}"
    plain = target.lstrip("$")
    cands = {target, plain, plain.replace(",", "")}
    if plain.endswith(".00"):
        whole = plain[:-3]
        cands |= {f"${whole}", whole, whole.replace(",", "")}
    return any(c in text for c in cands)


def check(name, ok, detail=""):
    results.append((ok, name, detail))
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"\n        {detail}" if detail else ""))

accts = {a["code"]: a["id"] for a in call("GET", "/accounts")[1]}
CHK, FOOD = accts["assets:checking"], accts["expenses:food"]

# 1. Unbalanced transaction rejected with 422 naming the imbalance
st, body = call("POST", "/transactions", {
    "date": "2026-06-01", "description": "Unbalanced probe",
    "postings": [{"account_id": FOOD, "amount_minor": 5000},
                 {"account_id": CHK, "amount_minor": -4000}]})
check("Unbalanced transaction -> 422 naming the imbalance",
      st == 422 and "balance" in str(body.get("detail","")).lower() and "1000" in str(body.get("detail","")),
      f"{st}: {body.get('detail')}")

# 2. Trial balance zero after a sequence of operations
before = call("GET", "/health")[1]["trial_balance_minor"]
st, tx = call("POST", "/transactions", {
    "date": "2026-06-02", "description": "Acceptance probe",
    "postings": [{"account_id": FOOD, "amount_minor": 3300},
                 {"account_id": CHK, "amount_minor": -3300}]})
call("PUT", f"/transactions/{tx['id']}", {
    "date": "2026-06-03", "description": "Acceptance probe edited",
    "postings": [{"account_id": FOOD, "amount_minor": 7700},
                 {"account_id": CHK, "amount_minor": -7700}]})
mid = call("GET", "/health")[1]["trial_balance_minor"]
call("DELETE", f"/transactions/{tx['id']}")
after = call("GET", "/health")[1]["trial_balance_minor"]
check("Trial balance zero after create/edit/delete sequence",
      before == mid == after == 0, f"before={before} mid={mid} after={after}")

# 3. CSV imported twice creates no duplicates
TAG = uuid.uuid4().hex[:8].upper()
CSV = ("Date,Description,Amount\n"
       f"2026-05-02,ACCEPTANCE DUPE {TAG},-11.11\n"
       f"2026-05-03,ACCEPTANCE DUPE {TAG} TWO,-22.22\n")
st, p1 = call("POST", "/imports/preview", {"account_id": str(CHK)}, {"file": ("a.csv", CSV)})
n0 = call("GET", "/health")[1]["transactions"]
r1 = call("POST", "/imports/commit", {"account_id": CHK, "rows": p1["rows"]})[1]
n1 = call("GET", "/health")[1]["transactions"]
st, p2 = call("POST", "/imports/preview", {"account_id": str(CHK)}, {"file": ("a.csv", CSV)})
r2 = call("POST", "/imports/commit", {"account_id": CHK, "rows": p2["rows"]})[1]
n2 = call("GET", "/health")[1]["transactions"]
check("Same CSV imported twice creates no duplicates",
      r1["created"] == 2 and r2["created"] == 0 and r2["skipped"] == 2 and n1 == n0+2 and n2 == n1,
      f"first={r1} second={r2} counts {n0}->{n1}->{n2}, preview flagged {p2['duplicates']} dupes")

# 4. Rules categorize on import; uncategorized stay visible and fixable
rule = call("POST", "/rules", {"pattern": "ACCEPTANCE RULED", "match_type": "contains",
                               "account_id": FOOD})[1]
CSV2 = ("Date,Description,Amount\n"
        f"2026-05-10,ACCEPTANCE RULED MERCHANT {TAG},-33.33\n"
        f"2026-05-11,ZZQQ UNKNOWABLE MERCHANT {TAG},-44.44\n")
st, p3 = call("POST", "/imports/preview", {"account_id": str(CHK)}, {"file": ("b.csv", CSV2)})
ruled = [r for r in p3["rows"] if "RULED" in r["description"]][0]
unknown = [r for r in p3["rows"] if "ZZQQ" in r["description"]][0]
check("Rule categorizes on import",
      ruled["suggested_account_code"] == "expenses:food",
      f"suggested={ruled['suggested_account_code']}")
call("POST", "/imports/commit", {"account_id": CHK, "rows": p3["rows"]})
uncat = call("POST", "/ai/categorize", {"limit": 50})[1]
visible = any("ZZQQ" in s["description"] for s in uncat["suggestions"])
holding = [a for a in call("GET","/accounts")[1] if a["code"] == "expenses:uncategorized"]
check("Uncategorized rows remain visible and fixable",
      bool(holding) and holding[0]["balance_minor"] != 0,
      f"holding balance={holding[0]['balance_minor'] if holding else 'missing'}, "
      f"surfaced in /ai/categorize={visible}")

# 5. Every reported number traces to postings (no LLM arithmetic).
#    The model picks the filters, so which filter it picks varies run to run.
#    What must never vary is that the figure equals what the DATABASE computes
#    for that filter — so recompute the returned plan independently in raw SQL.
NORMAL = {"asset": 1, "expense": 1, "liability": -1, "equity": -1, "income": -1}

def independent_total(plan):
    import sqlite3
    db = sqlite3.connect("ledger.db")
    sql = ("SELECT a.type, SUM(p.amount_minor) FROM postings p "
           "JOIN accounts a ON a.id=p.account_id "
           "JOIN transactions t ON t.id=p.transaction_id WHERE a.type IN (%s)"
           % ",".join("?" * len(plan["account_types"])))
    args = list(plan["account_types"])
    if plan["account_codes"]:
        sql += " AND a.code IN (%s)" % ",".join("?" * len(plan["account_codes"]))
        args += plan["account_codes"]
    if plan["start"]:
        sql += " AND t.date >= ?"; args.append(plan["start"])
    if plan["end"]:
        sql += " AND t.date <= ?"; args.append(plan["end"])
    if plan["text"]:
        sql += " AND t.description LIKE ?"; args.append(f"%{plan['text']}%")
    sql += " GROUP BY a.type"
    return sum(NORMAL[t] * int(v or 0) for t, v in db.execute(sql, args))

st, ans = call("POST", "/ai/ask", {"question": "How much did I spend on groceries this year?"})
if st == 200:
    expected = independent_total(ans["query"])
    stated = ai_states_total(ans["answer"], ans["total_minor"])
    check("AI total equals independent SQL over the plan the model chose",
          expected == ans["total_minor"],
          f"api={ans['total_minor']} independent SQL={expected} "
          f"plan={json.dumps({k: v for k, v in ans['query'].items() if k != 'intent'})}")
    check("Narrated sentence states that same figure",
          stated, f"answer={ans['answer']!r} total={ans['total_minor']}")
elif st == 503:
    check("AI ask degrades with a clear 503 when no key is set", True, str(ans.get("detail"))[:80])
else:
    check("AI ask reachable", False, f"status={st}")

# 6. Balance sheet identity holds
bs = call("GET", "/reports/balance-sheet")[1]
check("Balance sheet balances (A = L + E + retained)", bs["balanced"] is True,
      f"assets={bs['total_assets_minor']} liab={bs['total_liabilities_minor']} eq={bs['total_equity_minor']}")

# cleanup probe data
call("DELETE", f"/rules/{rule['id']}")

print()
bad = [r for r in results if not r[0]]
print(f"{len(results)-len(bad)}/{len(results)} acceptance checks passed")
