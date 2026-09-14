"""Real MCP stdio client integration against local static APK fixtures."""
import asyncio
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from contextlib import asynccontextmanager
from datetime import timedelta
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from asc_mcp.backend import Backend, AnalysisError

ROOT = Path(__file__).resolve().parents[2]
APK_DIR = Path(os.environ.get('ASC_MCP_APK_DIR','/nonexistent'))


class ProtocolTests(unittest.IsolatedAsyncioTestCase):
    @asynccontextmanager
    async def connected(self):
        parameters = StdioServerParameters(command=sys.executable, args=['-m','asc_mcp'], cwd=str(ROOT),
                                            env={'PYTHONPATH':str(ROOT),'PYTHONDONTWRITEBYTECODE':'1'})
        with tempfile.TemporaryFile(mode='w+') as logs:
            async with stdio_client(parameters,errlog=logs) as streams:
                async with ClientSession(*streams,read_timeout_seconds=timedelta(seconds=60)) as client:
                    await client.initialize()
                    self.client=client
                    yield client

    async def call(self, tool_name, **args):
        result = await self.client.call_tool(tool_name,args)
        self.assertFalse(result.isError,str(result.content))
        return result.structuredContent

    async def test_discovery_schema_resource_and_errors(self):
        async with self.connected():
            tools = (await self.client.list_tools()).tools
            self.assertEqual(len(tools),11)
            by_name = {tool.name:tool for tool in tools}
            self.assertTrue(by_name['asc_source'].annotations.readOnlyHint)
            self.assertFalse(by_name['asc_open_apk'].annotations.readOnlyHint)
            self.assertEqual(by_name['asc_source'].inputSchema['properties']['limit']['maximum'],64000)
            self.assertFalse((await self.call('asc_status'))['loaded'])
            resources = (await self.client.list_resources()).resources
            self.assertIn('asc://session',[str(item.uri) for item in resources])
            resource = await self.client.read_resource('asc://session')
            self.assertFalse(json.loads(resource.contents[0].text)['loaded'])
            for name,args in [('asc_list_classes',{}),('asc_source',{'class_name':'G','limit':0}),
                              ('asc_source',{'class_name':'G','offset':-1}),('asc_open_apk',{'path':'/not-present.apk'})]:
                response = await self.client.call_tool(name,args)
                self.assertTrue(response.isError, (name,response))
            self.assertFalse((await self.call('asc_status'))['loaded'])

    @unittest.skipUnless((APK_DIR/'funnyV3.apk').exists(),'设置 ASC_MCP_APK_DIR 以验证真实 APK')
    async def test_apk_tools_roundtrip_and_recovery(self):
        async with self.connected():
            status = await self.call('asc_open_apk',path=str(APK_DIR/'funnyV3.apk'))
            self.assertEqual((status['dex_count'],status['class_count']),(1,93))
            sid=status['session_id']
            first=await self.call('asc_list_classes',limit=2)
            second=await self.call('asc_list_classes',offset=first['next_offset'],limit=2)
            self.assertFalse(set(first['items']) & set(second['items']))
            self.assertEqual(first['session_id'],sid)
            members=await self.call('asc_class_members',class_name='G')
            self.assertTrue(any(m['name']=='a' and m['kind']=='method' for m in members['items']))
            self.assertTrue(any(m['name']=='a' and m['kind']=='field' for m in members['items']))
            key='Lcom/yuuki/ukiinjector/MainActivity;'
            a=await self.call('asc_source',class_name=key,limit=128)
            b=await self.call('asc_source',class_name=key,offset=128,limit=128)
            all_source=await self.call('asc_source',class_name=key,limit=64000)
            self.assertEqual(a['text']+b['text'],all_source['text'][:256])
            self.assertIn('<manifest',(await self.call('asc_manifest'))['text'])
            defs=await self.call('asc_find_definitions',class_name='G',method_name='a',arity=1)
            self.assertTrue(defs['items'])
            self.assertEqual({item['class_name'] for item in defs['items']},{'LG;'})
            refs=await self.call('asc_find_references',class_name='G',kind='method',name='a',arity=1)
            self.assertEqual(refs['total'],2)
            self.assertTrue(all(item['matched_text']=='LG;->a(int)' for item in refs['items']))
            fields=await self.call('asc_find_references',class_name=key,kind='field',name='navigator')
            self.assertEqual(fields['total'],3)
            types=await self.call('asc_find_references',class_name='G',kind='class')
            self.assertTrue(any(item['class_name']!='LG;' for item in types['items']))
            global_names=await self.call('asc_search_members',kind='method',query='a',limit=3)
            self.assertGreater(global_names['total'],3)
            source=all_source['text'];offset=source.index('this.navigator.a(')+len('this.navigator.')
            line=source.count('\n',0,offset)+1;column=offset-source.rfind('\n',0,offset)
            resolved=await self.call('asc_resolve_symbol',class_name=key,line=line,column=column)
            self.assertEqual(resolved['owners'],['LG;'])
            bad=await self.client.call_tool('asc_open_apk',{'path':'/no-such-app.apk'})
            self.assertTrue(bad.isError)
            self.assertEqual((await self.call('asc_status'))['session_id'],sid)
            self.assertTrue((await self.client.call_tool('asc_find_references',{'class_name':'G','kind':'field'})).isError)
            self.assertTrue((await self.client.call_tool('asc_resolve_symbol',{'class_name':'G','line':999999,'column':1})).isError)
            self.assertFalse((await self.call('asc_close_apk'))['loaded'])
            self.assertTrue((await self.client.call_tool('asc_source',{'class_name':'G'})).isError)
            await self.call('asc_open_apk',path=str(APK_DIR/'check_env.apk'))
            self.assertEqual((await self.call('asc_status'))['dex_count'],4)
            refs=await self.call('asc_find_references',class_name='com.yuuki.check_env.MainActivity',kind='method',name='is_env_abnormal')
            self.assertGreater(refs['total'],0)
            self.assertIn('is_env_abnormal',(await self.call('asc_source',class_name='com.yuuki.check_env.MainActivity'))['text'])
            # Concurrent requests are serialized without mixing response frames.
            results=await asyncio.gather(self.call('asc_list_classes',query='MainActivity'),self.call('asc_status'))
            self.assertTrue(results[0]['items']);self.assertTrue(results[1]['loaded'])

    @unittest.skipUnless(Path('/proc/self/task').exists(), 'Linux process lifecycle check')
    async def test_disconnect_reaps_worker(self):
        def children(pid):
            path=Path(f'/proc/{pid}/task/{pid}/children')
            return set(map(int,path.read_text().split())) if path.exists() else set()
        before=children(os.getpid())
        async with self.connected():
            await self.call('asc_status')
            servers=children(os.getpid())-before
            workers={child for server in servers for child in children(server)}
            self.assertTrue(workers)
        for _ in range(30):
            if all(not Path(f'/proc/{pid}').exists() for pid in workers):break
            await asyncio.sleep(0.1)
        self.assertTrue(all(not Path(f'/proc/{pid}').exists() for pid in workers))


class WorkerLifecycle(unittest.IsolatedAsyncioTestCase):
    async def test_timeout_kills_worker_and_next_call_recovers(self):
        backend=Backend(timeout=0.01)
        try:
            await backend._start()
            pid=backend.process.pid
            # Stop the actual worker so a status request deterministically times out.
            import signal
            os.kill(pid,signal.SIGSTOP)
            with self.assertRaisesRegex(AnalysisError,'超时'):
                await backend.call('status')
            self.assertIsNone(backend.process)
            with self.assertRaises(ProcessLookupError):os.kill(pid,0)
            backend.timeout=30
            self.assertFalse((await backend.call('status'))['loaded'])
            pid=backend.process.pid
        finally:await backend.close()
        with self.assertRaises(ProcessLookupError):os.kill(pid,0)

    async def test_cancel_kills_worker(self):
        import signal
        backend=Backend(timeout=30)
        try:
            await backend._start();pid=backend.process.pid
            os.kill(pid,signal.SIGSTOP)
            task=asyncio.create_task(backend.call('status'))
            await asyncio.sleep(0.03)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):await task
            self.assertIsNone(backend.process)
            with self.assertRaises(ProcessLookupError):os.kill(pid,0)
        finally:await backend.close()


if __name__=='__main__':unittest.main(verbosity=2)
