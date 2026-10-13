"""Offline streaming regressions: original image context, deferred work, usage chunks."""
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.chat import ChatService
from app.llm import LLMClient


def main():
    class Stream:
        closed = False
        def __iter__(self):
            yield NS(choices=[])
            yield NS(choices=[NS(delta=NS(content='Live '))])
            yield NS(choices=[NS(delta=NS(content='answer'))])
            yield NS(choices=[])
        def close(self): self.closed = True

    stream = Stream()
    client = object.__new__(LLMClient)
    client.model, client.temperature, client.max_tokens = 'test', 0, 50
    client._client = NS(chat=NS(completions=NS(create=lambda **kwargs: stream)))
    received = []
    assert client.chat([], on_chunk=received.append) == 'Live answer'
    assert received == ['Live ', 'answer'] and stream.closed
    print('PASS: usage-only chunks do not interrupt live output; stream closes')

    class EmptyStream(Stream):
        def __iter__(self): yield NS(choices=[])
    class ErrorStream(Stream):
        def __iter__(self): yield NS(choices=[], error={"message": "upstream failed"})
    for invalid in (EmptyStream(), ErrorStream()):
        client._client.chat.completions.create = lambda **kwargs: invalid
        try: client.chat([], on_chunk=lambda _: None)
        except RuntimeError: pass
        else: raise AssertionError("An empty or failed response must not become a successful answer")
        assert invalid.closed
    print("PASS: empty/provider-error streams surface a failure")

    service = object.__new__(ChatService)
    chat = NS(id='chat', project_id='project', title='New chat', summary='')
    persisted = []
    service.settings = NS(memory_chat_window=10, top_k=3, max_distance=.8, memory_context_chars=10000)
    service.chats = NS(get_owned=lambda *_: chat, recent_messages=lambda *a, **k: [],
                       add_message=lambda *args: persisted.append(args), rename=Mock())
    source = NS(id='image-source',project_id='project',filename='image.png')
    service.sources = NS(ready_ids=lambda *_: ['image-source'],get=lambda *_: source)
    service.embedder = NS(embed=lambda _: [[1.,0.]])
    chunk = NS(source_id=source.id, locator_kind='image', locator_value=source.id,
               heading='',text='Image content embedded directly',start_ts=None,end_ts=None,distance=0)
    service.store = NS(query=lambda *a, **k: [chunk])
    service._rewrite = lambda question, prior: (question, False)
    service._blocks = lambda *_: ('','')
    service._after_turn = Mock()
    service._generate_title = Mock(return_value='Diagram Summary')
    queued = []
    received = []
    def reply(messages, on_chunk=None):
        assert any(part.get('type') == 'image_url' for part in messages[-1]['content'])
        for token in ['A diagram ', '[1].']: on_chunk(token)
        return 'A diagram [1].'
    service.llm = NS(chat=reply)
    root = Path(__file__).resolve().parents[1]/'data'
    root.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(dir=root) as temp:
        image = Path(temp)/'image.png'; image.write_bytes(b'original image bytes')
        service.storage = NS(open_for_read=lambda *_: image)
        result = service.send('user','chat','Explain this image', received.append,
                              source_ids=['image-source'], defer=lambda *a, **k: queued.append((a,k)))
    assert received == ['A diagram ', '[1].']
    assert result['grounded'] and result['citations'][0]['source_id'] == 'image-source'
    assert result['chat_title'] == 'Diagram Summary'
    assert persisted[-1][1] == 'assistant'
    service._after_turn.assert_not_called()
    service._generate_title.assert_called_once()
    assert len(queued) == 1
    args, kwargs = queued[0]; args[0](*args[1:], **kwargs)
    service._after_turn.assert_called_once()
    print('PASS: original image streams with audited citations before auxiliary work')
    print('STREAMING VERIFIED')

if __name__ == '__main__': main()
