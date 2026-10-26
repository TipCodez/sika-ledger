import csv
import asyncio
import hashlib
import hmac
import io
import json
import os
import secrets
import threading
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse
import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request, Response, Depends
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, ConfigDict, field_validator
from sqlalchemy import select, func, and_
from sqlalchemy.exc import IntegrityError
from .db import (make_engine, write, merchants, sessions, customers, debts, entries,
    payments, disputes, outbox, audit, susu, contributions, proposals,
    webhook_inbox, sms_attempts, conversations, turns)
from .domain import (uid, now, today, money, phone, password_hash, password_ok,
    log, balance, create_debt, owned_debt, debt_view, confirm_payment,
    reverse_payment, customer_get_or_create, invalidate_previews)
from . import agent

load_dotenv()
STATIC = Path(__file__).parent/'static'

class Strict(BaseModel):
    model_config = ConfigDict(extra='forbid')

class Login(Strict):
    login: str = Field(min_length=3,max_length=100)
    password: str = Field(min_length=10,max_length=128)

class Register(Login):
    name: str = Field(min_length=2,max_length=100)

class DebtIn(Strict):
    name: str = Field(min_length=1,max_length=100)
    phone: str = Field(min_length=10,max_length=30)
    amount: str = Field(max_length=20)
    initial_paid: str = Field(default='0',max_length=20)
    due_date: str = Field(min_length=10,max_length=10)
    description: str = Field(default='Credit sale',max_length=300)
    @field_validator('name')
    @classmethod
    def clean_name(cls,v):
        if not v.strip(): raise ValueError('Name cannot be blank')
        return v.strip()
    @field_validator('amount','initial_paid')
    @classmethod
    def validate_money(cls,v,info):
        money(v,allow_zero=info.field_name=='initial_paid')
        return v

class PaymentIn(Strict):
    amount: str = Field(max_length=20)
    idempotency_key: str = Field(min_length=8,max_length=100,pattern=r'^[a-zA-Z0-9_-]+$')
    source: Literal['cash','direct_momo'] = 'cash'
    note: str = Field(default='Recorded by trader',max_length=300)

class CheckoutIn(Strict):
    amount: str = Field(max_length=20)
    mode: Literal['simulation','paystack_test'] = 'simulation'
    idempotency_key: str = Field(min_length=8,max_length=100,pattern=r'^[a-zA-Z0-9_-]+$')

class DisputeIn(Strict):
    reason: Literal['incorrect_balance','wrong_number','other']
    message: str = Field(min_length=3,max_length=500)

class Reason(Strict):
    reason: str = Field(min_length=5,max_length=500)

class Pause(Strict):
    paused: bool

class AgentIn(Strict):
    message: str = Field(min_length=3,max_length=2000)
    consent: bool = False
    conversation_id: str | None = Field(default=None,max_length=64)
    request_id: str = Field(default_factory=uid,min_length=8,max_length=100,pattern=r'^[a-zA-Z0-9_-]+$')

class SusuIn(Strict):
    name: str = Field(min_length=1,max_length=100)
    phone: str = Field(min_length=10,max_length=30)
    label: str = Field(min_length=1,max_length=100)
    daily_target: str = Field(max_length=20)

class SusuEntry(Strict):
    amount: str = Field(max_length=20)
    kind: Literal['contribution','payout']
    reference: str = Field(min_length=8,max_length=100,pattern=r'^[a-zA-Z0-9_-]+$')
    note: str = Field(min_length=3,max_length=300)

def create_app(database_url=None, overrides=None):
    settings = {'database_url':database_url or os.getenv('DATABASE_URL','sqlite:///./sika.db'),
        'base_url':os.getenv('PUBLIC_BASE_URL','http://127.0.0.1:8000').rstrip('/'),
        'secure_cookie':os.getenv('COOKIE_SECURE','false').lower()=='true',
        'demo':os.getenv('DEMO_MODE','false').lower()=='true',
        'gemini_key':os.getenv('GEMINI_API_KEY',''),'gemini_model':os.getenv('GEMINI_MODEL','gemini-3.8-flash'),
        'paystack_key':os.getenv('PAYSTACK_SECRET_KEY',''),
        'paystack_email':os.getenv('PAYSTACK_TEST_EMAIL',''),
        'reminder_start':int(os.getenv('REMINDER_START_HOUR','9')),
        'reminder_end':int(os.getenv('REMINDER_END_HOUR','18')),
        'sms_mode':os.getenv('SMS_MODE','dry_run'),
        'arkesel_key':os.getenv('ARKESEL_API_KEY',''),
        'sms_sender':os.getenv('SMS_SENDER_ID','SikaLedger'),
        'automatic_reminders':os.getenv('AUTOMATIC_REMINDERS','false').lower()=='true',
        'worker_enabled':os.getenv('RUN_BACKGROUND_WORKER','false').lower()=='true'}
    settings.update(overrides or {})
    if settings['sms_mode'] not in ('dry_run','arkesel_sandbox'):
        raise RuntimeError('SMS_MODE must be dry_run or arkesel_sandbox. Live SMS is blocked in this prototype.')
    if not 0 <= settings['reminder_start'] < settings['reminder_end'] <= 24:
        raise RuntimeError('Reminder hours must satisfy 0 <= start < end <= 24.')
    if settings['paystack_key'] and not settings['paystack_key'].startswith('sk_test_'):
        raise RuntimeError('This prototype accepts Paystack TEST keys only; live payment keys are blocked.')
    engine = make_engine(settings['database_url'])
    @asynccontextmanager
    async def lifespan(app):
        from .jobs import worker_loop
        task=asyncio.create_task(worker_loop(app)) if settings['worker_enabled'] else None
        yield
        if task:
            task.cancel()
            try:await task
            except asyncio.CancelledError:pass
        engine.dispose()
    app = FastAPI(title='Sika Ledger Prototype',version='0.2.0',lifespan=lifespan)
    app.state.engine=engine
    app.state.settings=settings
    buckets=defaultdict(deque)
    bucket_lock=threading.Lock()

    @app.middleware('http')
    async def boundaries(request, call_next):
        # Same-origin browser mutations; webhooks use their cryptographic signature.
        if request.method not in ('GET','HEAD','OPTIONS') and request.url.path!='/api/webhooks/paystack':
            origin=request.headers.get('origin')
            if origin and origin!=settings['base_url']:
                return Response('Cross-origin request rejected',status_code=403)
            if request.headers.get('sec-fetch-site')=='cross-site':
                return Response('Cross-site request rejected',status_code=403)
        if request.url.path.startswith('/api/'):
            group='auth' if request.url.path.startswith('/api/auth/') else ('agent' if request.url.path.startswith('/api/agent') else 'general')
            key=(request.client.host if request.client else 'local',group)
            limit={'auth':40,'agent':15,'general':300}[group]
            t=time.monotonic()
            with bucket_lock:
                q=buckets[key]
                while q and t-q[0]>60:q.popleft()
                if len(q)>=limit:return Response('Rate limit exceeded',status_code=429)
                q.append(t)
        response=await call_next(request)
        response.headers['X-Content-Type-Options']='nosniff'
        response.headers['Referrer-Policy']='no-referrer'
        response.headers['X-Frame-Options']='DENY'
        response.headers['Cache-Control']='no-store'
        response.headers['Content-Security-Policy']="default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
        return response

    @app.exception_handler(IntegrityError)
    async def conflict(request,exc):
        from fastapi.responses import JSONResponse
        return JSONResponse({'detail':'A conflicting record already exists. Reload and check your previous submission.'},status_code=409)

    def current(request:Request):
        token=request.cookies.get('sika_session','')
        digest=hashlib.sha256(token.encode()).hexdigest()
        with engine.connect() as conn:
            row=conn.execute(select(merchants).join(sessions,sessions.c.merchant_id==merchants.c.id)
                .where(sessions.c.token_hash==digest,sessions.c.expires_at>now())).mappings().first()
        if not row:raise HTTPException(401,'Sign in to continue.')
        return dict(row)

    def establish(conn,merchant,response):
        token=secrets.token_urlsafe(40)
        conn.execute(sessions.insert().values(token_hash=hashlib.sha256(token.encode()).hexdigest(),
            merchant_id=merchant,expires_at=(datetime.now(timezone.utc)+timedelta(hours=12)).isoformat()))
        response.set_cookie('sika_session',token,httponly=True,secure=settings['secure_cookie'],
                            samesite='strict',max_age=43200,path='/')

    def public_debt(conn,token,lock=False):
        q=select(debts).where(debts.c.public_token==token)
        if lock:q=q.with_for_update()
        row=conn.execute(q).mappings().first()
        if not row:raise HTTPException(404,'Payment link not found.')
        return dict(row)

    def acknowledgement(conn,p):
        d=owned_debt(conn,p['merchant_id'],p['debt_id'])
        business=conn.scalar(select(merchants.c.name).where(merchants.c.id==p['merchant_id']))
        customer=conn.scalar(select(customers.c.name).where(customers.c.id==d['customer_id']))
        mode=('PROVIDER-VERIFIED TEST PAYMENT — NO REAL MONEY MOVED' if p['source']=='paystack_test'
            else 'SIMULATED PAYMENT — NO REAL MONEY MOVED' if p['source']=='simulation'
            else 'TRADER-REPORTED PAYMENT — NOT PROVIDER VERIFIED')
        body='\n'.join(['SIKA LEDGER — REPAYMENT ACKNOWLEDGEMENT',mode,'',
            'Acknowledgement ID: '+p['id'],'Business: '+business,'Customer: '+customer,
            'Description: '+d['description'],'Recorded at: '+(p['confirmed_at'] or p['created_at']),
            'Payment source: '+p['source'],'Payment status: '+p['status'],
            f"Original sale: GHS {d['principal_minor']/100:.2f}",
            f"Payment recorded: GHS {p['amount_minor']/100:.2f}",
            f"Originally allocated to this debt: GHS {p['applied_minor']/100:.2f}",
            f"Current unallocated credit: GHS {p['unallocated_minor']/100:.2f}",
            f"Current debt balance at download: GHS {balance(conn,d['id'])/100:.2f}",
            '','REVERSED: this payment no longer reduces the debt.' if p['status']=='reversed' else '',
            'This is a prototype account acknowledgement, not a tax invoice.',
            'Ledger reversal does not itself refund or transfer money.',''])
        return Response(body,media_type='text/plain',headers={
            'Content-Disposition':f'attachment; filename="repayment-{p["id"]}.txt"'})

    def all_debts(conn,merchant):
        return [debt_view(conn,d) for d in conn.execute(select(debts).where(
            debts.c.merchant_id==merchant).order_by(debts.c.created_at.desc())).mappings()]

    def overview(conn,merchant):
        ds=all_debts(conn,merchant)
        return {'outstanding_minor':sum(d['balance_minor'] for d in ds),
            'overdue_minor':sum(d['balance_minor'] for d in ds if d['status']=='overdue'),
            'collected_minor':sum(d['paid_minor'] for d in ds),
            'open_debts':sum(d['balance_minor']>0 for d in ds),
            'customers':conn.scalar(select(func.count()).select_from(customers).where(customers.c.merchant_id==merchant)),
            'unallocated_minor':conn.scalar(select(func.coalesce(func.sum(payments.c.unallocated_minor),0))
                .where(payments.c.merchant_id==merchant,payments.c.status=='confirmed'))}

    @app.get('/api/health')
    def health():return {'status':'ok','version':'0.2.0','prototype':True}

    @app.get('/api/config')
    def config():return {'demo_mode':settings['demo'],'gemini_configured':bool(settings['gemini_key']),
        'gemini_model':settings['gemini_model'],'paystack_test_configured':bool(settings['paystack_key'] and settings['paystack_email']),
        'sms_mode':settings['sms_mode'],'sms_configured':bool(settings['arkesel_key']),
        'background_worker':settings['worker_enabled'],'automatic_reminders':settings['automatic_reminders'],
        'payment_mode':'test_only'}

    @app.post('/api/auth/register')
    def register(data:Register,response:Response):
        login=data.login.strip().lower()
        if not login or not data.name.strip():raise HTTPException(422,'Business name and login are required.')
        mid=uid()
        hashed=password_hash(data.password)
        with write(engine) as conn:
            conn.execute(merchants.insert().values(id=mid,name=data.name.strip(),login=login,
                password_hash=hashed,created_at=now()))
            log(conn,mid,'merchant.registered',mid)
            establish(conn,mid,response)
        return {'id':mid,'name':data.name.strip(),'login':login}

    @app.post('/api/auth/login')
    def login(data:Login,response:Response):
        with write(engine) as conn:
            row=conn.execute(select(merchants).where(merchants.c.login==data.login.strip().lower())).mappings().first()
            if not row or not password_ok(data.password,row['password_hash']):
                raise HTTPException(401,'Incorrect login or password.')
            establish(conn,row['id'],response)
            return {'id':row['id'],'name':row['name'],'login':row['login']}

    @app.post('/api/auth/logout')
    def logout(request:Request,response:Response):
        token=request.cookies.get('sika_session','')
        with write(engine) as conn:
            conn.execute(sessions.delete().where(sessions.c.token_hash==hashlib.sha256(token.encode()).hexdigest()))
        response.delete_cookie('sika_session',path='/')
        return {'ok':True}

    @app.get('/api/me')
    def me(m=Depends(current)):return {k:m[k] for k in ('id','name','login')}

    @app.get('/api/dashboard')
    def dashboard(m=Depends(current)):
        with engine.connect() as conn:return overview(conn,m['id'])

    @app.get('/api/debts')
    def list_debts(m=Depends(current)):
        with engine.connect() as conn:return all_debts(conn,m['id'])

    @app.post('/api/debts',status_code=201)
    def new_debt(data:DebtIn,m=Depends(current)):
        with write(engine) as conn:
            did=create_debt(conn,m['id'],data.model_dump())
            return debt_view(conn,owned_debt(conn,m['id'],did))

    @app.get('/api/debts/{did}')
    def detail(did:str,m=Depends(current)):
        with engine.connect() as conn:
            d=debt_view(conn,owned_debt(conn,m['id'],did))
            d['ledger']=[dict(x) for x in conn.execute(select(entries).where(entries.c.debt_id==did).order_by(entries.c.created_at)).mappings()]
            d['payments']=[dict(x) for x in conn.execute(select(payments).where(payments.c.debt_id==did).order_by(payments.c.created_at.desc())).mappings()]
            return d

    @app.post('/api/debts/{did}/payments')
    def manual(did:str,data:PaymentIn,m=Depends(current)):
        amt=money(data.amount)
        reference='manual-'+m['id']+'-'+data.idempotency_key
        with write(engine) as conn:
            d=owned_debt(conn,m['id'],did,True)
            prior=conn.execute(select(payments).where(payments.c.reference==reference)).mappings().first()
            if prior:
                if prior['debt_id']!=did or prior['amount_minor']!=amt or prior['source']!=data.source:
                    raise HTTPException(409,'Idempotency key was used for different payment details.')
                return {'duplicate':True,'payment_id':prior['id'],'balance_minor':balance(conn,did)}
            if amt>balance(conn,did):raise HTTPException(422,'Manual payment exceeds the outstanding balance.')
            conn.execute(payments.insert().values(id=uid(),merchant_id=m['id'],debt_id=did,
                reference=reference,amount_minor=amt,applied_minor=0,unallocated_minor=0,
                source=data.source,status='pending',created_at=now()))
            return confirm_payment(conn,reference,'Trader recorded '+data.source+': '+data.note)

    @app.post('/api/payments/{pid}/reverse')
    def reverse(pid:str,data:Reason,m=Depends(current)):
        with write(engine) as conn:reverse_payment(conn,m['id'],pid,data.reason)
        return {'ok':True,'note':'Ledger reversal only. No money has been transferred or refunded.'}

    @app.get('/api/payments/{pid}/receipt')
    def merchant_receipt(pid:str,m=Depends(current)):
        with engine.connect() as conn:
            p=conn.execute(select(payments).where(payments.c.id==pid,
                payments.c.merchant_id==m['id'],payments.c.status.in_(['confirmed','reversed']))).mappings().first()
            if not p:raise HTTPException(404,'Acknowledgement not found.')
            return acknowledgement(conn,p)

    @app.get('/api/public/{token}/receipts/{pid}')
    def public_receipt(token:str,pid:str):
        with engine.connect() as conn:
            d=public_debt(conn,token)
            p=conn.execute(select(payments).where(payments.c.id==pid,
                payments.c.debt_id==d['id'],payments.c.status.in_(['confirmed','reversed']))).mappings().first()
            if not p:raise HTTPException(404,'Acknowledgement not found.')
            return acknowledgement(conn,p)

    @app.post('/api/debts/{did}/pause')
    def pause(did:str,data:Pause,m=Depends(current)):
        with write(engine) as conn:
            owned_debt(conn,m['id'],did,True)
            conn.execute(debts.update().where(debts.c.id==did).values(paused=int(data.paused)))
            invalidate_previews(conn,did)
            log(conn,m['id'],'debt.pause_changed',did,str(data.paused))
        return {'ok':True}

    @app.post('/api/debts/{did}/rotate-link')
    def rotate_link(did:str,m=Depends(current)):
        with write(engine) as conn:
            owned_debt(conn,m['id'],did,True)
            token=secrets.token_urlsafe(32)
            conn.execute(debts.update().where(debts.c.id==did).values(public_token=token))
            invalidate_previews(conn,did)
            log(conn,m['id'],'debt.link_rotated',did)
        return {'public_token':token,'note':'Previous payment links are now invalid. Existing provider intents remain reconcilable.'}

    @app.get('/api/public/{token}')
    def public(token:str):
        with engine.connect() as conn:
            d=public_debt(conn,token)
            result=debt_view(conn,d,True)
            result['business_name']=conn.scalar(select(merchants.c.name).where(merchants.c.id==d['merchant_id']))
            result['receipts']=[{'id':p['id'],'amount_minor':p['amount_minor'],
                'applied_minor':p['applied_minor'],'unallocated_minor':p['unallocated_minor'],
                'status':p['status'],'source':p['source'],'date':p['confirmed_at']}
                for p in conn.execute(select(payments).where(payments.c.debt_id==d['id'],
                    payments.c.status.in_(['confirmed','reversed']))).mappings()]
            return result

    @app.post('/api/public/{token}/dispute')
    def dispute(token:str,data:DisputeIn):
        with write(engine) as conn:
            d=public_debt(conn,token,True)
            active=conn.execute(select(disputes).where(disputes.c.debt_id==d['id'],disputes.c.status=='open')).mappings().first()
            if active:
                if data.reason=='wrong_number':
                    conn.execute(customers.update().where(customers.c.id==d['customer_id']).values(do_not_contact=1))
                    ids=select(debts.c.id).where(debts.c.customer_id==d['customer_id'])
                    conn.execute(outbox.update().where(outbox.c.debt_id.in_(ids),outbox.c.status.in_(['preview','queued'])).values(status='stale'))
                    log(conn,d['merchant_id'],'customer.wrong_number_reported',d['customer_id'])
                return {'ok':True,'duplicate':True}
            conn.execute(disputes.insert().values(id=uid(),merchant_id=d['merchant_id'],debt_id=d['id'],
                reason=data.reason,message=data.message,status='open',created_at=now()))
            conn.execute(debts.update().where(debts.c.id==d['id']).values(disputed=1))
            if data.reason=='wrong_number':
                conn.execute(customers.update().where(customers.c.id==d['customer_id']).values(do_not_contact=1))
                ids=select(debts.c.id).where(debts.c.customer_id==d['customer_id'])
                conn.execute(outbox.update().where(outbox.c.debt_id.in_(ids),outbox.c.status.in_(['preview','queued'])).values(status='stale'))
            invalidate_previews(conn,d['id'])
            log(conn,d['merchant_id'],'debt.disputed',d['id'],data.reason)
        return {'ok':True,'message':'Report received. Automated reminders are suspended for review.'}

    @app.get('/api/disputes')
    def list_disputes(m=Depends(current)):
        with engine.connect() as conn:return [dict(x) for x in conn.execute(select(disputes).where(disputes.c.merchant_id==m['id']).order_by(disputes.c.created_at.desc())).mappings()]

    @app.post('/api/disputes/{sid}/resolve')
    def resolve(sid:str,data:Reason,m=Depends(current)):
        with write(engine) as conn:
            row=conn.execute(select(disputes).where(disputes.c.id==sid,disputes.c.merchant_id==m['id']).with_for_update()).mappings().first()
            if not row:raise HTTPException(404,'Dispute not found.')
            conn.execute(disputes.update().where(disputes.c.id==sid).values(status='resolved',resolution=data.reason))
            conn.execute(debts.update().where(debts.c.id==row['debt_id']).values(disputed=0,paused=1))
            log(conn,m['id'],'dispute.resolved',sid,data.reason)
        return {'ok':True,'message':'Resolved. Reminders remain paused; a wrong-number contact remains opted out.'}

    @app.post('/api/public/{token}/checkout')
    async def checkout(token:str,data:CheckoutIn):
        amount=money(data.amount)
        if data.mode=='simulation' and not settings['demo']:raise HTTPException(403,'Payment simulation is disabled.')
        if data.mode=='paystack_test' and not(settings['paystack_key'] and settings['paystack_email']):
            raise HTTPException(503,'Configure PAYSTACK_SECRET_KEY (test only) and PAYSTACK_TEST_EMAIL first.')
        with write(engine) as conn:
            d=public_debt(conn,token,True)
            ref='sika-'+d['id']+'-'+data.idempotency_key
            prior=conn.execute(select(payments).where(payments.c.reference==ref)).mappings().first()
            if prior:
                if prior['amount_minor']!=amount or prior['source']!=data.mode:
                    raise HTTPException(409,'Idempotency key was used for another checkout.')
                return {'reference':ref,'mode':data.mode,'status':prior['status'],'retry_verify':True,
                        'authorization_url':prior['checkout_url'] if prior['status']=='pending' else None}
            if d['disputed']:raise HTTPException(409,'This balance is under review. Contact the business before paying.')
            if amount>balance(conn,d['id']):raise HTTPException(422,'Amount exceeds the outstanding balance.')
            conn.execute(payments.insert().values(id=uid(),merchant_id=d['merchant_id'],debt_id=d['id'],
                reference=ref,amount_minor=amount,applied_minor=0,unallocated_minor=0,
                source=data.mode,status='pending',created_at=now()))
            log(conn,d['merchant_id'],'checkout.started',d['id'],data.mode)
        if data.mode=='simulation':return {'reference':ref,'mode':'simulation','status':'pending'}
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                r=await client.post('https://api.paystack.co/transaction/initialize',
                    headers={'Authorization':'Bearer '+settings['paystack_key']},json={
                        'reference':ref,'amount':amount,'currency':'GHS','email':settings['paystack_email'],
                        'channels':['mobile_money'], 'metadata':{'debt_id':d['id'],'merchant_id':d['merchant_id']},
                        'callback_url':settings['base_url']+'/pay/'+token})
            r.raise_for_status()
            body=r.json()
            if not body.get('status'):raise ValueError()
            url=body['data']['authorization_url']
            if urlparse(url).scheme!='https' or urlparse(url).hostname!='checkout.paystack.com':raise ValueError()
            with write(engine) as conn:
                conn.execute(payments.update().where(payments.c.reference==ref).values(checkout_url=url))
            return {'reference':ref,'mode':data.mode,'authorization_url':url,'status':'pending'}
        except (httpx.HTTPError,ValueError,KeyError):
            with write(engine) as conn:
                log(conn,d['merchant_id'],'checkout.initialization_uncertain',d['id'],ref)
            raise HTTPException(502,'Test checkout could not be started. No balance was changed. Retry with a new checkout.')

    @app.post('/api/public/{token}/simulate/{reference}')
    def simulate(token:str,reference:str):
        if not settings['demo']:raise HTTPException(403,'Simulation is disabled.')
        with write(engine) as conn:
            d=public_debt(conn,token,True)
            p=conn.execute(select(payments).where(payments.c.reference==reference,
                payments.c.debt_id==d['id'],payments.c.source=='simulation')).mappings().first()
            if not p:raise HTTPException(404,'Simulation reference not found.')
            return confirm_payment(conn,reference,'SIMULATED payment. No money moved.')

    async def verify_paystack(reference):
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                r=await client.get('https://api.paystack.co/transaction/verify/'+reference,
                    headers={'Authorization':'Bearer '+settings['paystack_key']})
            r.raise_for_status()
            result=r.json()
            if not result.get('status'):raise ValueError()
            verified=result['data']
        except (httpx.HTTPError,KeyError,ValueError):
            raise HTTPException(502,'Provider verification unavailable. Balance remains unchanged; retry verification later.')
        with write(engine) as conn:
            p=conn.execute(select(payments).where(payments.c.reference==reference)).mappings().first()
            if not p:raise HTTPException(404,'Unknown payment reference.')
            if p['source']!='paystack_test':raise HTTPException(409,'Incorrect payment source.')
            metadata=verified.get('metadata') or {}
            if isinstance(metadata,str):
                try:metadata=json.loads(metadata)
                except ValueError:metadata={}
            if (verified.get('reference')!=reference or verified.get('amount')!=p['amount_minor']
                or verified.get('currency')!='GHS' or verified.get('domain')!='test'
                or not isinstance(metadata,dict) or metadata.get('debt_id')!=p['debt_id']
                or metadata.get('merchant_id')!=p['merchant_id']):
                raise HTTPException(409,'Provider transaction does not match the expected test payment.')
            if verified.get('status')!='success':return {'status':verified.get('status','pending'),'balance_unchanged':True}
            return confirm_payment(conn,reference,'Paystack server-verified TEST payment. No real money moved.')

    app.state.verify_paystack=verify_paystack

    @app.post('/api/public/{token}/verify/{reference}')
    async def verify(token:str,reference:str):
        with engine.connect() as conn:
            d=public_debt(conn,token)
            if not conn.scalar(select(payments.c.id).where(payments.c.reference==reference,payments.c.debt_id==d['id'])):
                raise HTTPException(404,'Payment reference not found.')
        return await verify_paystack(reference)

    @app.post('/api/webhooks/paystack')
    async def webhook(request:Request):
        key=settings['paystack_key']
        if not key:raise HTTPException(503,'Test payment provider is not configured.')
        raw=await request.body()
        if len(raw)>100000:raise HTTPException(413,'Payload too large.')
        expected=hmac.new(key.encode(),raw,hashlib.sha512).hexdigest()
        if not hmac.compare_digest(expected,request.headers.get('x-paystack-signature','')):
            raise HTTPException(401,'Invalid webhook signature.')
        try:event=json.loads(raw)
        except ValueError:raise HTTPException(400,'Invalid webhook payload.')
        if not isinstance(event,dict):raise HTTPException(400,'Invalid webhook payload.')
        if event.get('event')!='charge.success':return {'received':True,'ignored':True}
        event_data=event.get('data')
        if not isinstance(event_data,dict):raise HTTPException(400,'Invalid transaction data.')
        ref=event_data.get('reference','')
        if not isinstance(ref,str) or len(ref)>200 or not ref.startswith('sika-'):
            raise HTTPException(400,'Invalid transaction reference.')
        # Acknowledge only after durable storage; verification runs in the worker.
        with write(engine) as conn:
            p=conn.execute(select(payments).where(payments.c.reference==ref,
                payments.c.source=='paystack_test').with_for_update()).mappings().first()
            if not p:return {'received':True,'ignored':True}
            key='charge.success:'+ref
            existing=conn.execute(select(webhook_inbox).where(webhook_inbox.c.event_key==key)).mappings().first()
            if existing:return {'received':True,'duplicate':True,'job_id':existing['id']}
            jid=uid()
            conn.execute(webhook_inbox.insert().values(id=jid,merchant_id=p['merchant_id'],
                event_key=key,reference=ref,status='pending',attempts=0,
                next_attempt_at=now(),created_at=now()))
            log(conn,p['merchant_id'],'webhook.received',jid)
        return {'received':True,'queued':True,'job_id':jid}

    @app.get('/api/integrations/webhooks')
    def webhook_jobs(m=Depends(current)):
        with engine.connect() as conn:
            return [dict(r) for r in conn.execute(select(webhook_inbox).where(
                webhook_inbox.c.merchant_id==m['id']).order_by(webhook_inbox.c.created_at.desc()).limit(100)).mappings()]

    @app.post('/api/integrations/webhooks/{jid}/retry')
    def retry_webhook(jid:str,data:Reason,m=Depends(current)):
        with write(engine) as conn:
            row=conn.execute(select(webhook_inbox).where(webhook_inbox.c.id==jid,
                webhook_inbox.c.merchant_id==m['id']).with_for_update()).mappings().first()
            if not row:raise HTTPException(404,'Verification job not found.')
            if row['status'] not in ('needs_review','retry'):raise HTTPException(409,'This job is not available for retry.')
            conn.execute(webhook_inbox.update().where(webhook_inbox.c.id==jid).values(
                status='pending',next_attempt_at=now(),locked_until=None,attempts=0,last_error=None))
            log(conn,m['id'],'webhook.retry_requested',jid,data.reason)
        return {'queued':True}

    def make_previews(conn,merchant):
        current_day=today()
        made=0
        # Preview only. A production sender must recheck balance/opt-out just before dispatch.
        for d in all_debts(conn,merchant):
            days=(current_day-date.fromisoformat(d['due_date'])).days
            eligible=days in (-1,0) or (days>0 and days%3==0)
            if not eligible or d['balance_minor']==0 or d['paused'] or d['disputed'] or d['do_not_contact']:continue
            day=current_day.isoformat()
            existing=conn.execute(select(outbox).where(outbox.c.debt_id==d['id'],outbox.c.day_key==day)).mappings().first()
            if existing and existing['status']!='stale':continue
            business=conn.scalar(select(merchants.c.name).where(merchants.c.id==merchant))
            body=f"Hello {d['name']}, a friendly reminder from {business}: GHS {d['balance_minor']/100:,.2f} is due on {d['due_date']}. Pay all or part: {settings['base_url']}/pay/{d['public_token']}. Incorrect record? Contact {business}."
            if existing:conn.execute(outbox.update().where(outbox.c.id==existing['id']).values(body=body,status='preview',created_at=now()))
            else:conn.execute(outbox.insert().values(id=uid(),merchant_id=merchant,debt_id=d['id'],day_key=day,body=body,status='preview',created_at=now()))
            made+=1
        return made
    app.state.make_previews=make_previews

    @app.post('/api/reminders/preview')
    def previews(m=Depends(current)):
        with write(engine) as conn:count=make_previews(conn,m['id'])
        return {'created':count,'mode':'dry_run','sent':0}

    @app.get('/api/reminders')
    def reminders(m=Depends(current)):
        with engine.connect() as conn:
            rows=[]
            for r in conn.execute(select(outbox).where(outbox.c.merchant_id==m['id']).order_by(outbox.c.created_at.desc())).mappings():
                item=dict(r)
                item['attempts']=[dict(a) for a in conn.execute(select(sms_attempts).where(
                    sms_attempts.c.outbox_id==r['id']).order_by(sms_attempts.c.started_at.desc())).mappings()]
                rows.append(item)
            return rows

    @app.post('/api/reminders/{rid}/queue')
    def queue_reminder(rid:str,m=Depends(current)):
        with write(engine) as conn:
            row=conn.execute(select(outbox).where(outbox.c.id==rid,
                outbox.c.merchant_id==m['id']).with_for_update()).mappings().first()
            if not row:raise HTTPException(404,'Reminder not found.')
            if row['status']=='queued':return {'queued':True,'duplicate':True}
            if row['status']!='preview':raise HTTPException(409,'Only a fresh preview can be queued. Unknown outcomes cannot be automatically resent.')
            d=debt_view(conn,owned_debt(conn,m['id'],row['debt_id'],True))
            from .jobs import eligible
            if row['day_key']!=today().isoformat() or not eligible(d):
                raise HTTPException(409,'This reminder is no longer eligible. Refresh your previews.')
            if settings['sms_mode']=='arkesel_sandbox' and not settings['arkesel_key']:
                raise HTTPException(503,'Configure ARKESEL_API_KEY before queuing sandbox messages.')
            conn.execute(outbox.update().where(outbox.c.id==rid).values(status='queued'))
            log(conn,m['id'],'sms.queued',rid,settings['sms_mode'])
        return {'queued':True,'mode':settings['sms_mode'],'real_messages_sent':0}

    @app.get('/api/audit')
    def audit_list(m=Depends(current)):
        with engine.connect() as conn:return [dict(x) for x in conn.execute(select(audit).where(audit.c.merchant_id==m['id']).order_by(audit.c.created_at.desc()).limit(200)).mappings()]

    @app.get('/api/export/debts.csv')
    def export(m=Depends(current)):
        with engine.connect() as conn:rows=all_debts(conn,m['id'])
        stream=io.StringIO();writer=csv.writer(stream)
        writer.writerow(['Customer','Phone','Description','Due date','Original GHS','Paid GHS','Outstanding GHS','Status'])
        def safe(v):
            v=str(v)
            return "'"+v if v.startswith(('=','+','-','@','\t','\r','\n')) else v
        for d in rows:writer.writerow([safe(d['name']),safe(d['phone']),safe(d['description']),d['due_date'],
            f"{d['principal_minor']/100:.2f}",f"{d['paid_minor']/100:.2f}",f"{d['balance_minor']/100:.2f}",d['status']])
        return Response(stream.getvalue(),media_type='text/csv',headers={'Content-Disposition':'attachment; filename=debt-book.csv'})

    def owned_conversation(conn,merchant,cid,lock=False):
        q=select(conversations).where(conversations.c.id==cid,
            conversations.c.merchant_id==merchant,conversations.c.expires_at>now())
        if lock:q=q.with_for_update()
        row=conn.execute(q).mappings().first()
        if not row:raise HTTPException(404,'Conversation not found or expired. Start a new conversation.')
        return row

    def new_conversation(conn,merchant):
        cid=uid()
        conn.execute(conversations.insert().values(id=cid,merchant_id=merchant,
            created_at=now(),expires_at=(datetime.now(timezone.utc)+timedelta(hours=24)).isoformat()))
        return cid

    @app.post('/api/agent/conversations')
    def start_conversation(m=Depends(current)):
        with write(engine) as conn:cid=new_conversation(conn,m['id'])
        return {'id':cid}

    @app.get('/api/agent/conversations/{cid}')
    def conversation_history(cid:str,m=Depends(current)):
        with engine.connect() as conn:
            c=owned_conversation(conn,m['id'],cid)
            history=[{'id':r['id'],'message':r['user_message'],'result':json.loads(r['result'])}
                for r in conn.execute(select(turns).where(turns.c.conversation_id==cid)
                    .order_by(turns.c.created_at)).mappings()]
        return {'id':cid,'expires_at':c['expires_at'],'turns':history}

    @app.delete('/api/agent/conversations/{cid}')
    def delete_conversation(cid:str,m=Depends(current)):
        with write(engine) as conn:
            row=owned_conversation(conn,m['id'],cid,True)
            if row['busy_until'] and row['busy_until']>now():
                raise HTTPException(409,'Wait for the in-flight assistant request before deleting this conversation.')
            # Cancel unconfirmed proposals associated with this conversation.
            for result in conn.scalars(select(turns.c.result).where(turns.c.conversation_id==cid)):
                proposal=json.loads(result).get('proposal')
                if proposal:
                    conn.execute(proposals.update().where(proposals.c.id==proposal['id'],
                        proposals.c.status=='pending').values(status='cancelled',payload='{}'))
            conn.execute(turns.delete().where(turns.c.conversation_id==cid))
            conn.execute(conversations.delete().where(conversations.c.id==cid))
        return {'deleted':True}

    @app.post('/api/agent')
    async def agent_run(data:AgentIn,m=Depends(current)):
        if not data.consent:raise HTTPException(422,'Confirm that this request and scoped debt context may be sent to Gemini.')
        claim=uid()
        with write(engine) as conn:
            cid=data.conversation_id or new_conversation(conn,m['id'])
            conversation=owned_conversation(conn,m['id'],cid,True)
            previous=conn.execute(select(turns).where(turns.c.conversation_id==cid,
                turns.c.request_id==data.request_id)).mappings().first()
            if previous:
                if previous['user_message']!=data.message:raise HTTPException(409,'Request ID was used for a different message.')
                return json.loads(previous['result'])
            if conversation['busy_until'] and conversation['busy_until']>now():
                raise HTTPException(409,'An assistant request is already running for this conversation.')
            count=conn.scalar(select(func.count()).select_from(turns).where(turns.c.conversation_id==cid))
            if count>=20:raise HTTPException(422,'This conversation has reached 20 turns. Start a new one.')
            history=[{'user':r['user_message'],'assistant':json.loads(r['result'])}
                for r in reversed(list(conn.execute(select(turns).where(turns.c.conversation_id==cid)
                    .order_by(turns.c.created_at.desc()).limit(6)).mappings()))]
            conn.execute(conversations.update().where(conversations.c.id==cid).values(
                busy_token=claim,busy_until=(datetime.now(timezone.utc)+timedelta(seconds=60)).isoformat()))
            # Do not send payment tokens or saved phone numbers to the model.
            context=[{k:d[k] for k in ('id','name','balance_minor','due_date','status')}
                     for d in all_debts(conn,m['id'])][:100]
        try:
            plan=await agent.gemini_plan(settings,data.message,context,history)
            with write(engine) as conn:
                conversation=owned_conversation(conn,m['id'],cid,True)
                if conversation['busy_token']!=claim:raise HTTPException(409,'Conversation changed while this request was running. Retry.')
                # Rebuild context from current balances, not an earlier model snapshot.
                context=[{k:d[k] for k in ('id','name','balance_minor','due_date','status')}
                         for d in all_debts(conn,m['id'])]
                result=agent.execute_plan(conn,m['id'],plan,context)
                result['conversation_id']=cid
                conn.execute(turns.insert().values(id=uid(),conversation_id=cid,merchant_id=m['id'],
                    request_id=data.request_id,user_message=data.message,result=json.dumps(result),created_at=now()))
                conn.execute(conversations.update().where(conversations.c.id==cid).values(busy_token=None,busy_until=None))
                log(conn,m['id'],'agent.tool',uid(),plan.action)
                return result
        finally:
            with write(engine) as conn:
                conn.execute(conversations.update().where(conversations.c.id==cid,
                    conversations.c.busy_token==claim).values(busy_token=None,busy_until=None))

    @app.post('/api/agent/proposals/{pid}/confirm')
    def confirm_proposal(pid:str,m=Depends(current)):
        with write(engine) as conn:
            p=conn.execute(select(proposals).where(proposals.c.id==pid,proposals.c.merchant_id==m['id']).with_for_update()).mappings().first()
            if not p:raise HTTPException(404,'Proposal not found.')
            if p['status']!='pending' or p['expires_at']<now():raise HTTPException(409,'Proposal was already used or has expired.')
            data=DebtIn.model_validate_json(p['payload'])
            did=create_debt(conn,m['id'],data.model_dump())
            conn.execute(proposals.update().where(proposals.c.id==pid).values(status='confirmed'))
            log(conn,m['id'],'agent.proposal_confirmed',pid,did)
            return debt_view(conn,owned_debt(conn,m['id'],did))

    @app.post('/api/agent/proposals/{pid}/cancel')
    def cancel_proposal(pid:str,m=Depends(current)):
        with write(engine) as conn:
            row=conn.execute(select(proposals).where(proposals.c.id==pid,
                proposals.c.merchant_id==m['id']).with_for_update()).mappings().first()
            if not row:raise HTTPException(404,'Proposal not found.')
            if row['status']=='confirmed':raise HTTPException(409,'This proposal was already confirmed.')
            conn.execute(proposals.update().where(proposals.c.id==pid).values(status='cancelled',payload='{}'))
            log(conn,m['id'],'agent.proposal_cancelled',pid)
        return {'cancelled':True}

    @app.get('/api/susu')
    def susu_list(m=Depends(current)):
        with engine.connect() as conn:
            result=[]
            for r in conn.execute(select(susu).where(susu.c.merchant_id==m['id'])).mappings():
                item=dict(r)
                item['name']=conn.scalar(select(customers.c.name).where(customers.c.id==r['customer_id']))
                item['balance_minor']=conn.scalar(select(func.coalesce(func.sum(contributions.c.amount_minor),0)).where(contributions.c.account_id==r['id']))
                item['entries']=[dict(x) for x in conn.execute(select(contributions).where(contributions.c.account_id==r['id']).order_by(contributions.c.created_at.desc())).mappings()]
                result.append(item)
            return result

    @app.post('/api/susu',status_code=201)
    def new_susu(data:SusuIn,m=Depends(current)):
        target=money(data.daily_target)
        with write(engine) as conn:
            cid=customer_get_or_create(conn,m['id'],data.name,data.phone)
            sid=uid()
            conn.execute(susu.insert().values(id=sid,merchant_id=m['id'],customer_id=cid,label=data.label,
                daily_target_minor=target,created_at=now()))
            log(conn,m['id'],'susu.account_created',sid)
        return {'id':sid}

    @app.post('/api/susu/{sid}/entries')
    def new_contribution(sid:str,data:SusuEntry,m=Depends(current)):
        amt=money(data.amount)*(1 if data.kind=='contribution' else -1)
        with write(engine) as conn:
            account=conn.execute(select(susu).where(susu.c.id==sid,susu.c.merchant_id==m['id']).with_for_update()).mappings().first()
            if not account:raise HTTPException(404,'Savings account not found.')
            prior=conn.execute(select(contributions).where(contributions.c.merchant_id==m['id'],contributions.c.reference==data.reference)).mappings().first()
            if prior:
                if prior['account_id']!=sid or prior['amount_minor']!=amt:raise HTTPException(409,'Reference already used for another entry.')
                return {'duplicate':True}
            b=conn.scalar(select(func.coalesce(func.sum(contributions.c.amount_minor),0)).where(contributions.c.account_id==sid))
            if b+amt<0:raise HTTPException(422,'Payout exceeds recorded savings.')
            conn.execute(contributions.insert().values(id=uid(),merchant_id=m['id'],account_id=sid,
                amount_minor=amt,kind=data.kind,reference=data.reference,note=data.note,created_at=now()))
            log(conn,m['id'],'susu.'+data.kind,sid,str(amt))
            return {'balance_minor':b+amt,'note':'Manual record only; no funds transferred.'}

    @app.post('/api/demo/seed')
    def seed(m=Depends(current)):
        if not settings['demo']:raise HTTPException(403,'Demo data is disabled.')
        with write(engine) as conn:
            if conn.scalar(select(func.count()).select_from(debts).where(debts.c.merchant_id==m['id'])):
                raise HTTPException(409,'Sample data can only be loaded into an empty debt book.')
            for name,number,amt,paid,offset,desc in [
                ('Abena Mensah','0240000001','500','200',-1,'Fabric purchased on credit'),
                ('Kofi Asante','0240000002','1200','0',1,'Four bags of rice'),
                ('Ama Owusu','0240000003','300','0',4,'Household supplies'),
                ('Kwame Boateng','0240000004','850','850',-4,'Wholesale groceries')]:
                create_debt(conn,m['id'],{'name':name,'phone':number,'amount':amt,'initial_paid':paid,
                    'due_date':(today()+timedelta(days=offset)).isoformat(),'description':desc})
        return {'ok':True,'note':'Fictional demonstration data. No messages or payments were sent.'}

    app.mount('/static',StaticFiles(directory=STATIC),name='static')
    @app.get('/')
    def index():return FileResponse(STATIC/'index.html')
    @app.get('/pay/{token}')
    def payment_page(token:str):return FileResponse(STATIC/'index.html')
    return app

app=create_app()
