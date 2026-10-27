import sqlite3
from datetime import datetime, timedelta, timezone

from sqlalchemy import inspect

from app.db import debts, link_expired, make_engine, susu_links, write


def past():
    return (datetime.now(timezone.utc)-timedelta(seconds=1)).isoformat()


def test_debt_private_link_expires_and_rotation_renews_it(client, debt):
    old_token=debt['public_token']
    with write(client.app.state.engine) as conn:
        conn.execute(debts.update().where(debts.c.id==debt['id'])
            .values(public_token_expires_at=past()))

    expired=client.get('/api/public/'+old_token)
    assert expired.status_code==410
    assert 'expired' in expired.json()['detail'].lower()
    assert client.post('/api/public/'+old_token+'/checkout',json={
        'amount':'10','mode':'simulation','idempotency_key':'expired-debt-01'}).status_code==410

    renewed=client.post('/api/debts/'+debt['id']+'/rotate-link')
    assert renewed.status_code==200
    data=renewed.json()
    assert data['public_token']!=old_token
    assert not link_expired(data['link_expires_at'])
    assert client.get('/api/public/'+old_token).status_code==404
    assert client.get('/api/public/'+data['public_token']).status_code==200


def test_susu_private_link_expires_and_owner_can_renew_it(client):
    created=client.post('/api/susu',json={'name':'Kofi Saver','phone':'0247654321',
        'label':'Daily savings','daily_target':'20'}).json()
    old_token=created['public_token']
    with write(client.app.state.engine) as conn:
        conn.execute(susu_links.update().where(susu_links.c.account_id==created['id'])
            .values(expires_at=past()))

    assert client.get('/api/susu-public/'+old_token).status_code==410
    renewed=client.post('/api/susu/'+created['id']+'/rotate-link')
    assert renewed.status_code==200
    data=renewed.json()
    assert data['public_token']!=old_token
    assert not link_expired(data['link_expires_at'])
    assert client.get('/api/susu-public/'+old_token).status_code==404
    assert client.get('/api/susu-public/'+data['public_token']).status_code==200


def test_existing_database_receives_expiry_columns(tmp_path):
    path=tmp_path/'legacy.db'
    with sqlite3.connect(path) as conn:
        conn.executescript('''
            CREATE TABLE debts (
                id VARCHAR PRIMARY KEY, merchant_id VARCHAR NOT NULL,
                customer_id VARCHAR NOT NULL, principal_minor INTEGER NOT NULL,
                due_date VARCHAR NOT NULL, description VARCHAR NOT NULL,
                public_token VARCHAR NOT NULL UNIQUE, paused INTEGER NOT NULL,
                disputed INTEGER NOT NULL, created_at VARCHAR NOT NULL
            );
            CREATE TABLE susu_links (
                account_id VARCHAR PRIMARY KEY, public_token VARCHAR NOT NULL UNIQUE,
                created_at VARCHAR NOT NULL
            );
        ''')
    engine=make_engine('sqlite:///'+str(path))
    try:
        schema=inspect(engine)
        assert 'public_token_expires_at' in {c['name'] for c in schema.get_columns('debts')}
        assert 'expires_at' in {c['name'] for c in schema.get_columns('susu_links')}
    finally:
        engine.dispose()
