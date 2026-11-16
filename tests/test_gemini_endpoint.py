import runpy
import unittest
from unittest.mock import patch


class GeminiEndpointTests(unittest.TestCase):
    def test_client_explicitly_selects_preview_endpoint(self):
        with patch('google.genai.Client') as constructor:
            runpy.run_module('clients.gemini_client', run_name='endpoint_test')
        self.assertEqual(constructor.call_args.kwargs['http_options'].api_version, 'v1beta')

    def test_summary_uses_the_same_explicit_client(self):
        from clients import gemini_client, insights
        self.assertIs(insights._gemini_client, gemini_client._client)
