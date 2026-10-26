import json
import sys
import tempfile
from pathlib import Path
from fastapi.testclient import TestClient
from app.main import create_app

with tempfile.TemporaryDirectory() as tmp:
    app=create_app('sqlite:///'+str(Path(tmp)/'ui.db'),{'demo':True,
        'base_url':'http://testserver','gemini_key':'DOM_TEST_KEY','paystack_key':'','worker_enabled':False,'reminder_start':0,'reminder_end':24})
    from app import agent
    async def fake_plan(settings,message,context,history):
        base=dict(reply='Please provide the phone number and due date.',customer_name='',phone='',amount='',initial_paid='',due_date='',description='',debt_id='')
        if '0246661111' in message:
            assert history and 'Yaw' in history[-1]['user']
            base.update(action='propose_debt',customer_name='Yaw Demo',phone='0246661111',amount='600',initial_paid='200',due_date='2026-11-07',description='Rice')
        else:base['action']='reply'
        return agent.Plan(**base)
    agent.gemini_plan=fake_plan
    with TestClient(app) as client:
        for line in sys.stdin:
            try:
                item=json.loads(line)
                r=client.request(item['method'],item['path'],
                    content=item.get('body'),headers={'Content-Type':'application/json'})
                print(json.dumps({'id':item['id'],'status':r.status_code,'text':r.text}),flush=True)
            except Exception as exc:
                print(json.dumps({'id':item.get('id'),'status':500,'text':str(exc)}),flush=True)
