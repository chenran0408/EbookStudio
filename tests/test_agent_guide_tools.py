import json
import re
import unittest

from agent_mcp import _Session
from studio_agent import ACTIONS, execute


class AgentGuideTests(unittest.TestCase):
    def test_every_advertised_tool_has_one_entry_in_the_served_guide(self):
        session = _Session(execute, ACTIONS)
        session.request('initialize', {'protocolVersion': '2025-11-25'})
        session.handle({'jsonrpc': '2.0', 'method': 'notifications/initialized'})
        tools = session.request('tools/list', {})['tools']
        guide = session.request('tools/call', {'name': 'ebook_guide', 'arguments': {}})
        markdown = json.loads(guide['content'][0]['text'])['markdown']
        documented = re.findall(r'^\|\s*`?(ebook_\w+)`?\s*\|', markdown, re.MULTILINE)
        self.assertCountEqual(documented, [tool['name'] for tool in tools])
