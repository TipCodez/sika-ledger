"""Server-enforced owner/collector permissions and request actor attribution."""
from contextvars import ContextVar
import hashlib
import re
from fastapi import HTTPException
from sqlalchemy import select
from .db import staff,merchants,sessions,session_actors,write
from .domain import now

actor_context=ContextVar('sika_actor',default=None)

def bootstrap_owners(engine):
    with write(engine) as conn:
        # Existing merchant IDs become stable owner IDs, retaining old sessions.
        for m in conn.execute(select(merchants)).mappings():
            if not conn.scalar(select(staff.c.id).where(staff.c.id==m['id'])):
                conn.execute(staff.insert().values(id=m['id'],merchant_id=m['id'],name=m['name'],
                    login=m['login'],password_hash=m['password_hash'],role='owner',active=1,created_at=m['created_at']))

def identity(engine,token):
    if not token:return None
    digest=hashlib.sha256(token.encode()).hexdigest()
    with engine.connect() as conn:
        s=conn.execute(select(sessions).where(sessions.c.token_hash==digest,sessions.c.expires_at>now())).mappings().first()
        if not s:return None
        aid=conn.scalar(select(session_actors.c.actor_id).where(session_actors.c.token_hash==digest)) or s['merchant_id']
        a=conn.execute(select(staff).where(staff.c.id==aid,staff.c.merchant_id==s['merchant_id'],staff.c.active==1)).mappings().first()
        if not a:return None
        m=conn.execute(select(merchants).where(merchants.c.id==s['merchant_id'])).mappings().one()
        return {'id':m['id'],'name':m['name'],'login':a['login'],'actor_id':a['id'],
            'actor_name':a['name'],'role':a['role']}

def authorize(request,person):
    if person['role']=='owner':return
    path=request.url.path
    if request.method in ('GET','HEAD'):
        patterns=(r'/api/me',r'/api/dashboard',r'/api/debts(?:/[^/]+)?',
            r'/api/customers(?:/[^/]+)?',r'/api/reminders',r'/api/disputes',r'/api/susu',
            r'/api/reports/collections',r'/api/reports/customers/[^/]+/statement(?:\.pdf)?',
            r'/api/payments/[^/]+/receipt(?:\.pdf)?',r'/api/susu-payments/[^/]+/receipt\.pdf',
            r'/api/debts/[^/]+/plans')
    else:
        patterns=(r'/api/debts',r'/api/debts/[^/]+/payments',r'/api/susu/[^/]+/entries',
            r'/api/reminders/preview',r'/api/reminders/[^/]+/queue') if request.method=='POST' else ()
    if not any(re.fullmatch(p,path) for p in patterns):
        raise HTTPException(403,'Owner permission is required for this action.')
