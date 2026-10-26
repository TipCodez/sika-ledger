import pytest
from fastapi.testclient import TestClient
from app.main import create_app

@pytest.fixture
def app(tmp_path):
    application=create_app('sqlite:///'+str(tmp_path/'test.db'),{
        'demo':True,'gemini_key':'','paystack_key':'','paystack_email':'',
        'base_url':'http://testserver','worker_enabled':False,'sms_mode':'dry_run',
        'arkesel_key':'','automatic_reminders':False})
    yield application
    application.state.engine.dispose()

@pytest.fixture
def client(app):
    with TestClient(app) as client:
        r=client.post('/api/auth/register',json={'name':'Raphael Test Store',
            'login':'raphael','password':'Prototype-Test-2026'})
        assert r.status_code==200
        yield client

@pytest.fixture
def debt(client):
    r=client.post('/api/debts',json={'name':'Abena Mensah','phone':'0241234567',
        'amount':'500.00','initial_paid':'0','due_date':'2026-11-07','description':'Fabric'})
    assert r.status_code==201,r.text
    return r.json()
