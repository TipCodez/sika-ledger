"""Untrusted callback -> durable job -> authenticated provider status lookup.

No claim of SMS-signature verification is made: the full SMS signing contract
was unavailable. Callback status alone is never authoritative. IVR's different
signing algorithm is deliberately not reused for SMS.
"""
from datetime import datetime,timezone
import re
from fastapi import HTTPException
from sqlalchemy import select,and_,or_
import httpx
from .db import (write,sms_callback_inbox,sms_reports,sms_attempts,outbox,debts,customers)
from .domain import uid,now,log
from .jobs import future

STATUSES={'DELIVERED','SUBMITTED','PROHIBITED','QUEUED','NOT_DELIVERED','EXPIRED'}
TERMINAL={'DELIVERED','PROHIBITED','NOT_DELIVERED','EXPIRED'}

async def provider_status(settings,provider_id):
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,128}',provider_id):raise HTTPException(409,'Invalid saved provider message ID.')
    if not settings['arkesel_key']:raise HTTPException(503,'Arkesel key is not configured.')
    try:
        async with httpx.AsyncClient(timeout=12) as client:
            response=await client.get('https://sms.arkesel.com/api/v2/sms/'+provider_id,
                headers={'api-key':settings['arkesel_key']})
        if response.status_code in (401,403):raise HTTPException(503,'Arkesel rejected status-lookup credentials.')
        if response.status_code==404:raise HTTPException(409,'Provider does not recognize this saved message ID.')
        response.raise_for_status()
        result=response.json()
        if not isinstance(result,dict) or result.get('status')!='success':raise ValueError()
        data=result.get('data')
        if not isinstance(data,dict):raise ValueError()
        if data.get('id') is not None and str(data['id'])!=provider_id:raise ValueError()
        status=data.get('status')
        if status not in STATUSES:raise ValueError()
        return {'status':status,'recipient':data.get('recipient')}
    except HTTPException:raise
    except httpx.HTTPError:raise HTTPException(502,'Provider status lookup failed; retry scheduled.')
    except (TypeError,ValueError,KeyError):
        raise HTTPException(409,'Unsupported Arkesel message-details response. Inspect the provider response contract before retrying.')

def receive(conn,provider_id,status):
    attempt=conn.execute(select(sms_attempts).where(sms_attempts.c.provider_id==provider_id,
        sms_attempts.c.mode.in_(['arkesel_sandbox','arkesel_live'])).with_for_update()).mappings().first()
    if not attempt:return {'received':True,'ignored':True}
    if status not in STATUSES:return {'received':True,'ignored':True,'reason':'unsupported_status'}
    previous=conn.execute(select(sms_callback_inbox).where(
        sms_callback_inbox.c.attempt_id==attempt['id'],sms_callback_inbox.c.claimed_status==status)).mappings().first()
    if previous:return {'received':True,'duplicate':True,'job_id':previous['id']}
    jid=uid()
    conn.execute(sms_callback_inbox.insert().values(id=jid,merchant_id=attempt['merchant_id'],
        attempt_id=attempt['id'],provider_id=provider_id,claimed_status=status,status='pending',
        attempts=0,next_attempt_at=now(),created_at=now()))
    return {'received':True,'queued':True,'job_id':jid}

async def process_callbacks(app,limit=10):
    counts={'processed':0,'retry':0,'needs_review':0}
    for _ in range(limit):
        with write(app.state.engine) as conn:
            stamp=now()
            row=conn.execute(select(sms_callback_inbox).where(or_(
                and_(sms_callback_inbox.c.status.in_(['pending','retry']),sms_callback_inbox.c.next_attempt_at<=stamp),
                and_(sms_callback_inbox.c.status=='processing',sms_callback_inbox.c.locked_until<stamp)))
                .order_by(sms_callback_inbox.c.created_at).with_for_update(skip_locked=True).limit(1)).mappings().first()
            if not row:break
            job=dict(row);lease=future(90);attempts=job['attempts']+1
            conn.execute(sms_callback_inbox.update().where(sms_callback_inbox.c.id==job['id']).values(
                status='processing',attempts=attempts,locked_until=lease))
        report=None;error=None
        try:
            report=await provider_status(app.state.settings,job['provider_id'])
            state='processed'
        except HTTPException as exc:
            state='retry' if exc.status_code>=500 else 'needs_review';error=str(exc.detail)
        except Exception:
            state='retry';error='Unexpected status lookup failure.'
        if state=='retry' and attempts>=6:state='needs_review'
        with write(app.state.engine) as conn:
            active=conn.execute(select(sms_callback_inbox).where(sms_callback_inbox.c.id==job['id'])
                .with_for_update()).mappings().one()
            if active['locked_until']!=lease or active['status']!='processing':continue
            if report:
                a=conn.execute(select(sms_attempts).where(sms_attempts.c.id==job['attempt_id'])
                    .with_for_update()).mappings().one()
                existing=conn.execute(select(sms_reports).where(sms_reports.c.attempt_id==a['id'])
                    .with_for_update()).mappings().first()
                expected_number=conn.scalar(select(customers.c.phone).join(debts,debts.c.customer_id==customers.c.id)
                    .join(outbox,outbox.c.debt_id==debts.c.id).where(outbox.c.id==a['outbox_id']))
                recipient=report.get('recipient')
                recipient_matches=(recipient is None or str(recipient).lstrip('+')==expected_number.lstrip('+'))
                # A verified terminal result cannot regress through a delayed callback.
                if not recipient_matches:
                    state='needs_review';error='Provider recipient does not match the recorded message recipient.'
                elif existing and existing['status'] in TERMINAL and existing['status']!=report['status']:
                    state='needs_review';error='Conflicting provider status after a terminal report.'
                else:
                    values=dict(merchant_id=a['merchant_id'],provider_id=job['provider_id'],
                        status=report['status'],source='authenticated_provider_lookup',checked_at=now())
                    if existing:conn.execute(sms_reports.update().where(sms_reports.c.attempt_id==a['id']).values(**values))
                    else:conn.execute(sms_reports.insert().values(attempt_id=a['id'],**values))
            conn.execute(sms_callback_inbox.update().where(sms_callback_inbox.c.id==job['id']).values(
                status=state,locked_until=None,last_error=error,next_attempt_at=future(min(1800,30*2**min(attempts-1,6))),
                processed_at=now() if state=='processed' else None))
            log(conn,job['merchant_id'],'sms.callback_'+state,job['id'],error or '')
            counts[state]+=1
    return counts
