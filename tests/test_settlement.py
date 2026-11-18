from fastapi.testclient import TestClient
from app import main, store
from app.modules.settlement.rules import settle, CALIBER, PENDING_LABELS
from app.engine import evaluate
from copy import deepcopy
from decimal import Decimal
import pytest

def client(tmp_path, monkeypatch):
    monkeypatch.setattr(main, 'DB_PATH', str(tmp_path/'test.sqlite')); return TestClient(main.app)

def test_clean_closed(clean):
    s=clean['settlement']
    assert s['confirmed_kwh']=='26.6667';assert s['pending_segments']==0;assert s['pending_seconds']==0
    assert s['status']=='可归档';assert all(d['reconciled']=='是' for d in s['devices']);assert all(s['checks'].values())

def test_dirty_pending_retained(sample):
    s=sample['settlement']
    assert s['confirmed_kwh']=='13.3333';assert s['pending_segments']==2;assert s['pending_seconds']==1200
    assert s['pending_reasons']=={'无对照':1,'缺测':1};assert s['status']=='待复核';assert s['quarantined_rows']==5

def test_confirmed_equals_event_loss_total(clean,sample):
    for state in (clean,sample):
        assert state['settlement']['confirmed_kwh']==state['balance']['loss_kwh']

def test_no_overlap_no_gap(sample):
    rows=sample['energy_rows'];s=sample['settlement']
    buckets=[('确认' if r['loss_kwh'] is not None else '待查') for r in rows]
    assert len(buckets)==s['confirmed_segments']+s['pending_segments']==len(rows);assert s['checks']['segments_balanced']

def test_devices_reconciled_after_recompute(sample):
    s=sample['settlement'];unions={}
    for u in sample['event_unions']: unions[u['device']]=unions.get(u['device'],0)+u['seconds']
    for d in s['devices']:
        assert d['seconds']==unions[d['device']]==d['union_seconds']
        assert d['segments']==sum(1 for r in sample['energy_rows'] if r['device']==d['device'])
    total=sum(Decimal(d['confirmed_kwh']) for d in s['devices'])
    assert total.quantize(Decimal('.0001'))==Decimal(s['confirmed_kwh'])

def test_settlement_deterministic(sample):
    again=evaluate();assert again['settlement']['seal']==sample['settlement']['seal']
    assert again['settlement']['devices']==sample['settlement']['devices']

def test_segment_tamper_breaks_closure(clean):
    s=deepcopy(clean);s['energy_rows'].pop()
    assert settle(s)['checks']['segments_balanced'] is False;assert settle(s)['status']=='待复核'

def test_quarantine_blocks_closure(clean):
    s=deepcopy(clean);s['quarantine'].append({'line':99,'id':'X','code':'BAD_NUMBER','detail':'x','raw':{}})
    assert settle(s)['status']=='待复核'

def test_pending_never_zero_filled(sample):
    for row in sample['settlement']['pending_rows']:
        assert set(row)=={'device','start','seconds','code'};assert row['seconds']>0;assert row['code'] in PENDING_LABELS.values()

def test_caliber_stable():
    assert '待查差额' in CALIBER and '确认额' in CALIBER

def test_settle_voucher_idempotent(tmp_path,monkeypatch):
    c=client(tmp_path,monkeypatch);run=c.post('/api/runs',json={}).json();rid=run['manifest']['run_id']
    a=c.post(f'/api/runs/{rid}/settle').json();b=c.post(f'/api/runs/{rid}/settle').json()
    assert a['voucher_count']==b['voucher_count']==1;assert a['voucher']['seal']==b['voucher']['seal']
    assert len(c.get(f'/api/runs/{rid}/vouchers').json())==1
    rerun=c.post(f'/api/runs/{rid}/rerun').json();assert rerun['manifest']['run_id']==rid
    assert c.post(f'/api/runs/{rid}/settle').json()['voucher_count']==1

def test_settle_unclosed_stays_pending(tmp_path,monkeypatch):
    c=client(tmp_path,monkeypatch)
    dirty=c.post('/api/runs',json={'dataset':'dirty'}).json();clean=c.post('/api/runs',json={'dataset':'clean'}).json()
    assert c.post(f"/api/runs/{dirty['manifest']['run_id']}/settle").json()['voucher']['status']=='待复核'
    assert c.post(f"/api/runs/{clean['manifest']['run_id']}/settle").json()['voucher']['status']=='可归档'

def test_settle_missing_run(tmp_path,monkeypatch):
    c=client(tmp_path,monkeypatch);assert c.post('/api/runs/none/settle').status_code==404

def test_failed_submission_preserves_reviewable_state(tmp_path,monkeypatch):
    c=client(tmp_path,monkeypatch);run=c.post('/api/runs',json={'dataset':'clean'}).json();rid=run['manifest']['run_id']
    assert c.post(f'/api/runs/{rid}/settle').json()['voucher_count']==1
    assert c.post('/api/runs',json={'csv_text':'not,a,valid,csv\n1,2'}).status_code==422
    kept=c.get(f'/api/runs/{rid}').json();assert kept['revision']==1;assert kept['status']=='待复核'
    assert len(c.get(f'/api/runs/{rid}/vouchers').json())==1
    assert c.post(f'/api/runs/{rid}/review',json={'revision':1}).status_code==200

def test_settlement_page_shows_caliber_amount_pending(tmp_path,monkeypatch):
    c=client(tmp_path,monkeypatch);run=c.post('/api/runs',json={}).json();rid=run['manifest']['run_id']
    html=c.get('/settlement?run_id='+rid).text
    assert '本次采用口径' in html and '确认额' in html and '待查差额' in html
    assert '13.3333' in html and '未定' in html
    assert '无对照' in html and '缺测' in html

def test_settlement_module_entry(tmp_path,monkeypatch):
    c=client(tmp_path,monkeypatch)
    assert c.post('/api/settlement',json={}).status_code==200
    assert c.get('/settlement').status_code==200
