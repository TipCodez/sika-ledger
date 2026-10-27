"""Agreed schedules are projections over the ledger, never new financial entries."""
from datetime import date
import json
from fastapi import HTTPException
from sqlalchemy import select
from .db import plans,instalments
from .domain import now,today,uid,balance,log,invalidate_previews

def invalidate(conn,did,reason):
    rows=list(conn.execute(select(plans).where(plans.c.debt_id==did,plans.c.status=='active')).mappings())
    for row in rows:
        conn.execute(plans.update().where(plans.c.id==row['id']).values(status='needs_review'))
        log(conn,row['merchant_id'],'plan.needs_review',row['id'],reason)

def view(conn,did,current_balance,public=False):
    row=conn.execute(select(plans).where(plans.c.debt_id==did)
        .order_by(plans.c.created_at.desc()).limit(1)).mappings().first()
    if not row:return None
    result={k:row[k] for k in ('id','status','baseline_minor','created_at')}
    result['progress_available']=row['status']=='active'
    if not public:result['reason']=row['reason']
    remaining_paid=max(0,row['baseline_minor']-current_balance) if row['status']=='active' else 0
    items=[]
    for i in conn.execute(select(instalments).where(instalments.c.plan_id==row['id']).order_by(instalments.c.sequence)).mappings():
        applied=min(remaining_paid,i['amount_minor']);remaining_paid-=applied
        outstanding=i['amount_minor']-applied
        state='needs_review' if row['status']=='needs_review' else 'cancelled' if row['status']!='active' else (
            'paid' if outstanding==0 else 'overdue' if i['due_date']<today().isoformat() else 'partial' if applied else 'upcoming')
        items.append({'due_date':i['due_date'],'amount_minor':i['amount_minor'],
            'paid_minor':applied,'remaining_minor':outstanding,'status':state})
    result['instalments']=items
    result['overdue_minor']=sum(i['remaining_minor'] for i in items if i['status']=='overdue')
    pending=next((i for i in items if i['remaining_minor']),None)
    result['next_due_date']=pending['due_date'] if pending else None
    result['next_due_minor']=pending['remaining_minor'] if pending else 0
    if row['status']=='active' and current_balance==0:result['status']='completed'
    return result

def save(conn,merchant,debt,data):
    payload=data.model_dump_json()
    prior=conn.execute(select(plans).where(plans.c.merchant_id==merchant,plans.c.request_key==data.request_key)).mappings().first()
    if prior:
        if prior['debt_id']!=debt['id'] or prior['payload']!=payload:raise HTTPException(409,'Request key already used for another plan.')
        return {'id':prior['id'],'duplicate':True}
    amount=balance(conn,debt['id'])
    if not amount or amount!=data.expected_balance_minor:raise HTTPException(409,'Reload the current unpaid balance before agreeing a plan.')
    from .domain import money
    dates=[];values=[]
    for item in data.instalments:
        try:day=date.fromisoformat(item.due_date)
        except ValueError:raise HTTPException(422,'Use valid YYYY-MM-DD instalment dates.')
        if day<today():raise HTTPException(422,'A new agreed instalment cannot be dated in the past.')
        dates.append(day);values.append(money(item.amount))
    if dates!=sorted(set(dates)):raise HTTPException(422,'Instalment dates must be unique and in increasing order.')
    if sum(values)!=amount:raise HTTPException(422,'Instalments must add up exactly to the current balance.')
    conn.execute(plans.update().where(plans.c.debt_id==debt['id'],plans.c.status=='active').values(status='replaced'))
    pid=uid()
    conn.execute(plans.insert().values(id=pid,merchant_id=merchant,debt_id=debt['id'],
        baseline_minor=amount,status='active',reason=data.reason,request_key=data.request_key,payload=payload,created_at=now()))
    for n,(day,value) in enumerate(zip(dates,values),1):
        conn.execute(instalments.insert().values(id=uid(),plan_id=pid,sequence=n,due_date=day.isoformat(),amount_minor=value))
    invalidate_previews(conn,debt['id']);log(conn,merchant,'plan.agreed',pid,data.reason)
    return {'id':pid,'duplicate':False}
