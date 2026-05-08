"""
HEXIS Trust API v0.5
=====================
Standalone Trust Verification for AI Economy

Changes from v0.1:
  - SQLite persistence - data survives reboot/crash
  - API key authentication
  - Usage tracking per key
  - Rate limiting
  - /docs endpoint

ENDPOINTS:
    GET  /                          UI
    GET  /trust/{actor_id}          Trust score + x402 headers
    POST /integrity/submit          Submit integrity event
    GET  /integrity/{actor_id}      History
    GET  /leaderboard               Top actors
    GET  /status                    System status
    GET  /docs                      API documentation
    POST /keys/create               Create API key (admin)
    GET  /usage/{key_prefix}        Usage stats

RUN:
    pip install flask cryptography
    python3 hexis_api.py

DATA:
    Stored at: /opt/hexis_newflow/hexis.db (SQLite)
    Survives reboot and crash
"""

import json, math, hashlib, time, os, sqlite3, secrets
from datetime import datetime, timezone
from functools import wraps
from flask import Flask, request, jsonify, render_template_string, g

app = Flask(__name__)

DB_PATH    = os.environ.get("HEXIS_DB", "/opt/hexis_newflow/hexis.db")
ADMIN_KEY  = os.environ.get("HEXIS_ADMIN_KEY", "hexis-admin-2026")
FREE_LIMIT = 1000
PORT       = 8401

TOTAL_SUPPLY  = 12_800_000
WALLET_CAP    =     10_000
REFERENCE_GDP =     12_000

GDP_PER_CAPITA = {
    "US":80000,"GB":48000,"DE":52000,"JP":34000,"KR":33000,
    "SG":88000,"FR":44000,"CA":55000,"CN":13000,"BR":9000,
    "RU":14000,"IN":2500,"VN":4200,"ID":4900,"PH":3700,
    "TH":7000,"MY":12000,"NG":2200,"KE":2100,"ZA":6000,
    "AU":65000,"NZ":48000,"SE":56000,"NO":100000,"DEFAULT":12000,
}

GRADE_THRESHOLDS = [
    (0.05000,"High",1.0),(0.00500,"Moderate",1.5),
    (0.00100,"Low",3.0),(0.00010,"Minimal",5.0),
]

# Sensitivity tier - higher tier = more temptation to betray = more HEXIS earned
# when honest. Multiplier applied to effective gain in Betrayal Opportunity (BO).
SENSITIVITY_TIERS = {
    1: ("Public",       1.0),    # Public data - no privacy/compliance risk
    2: ("Internal",     5.0),    # Internal company data
    3: ("Confidential", 20.0),   # Trade secrets, customer PII
    4: ("Regulated",    100.0),  # Medical, financial, legal - max temptation
}

def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA journal_mode=WAL")
    return g.db

@app.teardown_appcontext
def close_db(e=None):
    db = g.pop("db", None)
    if db: db.close()

def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    c = sqlite3.connect(DB_PATH)
    c.executescript("""
        CREATE TABLE IF NOT EXISTS actors(
            actor_id TEXT PRIMARY KEY, country TEXT DEFAULT 'DEFAULT',
            hexis_total REAL DEFAULT 0.0, event_count INTEGER DEFAULT 0,
            first_seen TEXT, last_seen TEXT);
        CREATE TABLE IF NOT EXISTS events(
            id TEXT PRIMARY KEY, actor_id TEXT NOT NULL,
            hexis_mined REAL, hexis_total REAL, source TEXT,
            country TEXT, metadata TEXT DEFAULT '{}', ts TEXT);
        CREATE TABLE IF NOT EXISTS api_keys(
            key_hash TEXT PRIMARY KEY, key_prefix TEXT,
            label TEXT, tier TEXT DEFAULT 'free',
            daily_limit INTEGER DEFAULT 1000,
            calls_today INTEGER DEFAULT 0,
            calls_total INTEGER DEFAULT 0,
            last_reset TEXT, created_at TEXT, active INTEGER DEFAULT 1);
        CREATE INDEX IF NOT EXISTS idx_ev_actor ON events(actor_id);
    """)
    c.commit(); c.close()
    print(f"[DB] {DB_PATH}")

def context_multiplier(country):
    gdp = GDP_PER_CAPITA.get(country.upper(), 12000)
    return max(0.5, min(2.0, math.sqrt(REFERENCE_GDP / gdp)))

def get_grade(score):
    for t, g, c in GRADE_THRESHOLDS:
        if score >= t: return g, c
    return "Unverified", 10.0

def mine_hexis(s, bo, w, tdr, ti, country):
    c = context_multiplier(country)
    return round(max(0,min(1,s))*max(0,min(1,bo))*max(0,min(1,w))*
                 max(0,min(1,tdr))*max(0,min(1,ti))*c, 8)

def compute_job_hexis(fee, country, sensitivity_tier=1, prob=0.95):
    # Higher sensitivity tier -> bigger gain if data was leaked/misused
    # Worker resists this temptation -> earns proportionally more HEXIS
    tier_label, tier_mult = SENSITIVITY_TIERS.get(sensitivity_tier,
                                                   SENSITIVITY_TIERS[1])
    effective_gain = fee * tier_mult
    bo = math.log(effective_gain*(1-prob)+1)/math.log(1_000_000_000+1)
    w  = math.log(7)/math.log(1_000_001)
    return mine_hexis(1.0, bo, w, 0.05, 1.0, country)

def hash_key(k): return hashlib.sha256(k.encode()).hexdigest()

def check_api_key(key):
    db = get_db()
    row = db.execute("SELECT * FROM api_keys WHERE key_hash=? AND active=1",
                     (hash_key(key),)).fetchone()
    if not row: return None
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if row["last_reset"] != today:
        db.execute("UPDATE api_keys SET calls_today=0,last_reset=? WHERE key_hash=?",
                   (today, hash_key(key)))
        db.commit()
        row = db.execute("SELECT * FROM api_keys WHERE key_hash=?",
                         (hash_key(key),)).fetchone()
    return dict(row)

def require_key_or_free(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        key = (request.headers.get("X-Hexis-Key") or
               request.args.get("key",""))
        if key:
            info = check_api_key(key)
            if not info:
                return jsonify({"error":"Invalid API key"}), 401
            if info["calls_today"] >= info["daily_limit"]:
                return jsonify({"error":"Daily limit reached",
                                "limit":info["daily_limit"]}), 429
            db = get_db()
            db.execute("UPDATE api_keys SET calls_today=calls_today+1,"
                       "calls_total=calls_total+1 WHERE key_hash=?",
                       (hash_key(key),))
            db.commit()
            request.key_info = info
        else:
            request.key_info = {"tier":"free","key_prefix":"free"}
        return f(*args, **kwargs)
    return decorated

def db_get_trust(actor_id):
    db  = get_db()
    row = db.execute("SELECT * FROM actors WHERE actor_id=?",(actor_id,)).fetchone()
    if not row:
        return {"actor_id":actor_id,"hexis_total":0.0,"grade":"Unverified",
                "event_count":0,"collateral_mult":10.0,"x402_accept":False,
                "x402_headers":{"X-Hexis-Score":"0.000000","X-Hexis-Grade":"Unverified",
                "X-Hexis-Accept":"False","X-Hexis-Collateral-Mult":"10.0",
                "X-Hexis-Records":"0"},
                "message":"No integrity records. Submit events to build trust."}
    score = row["hexis_total"]
    grade,coll = get_grade(score)
    accept = score >= 0.00010
    return {"actor_id":actor_id,"hexis_total":round(score,6),
            "grade":grade,"event_count":row["event_count"],
            "collateral_mult":coll,"x402_accept":accept,
            "country":row["country"],"first_seen":row["first_seen"],
            "last_seen":row["last_seen"],
            "x402_headers":{"X-Hexis-Score":f"{score:.6f}",
            "X-Hexis-Grade":grade,"X-Hexis-Accept":str(accept),
            "X-Hexis-Collateral-Mult":str(coll),
            "X-Hexis-Records":str(row["event_count"])}}

def db_record_event(actor_id, hexis, country, source, metadata):
    db  = get_db()
    now = datetime.now(timezone.utc).isoformat()
    ex  = db.execute("SELECT hexis_total FROM actors WHERE actor_id=?",
                     (actor_id,)).fetchone()
    if ex:
        new = min(ex["hexis_total"]+hexis, WALLET_CAP)
        db.execute("UPDATE actors SET hexis_total=?,event_count=event_count+1,"
                   "last_seen=?,country=? WHERE actor_id=?",
                   (new,now,country,actor_id))
    else:
        new = min(hexis, WALLET_CAP)
        db.execute("INSERT INTO actors VALUES(?,?,?,1,?,?)",
                   (actor_id,country,new,now,now))
    eid = hashlib.sha256(f"{actor_id}{time.time()}".encode()).hexdigest()[:16]
    db.execute("INSERT INTO events VALUES(?,?,?,?,?,?,?,?)",
               (eid,actor_id,hexis,new,source,country,json.dumps(metadata),now))
    db.commit()
    return eid, new

def db_stats():
    db = get_db()
    a = db.execute("SELECT COUNT(*) as n FROM actors").fetchone()["n"]
    e = db.execute("SELECT COUNT(*) as n FROM events").fetchone()["n"]
    m = db.execute("SELECT COALESCE(SUM(hexis_mined),0) as s FROM events").fetchone()["s"]
    return a, e, round(m,6)

UI = """<!DOCTYPE html><html lang="en"><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>HEXIS Trust API</title><style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:'Courier New',monospace;background:#0a0a0a;color:#e0e0e0;min-height:100vh;padding:28px 20px}
h1{font-size:1.5rem;color:#f5c842;letter-spacing:2px;margin-bottom:3px}
.sub{color:#888;font-size:.82rem;margin-bottom:28px}
.card{background:#111;border:1px solid #222;border-radius:8px;padding:18px;margin-bottom:16px}
.card h2{color:#f5c842;font-size:.9rem;margin-bottom:12px;text-transform:uppercase;letter-spacing:1px}
.stat{display:inline-block;margin-right:20px}
.stat .val{color:#f5c842;font-size:1.2rem;font-weight:bold}
.stat .lbl{color:#666;font-size:.72rem}
input{background:#1a1a1a;border:1px solid #333;color:#fff;padding:9px 12px;
      border-radius:4px;width:100%;font-family:monospace;font-size:.88rem;margin-bottom:9px}
button{background:#f5c842;color:#000;border:none;padding:9px 18px;border-radius:4px;
       cursor:pointer;font-weight:bold;font-size:.88rem;width:100%;margin-bottom:6px}
button:hover{background:#ffe066}
pre{background:#0d0d0d;padding:14px;border-radius:6px;font-size:.76rem;
    overflow-x:auto;color:#4fc;min-height:50px;white-space:pre-wrap}
.ep{background:#0d0d0d;border-left:3px solid #f5c842;padding:8px 12px;margin:6px 0;border-radius:4px}
.method{color:#4fc;font-size:.78rem;margin-right:6px}
.path{color:#fff;font-size:.86rem}
.desc{color:#555;font-size:.74rem;margin-top:3px}
a{color:#4fc}
</style></head><body>
<h1>HEXIS x Trust API</h1>
<p class="sub">Proof of Integrity v0.5 - hexisfoundation.org - <a href="/docs">docs</a></p>
<div class="card"><h2>Network</h2>
  <div class="stat"><div class="val">{{actors}}</div><div class="lbl">Actors</div></div>
  <div class="stat"><div class="val">{{events}}</div><div class="lbl">Events</div></div>
  <div class="stat"><div class="val">{{mined}}</div><div class="lbl">HEXIS Mined</div></div>
</div>
<div class="card"><h2>Query Trust</h2>
  <input id="actor" placeholder="Actor ID - wallet, agent ID, any string"/>
  <button onclick="queryTrust()">GET /trust/{actor_id}</button>
  <pre id="trust_out">// Result appears here</pre>
</div>
<div class="card"><h2>Submit Integrity Event</h2>
  <input id="s_actor" placeholder="Actor ID"/>
  <input id="s_country" placeholder="Country (VN, IN, US...)" value="VN"/>
  <input id="s_fee" placeholder="Fee amount" value="50"/>
  <input id="s_source" placeholder="Source" value="compute_job"/>
  <button onclick="submitEvent()">POST /integrity/submit</button>
  <pre id="sub_out">// Result appears here</pre>
</div>
<div class="card"><h2>Endpoints</h2>
  <div class="ep"><span class="method">GET</span><span class="path">/trust/{actor_id}</span>
    <div class="desc">Trust score + x402 headers</div></div>
  <div class="ep"><span class="method">POST</span><span class="path">/integrity/submit</span>
    <div class="desc">Submit integrity event</div></div>
  <div class="ep"><span class="method">GET</span><span class="path">/leaderboard</span>
    <div class="desc">Top actors by score</div></div>
  <div class="ep"><span class="method">GET</span><span class="path">/docs</span>
    <div class="desc">Full API docs + integration examples</div></div>
  <div class="ep"><span class="method">GET</span><span class="path">/status</span>
    <div class="desc">System status</div></div>
</div>
<script>
async function queryTrust(){
  const id=document.getElementById('actor').value.trim();
  const out=document.getElementById('trust_out');
  if(!id){out.textContent='// Enter an actor ID';return}
  try{const r=await fetch('/trust/'+encodeURIComponent(id));
      out.textContent=JSON.stringify(await r.json(),null,2)}
  catch(e){out.textContent='// Error: '+e.message}
}
async function submitEvent(){
  const a=document.getElementById('s_actor').value.trim();
  const c=document.getElementById('s_country').value.trim()||'VN';
  const f=parseFloat(document.getElementById('s_fee').value)||50;
  const s=document.getElementById('s_source').value.trim()||'compute_job';
  const out=document.getElementById('sub_out');
  if(!a){out.textContent='// Enter an actor ID';return}
  try{const r=await fetch('/integrity/submit',{method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({actor_id:a,country:c,fee_amount:f,source:s})});
      out.textContent=JSON.stringify(await r.json(),null,2)}
  catch(e){out.textContent='// Error: '+e.message}
}
</script></body></html>"""

DOCS_HTML = """<!DOCTYPE html><html><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>HEXIS API Docs</title><style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:'Courier New',monospace;background:#0a0a0a;color:#e0e0e0;
     padding:32px 24px;max-width:780px;margin:0 auto}
h1{color:#f5c842;font-size:1.4rem;margin-bottom:4px}
h2{color:#f5c842;font-size:1rem;margin:28px 0 10px;text-transform:uppercase;letter-spacing:1px}
h3{color:#fff;font-size:.9rem;margin:16px 0 6px}
p{color:#aaa;font-size:.85rem;line-height:1.6;margin-bottom:10px}
pre{background:#111;padding:14px;border-radius:6px;font-size:.78rem;
    color:#4fc;overflow-x:auto;margin:10px 0;white-space:pre-wrap}
.ep{background:#111;border-left:3px solid #f5c842;padding:10px 14px;margin:10px 0;border-radius:4px}
.method{color:#4fc;font-weight:bold;margin-right:8px}
code{background:#1a1a1a;padding:2px 6px;border-radius:3px;color:#f5c842;font-size:.82rem}
a{color:#4fc;text-decoration:none}
.sub{color:#666;font-size:.8rem;margin-bottom:24px}
</style></head><body>
<h1>HEXIS Trust API - Docs</h1>
<p class="sub">v0.2 - <a href="mailto:contact@hexisfoundation.org">contact@hexisfoundation.org</a> - <a href="/"><- Back</a></p>

<h2>Overview</h2>
<p>HEXIS provides trust verification for AI agents and compute workers. One call returns a trust score with x402-compatible headers ready to use in payment flows.</p>
<p>Formula: <code>HEXIS = S x BO x W x TDR x T x C</code></p>
<p>Base URL: <code>http://174.138.9.102:8401</code></p>

<h2>Authentication</h2>
<p>Free tier: 1,000 calls/day, no key needed.<br>
Higher limits: include <code>X-Hexis-Key: your-key</code> header.<br>
Get a key: <a href="mailto:contact@hexisfoundation.org">contact@hexisfoundation.org</a></p>

<h2>GET /trust/{actor_id}</h2>
<p>Core endpoint. Call before any payment or job assignment.</p>
<pre>curl http://174.138.9.102:8401/trust/my-agent-001</pre>
<pre>{
  "actor_id":        "my-agent-001",
  "hexis_total":     0.052300,
  "grade":           "High",
  "event_count":     84,
  "collateral_mult": 1.0,
  "x402_accept":     true,
  "x402_headers": {
    "X-Hexis-Score":           "0.052300",
    "X-Hexis-Grade":           "High",
    "X-Hexis-Accept":          "True",
    "X-Hexis-Collateral-Mult": "1.0",
    "X-Hexis-Records":         "84"
  }
}</pre>

<h3>Grade Thresholds</h3>
<pre>High       >= 0.05000  collateral 1.0x  ~72 honest events
Moderate   >= 0.00500  collateral 1.5x  ~7 events
Low        >= 0.00100  collateral 3.0x
Minimal    >= 0.00010  collateral 5.0x
Unverified  < 0.00010  collateral 10.0x</pre>

<h2>POST /integrity/submit</h2>
<p>Submit an integrity event. Automatically mines HEXIS for the actor.</p>
<pre>curl -X POST http://174.138.9.102:8401/integrity/submit \
  -H "Content-Type: application/json" \
  -d '{"actor_id":"my-agent","country":"VN","fee_amount":50,"source":"compute_job"}'</pre>

<h2>GET /leaderboard</h2>
<pre>curl http://174.138.9.102:8401/leaderboard</pre>

<h2>Integration - JavaScript</h2>
<pre>const trust = await fetch(
  'http://174.138.9.102:8401/trust/' + agentId
);
const data = await trust.json();

if (data.x402_accept) {
  // Safe to proceed
  // Apply collateral: data.collateral_mult
}</pre>

<h2>Integration - Python</h2>
<pre>import requests

def check_trust(actor_id, api_key=None):
    headers = {'X-Hexis-Key': api_key} if api_key else {}
    r = requests.get(
        f'http://174.138.9.102:8401/trust/{actor_id}',
        headers=headers
    )
    return r.json()

trust = check_trust('my-agent-001')
print(trust['grade'], trust['hexis_total'])</pre>

<h2>Geographic Justice</h2>
<p>Same honest behavior earns more HEXIS in lower-GDP countries.
Vietnam (C~1.69) earns ~3.4x more than US (C~0.5) for the same job.
Geography is not a tax on integrity.</p>

<p style="margin-top:32px"><a href="/">Back to API</a> -
<a href="mailto:contact@hexisfoundation.org">contact@hexisfoundation.org</a> -
<a href="https://hexisfoundation.org">hexisfoundation.org</a></p>
</body></html>"""

@app.route("/")
def index():
    a,e,m = db_stats()
    return render_template_string(UI, actors=a, events=e, mined=m)

@app.route("/docs")
def docs():
    return DOCS_HTML

@app.route("/trust/<actor_id>")
@require_key_or_free
def get_trust(actor_id):
    data = db_get_trust(actor_id)
    resp = jsonify(data)
    for k,v in data["x402_headers"].items():
        resp.headers[k] = v
    return resp

@app.route("/integrity/submit", methods=["POST"])
@require_key_or_free
def submit_integrity():
    body     = request.get_json(silent=True) or {}
    actor_id = body.get("actor_id","").strip()
    country  = body.get("country","DEFAULT").upper()
    fee      = float(body.get("fee_amount",50))
    source   = body.get("source","manual")
    metadata = body.get("metadata",{})
    if not actor_id:
        return jsonify({"error":"actor_id required"}), 400
    if source == "compute_job":
        tier = int(body.get("sensitivity_tier", 1))
        if tier < 1 or tier > 4:
            return jsonify({"error":"sensitivity_tier must be 1-4"}), 400
        hexis = compute_job_hexis(fee, country, tier)
        metadata["sensitivity_tier"]  = tier
        metadata["sensitivity_label"] = SENSITIVITY_TIERS[tier][0]
    else:
        hexis = mine_hexis(
            float(body.get("sacrifice",0.8)),
            float(body.get("betrayal_opp",0.2)),
            float(body.get("witness",0.3)),
            float(body.get("tdr",0.1)),
            float(body.get("timing",0.9)),
            country)
    eid, new_total = db_record_event(actor_id,hexis,country,source,metadata)
    grade, coll    = get_grade(new_total)
    return jsonify({"status":"mined","actor_id":actor_id,
                    "hexis_mined":hexis,"hexis_total":round(new_total,6),
                    "grade":grade,"collateral_mult":coll,"event_id":eid,
                    "message":"Honest behavior = trust credential earned."})

@app.route("/integrity/<actor_id>")
@require_key_or_free
def get_history(actor_id):
    db   = get_db()
    rows = db.execute("SELECT * FROM events WHERE actor_id=? ORDER BY ts DESC LIMIT 20",
                      (actor_id,)).fetchall()
    trust = db_get_trust(actor_id)
    return jsonify({"actor_id":actor_id,"hexis_total":trust["hexis_total"],
                    "grade":trust["grade"],"event_count":trust["event_count"],
                    "events":[dict(r) for r in rows]})

@app.route("/leaderboard")
def leaderboard():
    limit = min(int(request.args.get("limit",20)),100)
    db    = get_db()
    rows  = db.execute("SELECT * FROM actors ORDER BY hexis_total DESC LIMIT ?",
                       (limit,)).fetchall()
    a,e,m = db_stats()
    return jsonify({"leaderboard":[
        {"rank":i+1,"actor_id":r["actor_id"],
         "hexis_total":round(r["hexis_total"],6),
         "grade":get_grade(r["hexis_total"])[0],
         "events":r["event_count"],"country":r["country"]}
        for i,r in enumerate(rows)],
        "total_actors":a,"total_events":e,"total_mined":m})

@app.route("/status")
def status():
    a,e,m = db_stats()
    tiers = {str(k): {"label": v[0], "multiplier": v[1]}
             for k, v in SENSITIVITY_TIERS.items()}
    return jsonify({"service":"HEXIS Trust API","version":"0.5",
                    "status":"live","testnet":True,"port":PORT,
                    "actors":a,"events":e,"hexis_mined":m,
                    "total_supply":TOTAL_SUPPLY,"wallet_cap":WALLET_CAP,
                    "formula":"HEXIS = S x BO x W x TDR x T x C",
                    "sensitivity_tiers":tiers,
                    "docs":f"http://174.138.9.102:{PORT}/docs",
                    "contact":"contact@hexisfoundation.org",
                    "website":"hexisfoundation.org",
                    "timestamp":datetime.now(timezone.utc).isoformat()})

@app.route("/keys/create", methods=["POST"])
def create_key():
    body = request.get_json(silent=True) or {}
    if body.get("admin_key","") != ADMIN_KEY:
        return jsonify({"error":"Unauthorized"}), 401
    label     = body.get("label","unnamed")
    tier      = body.get("tier","starter")
    daily_lim = int(body.get("daily_limit",100_000))
    raw_key   = "hexis-" + secrets.token_urlsafe(24)
    prefix    = raw_key[:12]
    now       = datetime.now(timezone.utc).isoformat()
    today     = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    db = get_db()
    db.execute("INSERT INTO api_keys VALUES(?,?,?,?,?,0,0,?,?,1)",
               (hash_key(raw_key),prefix,label,tier,daily_lim,today,now))
    db.commit()
    return jsonify({"api_key":raw_key,"prefix":prefix,"label":label,
                    "tier":tier,"daily_limit":daily_lim,
                    "note":"Save this key - it cannot be retrieved again."})

@app.route("/usage/<key_prefix>")
def usage(key_prefix):
    db  = get_db()
    row = db.execute("SELECT * FROM api_keys WHERE key_prefix=?",
                     (key_prefix,)).fetchone()
    if not row: return jsonify({"error":"Not found"}), 404
    return jsonify({"key_prefix":key_prefix,"label":row["label"],
                    "tier":row["tier"],"daily_limit":row["daily_limit"],
                    "calls_today":row["calls_today"],
                    "calls_total":row["calls_total"]})

if __name__ == "__main__":
    init_db()
    print("="*56)
    print("HEXIS Trust API v0.5 - with SQLite + API keys")
    print("="*56)
    print(f"  UI:    http://localhost:{PORT}/")
    print(f"  Trust: http://localhost:{PORT}/trust/{{actor_id}}")
    print(f"  Docs:  http://localhost:{PORT}/docs")
    print(f"  DB:    {DB_PATH}")
    print("="*56)
    app.run(host="0.0.0.0", port=PORT, debug=False)
