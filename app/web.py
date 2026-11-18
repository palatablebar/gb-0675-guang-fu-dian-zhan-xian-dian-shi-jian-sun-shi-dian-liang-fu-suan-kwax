from fastapi import Request
from fastapi.templating import Jinja2Templates
from app.config import ROOT, META
from app.engine import evaluate
from app.errors import BusinessError
from app import store

templates=Jinja2Templates(directory=str(ROOT/'app/templates'))

def render(request:Request,key):
    from app.main import DB_PATH
    run_id=request.query_params.get('run_id')
    if run_id:
        state=store.get(DB_PATH,run_id)
    else:
        state=store.save(DB_PATH,evaluate({'dataset':request.query_params.get('dataset','dirty')}))
    if key not in state['views']: raise BusinessError('VIEW_MISSING','该批次无此模块视图，请复算',404)
    return templates.TemplateResponse(request=request,name=f'{key}.html',context={'meta':META,'key':key,'state':state,'view':state['views'][key]})
