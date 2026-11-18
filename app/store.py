import sqlite3, json
from pathlib import Path
from app.errors import BusinessError

def connect(path):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    con=sqlite3.connect(path,timeout=15)
    con.row_factory=sqlite3.Row
    con.execute('PRAGMA journal_mode=WAL')
    con.execute("CREATE TABLE IF NOT EXISTS runs (id TEXT PRIMARY KEY, digest TEXT UNIQUE NOT NULL, result TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 1, status TEXT NOT NULL DEFAULT '待复核')")
    con.execute("CREATE TABLE IF NOT EXISTS vouchers (run_id TEXT NOT NULL, seq INTEGER NOT NULL, payload TEXT NOT NULL, PRIMARY KEY(run_id,seq))")
    return con

def save(path, result):
    run_id=result['manifest']['run_id']
    with connect(path) as con:
        con.execute('INSERT OR IGNORE INTO runs(id,digest,result) VALUES(?,?,?)', (run_id,result['manifest']['sha256'],json.dumps(result,ensure_ascii=False)))
    return get(path,run_id)

def get(path, run_id):
    with connect(path) as con: row=con.execute('SELECT * FROM runs WHERE id=?',(run_id,)).fetchone()
    if not row: raise BusinessError('NOT_FOUND','批次不存在',404)
    result=json.loads(row['result']);result.update(revision=row['revision'],status=row['status'])
    return result

def history(path):
    with connect(path) as con:
        return [dict(r) for r in con.execute('SELECT id,revision,status FROM runs ORDER BY rowid DESC LIMIT 100')]

def review(path,run_id,revision):
    with connect(path) as con:
        cur=con.execute('UPDATE runs SET revision=revision+1,status=? WHERE id=? AND revision=?',('已复核',run_id,revision))
        if cur.rowcount!=1:
            if not con.execute('SELECT 1 FROM runs WHERE id=?',(run_id,)).fetchone():
                raise BusinessError('NOT_FOUND','批次不存在',404)
            raise BusinessError('STALE_VERSION','复核版本已更新，请刷新',409)
    return get(path,run_id)

def issue_voucher(path,run_id):
    result=get(path,run_id)
    if 'settlement' not in result: raise BusinessError('STALE_RUN','旧批缺少结算数据，请重跑后再出据')
    s=result['settlement']
    payload={'run_id':run_id,'revision':result['revision'],'caliber':s['caliber'],'rule_version':s['rule_version'],
        'confirmed_kwh':s['confirmed_kwh'],'pending_segments':s['pending_segments'],'pending_seconds':s['pending_seconds'],
        'pending_reasons':s['pending_reasons'],'quarantined_rows':s['quarantined_rows'],'status':s['status'],'seal':s['seal']}
    # 同批凭据按(run_id,seq)幂等写入：重试不增数；写库失败整体回滚，旧凭据与批次保持可复核状态。
    with connect(path) as con:
        con.execute('INSERT OR IGNORE INTO vouchers(run_id,seq,payload) VALUES(?,?,?)',(run_id,1,json.dumps(payload,ensure_ascii=False)))
        count=con.execute('SELECT COUNT(*) AS c FROM vouchers WHERE run_id=?',(run_id,)).fetchone()['c']
    return {'voucher':payload,'voucher_count':count}

def vouchers(path,run_id):
    get(path,run_id)
    with connect(path) as con:
        return [json.loads(r['payload']) for r in con.execute('SELECT payload FROM vouchers WHERE run_id=? ORDER BY seq',(run_id,))]
