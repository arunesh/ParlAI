#!/usr/bin/env python3
# Copyright (c) Facebook, Inc. and its affiliates.
# This source code is licensed under the MIT license found in the
# LICENSE file in the root directory of this source tree.
"""
APIs for retrieving a list of "Contents" using an "Search Query".

The term "Search Query" here refers to any abstract form of input string. The definition
of "Contents" is also loose and depends on the API.
"""

from abc import ABC, abstractmethod
import requests
import googlesearch
import fire
import bs4
import rich
import rich.markup
import html2text
import json
from typing import Any, Dict, List

from parlai.core.opt import Opt
from parlai.utils import logging


requests.packages.urllib3.disable_warnings()

import ssl

try:
    _create_unverified_https_context = ssl._create_unverified_context
except AttributeError:
    # Legacy Python that doesn't verify HTTPS certificates by default
    pass
else:
    # Handle target environment that doesn't support HTTPS verification
    ssl._create_default_https_context = _create_unverified_https_context

CONTENT = 'content'
DEFAULT_NUM_TO_RETRIEVE = 5


class RetrieverAPI(ABC):
    """
    Provides the common interfaces for retrievers.

    Every retriever in this modules must implement the `retrieve` method.
    """

    def __init__(self, opt: Opt):
        self.skip_query_token = opt['skip_retrieval_token']

    @abstractmethod
    def retrieve(
        self, queries: List[str], num_ret: int = DEFAULT_NUM_TO_RETRIEVE
    ) -> List[Dict[str, Any]]:
        """
        Implements the underlying retrieval mechanism.
        """

    def create_content_dict(self, content: list, **kwargs) -> Dict:
        resp_content = {CONTENT: content}
        resp_content.update(**kwargs)
        return resp_content


class SearchEngineRetrieverMock(RetrieverAPI):
    """
    For unit tests and debugging (does not need a running server).
    """

    def retrieve(
        self, queries: List[str], num_ret: int = DEFAULT_NUM_TO_RETRIEVE
    ) -> List[Dict[str, Any]]:
        all_docs = []
        for query in queries:
            if query == self.skip_query_token:
                docs = None
            else:
                docs = []
                for idx in range(num_ret):
                    doc = self.create_content_dict(
                        f'content {idx} for query "{query}"',
                        url=f'url_{idx}',
                        title=f'title_{idx}',
                    )
                    docs.append(doc)
            all_docs.append(docs)
        return all_docs


class SearchEngineRetriever(RetrieverAPI):
    """
    Queries a server (eg, search engine) for a set of documents.

    This module relies on a running HTTP server. For each retrieval it sends the query
    to this server and receieves a JSON; it parses the JSON to create the the response.
    """

    def __init__(self, opt: Opt):
        super().__init__(opt=opt)
        self.server_address = self._validate_server(opt.get('search_server'))
        self.use_local = True
        if self.server_address.startswith('local_google'):
            logging.warning('Using local_google search')
            self.use_local = True

    def _query_search_server(self, query_term, n):
        server = self.server_address
        req = {'q': query_term, 'n': n}
        logging.debug(f'sending search request to {server}')
        server_response = requests.post(server, data=req)
        resp_status = server_response.status_code
        if resp_status == 200:
            return server_response.json().get('response', None)
        logging.error(
            f'Failed to retrieve data from server! Search server returned status {resp_status}'
        )
    
    def _get_and_parse(self, url: str) -> Dict[str, str]:
    
        resp = requests.get(url)
        try:
            resp = requests.get(url)
        except Exception as e:
            return None
        else:
            page = resp.content
    
        ###########################################################################
        # Prepare the title
        ###########################################################################
        output_dict = dict(title="", content="", url=url)
        soup = bs4.BeautifulSoup(page, features="lxml")
        pre_rendered = soup.find("title")
        output_dict["title"] = (
            pre_rendered.renderContents().decode() if pre_rendered else None
        )
        if (output_dict["title"] is None):
            output_dict["title"] = "None"

        output_dict["title"] = output_dict["title"].replace("\n", "").replace("\r", "")
    
        ###########################################################################
        # Prepare the content
        ###########################################################################
        text_maker = html2text.HTML2Text()
        text_maker.ignore_links = True
        text_maker.ignore_tables = True
        text_maker.ignore_images = True
        text_maker.ignore_emphasis = True
        text_maker.single_line = True
        output_dict["content"]  = text_maker.handle(page.decode("utf-8", errors="ignore"))
        
        ###########################################################################
        # Log it
        ###########################################################################
        title_str = (f"`{rich.markup.escape(output_dict['title'])}`" 
            if output_dict["title"] else '<No Title>'
        )
        print(
            f"title: {title_str}",
            f"url: {rich.markup.escape(output_dict['url'])}",
            f"content: {len(output_dict['content'])}"
        )
    
        return output_dict


    def _query_local_search_server(self, query_term, n):
        urls = googlesearch.search(query_term, num=n, stop=n)
        content = []
        for url in urls:
            if len(content) >= n:
                break
            maybe_content = self._get_and_parse(url)
            if maybe_content:
                content.append(maybe_content)

        content = content[:n]  # Redundant [:n]
        output = dict(response=content)
        return output.get('response', None)

    def _validate_server(self, address):
        if not address:
            raise ValueError('Must provide a valid server for search')
        if address.startswith('local_google'):
            logging.warning('Using local_google search')
            self.use_local = True
            return address
        if address.startswith('http://') or address.startswith('https://'):
            return address
        PROTOCOL = 'http://'
        logging.warning(f'No portocol provided, using "{PROTOCOL}"')
        return f'{PROTOCOL}{address}'

    def _retrieve_single(self, search_query: str, num_ret: int):
        if search_query == self.skip_query_token:
            return None

        retrieved_docs = []
        self.use_local = True
        if (self.use_local):
            search_server_resp = self._query_local_search_server(search_query, num_ret)
        else:
            search_server_resp = self._query_search_server(search_query, num_ret)
        if not search_server_resp:
            logging.warning(
                f'Server search did not produce any results for "{search_query}" query.'
                ' returning an empty set of results for this query.'
            )
            return retrieved_docs

        for rd in search_server_resp:
            url = rd.get('url', '')
            title = rd.get('title', '')
            sentences = [s.strip() for s in rd[CONTENT].split('\n') if s and s.strip()]
            print(sentences)
            retrieved_docs.append(
                self.create_content_dict(url=url, title=title, content=sentences)
            )
        return retrieved_docs

    def retrieve(
        self, queries: List[str], num_ret: int = DEFAULT_NUM_TO_RETRIEVE
    ) -> List[Dict[str, Any]]:
        # TODO: update the server (and then this) for batch responses.
        return [self._retrieve_single(q, num_ret) for q in queries]

class LocalSearchTest:
    def test_parser(self, url):
        print(_get_and_parse(url))

    def test_server(self, query, n):
        print(f"Query: `{query}`")
        print(f"n: {n}")

        retriever = SearchEngineRetriever(
            dict(
                search_server="local_google",
                skip_retrieval_token=False,
            )
        )
        retriever._validate_server("local_google")
        print("Retrieving one.")
        print(retriever._retrieve_single(query, n))
        print("Done.")

if __name__ == "__main__":
    fire.Fire(LocalSearchTest)
