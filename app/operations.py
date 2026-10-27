"""Audited debt correction, scoped payment recovery and ledger-based reports."""
import json
from datetime import date,timedelta
from fastapi import Depends,HTTPException,Response
from pydantic import BaseModel,Field,ConfigDict
from typing import Literal
from sqlalchemy import select,func
from .db import (write,debts,entries,payments,customers,debt_changes,debt_flags,
                 credit_reviews,susu_payments,susu,merchants)
from .domain import uid,now,today,money,log,owned_debt,balance,invalidate_previews

class Change(BaseModel):
    model_config=ConfigDict(extra='forbid')
    kind:Literal['due_date','adjustment','cancel']
    reason:str=Field(min_length=5,max_length=500)
    request_key:str=Field(min_length=8,max_length=100,pattern=r'^[a-zA-Z0-9_-]+$')
    expected_balance_minor:int=Field(ge=0)
    due_date:str|None=None
    amount:str|None=None
    direction:Literal['increase','decrease']|None=None

class Review(BaseModel):
    model_config=ConfigDict(extra='forbid')
    reason:str=Field(min_length=5,max_length=500)

def register_operations(app,engine,current,all_debts):
    @app.post('/api/debts/{did}/changes')
    def change_debt(did:str,data:Change,m=Depends(current)):
        payload=data.model_dump_json()
        with write(engine) as conn:
            d=owned_debt(conn,m['id'],did,True)
            previous=conn.execute(select(debt_changes).where(debt_changes.c.merchant_id==m['id'],
                debt_changes.c.request_key==data.request_key)).mappings().first()
            if previous:
                if previous['debt_id']!=did or json.loads(previous['payload'])['request']!=data.model_dump():raise HTTPException(409,'Request key was used for another correction.')
                return {'duplicate':True,'balance_minor':balance(conn,did)}
            if conn.scalar(select(debt_flags.c.debt_id).where(debt_flags.c.debt_id==did)):
                raise HTTPException(409,'Cancelled debts cannot be changed.')
            b=balance(conn,did)
            if b!=data.expected_balance_minor:raise HTTPException(409,'Balance changed. Reload the debt before applying a correction.')
            delta=0;cid=uid()
            if data.kind=='due_date':
                try:due=date.fromisoformat(data.due_date or '').isoformat()
                except ValueError:raise HTTPException(422,'Use a valid YYYY-MM-DD due date.')
                conn.execute(debts.update().where(debts.c.id==did).values(due_date=due))
            elif data.kind=='adjustment':
                if not data.direction:raise HTTPException(422,'Choose increase or decrease.')
                delta=money(data.amount)*(1 if data.direction=='increase' else -1)
                if b+delta<0:raise HTTPException(422,'Correction would create a negative debt. Review the existing repayments first.')
                conn.execute(entries.insert().values(id=uid(),merchant_id=m['id'],debt_id=did,
                    kind='adjustment',amount_minor=delta,reference='correction-'+cid,
                    note=data.reason,created_at=now()))
            else:
                if conn.scalar(select(func.count()).select_from(payments).where(payments.c.debt_id==did)):
                    raise HTTPException(409,'Debt has payment history or pending checkout. Use an explained adjustment instead of cancellation.')
                conn.execute(debt_flags.insert().values(debt_id=did,cancelled_at=now()))
                if b:
                    conn.execute(entries.insert().values(id=uid(),merchant_id=m['id'],debt_id=did,
                        kind='cancellation',amount_minor=-b,reference='cancellation-'+cid,note=data.reason,created_at=now()))
            conn.execute(debt_changes.insert().values(id=cid,merchant_id=m['id'],debt_id=did,
                request_key=data.request_key,payload=json.dumps({'request':data.model_dump(),
                    'before':{'due_date':d['due_date'],'balance_minor':b}}),
                kind=data.kind,reason=data.reason,created_at=now()))
            from .plans import invalidate
            invalidate(conn,did,'Debt correction changed the agreed basis or date.')
            invalidate_previews(conn,did)
            log(conn,m['id'],'debt.'+data.kind+'_corrected',did,data.reason)
            return {'duplicate':False,'balance_minor':balance(conn,did)}

    @app.get('/api/debts/{did}/changes')
    def changes(did:str,m=Depends(current)):
        with engine.connect() as conn:
            owned_debt(conn,m['id'],did)
            return [dict(x) for x in conn.execute(select(debt_changes).where(debt_changes.c.debt_id==did)
                .order_by(debt_changes.c.created_at)).mappings()]

    @app.get('/api/payment-recovery')
    def recovery(m=Depends(current)):
        with engine.connect() as conn:
            result=[]
            for kind,table in [('debt',payments),('susu',susu_payments)]:
                for p in conn.execute(select(table).where(table.c.merchant_id==m['id'])
                    .order_by(table.c.created_at.desc()).limit(200)).mappings():
                    item=dict(p)
                    if item['status']!='pending' and not item.get('unallocated_minor'):continue
                    entity=conn.execute(select(debts if kind=='debt' else susu).where(
                        (debts.c.id==item['debt_id']) if kind=='debt' else (susu.c.id==item['account_id']))).mappings().one()
                    item['name']=conn.scalar(select(customers.c.name).where(customers.c.id==entity['customer_id']))
                    item['kind']=kind
                    item.pop('checkout_url',None)
                    review=conn.execute(select(credit_reviews).where(credit_reviews.c.payment_id==item['id'])).mappings().first() if kind=='debt' else None
                    item['review']=dict(review) if review else None
                    result.append(item)
            return sorted(result,key=lambda r:r['created_at'],reverse=True)

    @app.post('/api/payment-recovery/{kind}/{pid}/verify')
    async def reverify(kind:Literal['debt','susu'],pid:str,m=Depends(current)):
        table=payments if kind=='debt' else susu_payments
        with engine.connect() as conn:
            p=conn.execute(select(table).where(table.c.id==pid,table.c.merchant_id==m['id'])).mappings().first()
            if not p:raise HTTPException(404,'Payment not found.')
            if p['source']!='paystack_test':raise HTTPException(409,'This payment is not a Paystack test transaction.')
            ref=p['reference']
        return await app.state.verify_paystack(ref)

    @app.post('/api/payment-recovery/debt/{pid}/review')
    def review_credit(pid:str,data:Review,m=Depends(current)):
        with write(engine) as conn:
            p=conn.execute(select(payments).where(payments.c.id==pid,payments.c.merchant_id==m['id'])
                .with_for_update()).mappings().first()
            if not p:raise HTTPException(404,'Payment not found.')
            if p['status']!='confirmed' or p['unallocated_minor']<=0:raise HTTPException(409,'This payment has no confirmed unallocated credit.')
            previous=conn.scalar(select(credit_reviews.c.payment_id).where(credit_reviews.c.payment_id==pid))
            values=dict(merchant_id=m['id'],note=data.reason,created_at=now())
            if previous:conn.execute(credit_reviews.update().where(credit_reviews.c.payment_id==pid).values(**values))
            else:conn.execute(credit_reviews.insert().values(payment_id=pid,**values))
            log(conn,m['id'],'payment.credit_reviewed',pid,data.reason)
        return {'reviewed':True,'note':'Review note saved. Credit balance is unchanged; no refund or transfer was initiated.'}

    @app.get('/api/reports/collections')
    def collections_report(start:str|None=None,end:str|None=None,m=Depends(current)):
        try:
            start_date=date.fromisoformat(start) if start else today().replace(day=1)
            end_date=date.fromisoformat(end) if end else today()
            if end_date<start_date or end_date==date.max:raise ValueError()
        except ValueError:raise HTTPException(422,'Provide a valid date range with start before end.')
        with engine.connect() as conn:
            ledger=list(conn.execute(select(entries).where(entries.c.merchant_id==m['id'],
                entries.c.created_at>=start_date.isoformat(),
                entries.c.created_at<(end_date+timedelta(days=1)).isoformat())).mappings())
            ds=all_debts(conn,m['id'])
            buckets={k:0 for k in ['Not overdue','1–7 days','8–30 days','31–60 days','61+ days']}
            for d in ds:
                if d['balance_minor']<=0:continue
                days=(today()-date.fromisoformat(d['collection_due_date'])).days
                bucket='Not overdue' if days<=0 else '1–7 days' if days<=7 else '8–30 days' if days<=30 else '31–60 days' if days<=60 else '61+ days'
                buckets[bucket]+=d['balance_minor']
            return {'start':start_date.isoformat(),'end':end_date.isoformat(),'aging_as_of':today().isoformat(),
                'new_credit_minor':sum(e['amount_minor'] for e in ledger if e['kind']=='opening'),
                'adjustments_minor':sum(e['amount_minor'] for e in ledger if e['kind']=='adjustment'),
                'net_recorded_repayments_minor':-sum(e['amount_minor'] for e in ledger if e['kind'] in ('repayment','reversal')),
                'outstanding_now_minor':sum(d['balance_minor'] for d in ds),
                'aging':buckets,'debts':ds}

    @app.get('/api/reports/customers/{cid}/statement')
    def customer_statement(cid:str,m=Depends(current)):
        with engine.connect() as conn:
            c=conn.execute(select(customers).where(customers.c.id==cid,customers.c.merchant_id==m['id'])).mappings().first()
            if not c:raise HTTPException(404,'Customer not found.')
            name=conn.scalar(select(merchants.c.name).where(merchants.c.id==m['id']))
            ds=[d for d in all_debts(conn,m['id']) if d['customer_id']==cid]
            rows=['SIKA LEDGER — CUSTOMER ACCOUNT STATEMENT','Business: '+name,'Customer: '+c['name'],
                'Generated: '+now(),'Currency: GHS','']
            for d in ds:
                rows.extend([d['description'],f"Due: {d['due_date']} | Status: {d['status']}",
                    f"Original: {d['principal_minor']/100:.2f} | Adjusted total: {d['adjusted_total_minor']/100:.2f} | Outstanding: {d['balance_minor']/100:.2f}"])
                for e in conn.execute(select(entries).where(entries.c.debt_id==d['id']).order_by(entries.c.created_at)).mappings():
                    rows.append(f"{e['created_at']} | {e['kind']} | GHS {e['amount_minor']/100:.2f} | {e['note']}")
                rows.append('')
            rows.extend([f"TOTAL OUTSTANDING: GHS {sum(d['balance_minor'] for d in ds)/100:.2f}",
                'Prototype records include manually reported and simulated activity; this is not proof of bank settlement.'])
        return Response('\n'.join(rows),media_type='text/plain',headers={
            'Content-Disposition':f'attachment; filename="customer-statement-{cid}.txt"'})
