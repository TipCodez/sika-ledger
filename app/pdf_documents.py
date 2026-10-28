"""Portable, branded account PDFs. All amounts come from recorded ledger data."""
from html import escape
from io import BytesIO
from pathlib import Path
from fastapi import Depends,HTTPException,Response
from sqlalchemy import select
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import SimpleDocTemplate,Paragraph,Spacer,LongTable,TableStyle
from .db import merchants,customers,debts,entries,payments,branding,susu,susu_payments,contributions
from .domain import now,debt_view,owned_debt
from .workspace import own_customer
from .savings import public_account,savings_balance

FONTS=Path(__file__).parent/'assets'
pdfmetrics.registerFont(TTFont('Sika',str(FONTS/'DejaVuSans.ttf')))
pdfmetrics.registerFont(TTFont('Sika-Bold',str(FONTS/'DejaVuSans-Bold.ttf')))
pdfmetrics.registerFontFamily('Sika',normal='Sika',bold='Sika-Bold',italic='Sika',boldItalic='Sika-Bold')

def amount(value):return f'GHS {value/100:,.2f}'

def business(conn,mid):
    row=conn.execute(select(branding).where(branding.c.merchant_id==mid)).mappings().first()
    return dict(row) if row else {'display_name':conn.scalar(select(merchants.c.name).where(merchants.c.id==mid)),
        'address':'','contact':'','footer':'Thank you for keeping clear records.','accent':'#087f5b'}

def document(brand,title,subtitle,sections):
    stream=BytesIO();accent=colors.HexColor(brand['accent'])
    styles={
        'body':ParagraphStyle('body',fontName='Sika',fontSize=9,leading=14,spaceAfter=6,wordWrap='CJK'),
        'small':ParagraphStyle('small',fontName='Sika',fontSize=7.5,leading=11,textColor=colors.HexColor('#58636c'),wordWrap='CJK'),
        'title':ParagraphStyle('title',fontName='Sika-Bold',fontSize=20,leading=26,spaceAfter=10,textColor=accent,wordWrap='CJK'),
        'heading':ParagraphStyle('heading',fontName='Sika-Bold',fontSize=11,leading=16,spaceBefore=12,spaceAfter=6,wordWrap='CJK')}
    def paragraph(text,style='body'):return Paragraph(escape(str(text)).replace('\n','<br/>'),styles[style])
    story=[paragraph(brand['display_name'],'title')]
    if brand['address']:story.append(paragraph(brand['address'],'small'))
    if brand['contact']:story.append(paragraph(brand['contact'],'small'))
    story.extend([Spacer(1,16),paragraph(title,'heading'),paragraph(subtitle),paragraph('Generated: '+now(),'small')])
    for heading,rows in sections:
        story.append(paragraph(heading,'heading'))
        if isinstance(rows,str):story.append(paragraph(rows));continue
        widths=[105,380] if len(rows[0])==2 else [92,83,74,236]
        data=[[paragraph(v,'small') for v in row] for row in rows]
        table=LongTable(data,colWidths=widths,repeatRows=1,hAlign='LEFT')
        table.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,0),colors.HexColor('#edf5f1')),
            ('VALIGN',(0,0),(-1,-1),'TOP'),('LEFTPADDING',(0,0),(-1,-1),8),
            ('RIGHTPADDING',(0,0),(-1,-1),8),('TOPPADDING',(0,0),(-1,-1),7),
            ('BOTTOMPADDING',(0,0),(-1,-1),7),('LINEBELOW',(0,0),(-1,-1),0.35,colors.HexColor('#dde5e0'))]))
        story.extend([table,Spacer(1,8)])
    story.extend([Spacer(1,16),paragraph(brand['footer']),paragraph(
        'Prototype account record, not a tax invoice or proof of bank settlement. Manual records are trader-reported; simulations and provider TEST transactions move no real funds. A ledger reversal does not refund money.','small')])
    def footer(canvas,doc):
        canvas.saveState();canvas.setFont('Sika',7);canvas.setFillColor(colors.HexColor('#58636c'))
        canvas.drawString(55,29,'Sika Ledger | Account records')
        canvas.drawRightString(A4[0]-55,29,f'Page {doc.page}');canvas.restoreState()
    SimpleDocTemplate(stream,pagesize=A4,leftMargin=55,rightMargin=55,topMargin=45,bottomMargin=55,
        title=title,author=brand['display_name']).build(story,onFirstPage=footer,onLaterPages=footer)
    return stream.getvalue()

def repayment_pdf(conn,p):
    d=debt_view(conn,owned_debt(conn,p['merchant_id'],p['debt_id']))
    source='PROVIDER-VERIFIED TEST - NO REAL MONEY MOVED' if p['source']=='paystack_test' else (
        'SIMULATION - NO REAL MONEY MOVED' if p['source']=='simulation' else 'TRADER-REPORTED - NOT PROVIDER VERIFIED')
    rows=[['Record','Value'],['Customer',d['name']],['Payment ID',p['id']],['Recorded',p['confirmed_at'] or p['created_at']],
        ['Source',p['source']],['Status',p['status']],['Original sale',amount(d['principal_minor'])],
        ['Payment',amount(p['amount_minor'])],['Originally allocated',amount(p['applied_minor'])],
        ['Unallocated credit',amount(p['unallocated_minor'])],['Outstanding now',amount(d['balance_minor'])]]
    if p['status']=='reversed':rows.append(['Reversal','This payment no longer reduces the debt.'])
    return document(business(conn,p['merchant_id']),'Repayment acknowledgement',source,[('Payment details',rows)])

def contribution_pdf(conn,p):
    a=conn.execute(select(susu).where(susu.c.id==p['account_id'])).mappings().one()
    name=conn.scalar(select(customers.c.name).where(customers.c.id==a['customer_id']))
    source='SIMULATION - NO REAL MONEY MOVED' if p['source']=='simulation' else 'PROVIDER-VERIFIED TEST - NO REAL MONEY MOVED'
    return document(business(conn,p['merchant_id']),'Susu contribution acknowledgement',source,[('Contribution details',[
        ['Record','Value'],['Customer',name],['Savings record',a['label']],['Payment ID',p['id']],['Source',p['source']],
        ['Recorded',p['confirmed_at']],['Contribution',amount(p['amount_minor'])],['Savings balance now',amount(savings_balance(conn,a['id']))],
        ['Trade debt','This contribution does not repay trade debt.']])])

def statement_pdf(conn,mid,cid):
    c=own_customer(conn,mid,cid);sections=[];total=0
    for raw in conn.execute(select(debts).where(debts.c.customer_id==cid,debts.c.merchant_id==mid).order_by(debts.c.created_at)).mappings():
        d=debt_view(conn,raw);total+=d['balance_minor']
        sections.append((d['description'],[['Record','Value'],['Status',d['status']],['Original sale',amount(d['principal_minor'])],
            ['Adjusted credit',amount(d['adjusted_total_minor'])],['Outstanding now',amount(d['balance_minor'])],['Original due date',d['due_date']]]))
        rows=[['Date','Entry','Amount','Details']]
        rows.extend([e['created_at'][:10],e['kind'],amount(e['amount_minor']),e['note']] for e in conn.execute(
            select(entries).where(entries.c.debt_id==d['id']).order_by(entries.c.created_at)).mappings())
        sections.append(('Debt ledger',rows))
        if d['plan']:
            plan=d['plan'];rows=[['Due date','State','Remaining','Agreed amount']]
            rows.extend([i['due_date'],i['status'],amount(i['remaining_minor']) if plan['progress_available'] else 'See current ledger',amount(i['amount_minor'])] for i in plan['instalments'])
            sections.append(('Repayment plan: '+plan['status'],rows))
    sections.append(('Total trade debt outstanding',amount(total)))
    for a in conn.execute(select(susu).where(susu.c.customer_id==cid,susu.c.merchant_id==mid)).mappings():
        rows=[['Date','Entry','Amount','Details']]
        rows.extend([e['created_at'][:10],e['kind'],amount(e['amount_minor']),e['note']] for e in conn.execute(
            select(contributions).where(contributions.c.account_id==a['id']).order_by(contributions.c.created_at)).mappings())
        sections.extend([(a['label']+' - separate savings',f"Savings balance: {amount(savings_balance(conn,a['id']))}"),('Savings ledger',rows)])
    return document(business(conn,mid),'Customer account statement',c['name'],sections)

def pdf_response(content,name):
    return Response(content,media_type='application/pdf',headers={'Content-Disposition':f'attachment; filename="{name}.pdf"'})

def register_documents(app,engine,current):
    @app.get('/api/payments/{pid}/receipt.pdf')
    def receipt(pid:str,m=Depends(current)):
        with engine.connect() as conn:
            p=conn.execute(select(payments).where(payments.c.id==pid,payments.c.merchant_id==m['id'],payments.c.status.in_(['confirmed','reversed','partially_refunded','refunded']))).mappings().first()
            if not p:raise HTTPException(404,'Receipt not found.')
            return pdf_response(repayment_pdf(conn,p),'repayment-'+pid)
    @app.get('/api/public/{token}/receipts/{pid}/pdf')
    def public_receipt(token:str,pid:str):
        with engine.connect() as conn:
            did=conn.scalar(select(debts.c.id).where(debts.c.public_token==token))
            p=conn.execute(select(payments).where(payments.c.debt_id==did,payments.c.id==pid,payments.c.status.in_(['confirmed','reversed','partially_refunded','refunded']))).mappings().first() if did else None
            if not p:raise HTTPException(404,'Receipt not found.')
            return pdf_response(repayment_pdf(conn,p),'repayment-'+pid)
    @app.get('/api/reports/customers/{cid}/statement.pdf')
    def statement(cid:str,m=Depends(current)):
        with engine.connect() as conn:return pdf_response(statement_pdf(conn,m['id'],cid),'customer-statement-'+cid)
    @app.get('/api/susu-payments/{pid}/receipt.pdf')
    def savings_receipt(pid:str,m=Depends(current)):
        with engine.connect() as conn:
            p=conn.execute(select(susu_payments).where(susu_payments.c.id==pid,susu_payments.c.merchant_id==m['id'],susu_payments.c.status=='confirmed')).mappings().first()
            if not p:raise HTTPException(404,'Receipt not found.')
            return pdf_response(contribution_pdf(conn,p),'contribution-'+pid)
    @app.get('/api/susu-public/{token}/receipts/{pid}.pdf')
    def public_savings_receipt(token:str,pid:str):
        with engine.connect() as conn:
            a=public_account(conn,token)
            p=conn.execute(select(susu_payments).where(susu_payments.c.id==pid,susu_payments.c.account_id==a['id'],susu_payments.c.status=='confirmed')).mappings().first()
            if not p:raise HTTPException(404,'Receipt not found.')
            return pdf_response(contribution_pdf(conn,p),'contribution-'+pid)
