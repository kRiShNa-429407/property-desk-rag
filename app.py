import os
import re
from pathlib import Path

import streamlit as st
from dotenv import dotenv_values

from llm import ChatError, ask
from rag import DATA_FILE, Retriever, load_passages

st.set_page_config(page_title='Property Desk', page_icon='🏠', layout='centered')


def configured_key():
    values = dotenv_values(Path(__file__).parent / '.env')
    return os.getenv('OPENROUTER_API_KEY') or values.get('OPENROUTER_API_KEY') or ''


@st.cache_resource
def build_retriever(file_contents):
    # File contents form the cache key, so replacing the text file rebuilds the index.
    return Retriever(load_passages(DATA_FILE))


def show_sources(sources):
    if not sources:
        return
    with st.expander('Sources'):
        for number, item in enumerate(sources, 1):
            st.markdown(f'[{number}] [{item["title"]}]({item["url"]})')


st.title('Property Desk')
st.caption('Ask about DarGlobal and Wasalt.')

if not DATA_FILE.exists():
    st.error('No data file yet. Run: python scrape.py')
    st.stop()
try:
    retriever = build_retriever(DATA_FILE.read_text(encoding='utf-8'))
except ValueError as error:
    st.error(str(error))
    st.stop()

api_key = configured_key()
if st.button('New chat'):
    st.session_state.messages = []
    st.rerun()

if 'messages' not in st.session_state:
    st.session_state.messages = []

for message in st.session_state.messages:
    with st.chat_message(message['role']):
        st.markdown(message['content'])
        show_sources(message.get('sources', []))

if question := st.chat_input('Ask a question…', max_chars=2000):
    with st.chat_message('user'):
        st.write(question)
    history = st.session_state.messages[-6:]
    search_question = question
    if re.search(r'\b(it|they|those|these|there|more|them|that)\b', question, re.I):
        previous = ' '.join(item['content'] for item in history if item['role'] == 'user')
        search_question = f'{previous} {question}'
    passages = retriever.search(search_question)
    with st.chat_message('assistant'):
        try:
            if not passages:
                answer = 'I could not find that in the collected pages.'
            else:
                with st.spinner('Thinking…'):
                    answer, _ = ask(question, passages, api_key, history)
            st.markdown(answer)
            sources = [vars(item) for item in passages]
            show_sources(sources)
            st.session_state.messages.extend([
                {'role': 'user', 'content': question},
                {'role': 'assistant', 'content': answer, 'sources': sources},
            ])
            st.session_state.messages = st.session_state.messages[-40:]
        except ChatError as error:
            st.error(str(error))
            show_sources([vars(item) for item in passages])
