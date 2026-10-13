from pathlib import Path
import importlib.util,json,threading,urllib.request
from http.server import ThreadingHTTPServer
spec=importlib.util.spec_from_file_location('mock_llm',Path('scripts/mock_llm.py'))
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
server=ThreadingHTTPServer(('127.0.0.1',0),m.Handler)
thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
def ask(system):
 body={'messages':[{'role':'system','content':system},{'role':'user','content':[{'type':'text','text':'Generate exactly 3 questions types [mcq]. TOPIC topic-1 (Image)\nSOURCE sample LOCATOR image:1\nA pressure gauge.'},{'type':'image_url','image_url':{'url':'data:image/png;base64,eA=='}}]}]}
 req=urllib.request.Request(f'http://127.0.0.1:{server.server_port}/v1/chat/completions',data=json.dumps(body).encode(),headers={'Content-Type':'application/json'})
 with urllib.request.urlopen(req) as response:return json.load(response)['choices'][0]['message']['content']
try:
 for system in ['You write quiz questions from the sources.', 'Write multiple-choice questions.']:
  assert len(json.loads(ask(system))['questions'])==3
 assert 'image shows' in ask('Describe this image as study text.')
 print('MOCK MULTIMODAL VERIFIED: image quiz JSON and image caption routes')
finally:server.shutdown();server.server_close();thread.join()
