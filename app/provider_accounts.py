"""Owner-only Paystack settlement-account setup.

The platform stores only provider identifiers and masked settlement details. Full
account numbers are sent directly to Paystack and are never written locally.
"""
from typing import Literal
import httpx
from fastapi import Depends,HTTPException
from pydantic import BaseModel,ConfigDict,Field
from sqlalchemy import select
from .db import write,provider_accounts
from .domain import now,log

class SubaccountIn(BaseModel):
    model_config=ConfigDict(extra='forbid')
    business_name:str=Field(min_length=2,max_length=100)
    settlement_bank:str=Field(min_length=2,max_length=30,pattern=r'^[A-Za-z0-9_-]+$')
    account_number:str=Field(min_length=6,max_length=30,pattern=r'^[0-9]+$')
    contact_name:str|None=Field(default=None,max_length=100)
    contact_email:str|None=Field(default=None,max_length=254,pattern=r'^[^@\s]+@[^@\s]+\.[^@\s]+$')
    contact_phone:str|None=Field(default=None,max_length=30)
    confirmed:Literal[True]

def owner(m):
    if m.get('role')!='owner':raise HTTPException(403,'Only an owner can manage settlement details.')

def expected_domain(settings):
    return 'live' if settings['paystack_key'].startswith('sk_live_') else 'test'

def public_row(row):
    if not row:return None
    return {k:row[k] for k in ('provider','subaccount_code','domain','status','business_name',
        'settlement_bank','account_name','account_last4','created_at','updated_at')}

def register_provider_accounts(app,engine,current):
    @app.get('/api/integrations/paystack/subaccount')
    def get_subaccount(m=Depends(current)):
        owner(m)
        with engine.connect() as conn:
            row=conn.execute(select(provider_accounts).where(
                provider_accounts.c.merchant_id==m['id'])).mappings().first()
        return {'account':public_row(row)}

    @app.post('/api/integrations/paystack/subaccount')
    async def create_subaccount(data:SubaccountIn,m=Depends(current)):
        owner(m);settings=app.state.settings
        if not settings['paystack_key']:raise HTTPException(503,'Configure the Paystack secret key first.')
        with engine.connect() as conn:
            if conn.scalar(select(provider_accounts.c.merchant_id).where(
                provider_accounts.c.merchant_id==m['id'])):
                raise HTTPException(409,'A Paystack subaccount is already linked to this business.')
        payload={'business_name':data.business_name.strip(),'settlement_bank':data.settlement_bank,
            'account_number':data.account_number,'percentage_charge':0,
            'description':'Sika Ledger direct merchant settlement',
            'primary_contact_name':data.contact_name,'primary_contact_email':data.contact_email,
            'primary_contact_phone':data.contact_phone}
        payload={k:v for k,v in payload.items() if v not in (None,'')}
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                response=await client.post('https://api.paystack.co/subaccount',
                    headers={'Authorization':'Bearer '+settings['paystack_key']},json=payload)
            response.raise_for_status();body=response.json();result=body['data']
            if not body.get('status'):raise ValueError()
        except (httpx.HTTPError,ValueError,KeyError,TypeError):
            raise HTTPException(502,'Paystack could not create the settlement subaccount. No local account details were saved.')
        domain=expected_domain(settings)
        code=str(result.get('subaccount_code',''))
        currency=str(result.get('currency',''))
        if (not code.startswith('ACCT_') or result.get('domain')!=domain or currency!='GHS'
            or not result.get('account_name')):
            raise HTTPException(409,'Paystack returned settlement details that do not match the expected GHS environment.')
        status='active' if result.get('active') and result.get('is_verified') else 'pending_review'
        stamp=now()
        with write(engine) as conn:
            conn.execute(provider_accounts.insert().values(merchant_id=m['id'],provider='paystack',
                subaccount_code=code,provider_account_id=str(result.get('id','')),domain=domain,
                status=status,business_name=str(result.get('business_name') or data.business_name)[:100],
                settlement_bank=str(result.get('settlement_bank') or data.settlement_bank)[:100],
                account_name=str(result['account_name'])[:100],account_last4=data.account_number[-4:],
                created_at=stamp,updated_at=stamp))
            log(conn,m['id'],'paystack.subaccount_created',code,status)
        return {'account':public_row({**result,'provider':'paystack','subaccount_code':code,
            'domain':domain,'status':status,'business_name':str(result.get('business_name') or data.business_name),
            'settlement_bank':str(result.get('settlement_bank') or data.settlement_bank),
            'account_name':str(result['account_name']),'account_last4':data.account_number[-4:],
            'created_at':stamp,'updated_at':stamp}),
            'note':'Full account details were sent to Paystack and were not stored by Sika Ledger.'}

    @app.post('/api/integrations/paystack/subaccount/refresh')
    async def refresh_subaccount(m=Depends(current)):
        owner(m);settings=app.state.settings
        with engine.connect() as conn:
            row=conn.execute(select(provider_accounts).where(
                provider_accounts.c.merchant_id==m['id'])).mappings().first()
        if not row:raise HTTPException(404,'No Paystack subaccount is linked.')
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                response=await client.get('https://api.paystack.co/subaccount/'+row['subaccount_code'],
                    headers={'Authorization':'Bearer '+settings['paystack_key']})
            response.raise_for_status();body=response.json();result=body['data']
            if not body.get('status'):raise ValueError()
        except (httpx.HTTPError,ValueError,KeyError,TypeError):
            raise HTTPException(502,'Paystack subaccount status could not be refreshed.')
        if result.get('domain')!=row['domain'] or str(result.get('subaccount_code'))!=row['subaccount_code']:
            raise HTTPException(409,'Provider subaccount identity does not match the linked account.')
        status='active' if result.get('active') and result.get('is_verified') else ('disabled' if not result.get('active') else 'pending_review')
        with write(engine) as conn:
            conn.execute(provider_accounts.update().where(provider_accounts.c.merchant_id==m['id'])
                .values(status=status,updated_at=now()))
            log(conn,m['id'],'paystack.subaccount_refreshed',row['subaccount_code'],status)
        return {'status':status}

def active_subaccount(conn,merchant_id,domain):
    row=conn.execute(select(provider_accounts).where(provider_accounts.c.merchant_id==merchant_id,
        provider_accounts.c.provider=='paystack',provider_accounts.c.domain==domain,
        provider_accounts.c.status=='active')).mappings().first()
    if not row:raise HTTPException(409,'This business does not have an active Paystack settlement subaccount.')
    return row
