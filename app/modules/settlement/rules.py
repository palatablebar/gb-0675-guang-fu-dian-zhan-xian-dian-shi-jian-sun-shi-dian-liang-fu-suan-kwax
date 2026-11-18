from decimal import Decimal
import hashlib, json
from app.coerce import number
from app.config import RULE_VERSION

# 本次结算口径（裁定文本，页面与凭据同源，改动即改规则版本）。
CALIBER='限电事件并集内逐片积分，损失=max(对照基线−实际,0)；确认额=已闭合片段损失合计，即本期结算额；非限电停运、缺测、无对照片段不定性为事件损失，作为待查差额保留原因码与时长，不填0、不丢弃；表计差额=确认额+待查差额，未闭合前正式凭据保持待复核。'
# 未闭合差额定性：原因码→去向标签。
PENDING_LABELS={'TARGET_GAP':'缺测','TARGET_FAULT':'非限电停运','NO_REFERENCE':'无对照'}
QUANT=Decimal('.0001')

def settle(state):
    rows=state['energy_rows'];balance=state['balance']
    # 每切片唯一定性：确认（损失已定）或待查（损失保持未定），互斥且穷尽。
    confirmed=[r for r in rows if r['loss_kwh'] is not None]
    pending=[r for r in rows if r['loss_kwh'] is None]
    confirmed_kwh=sum((number(r['loss_kwh']) for r in confirmed),Decimal(0))
    reasons={}
    for r in pending:
        label=PENDING_LABELS.get(r['code'],r['code']);reasons[label]=reasons.get(label,0)+1
    # 受影响设备重算后逐台重新对账：并集时长=切片时长，确认+待查覆盖该台全部切片。
    union_seconds={}
    for u in state['event_unions']:
        union_seconds[u['device']]=union_seconds.get(u['device'],0)+u['seconds']
    devices={}
    for r in rows:
        d=devices.setdefault(r['device'],{'device':r['device'],'segments':0,'seconds':0,'confirmed_kwh':Decimal(0),'pending_segments':0,'pending_seconds':0})
        d['segments']+=1;d['seconds']+=r['seconds']
        if r['loss_kwh'] is None:d['pending_segments']+=1;d['pending_seconds']+=r['seconds']
        else:d['confirmed_kwh']+=number(r['loss_kwh'])
    device_rows=[];reconciled={}
    for device in sorted(devices):
        d=devices[device];ok=d['seconds']==union_seconds.get(device,0);reconciled[device]=ok
        # 设备行保留完整精度，与损失模块一致：汇总后才统一量化到0.0001kWh，避免逐台量化制造尾差。
        device_rows.append({'device':device,'segments':d['segments'],'seconds':d['seconds'],'union_seconds':union_seconds.get(device,0),
            'confirmed_kwh':str(d['confirmed_kwh']),'pending_segments':d['pending_segments'],'pending_seconds':d['pending_seconds'],
            'reconciled':'是' if ok else '否','status':'闭合' if not d['pending_segments'] else '待复核'})
    checks={'row_balanced':balance['row_balanced'],'time_balanced':balance['time_balanced'],'energy_balanced':balance['energy_balanced'],
        'segments_balanced':len(confirmed)+len(pending)==len(state['segments'])==len(rows),
        'devices_reconciled':all(reconciled.values())}
    closed=all(checks.values()) and not pending and not state['quarantine']
    core={'caliber':CALIBER,'rule_version':RULE_VERSION,'confirmed_kwh':str(confirmed_kwh.quantize(QUANT)),
          'confirmed_segments':len(confirmed),'pending_segments':len(pending),'pending_seconds':sum(r['seconds'] for r in pending),
          'pending_reasons':reasons,'quarantined_rows':len(state['quarantine']),'devices':device_rows,'checks':checks}
    return {**core,'status':'可归档' if closed else '待复核',
            'seal':hashlib.sha256(json.dumps(core,sort_keys=True,ensure_ascii=False).encode()).hexdigest(),
            'pending_rows':[{'device':r['device'],'start':r['start'],'seconds':r['seconds'],'code':PENDING_LABELS.get(r['code'],r['code'])} for r in pending]}

def apply(state):
    state['settlement']=settle(state);s=state['settlement']
    state['views']['settlement']={'title':'结算裁定','rows':s['devices'],'metrics':{'确认额_kWh':s['confirmed_kwh'],'待查片段':s['pending_segments'],'待查分钟':s['pending_seconds']//60,'凭据状态':s['status']}}
