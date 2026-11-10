"""Negative boundary tests: no real AWS, Gmail, DNS, or SMTP access."""
import asyncio
import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch, MagicMock, AsyncMock

sys.path.insert(0, str(Path(__file__).parents[1]))
temp = tempfile.TemporaryDirectory()
os.environ['DATABASE_URL'] = 'sqlite:///' + str(Path(temp.name) / 'security.db')
os.environ['JWT_SECRET'] = 'security-contract-test-secret-not-production'

from fastapi import HTTPException
from app.database import init_db, get_db
from app.services import llm, email_verifier as verifier
from app.services.delivery_policy import require_delivery_enabled
from app.services.generation_policy import reserve_bedrock_invocation
from botocore.exceptions import ClientError
import dns.resolver


async def tests():
    await init_db()
    for env in ({'APP_ENV': 'beta', 'EMAIL_DELIVERY_ENABLED': 'true'},
                {'APP_ENV': 'production', 'EMAIL_DELIVERY_ENABLED': 'false'}):
        with patch.dict(os.environ, env):
            try:
                require_delivery_enabled()
                raise AssertionError('Delivery escaped beta guard')
            except ValueError:
                pass
    with patch.dict(os.environ, {'LLM_PROVIDER': 'bedrock'}):
        for model in ('us.anthropic.unapproved', 'global.anthropic.unapproved', 'ollama:unapproved'):
            with patch('boto3.client') as client:
                try:
                    llm.complete_text('draft', model)
                    raise AssertionError('Unapproved model accepted')
                except HTTPException as exc:
                    assert exc.status_code == 400
                client.assert_not_called()
        with patch('boto3.client') as factory:
            factory.return_value.converse.return_value = {'output': {'message': {'content': [{'text': 'draft'}]}}}
            assert llm.complete_text('brief', llm.default_model_id()) == 'draft'
            assert factory.return_value.converse.call_args.kwargs['inferenceConfig']['maxTokens'] == 2048
    for invalid in ('a..b@example.org', '.a@example.org', 'a@-example.org', 'a@x..org', 'x\r\nBcc:y@example.org'):
        assert not verifier.verify_email_format(invalid)['valid']
    with patch('dns.resolver.resolve', return_value=[SimpleNamespace(exchange='.', preference=0)]):
        assert await verifier.verify_mx('example.org') == (False, [])
    with patch('dns.resolver.resolve', return_value=[SimpleNamespace(exchange='b.example.org.', preference=20), SimpleNamespace(exchange='a.example.org.', preference=10)]):
        assert await verifier.verify_mx('example.org') == (True, ['a.example.org', 'b.example.org'])
    for side_effect, expected in [(dns.resolver.NXDOMAIN, False),
                                 ([dns.resolver.NoAnswer(), [SimpleNamespace(address='8.8.8.8')]], True),
                                 ([dns.resolver.NoAnswer(), dns.resolver.NoAnswer(), dns.resolver.NoAnswer()], False),
                                 ([dns.resolver.NoAnswer(), TimeoutError()], None)]:
        with patch('dns.resolver.resolve', side_effect=side_effect):
            assert (await verifier.verify_mx('example.org'))[0] is expected
    with patch('dns.resolver.resolve', side_effect=dns.resolver.LifetimeTimeout):
        assert (await verifier.verify_email_deliverability('a@example.org', smtp_probe=False))['status'] == 'unknown'
    with patch.object(verifier, 'INBOX_VERIFY_MODE', 'mx'), patch.object(verifier, '_smtp_rcpt_probe') as smtp:
        result = await verifier.verify_email_deliverability('a@example.org', mx_cache={'example.org': (True, ['mx.example.org'])})
        assert result['mailbox_exists'] is None and result['status'] == 'likely_valid'
        smtp.assert_not_called()
    with patch('socket.getaddrinfo', return_value=[(None, None, None, None, ('127.0.0.1', 25))]), patch('smtplib.SMTP') as smtp:
        assert await verifier._smtp_rcpt_probe('a@example.org', 'mx.example.org', 1) == 'unknown'
        smtp.assert_not_called()
    with patch('socket.getaddrinfo', return_value=[(None, None, None, None, ('8.8.8.8', 25))]), patch('smtplib.SMTP') as smtp:
        transport = smtp.return_value.__enter__.return_value
        for code, response, expected in [(250,b'OK','accepted'),(550,b'5.7.1 Policy denied','unknown'),(550,b'5.1.1 No such user','invalid')]:
            transport.rcpt.return_value = (code,response)
            assert await verifier._smtp_rcpt_probe('a@example.org', 'mx.example.org', 1) == expected
        transport.data.assert_not_called()
        transport.sendmail.assert_not_called()
    for error in (verifier.smtplib.SMTPServerDisconnected(), TimeoutError(), OSError(), RuntimeError()):
        with patch('socket.getaddrinfo', side_effect=error):
            assert await verifier._smtp_rcpt_probe('a@example.org', 'mx.example.org', 1) == 'unknown'
    progress = []
    async def record(done, total): progress.append((done, total))
    cache = {'example.org': (True, ['mx.example.org']), 'invalid.org': (False, []), 'unknown.org': (None, [])}
    with patch.object(verifier, 'INBOX_VERIFY_MODE', 'mx'):
        assert await verifier.verify_emails_parallel([]) == []
        result = await verifier.verify_emails_parallel([('bad', None), ('a@example.org', 'A'), ('a@invalid.org', None), ('a@unknown.org', None)], mx_cache=cache, on_progress=record)
        assert [r['status'] for r in result] == ['invalid', 'likely_valid', 'invalid', 'unknown']
        assert progress == [(4, 4)]
        result = await verifier.verify_email_deliverability('a@invalid.org', mx_cache=cache)
        assert result['status'] == 'invalid'
        assert (await verifier.verify_email_deliverability('bad', mx_cache=cache))['status'] == 'invalid'
    with patch.object(verifier, 'verify_mx', new=AsyncMock(return_value=(True, ['mx.example.org']))) as lookup:
        fresh = {}
        await verifier.preload_mx_for_domains({'example.org'}, fresh)
        assert fresh['example.org'][0]
        await verifier.get_mx_cached('second.org', fresh)
        assert lookup.await_count == 2
    for mode in ('smtp', 'auto'):
        with patch.object(verifier, 'INBOX_VERIFY_MODE', mode), patch.object(verifier, '_smtp_rcpt_probe', new=AsyncMock(return_value='accepted')):
            result = await verifier.verify_emails_parallel([{'email': 'a@example.org'}, ('b@example.org', None)], mx_cache=cache, on_progress=record)
            assert all(r['status'] == 'likely_valid' and r['mailbox_exists'] is None for r in result)
        with patch.object(verifier, 'INBOX_VERIFY_MODE', mode), patch.object(verifier, '_smtp_rcpt_probe', new=AsyncMock(return_value='invalid')):
            result = await verifier.verify_email_deliverability('a@example.org', mx_cache=cache)
            assert result['status'] == 'invalid'
    for raw, expected in [('prefix {"ok":true} suffix', {'ok': True}), ('invalid', None), ('{bad}', None)]:
        with patch.object(llm, 'complete_text', return_value=raw):
            assert llm.complete_json('test') == expected
    with patch.dict(os.environ, {'LLM_PROVIDER': 'bedrock'}):
        with patch.dict(os.environ, {'BEDROCK_ALLOWED_MODEL_IDS': llm.rank_model_id()}):
            assert all(m['id'] == llm.rank_model_id() for m in llm.list_models()['groups'][0]['models'])
    with patch.dict(os.environ, {'LLM_PROVIDER': 'ollama'}), patch('boto3.client') as factory:
        factory.return_value.converse.return_value = {'output': {'message': {'content': [{'text': 'hosted'}]}}}
        assert llm.complete_text('brief') == 'hosted'
        try:
            llm.complete_text('brief', 'ollama:local')
            raise AssertionError('Ollama remaining as an inference outlet')
        except HTTPException as exc:
            assert exc.status_code == 400
        factory.return_value.converse.assert_called()
    with patch.dict(os.environ, {'LLM_PROVIDER': 'bedrock'}), patch('boto3.client') as factory:
        factory.return_value.converse.return_value = {'output': {'message': {'content': [{'text': 'draft'}]}}}
        assert llm.complete_text('brief', llm.default_model_id(), 'system') == 'draft'
        try:
            llm.complete_text('x' * 24001, llm.default_model_id())
            raise AssertionError('Oversized prompt accepted')
        except HTTPException as error:
            assert error.status_code == 413
        with patch.object(llm, '_inference_slots', MagicMock(acquire=MagicMock(return_value=False))):
            try:
                llm.complete_text('brief', llm.default_model_id())
                raise AssertionError('Concurrency guard bypassed')
            except HTTPException as error:
                assert error.status_code == 429
    from app.services.ollama_email_service import generate_email
    for error in (HTTPException(429, 'Quota reached'), RuntimeError('provider unavailable')):
        with patch.object(llm, 'complete_json', side_effect=error):
            try:
                generate_email(None, None, None, None)
                raise AssertionError('Generation failure became a fabricated draft')
            except HTTPException as result:
                assert result.status_code == (429 if isinstance(error, HTTPException) else 502)
    await init_db()
    async def reset_paid():
        db=await get_db()
        await db.execute("DELETE FROM usage_events WHERE event_type='bedrock_reserved'")
        await db.commit()
        await db.close()
    # One call's worst case (2,048 output tokens on Haiku 5.5) fits a budget of
    # 0.0015 USD; a second does not, whichever caller makes it.
    one_call = {'AI_MONTHLY_BUDGET_USD': '0.0015'}
    await reset_paid()
    with patch.dict(os.environ,{'LLM_PROVIDER':'bedrock',**one_call}),patch('boto3.client') as factory:
        factory.return_value.converse.return_value={'output':{'message':{'content':[{'text':'{"rank":1}'}]}}}
        assert llm.complete_json('private ranking prompt',llm.rank_model_id())=={'rank':1}
        assert factory.call_args.kwargs['config'].retries['total_max_attempts']==1
        factory.reset_mock()
        try:
            llm.complete_json('another ranking request',llm.rank_model_id())
            raise AssertionError('Second caller bypassed the monthly budget')
        except HTTPException as exc:
            assert exc.status_code==429
        factory.return_value.converse.assert_not_called()
    await reset_paid()
    with patch.dict(os.environ,one_call):
        attempts=await asyncio.gather(*(asyncio.to_thread(reserve_bedrock_invocation,llm.rank_model_id(),None,'rank',1,2048) for _ in range(8)),return_exceptions=True)
        assert sum(isinstance(item,int) for item in attempts)==1
        assert all(isinstance(item,int) or isinstance(item,HTTPException) and item.status_code==429 for item in attempts)
    # Spend is dollars: many cheap calls fit where one expensive call does not.
    await reset_paid()
    with patch.dict(os.environ,{'AI_MONTHLY_BUDGET_USD':'0.05'}):
        cheap=[reserve_bedrock_invocation(llm.rank_model_id(),None,'rank',1000,1000) for _ in range(20)]
        assert all(isinstance(item,int) for item in cheap),'cheap calls exhausted the budget'
        try:
            reserve_bedrock_invocation(llm.TIERS['deep'][0],None,'draft',1000,4096)
            raise AssertionError('An Opus call larger than the remaining budget was allowed')
        except HTTPException as exc:
            assert exc.status_code==429
    # An unrecognised model is charged as the most expensive one, never as free.
    assert llm.cost_usd('some-unpriced-model',1000,1000) >= llm.cost_usd(llm.TIERS['deep'][-1],1000,1000)
    # Last month's spend does not count against this month.
    await reset_paid()
    db=await get_db()
    await db.execute("""INSERT INTO usage_events(event_type,resource_type,details_json,created_at)
        VALUES('bedrock_reserved','rank','{"cost_usd": 999}',datetime('now','start of month','-1 day'))""")
    await db.commit()
    await db.close()
    with patch.dict(os.environ,one_call):
        assert isinstance(reserve_bedrock_invocation(llm.rank_model_id(),None,'rank',1,2048),int)
    # Independent interpreters contend against the same durable SQLite record.
    await reset_paid()
    worker="""
from fastapi import HTTPException
from app.services.generation_policy import reserve_bedrock_invocation
try:
 reserve_bedrock_invocation('us.anthropic.claude-haiku-5-5',None,'rank',1,2048)
 print('reserved')
except HTTPException as exc:
 assert exc.status_code==429
 print('blocked')
"""
    async def contender():
        process=await asyncio.create_subprocess_exec(sys.executable,'-c',worker,
            cwd=str(Path(__file__).parents[1]),stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE)
        output,error=await process.communicate()
        assert process.returncode==0,error.decode()
        return output.decode().strip()
    with patch.dict(os.environ,one_call):
        outcomes=await asyncio.gather(*(contender() for _ in range(4)))
        assert outcomes.count('reserved')==1 and outcomes.count('blocked')==3
    # A finished call is charged what its tokens cost, not the worst case: two
    # worst-case Haiku 5.5 reservations (about 0.0011 USD each) would not fit
    # 0.002 USD together. A call of uncertain outcome keeps its worst case.
    await reset_paid()
    finished={'output':{'message':{'content':[{'text':'done'}]}},'usage':{'inputTokens':10,'outputTokens':20}}
    with patch.dict(os.environ,{'LLM_PROVIDER':'bedrock','AI_MONTHLY_BUDGET_USD':'0.002'}),patch('boto3.client') as factory:
        factory.return_value.converse.return_value=finished
        assert llm.complete_text('private prompt',llm.rank_model_id())=='done'
        assert llm.complete_text('private prompt',llm.rank_model_id())=='done','finished call kept its worst-case charge'
        factory.return_value.converse.side_effect=TimeoutError('Acceptance unknown')
        try:
            llm.complete_text('private prompt',llm.rank_model_id())
            raise AssertionError('Expected provider error')
        except HTTPException as exc:
            assert exc.status_code==503
        factory.return_value.converse.side_effect=None
        factory.reset_mock()
        try:
            llm.complete_text('private prompt',llm.rank_model_id())
            raise AssertionError('Failed provider request was not counted')
        except HTTPException as exc:
            assert exc.status_code==429
        factory.return_value.converse.assert_not_called()
    db=await get_db()
    records=await (await db.execute("SELECT user_id,details_json FROM usage_events WHERE event_type='bedrock_reserved'")).fetchall()
    assert len(records)==3 and all(r['user_id'] is None and 'private prompt' not in r['details_json'] for r in records)
    await db.close()
    # Provider errors never leak inference slots.
    await reset_paid()
    with patch.dict(os.environ,{'LLM_PROVIDER':'bedrock','AI_MONTHLY_BUDGET_USD':'10'}),patch('boto3.client') as factory,patch.object(llm,'SLOT_WAIT_SEC',0.1):
        factory.return_value.converse.side_effect=TimeoutError('Acceptance unknown')
        for _ in range(llm.INFERENCE_SLOTS+1):
            try:
                llm.complete_text('private prompt',llm.rank_model_id())
            except HTTPException as exc:
                assert exc.status_code==503
        factory.return_value.converse.side_effect=None
        factory.return_value.converse.return_value=finished
        assert llm.complete_text('private prompt',llm.rank_model_id())=='done','Exception leaked semaphore slot'
    # A model AWS has not enabled for the account steps down to the tier's older
    # model, is not charged, and is skipped on later calls; any other failure does not fall back.
    await reset_paid()
    gated=ClientError({'Error':{'Code':'AccessDeniedException','Message':'anthropic.claude-sonnet-5-5 is not available for this account.'}},'Converse')
    newest,older=llm.TIERS['write']
    with patch.dict(os.environ,{'LLM_PROVIDER':'bedrock','AI_MONTHLY_BUDGET_USD':'10'}),patch('boto3.client') as factory,patch.dict(llm._gated_until,clear=True):
        answered={'output':{'message':{'content':[{'text':'fallback'}]}},'usage':{'inputTokens':5,'outputTokens':5}}
        factory.return_value.converse.side_effect=lambda **kw: (_ for _ in ()).throw(gated) if kw['modelId']==newest else answered
        used=[]
        assert llm.complete_text('brief',newest,on_model=used.append)=='fallback' and used==[older]
        assert [c.kwargs['modelId'] for c in factory.return_value.converse.call_args_list]==[newest,older]
        factory.return_value.converse.reset_mock()
        assert llm.complete_text('brief',newest)=='fallback'
        assert [c.kwargs['modelId'] for c in factory.return_value.converse.call_args_list]==[older],'gated model retried at once'
    db=await get_db()
    charged=[r['details_json'] for r in await (await db.execute("SELECT details_json FROM usage_events WHERE event_type='bedrock_reserved'")).fetchall()]
    await db.close()
    assert len(charged)==2 and all(older in d for d in charged),'gated attempt was charged'
    # The whole account barred (Error 002): every model is refused before inference,
    # nothing stays reserved, and contact review stops instead of retrying and splitting.
    await reset_paid()
    barred=ClientError({'Error':{'Code':'ValidationException','Message':'Error 002: Access to Bedrock models is not allowed for this account'}},'Converse')
    with patch.dict(os.environ,{'LLM_PROVIDER':'bedrock','AI_MONTHLY_BUDGET_USD':'10'}),patch('boto3.client') as factory,patch.dict(llm._gated_until,clear=True):
        factory.return_value.converse.side_effect=barred
        try:
            llm.complete_text('brief',llm.rank_model_id())
            raise AssertionError('A barred account returned text')
        except HTTPException as exc:
            assert exc.status_code==503
        assert llm.refused_everywhere(llm.rank_model_id())
        from app.services import contact_ai_review
        factory.return_value.converse.reset_mock()
        batch=[{'email':f'p{i}@co.com','first_name':'P','last_name':str(i),'title':'Analyst'} for i in range(8)]
        assert await contact_ai_review._review_batch(batch,'Co')=={}
        assert factory.return_value.converse.call_count<=len(llm.model_chain(llm.validate_model(llm.rank_model_id()))),\
            f'review retried a barred account {factory.return_value.converse.call_count} times'
    db=await get_db()
    held=(await (await db.execute("SELECT COUNT(*) FROM usage_events WHERE event_type='bedrock_reserved'")).fetchone())[0]
    await db.close()
    assert held==0,'a refused call kept its budget reservation'
    await reset_paid()
    with patch.dict(os.environ,{'LLM_PROVIDER':'bedrock','AI_MONTHLY_BUDGET_USD':'10'}),patch('boto3.client') as factory,patch.dict(llm._gated_until,clear=True):
        factory.return_value.converse.side_effect=ClientError({'Error':{'Code':'ThrottlingException','Message':'Too many tokens'}},'Converse')
        try:
            llm.complete_text('brief',newest)
            raise AssertionError('Throttling silently fell back to an older model')
        except HTTPException as exc:
            assert exc.status_code==503
        assert factory.return_value.converse.call_count==1
    for configured in ('bad','-1','100001'):
        with patch.dict(os.environ,{'AI_MONTHLY_BUDGET_USD':configured}):
            try:
                reserve_bedrock_invocation(llm.rank_model_id())
                raise AssertionError('Invalid budget configuration accepted')
            except HTTPException as exc:
                assert exc.status_code==503
    with patch('app.database.is_postgres',return_value=True):
        try:
            reserve_bedrock_invocation(llm.rank_model_id())
            raise AssertionError('Unverified PostgreSQL adapter accepted')
        except HTTPException as exc:
            assert exc.status_code==503
    import sqlite3
    with patch.dict(os.environ,{'LLM_PROVIDER':'bedrock'}), patch('app.services.generation_policy.sqlite3.connect',side_effect=sqlite3.OperationalError('unavailable')),patch('boto3.client') as factory:
        try:
            llm.complete_text('private prompt',llm.rank_model_id())
            raise AssertionError('Provider called without durable quota')
        except HTTPException as exc:
            assert exc.status_code==503
        factory.return_value.converse.assert_not_called()
    print('Environment, Bedrock, generation concurrency, DNS and SMTP safety passed')


if __name__ == '__main__':
    asyncio.run(tests())
