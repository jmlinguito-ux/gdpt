"""Guards for the window URL / WebView2 cache behaviour.

These are load-bearing, not cosmetic: pywebview serves local files from a fixed
port with a persistent profile, so Chromium can re-serve a cached index.html
forever. A stale page keeps pointing at the old ?v= asset URLs, which is how a
rebuilt records editor (ACT column + delete buttons) stayed invisible even
though the files on disk were correct.
"""

import os
import re
import unittest

import app


class UiCacheBustingTests(unittest.TestCase):
    def test_index_url_stamps_the_page_with_its_mtime(self):
        index_html = os.path.join(app._resource_dir(), 'index.html')
        url = app._index_url()
        self.assertTrue(url.startswith(index_html + '?v='), url)
        self.assertEqual(int(url.rsplit('?v=', 1)[1]), int(os.path.getmtime(index_html)))

    def test_index_url_stamp_follows_a_rebuilt_page(self):
        index_html = os.path.join(app._resource_dir(), 'index.html')
        original = os.path.getmtime(index_html)
        try:
            os.utime(index_html, (original + 60, original + 60))
            moved = app._index_url()
        finally:
            os.utime(index_html, (original, original))
        self.assertNotEqual(moved, app._index_url())
        self.assertEqual(int(moved.rsplit('?v=', 1)[1]), int(original + 60))

    def test_page_assets_are_version_stamped(self):
        # Assets must carry ?v= so a cached copy cannot outlive the change that
        # made it stale. Bump the stamp whenever the asset changes.
        index_html = os.path.join(app._resource_dir(), 'index.html')
        with open(index_html, encoding='utf-8') as handle:
            html = handle.read()
        for asset in ('style.css', 'app.js', 'editor.js', 'shell.js', 'icons.js'):
            self.assertRegex(html, re.escape(asset) + r'\?v=\d')

    def test_disk_cache_guard_sets_webview_args_without_clobbering(self):
        key = 'WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS'
        original = os.environ.get(key)
        try:
            os.environ.pop(key, None)
            app._disable_webview_disk_cache()
            self.assertIn('disk-cache-size=1', os.environ[key])

            os.environ[key] = '--foo=bar'
            app._disable_webview_disk_cache()
            self.assertEqual(os.environ[key], '--foo=bar --disk-cache-size=1')

            app._disable_webview_disk_cache()   # idempotent
            self.assertEqual(os.environ[key], '--foo=bar --disk-cache-size=1')
        finally:
            if original is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = original


if __name__ == '__main__':
    unittest.main()
