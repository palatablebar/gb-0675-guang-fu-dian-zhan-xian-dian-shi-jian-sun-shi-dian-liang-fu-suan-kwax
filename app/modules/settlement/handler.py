from fastapi import APIRouter, Request, Body
from app.engine import evaluate
from app.web import render
router=APIRouter()
@router.get('/settlement')
def page(request:Request):
    return render(request,'settlement')
@router.post('/api/settlement')
def calculate(payload:dict=Body(default={})):
    return evaluate(payload,until=8)
